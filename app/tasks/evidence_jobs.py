"""G-2 evidence observation job bodies.

Extracted verbatim from ``app.tasks.jobs`` (§4.5 size decomposition). The
``@task`` entries ``run_g2_candidate_recheck`` / ``collect_g2_evidence`` stay in
``app.tasks.jobs`` (registration names unchanged) as thin shells that own the db
lifecycle via the shared ``task_session`` seam
(``app.core.database.task_session``) and delegate the read-only sweep here.

``_write_g2_daily_evidence_draft`` lives here but is re-exported from
``app.tasks.jobs`` and passed back in by the ``collect_g2_evidence`` shell as
``write_daily_draft`` so the test that patches ``jobs._write_g2_daily_evidence_draft``
still intercepts the draft write. Service methods
(``StrategyMonitoringService.preview_candidates`` /
``AnalyticsReportingService.build_g2_evidence_summary``) are patched at the class
level in tests, so they are intercepted regardless of this module boundary.

Payload 계약(방어적 DTO 규율 Phase 5): 두 sweep 이 내보내는 요약은
:mod:`app.schemas.g2_evidence` 가 선언하고, ``Analytics.event_data`` 직렬화는 P4.2 의
단일 경로(``dump_analytics_event``)를 쓴다. 남은 ``json.dumps`` 한 곳은 daily
``manifest-draft.json`` **파일** 쓰기다: 그 파일은 ``scripts/build_g2_exit_review.py``
가 되읽는 diff 가능한 증적 산출물이라 ``indent=2, sort_keys=True`` 로 바이트 안정성을
유지해야 하는데 pydantic 직렬화에는 ``sort_keys`` 대응이 없어, 모델로 태우면 기존 모든
draft 의 키 순서가 조용히 바뀐다. 같은 이유로 ``build_daily_evidence_draft`` 는 아직
dict 를 받고, 타입 요약은 그 호출 한 줄에서만 dict 로 낮춘다(builder 타입화는 후속).
"""

import logging
from typing import Callable

from app.core.config import settings
from app.core.constants import (
    COLLECT_G2_EVIDENCE_EVENT_TYPE,
    G2_CANDIDATE_RECHECK_EVENT_TYPE,
)
from app.models.models import User
from app.schemas.g2_evidence import (
    G2CandidateRecheckOperatorEntry,
    G2CandidateRecheckOperatorError,
    G2CandidateRecheckOperatorResult,
    G2CandidateRecheckSummary,
    G2CollectEvidenceSummary,
    G2EvidenceOperatorSnapshot,
    G2EvidenceOperatorSnapshotEntry,
    G2EvidenceOperatorSnapshotError,
    G2LedgerOperatorSummary,
    G2LedgerOperatorSummaryEntry,
    G2LedgerOperatorSummaryError,
)
from app.services.analytics_event_payload import dump_analytics_event
from app.services.opportunity_monitoring import StrategyMonitoringService

logger = logging.getLogger(__name__)


def run_g2_candidate_recheck_job(db) -> G2CandidateRecheckSummary:
    """Read-only G-2 candidate re-check body (db lifecycle owned by the caller).

    Re-runs the read-only ``preview_candidates`` for every active synthetic
    operator (``username LIKE 'synthetic-%'``) to measure when niche biddable
    inventory recovers and candidates reappear. This is an *observation* tool for
    G-2 live evidence — it deliberately calls ``preview_candidates`` (read-only),
    never ``execute_monitoring``, and writes nothing to operator data
    (``operator_strategy_runs`` / ``bid_decision_records`` / notifications). The
    only permitted write is a single ``g2_candidate_recheck`` analytics evidence
    event carrying the per-operator + aggregate summary.

    Each operator is swept inside its own ``try/except`` so one failure cannot
    abort the whole sweep; failures are recorded per operator and the task
    continues.

    Returns the typed summary (:class:`G2CandidateRecheckSummary`); the celery
    shell serializes it for the result backend.
    """
    from app.core.single_user import ensure_operator_account
    from app.models.models import Analytics

    operator = ensure_operator_account(db)
    operators = (
        db.query(User)
        .filter(User.username.like("synthetic-%"), User.is_active.is_(True))
        .order_by(User.id.asc())
        .all()
    )

    service = StrategyMonitoringService()
    per_operator: list[G2CandidateRecheckOperatorEntry] = []
    total_candidates = 0
    operators_with_candidates = 0
    error_count = 0

    for op in operators:
        try:
            preview = service.preview_candidates(
                db,
                limit=10,
                high_priority_only=False,
                operator=op,
            )
            evaluated = int(preview.get("evaluated_project_count", 0) or 0)
            returned = int(preview.get("returned_candidate_count", 0) or 0)
            total_candidates += returned
            if returned > 0:
                operators_with_candidates += 1
            per_operator.append(
                G2CandidateRecheckOperatorResult(
                    operator_id=int(op.id),
                    username=str(op.username or ""),
                    evaluated_project_count=evaluated,
                    returned_candidate_count=returned,
                )
            )
        except Exception as exc:  # noqa: BLE001 — one operator must not abort the sweep
            error_count += 1
            logger.exception(
                "g2_candidate_recheck failed for operator_id=%s username=%s",
                getattr(op, "id", None),
                getattr(op, "username", None),
            )
            per_operator.append(
                G2CandidateRecheckOperatorError(
                    operator_id=int(op.id),
                    username=str(op.username or ""),
                    error=type(exc).__name__,
                )
            )

    summary = G2CandidateRecheckSummary(
        operator_count=len(operators),
        total_candidates=total_candidates,
        operators_with_candidates=operators_with_candidates,
        error_count=error_count,
        per_operator=per_operator,
    )

    # The only permitted write: one analytics evidence event for the sweep.
    analytics = Analytics(
        user_id=operator.id,
        event_type=G2_CANDIDATE_RECHECK_EVENT_TYPE,
        event_data=dump_analytics_event(summary),
    )
    db.add(analytics)
    db.commit()

    logger.info(
        "g2_candidate_recheck completed operators=%s total_candidates=%s "
        "operators_with_candidates=%s errors=%s",
        summary.operator_count,
        total_candidates,
        operators_with_candidates,
        error_count,
    )
    return summary


def _write_g2_daily_evidence_draft(
    *, target_summaries: list[G2LedgerOperatorSummaryEntry]
) -> None:
    """Write today's ledger-based G-2 ``manifest-draft.json`` for the targets.

    Best-effort and idempotent: the KST-day directory is overwritten on re-run so
    ``scripts/build_g2_exit_review.py`` can accumulate ``counted_days``. Writes
    ONLY this local JSON file — no operator data, no execution, no external
    calls. Skipped in ``ENVIRONMENT=test`` to keep the repo working tree clean.
    Callers wrap this in ``try/except`` so a write failure never aborts the sweep.
    See the module docstring for why the draft file keeps ``json.dumps`` and why
    the typed ledger summaries are lowered to dicts at the builder boundary.
    """
    import json
    from pathlib import Path

    from app.core.time import kst_now
    from app.services.g2_evidence_draft import build_daily_evidence_draft

    if settings.ENVIRONMENT == "test":
        return
    target_ids = settings.g2_evidence_target_operator_ids
    if not target_ids:
        return

    run_date_kst = kst_now().date().isoformat()
    draft = build_daily_evidence_draft(
        operator_summaries=[summary.model_dump() for summary in target_summaries],
        target_operator_ids=target_ids,
        run_date_kst=run_date_kst,
        required_days=max(1, int(settings.G2_EVIDENCE_REQUIRED_DAYS)),
    )
    repo_root = Path(__file__).resolve().parents[2]
    draft_dir = repo_root / settings.G2_EVIDENCE_DAILY_DRAFT_DIR / run_date_kst
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "manifest-draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "collect_g2_evidence wrote daily draft date=%s status=%s targets=%s path=%s",
        run_date_kst,
        draft["daily_status"][0]["status"],
        len(target_ids),
        draft_dir / "manifest-draft.json",
    )


def run_collect_g2_evidence_job(
    db,
    *,
    window_days: int,
    recent_limit: int,
    write_daily_draft: Callable[..., None],
) -> G2CollectEvidenceSummary:
    """Read-only per-operator G-2 evidence ledger snapshot body.

    db lifecycle is owned by the caller; ``write_daily_draft`` is injected so the
    ``jobs._write_g2_daily_evidence_draft`` monkeypatch seam is honoured.

    Iterates every active operator — BOTH the canonical operator
    (``ensure_operator_account``) AND every synthetic operator
    (``username LIKE 'synthetic-%'``) — and builds the read-only G-2 evidence
    summary via ``AnalyticsReportingService.build_g2_evidence_summary`` for each.
    It records a SINGLE ``collect_g2_evidence`` analytics evidence event carrying
    a compact per-operator + aggregate roll-up so ``counted_days`` can accumulate
    toward the G-2 exit review.

    When ``G2_EVIDENCE_WRITE_DAILY_DRAFT`` is on it ALSO writes one
    ledger-based ``manifest-draft.json`` per KST day for the configured target
    operators (``G2_EVIDENCE_TARGET_OPERATOR_IDS``) so ``build_g2_exit_review``
    accumulates ``counted_days`` automatically. That draft is the ONLY extra
    write.

    This is a pure *observation* tool: it reads existing data only and writes
    nothing to operator data (``operator_strategy_runs`` / ``bid_decision_records``
    / notifications), never runs the heavy strategy monitor, and never calls
    external services or sends Telegram. The only permitted writes are the single
    analytics evidence event and the daily manifest-draft.json file.

    Each operator is summarized inside its own ``try/except`` so one failure
    cannot abort the whole sweep; failures are recorded per operator and the task
    continues.

    Returns the typed summary (:class:`G2CollectEvidenceSummary`); the celery
    shell serializes it for the result backend.
    """
    from app.core.single_user import ensure_operator_account
    from app.models.models import Analytics
    from app.services.analytics_reporting import AnalyticsReportingService

    operator = ensure_operator_account(db)
    # Canonical operator first, then active synthetic operators (deterministic).
    synthetic_operators = (
        db.query(User)
        .filter(User.username.like("synthetic-%"), User.is_active.is_(True))
        .order_by(User.id.asc())
        .all()
    )
    operators = [operator, *synthetic_operators]

    service = AnalyticsReportingService()
    section_keys = (
        "smoke",
        "strategy_monitor",
        "decision_experiments",
        "synthetic_experiments",
        "notifications",
    )
    per_operator: list[G2EvidenceOperatorSnapshotEntry] = []
    # Full per-target ledger summaries (with blocking_gaps list) feeding the
    # daily manifest-draft.json; a subset of the swept operators.
    target_id_set = set(settings.g2_evidence_target_operator_ids)
    target_summaries: list[G2LedgerOperatorSummaryEntry] = []
    ready_count = 0
    error_count = 0

    for op in operators:
        try:
            summary = service.build_g2_evidence_summary(
                db,
                window_days=window_days,
                recent_limit=recent_limit,
                operator=op,
            )
            evidence_status = str(summary.get("evidence_status") or "")
            if evidence_status == "ready":
                ready_count += 1
            sections = {
                key: str((summary.get(key) or {}).get("status") or "")
                for key in section_keys
            }
            blocking_gaps = [str(gap) for gap in summary.get("blocking_gaps") or []]
            per_operator.append(
                G2EvidenceOperatorSnapshot(
                    operator_id=int(op.id),
                    username=str(op.username or ""),
                    evidence_status=evidence_status,
                    blocking_gaps_count=len(blocking_gaps),
                    sections=sections,
                )
            )
            if int(op.id) in target_id_set:
                target_summaries.append(
                    G2LedgerOperatorSummary(
                        operator_id=int(op.id),
                        username=str(op.username or ""),
                        evidence_status=evidence_status,
                        sections=sections,
                        blocking_gaps=blocking_gaps,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — one operator must not abort the sweep
            error_count += 1
            logger.exception(
                "collect_g2_evidence failed for operator_id=%s username=%s",
                getattr(op, "id", None),
                getattr(op, "username", None),
            )
            per_operator.append(
                G2EvidenceOperatorSnapshotError(
                    operator_id=int(op.id),
                    username=str(op.username or ""),
                    error=type(exc).__name__,
                )
            )
            if int(op.id) in target_id_set:
                target_summaries.append(
                    G2LedgerOperatorSummaryError(
                        operator_id=int(op.id),
                        username=str(op.username or ""),
                        error=type(exc).__name__,
                    )
                )

    summary_payload = G2CollectEvidenceSummary(
        generated_window_days=window_days,
        recent_limit=recent_limit,
        operator_count=len(operators),
        ready_count=ready_count,
        error_count=error_count,
        per_operator=per_operator,
    )

    # The only permitted write: one analytics evidence event for the snapshot.
    analytics = Analytics(
        user_id=operator.id,
        event_type=COLLECT_G2_EVIDENCE_EVENT_TYPE,
        event_data=dump_analytics_event(summary_payload),
    )
    db.add(analytics)
    db.commit()

    # Additional permitted write: the daily ledger-based manifest draft so
    # counted_days accumulates. Never let a draft-write failure abort the
    # sweep or the analytics event already committed above.
    if settings.G2_EVIDENCE_WRITE_DAILY_DRAFT:
        try:
            write_daily_draft(target_summaries=target_summaries)
        except Exception:  # noqa: BLE001 — draft write must not abort the sweep
            logger.exception("collect_g2_evidence daily draft write failed")

    logger.info(
        "collect_g2_evidence completed operators=%s ready=%s errors=%s window_days=%s",
        summary_payload.operator_count,
        ready_count,
        error_count,
        window_days,
    )
    return summary_payload
