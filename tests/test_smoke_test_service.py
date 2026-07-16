"""Smoke test schedule + service."""

from __future__ import annotations

import json
import sqlite3

import pytest
from unittest.mock import MagicMock

# Import the ORM models at module top so SQLAlchemy registers the `projects`
# table on Base.metadata before the `test_db` fixture runs create_all. A lazy
# (in-function) import makes whichever DB-touching test runs first in the
# process fail with "no such table: projects" under isolation (`-k sbert`,
# single-test runs, pytest-xdist sharding). Matches the codebase convention
# (tests/test_predictions.py, tests/test_projects.py).
from app.models.models import Project  # noqa: F401 — registers table metadata
from app.tasks.celery_app import (
    SMOKE_TEST_TASK_NAME,
    build_smoke_test_beat_schedule,
)

# Test DBs use SQLite even when the model column is VECTOR(384). Keep helper
# assignments vector-shaped while letting SQLite persist a non-NULL marker.
sqlite3.register_adapter(list, lambda value: json.dumps(value, ensure_ascii=False))


def test_smoke_test_schedule_empty_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", False)
    assert build_smoke_test_beat_schedule() == {}


def test_smoke_test_schedule_builds_when_enabled(monkeypatch):
    from celery.schedules import crontab
    from app.core.config import settings
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "SMOKE_TEST_HOUR_KST", 7)
    monkeypatch.setattr(settings, "SMOKE_TEST_MINUTE", 0)
    schedule = build_smoke_test_beat_schedule()
    entry = schedule["smoke_test_daily"]
    assert entry["task"] == SMOKE_TEST_TASK_NAME
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {7}
    assert entry["schedule"].minute == {0}


def test_smoke_service_reports_phases_with_full_chain(monkeypatch):
    """Service produces one PhaseResult per phase even when later phases skip."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    svc = KonepsTelegramSmokeTestService()
    # Mock phase methods individually
    monkeypatch.setattr(svc, "_phase_koneps_collect", lambda db: _mk_phase("koneps_collect", True, "collected 5"))
    monkeypatch.setattr(svc, "_phase_sbert_embedding", lambda db: _mk_phase("sbert_embedding", True, "id=99", project={"id": 99, "title": "t", "budget_estimate": 100.0}))
    monkeypatch.setattr(svc, "_phase_predict_price", lambda db, p: _mk_phase("predict_price", True, "rate=0.9"))
    monkeypatch.setattr(svc, "_phase_candidate_generation", lambda db: _mk_phase("candidate_generation", True, "run_id=10 selected=1"))
    monkeypatch.setattr(svc, "_phase_telegram_ping", lambda **kw: _mk_phase("telegram_ping", True, "sent"))

    report = svc.run(db=MagicMock())
    assert report.overall_passed is True
    assert [p["name"] for p in report.phases] == [
        "koneps_collect",
        "sbert_embedding",
        "predict_price",
        "candidate_generation",
        "telegram_ping",
    ]


def test_smoke_service_skips_downstream_when_collect_fails(monkeypatch):
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    svc = KonepsTelegramSmokeTestService()
    monkeypatch.setattr(svc, "_phase_koneps_collect", lambda db: _mk_phase("koneps_collect", False, "exception"))
    monkeypatch.setattr(svc, "_phase_telegram_ping", lambda **kw: _mk_phase("telegram_ping", True, "sent"))

    report = svc.run(db=MagicMock())
    # Phases 2 and 3 should be marked skipped (passed=False)
    by_name = {p["name"]: p for p in report.phases}
    assert by_name["sbert_embedding"]["passed"] is False
    assert "skipped" in by_name["sbert_embedding"]["detail"]
    assert by_name["predict_price"]["passed"] is False
    assert by_name["candidate_generation"]["passed"] is False
    assert by_name["candidate_generation"]["failure_category"] == "koneps_response"
    # Telegram still attempted (the point of the smoke is to verify Telegram even on failure)
    assert by_name["telegram_ping"]["passed"] is True


class CapturingPredictionPort:
    def __init__(self) -> None:
        self.calls = []

    def predict_price(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "predicted_bid_rate": 0.9,
            "predictor_name": "fake",
            "predicted_price": 90_000_000.0,
            "price_range_min": 89_000_000.0,
            "price_range_max": 91_000_000.0,
            "confidence_score": 0.7,
            "model_version": "fake",
            "predictor_family": "fake",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "fake",
        }


def test_smoke_predict_price_phase_uses_injected_prediction_port(test_db):
    from app.models.models import Project
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    project = Project(
        title="Smoke port project",
        description="port description",
        requirements="port requirements",
        budget_estimate=100_000_000.0,
        category="software",
        business_type_code="0621",
    )
    test_db.add(project)
    test_db.commit()

    port = CapturingPredictionPort()
    service = KonepsTelegramSmokeTestService(price_prediction_port=port)
    result = service._phase_predict_price(test_db, {"id": project.id})

    assert result.passed is True
    assert port.calls
    assert port.calls[0]["category"] == "software"
    assert "port description" in port.calls[0]["description"]
    assert port.calls[0]["business_type_code"] == "0621"


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0.69, False),  # below floor band
        (0.70, True),   # lower edge
        (0.90, True),   # typical
        (1.00, True),   # upper edge == max guardrail ceiling
        (1.01, False),  # above ceiling — clamp didn't apply, smoke must fail
        (1.10, False),  # old upper bound must no longer pass
    ],
)
def test_predict_price_phase_band_rejects_above_ceiling(monkeypatch, rate, expected):
    """predict_price phase passes iff 0.7 <= rate <= 1.0 (guardrail ceiling)."""
    import app.ai.business_group as bg_mod
    import app.services.backtest_cutoff as cutoff_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _RatePredictionPort:
        def predict_price(self, **kwargs):
            return {"predicted_bid_rate": rate, "predictor_name": "stub"}

    class _StubProject:
        id = 1
        budget_estimate = 100_000_000.0
        title = "t"
        description = ""
        requirements = ""
        category = "소프트웨어"
        business_type_code = None
        issuing_agency = None
        demand_agency = None

    class _StubQuery:
        def filter(self, *a, **k):
            return self

        def one(self):
            return _StubProject()

    class _StubDB:
        def query(self, *a, **k):
            return _StubQuery()

    class _StubCutoff:
        def resolve_data_cutoff_at(self, *a, **k):
            return None

        def load_price_history_at_cutoff(self, *a, **k):
            return []

    monkeypatch.setattr(bg_mod, "resolve_business_group", lambda code: None)
    monkeypatch.setattr(cutoff_mod, "BacktestCutoffService", _StubCutoff)

    svc = KonepsTelegramSmokeTestService(price_prediction_port=_RatePredictionPort())
    res = svc._phase_predict_price(_StubDB(), {"id": 1})
    assert res.passed is expected


def test_candidate_generation_phase_records_notification_skip_reason(monkeypatch):
    """A zero-notification monitor run can pass when the skip reason is explicit."""
    import app.services.opportunity_monitoring as monitoring_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _StubMonitoringService:
        SCHEDULED_TRIGGER_SOURCE = "scheduled"

        def execute_monitoring(self, db, *, request, trigger_source):
            return {
                "monitor_run_id": 77,
                "operator_id": 42,
                "current_operator_id": 42,
                "current_operator_username": "operator",
                "evaluated_project_count": 5,
                "selected_candidate_count": 0,
                "persisted_candidate_count": 0,
                "notification_count": 0,
                "new_candidate_count": 0,
            }

    monkeypatch.setattr(monitoring_mod, "StrategyMonitoringService", _StubMonitoringService)

    result = KonepsTelegramSmokeTestService()._phase_candidate_generation(MagicMock())

    assert result.passed is True
    assert result.failure_category == ""
    assert result.skip_reason == "no strategy candidates selected"
    assert result.data["monitor_run_id"] == 77
    assert result.data["source_run_type"] == "operator_strategy_monitor"
    assert result.data["source_run_id"] == 77
    assert result.data["operator_id"] == 42
    assert result.data["operator_scope"] == "operator"
    assert "skip_reason=no strategy candidates selected" in result.detail


def test_persist_report_inserts_trimmed_row(test_db):
    """persist_report inserts a SmokeTestRun, trims per-phase data, parses timestamps."""
    import json

    from app.models.models import SmokeTestRun
    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="2026-06-16T07:00:00+00:00",
        completed_at="2026-06-16T07:01:30+00:00",
        overall_passed=True,
        phases=[
            {"name": "koneps_collect", "passed": True, "detail": "collected 5", "data": {"collected_count": 5}},
            {"name": "telegram_ping", "passed": True, "detail": "sent", "data": {"delivery": {"big": "payload"}}},
        ],
        telegram_message_id=4242,
        telegram_status="sent",
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    assert run.id is not None
    assert run.overall_passed is True
    assert run.telegram_message_id == 4242
    assert run.telegram_status == "sent"
    assert run.started_at is not None and run.completed_at is not None

    stored = json.loads(run.phases)
    assert stored == [
        {
            "name": "koneps_collect",
            "passed": True,
            "detail": "collected 5",
            "evidence": {
                "collected_count": 5,
                "evidence_scope": "g0_scheduled_smoke",
                "operator_scope": "canonical_only",
                "canonical_only_reason": (
                    "G-0 scheduled smoke validates the canonical shared pipeline; "
                    "G-2 per-operator evidence is recorded on operator-scoped monitor and experiment runs."
                ),
            },
        },
        {
            "name": "telegram_ping",
            "passed": True,
            "detail": "sent",
            "evidence": {
                "evidence_scope": "g0_scheduled_smoke",
                "operator_scope": "canonical_only",
                "canonical_only_reason": (
                    "G-0 scheduled smoke validates the canonical shared pipeline; "
                    "G-2 per-operator evidence is recorded on operator-scoped monitor and experiment runs."
                ),
            },
        },
    ]
    # bulky per-phase data dict must be dropped
    assert all("data" not in phase for phase in stored)

    rows = test_db.query(SmokeTestRun).all()
    assert len(rows) == 1


def test_persist_report_tolerates_empty_timestamps_and_null_telegram(test_db):
    """persist_report must not raise when timestamps are empty and Telegram is null.

    ENVIRONMENT=test skips Telegram, so telegram_message_id is None.
    """
    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="",
        completed_at="",
        overall_passed=False,
        phases=[{"name": "koneps_collect", "passed": False, "detail": "exception"}],
        telegram_message_id=None,
        telegram_status="",
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    assert run.id is not None
    assert run.started_at is None
    assert run.completed_at is None
    assert run.overall_passed is False
    assert run.telegram_message_id is None
    assert run.telegram_status is None


def test_persist_report_keeps_actionable_failure_evidence(test_db):
    """Failed phases keep category/action/retry/skip evidence without bulky data."""
    import json

    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="2026-06-16T07:00:00+00:00",
        completed_at="2026-06-16T07:01:30+00:00",
        overall_passed=False,
        phases=[
            {
                "name": "candidate_generation",
                "passed": False,
                "detail": "exception: RuntimeError: strategy monitor failed",
                "failure_category": "candidate_generation",
                "action_required": "Inspect the strategy monitor run and candidate filters.",
                "retry_method": "Rerun /api/v1/operator/strategy/monitor.",
                "skip_reason": "no strategy candidates selected",
                "data": {
                    "monitor_run_id": 77,
                    "operator_id": 42,
                    "evaluated_project_count": 4,
                    "selected_candidate_count": 0,
                    "large_results": [{"ignore": True}],
                },
            }
        ],
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    stored = json.loads(run.phases)
    assert stored == [
        {
            "name": "candidate_generation",
            "passed": False,
            "detail": "exception: RuntimeError: strategy monitor failed",
            "failure_category": "candidate_generation",
            "action_required": "Inspect the strategy monitor run and candidate filters.",
            "retry_method": "Rerun /api/v1/operator/strategy/monitor.",
            "skip_reason": "no strategy candidates selected",
            "evidence": {
                "monitor_run_id": 77,
                "operator_id": 42,
                "source_run_type": "operator_strategy_monitor",
                "source_run_id": 77,
                "operator_scope": "operator",
                "evidence_scope": "g0_scheduled_smoke",
                "evaluated_project_count": 4,
                "selected_candidate_count": 0,
            },
        }
    ]


def _mk_phase(name, passed, detail, **data):
    from app.services.smoke_test import PhaseResult
    return PhaseResult(name=name, passed=passed, detail=detail, data=data)


# ---------------------------------------------------------------------------
# Real _phase_sbert_embedding selection logic (DB-backed, not monkeypatched).
#
# These exercise the actual SELECT that the monkeypatched suite above could not
# catch: the old "new project in last 30 minutes" criterion was structurally
# always empty because the smoke never persists a fresh project. The B-plan
# decouples selection from collection novelty and instead picks the most recent
# real (non-fallback) embedded project within a recency window, preferring a
# usable budget so predict_price can exercise the guardrail band.
# ---------------------------------------------------------------------------

_REAL_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def _make_embedded_project(
    test_db,
    *,
    title,
    budget=100_000_000.0,
    embedding=None,
    embedding_model=_REAL_EMBEDDING_MODEL,
    embedding_updated_at="now",
    created_at=None,
):
    """Insert a Project with a real 384-dim embedding vector for realism.

    pgvector only enforces the dimension when the optional dependency is
    installed; without it ``app/core/vector.py`` falls back to a dimensionless
    UserDefinedType, so the SQLite dialect does not validate the vector length.
    ``embedding_updated_at='now'`` uses a fresh UTC timestamp; pass an explicit
    datetime to age it out of the window.
    """
    from app.core.time import utc_now

    if embedding is None and embedding_model is not None:
        # 384-dim unit-ish vector so the column is genuinely non-NULL.
        embedding = [0.1] * 384
    if embedding_updated_at == "now":
        embedding_updated_at = utc_now()
    embedding_payload = json.dumps(embedding or [], ensure_ascii=False)

    project = Project(
        title=title,
        description="설명",
        requirements="",
        budget_estimate=budget,
        category="소프트웨어",
        status="open",
        embedding=embedding if embedding is not None else None,
        embedding_payload=embedding_payload,
        embedding_model=embedding_model,
        embedding_updated_at=embedding_updated_at,
        semantic_text="ignored",
    )
    if created_at is not None:
        project.created_at = created_at
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def test_sbert_embedding_selects_recent_nonfallback_with_budget(test_db):
    """Passes and fills data['project'] for a recent non-fallback embedded row."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    project = _make_embedded_project(test_db, title="최근 임베딩 공고", budget=120_000_000.0)

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is True
    assert result.failure_category == ""
    assert result.data["project"]["id"] == project.id
    assert result.data["project"]["budget_estimate"] == 120_000_000.0
    assert result.data["project_id"] == project.id
    assert result.data["project_title"] == "최근 임베딩 공고"
    assert "fallback" not in result.detail


def test_sbert_embedding_prefers_budgeted_over_more_recent_zero_budget(test_db):
    """A usable-budget project wins even if a zero-budget row is more recent.

    This keeps predict_price from immediately skipping on 'no usable budget',
    maximising end-to-end health coverage. predict_price still validates the
    guardrail band, so this preference cannot fabricate a green result.
    """
    from datetime import timedelta

    from app.core.time import utc_now
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    budgeted = _make_embedded_project(
        test_db,
        title="예산 있는 공고",
        budget=90_000_000.0,
        embedding_updated_at=utc_now() - timedelta(days=2),
    )
    # More recent, but zero budget -> would skip in predict_price.
    _make_embedded_project(
        test_db,
        title="예산 없는 최신 공고",
        budget=0.0,
        embedding_updated_at=utc_now() - timedelta(hours=1),
    )

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is True
    assert result.data["project"]["id"] == budgeted.id


def test_sbert_embedding_skips_when_no_embedded_project(test_db):
    """Empty DB / no real embeddings -> passed=False with explicit skip reason."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is False
    assert result.skip_reason == "no non-fallback embedded project"
    assert "fallback" in result.skip_reason
    # Must not regurgitate the old, misleading "no recent project" string.
    assert result.skip_reason != "no recent project"


def test_sbert_embedding_skips_fallback_only_embeddings(test_db):
    """A project whose only embedding is fallback-hash must not be selected."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _make_embedded_project(
        test_db,
        title="폴백 임베딩 공고",
        embedding_model="fallback-hash-v1",
    )

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is False
    assert result.skip_reason == "no non-fallback embedded project"


def test_sbert_embedding_skips_when_embeddings_too_old(test_db):
    """Real embeddings exist but all older than the window -> distinct reason."""
    from datetime import timedelta

    from app.core.time import utc_now
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    window = KonepsTelegramSmokeTestService.SBERT_RECENT_WINDOW_DAYS
    _make_embedded_project(
        test_db,
        title="오래된 임베딩 공고",
        embedding_updated_at=utc_now() - timedelta(days=window + 3),
    )

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is False
    assert result.skip_reason == f"no embedded project in last {window}d"
    # Distinguishes "exists but stale" from "no embeddings at all".
    assert result.skip_reason != "no non-fallback embedded project"


def test_sbert_embedding_skips_null_embedding_even_with_model(test_db):
    """embedding_model set but vector NULL must not be treated as embedded."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    # Insert a genuine NULL embedding vector while keeping a non-fallback
    # embedding_model populated, to prove the selection requires the vector
    # itself (embedding IS NOT NULL), not merely a model label.
    project = Project(
        title="모델만 있고 벡터 없는 공고",
        description="설명",
        requirements="",
        budget_estimate=100_000_000.0,
        category="소프트웨어",
        status="open",
        embedding=None,
        embedding_model=_REAL_EMBEDDING_MODEL,
        semantic_text="ignored",
    )
    test_db.add(project)
    test_db.commit()

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is False
    assert result.skip_reason == "no non-fallback embedded project"


def test_sbert_embedding_chains_into_predict_price(test_db, monkeypatch):
    """Integration: real sbert selection hands a project to predict_price.

    The embedding selection is the real DB query; price prediction itself is
    stubbed through the injected port so the test asserts the wiring
    (data['project'] -> _phase_predict_price) rather than predictor internals.
    The guardrail-band check inside _phase_predict_price is left intact.
    """
    import app.ai.business_group as bg_mod
    import app.services.backtest_cutoff as cutoff_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _RatePredictionPort:
        def predict_price(self, **kwargs):
            return {"predicted_bid_rate": 0.88, "predictor_name": "stub"}

    project = _make_embedded_project(
        test_db,
        title="예측까지 이어지는 공고",
        budget=80_000_000.0,
    )

    class _StubCutoff:
        def resolve_data_cutoff_at(self, *a, **k):
            return None

        def load_price_history_at_cutoff(self, *a, **k):
            return []

    monkeypatch.setattr(bg_mod, "resolve_business_group", lambda code: None)
    monkeypatch.setattr(cutoff_mod, "BacktestCutoffService", _StubCutoff)

    svc = KonepsTelegramSmokeTestService(price_prediction_port=_RatePredictionPort())
    p2 = svc._phase_sbert_embedding(test_db)
    assert p2.passed is True

    selected = p2.data.get("project")
    assert selected is not None and selected["id"] == project.id

    p3 = svc._phase_predict_price(test_db, selected)
    assert p3.passed is True
    assert p3.data["project_id"] == project.id
    assert p3.data["predicted_bid_rate"] == 0.88
    assert p3.data["predictor_name"] == "stub"


def test_sbert_embedding_recency_breaks_tie_within_same_budget_tier(test_db):
    """Among equally-budgeted rows, the most recently embedded one wins."""
    from datetime import timedelta

    from app.core.time import utc_now
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    # Both have a usable budget (same tier), so has_budget cannot break the tie
    # — recency_key (embedding_updated_at DESC) must decide.
    _make_embedded_project(
        test_db,
        title="오래된 임베딩 (동일 예산대)",
        budget=100_000_000.0,
        embedding_updated_at=utc_now() - timedelta(days=3),
    )
    newer = _make_embedded_project(
        test_db,
        title="최신 임베딩 (동일 예산대)",
        budget=100_000_000.0,
        embedding_updated_at=utc_now() - timedelta(hours=2),
    )

    result = KonepsTelegramSmokeTestService()._phase_sbert_embedding(test_db)

    assert result.passed is True
    assert result.data["project"]["id"] == newer.id


def test_sbert_embedding_passes_then_predict_price_skips_zero_budget(test_db, monkeypatch):
    """A window has only a zero-budget embedded project.

    sbert_embedding still passes (the embedding pipeline is healthy), but
    predict_price then skips with 'no usable budget' rather than fabricating a
    rate — the guardrail/band path is never reached without a real budget.
    """
    import app.ai.business_group as bg_mod
    import app.services.backtest_cutoff as cutoff_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _ExplodingPredictionPort:
        def predict_price(self, **kwargs):
            raise AssertionError("predict_price called despite zero budget")

    project = _make_embedded_project(
        test_db,
        title="예산 0 임베딩 공고",
        budget=0.0,
    )

    class _StubCutoff:
        def resolve_data_cutoff_at(self, *a, **k):
            return None

        def load_price_history_at_cutoff(self, *a, **k):
            return []

    monkeypatch.setattr(bg_mod, "resolve_business_group", lambda code: None)
    monkeypatch.setattr(cutoff_mod, "BacktestCutoffService", _StubCutoff)

    svc = KonepsTelegramSmokeTestService(price_prediction_port=_ExplodingPredictionPort())
    p2 = svc._phase_sbert_embedding(test_db)
    assert p2.passed is True
    assert p2.data["project"]["id"] == project.id

    p3 = svc._phase_predict_price(test_db, p2.data["project"])
    assert p3.passed is False
    assert p3.skip_reason == "no usable budget"
    assert p3.data["project_id"] == project.id


# ---------------------------------------------------------------------------
# koneps_collect phase: live fetch + off-peak pipeline-health fallback
# ---------------------------------------------------------------------------


def _make_crawl_job(
    test_db,
    *,
    source="koneps-openapi",
    status="completed",
    result_count=100,
    created_at="now",
):
    """Insert a CrawlJob row for the collection-health cross-check tests."""
    from app.core.time import utc_now
    from app.models.models import CrawlJob

    if created_at == "now":
        created_at = utc_now()
    job = CrawlJob(
        source=source,
        status=status,
        result_count=result_count,
        created_at=created_at,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _patch_collect_notices(monkeypatch, *, collected_count):
    from app.services.koneps.collector import KonepsCollectorService

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, req: {
            "collected_count": collected_count,
            "items": [],
            "metadata": {"resolved_mode": "openapi"},
        },
    )


def test_koneps_collect_passes_on_fresh_live_notices(test_db, monkeypatch):
    """A non-zero live fetch passes without consulting the health fallback."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _patch_collect_notices(monkeypatch, collected_count=5)

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is True
    assert result.detail == "collected 5"
    assert result.data["collected_count"] == 5
    # No health cross-check fields when the live fetch already succeeded.
    assert "recent_collection_count" not in result.data


def test_koneps_collect_passes_offpeak_when_pipeline_healthy(test_db, monkeypatch):
    """Zero live notices in the KST trough still passes when the hourly beat
    recently persisted real notices (the collection pipeline is alive)."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _patch_collect_notices(monkeypatch, collected_count=0)
    _make_crawl_job(test_db, result_count=120)

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is True
    assert "off-peak" in result.detail
    assert result.data["collected_count"] == 0
    assert result.data["recent_collection_jobs"] == 1
    assert result.data["recent_collection_count"] == 120
    assert result.data["recent_collection_last_at"] is not None
    # A passing phase must not carry failure annotations.
    assert result.failure_category == ""


def test_koneps_collect_fails_when_live_zero_and_pipeline_stale(test_db, monkeypatch):
    """Zero live notices AND no recent successful collection is a real failure."""
    from app.services.smoke_failure_taxonomy import guidance_for
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _patch_collect_notices(monkeypatch, collected_count=0)
    # No crawl jobs at all -> pipeline is stale.

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is False
    assert "no notices persisted" in result.detail
    assert result.skip_reason == "KONEPS returned zero notices and pipeline is stale"
    # A genuine stall must be classified as a KONEPS/collection problem, not the
    # "collected 0" token's default no_candidate ("widen strategy filters"),
    # which would misdirect the operator.
    assert result.failure_category == "koneps_response"
    assert result.action_required == guidance_for("koneps_response")["action_required"]


def test_koneps_collect_health_sums_multiple_recent_jobs(test_db, monkeypatch):
    """recent_collection_count sums result_count across all in-window jobs."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _patch_collect_notices(monkeypatch, collected_count=0)
    _make_crawl_job(test_db, result_count=120)
    _make_crawl_job(test_db, result_count=80)

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is True
    assert result.data["recent_collection_jobs"] == 2
    assert result.data["recent_collection_count"] == 200


def test_koneps_collect_health_ignores_stale_failed_and_zero_jobs(test_db, monkeypatch):
    """Jobs outside the window, failed jobs, and zero-count (trough) jobs do not
    count as evidence of a healthy pipeline."""
    from datetime import timedelta

    from app.core.time import utc_now
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    _patch_collect_notices(monkeypatch, collected_count=0)
    # Real data, but outside the 26h window.
    _make_crawl_job(
        test_db, result_count=500, created_at=utc_now() - timedelta(hours=48)
    )
    # Failed job inside the window.
    _make_crawl_job(test_db, status="failed", result_count=0)
    # Completed but zero-count inside the window (the trough itself).
    _make_crawl_job(test_db, result_count=0)
    # Wrong source inside the window.
    _make_crawl_job(test_db, source="scsbid-openapi", result_count=200)

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is False
    assert result.data["recent_collection_jobs"] == 0
    assert result.data["recent_collection_count"] == 0


def test_koneps_collect_fails_on_live_exception_even_with_healthy_history(
    test_db, monkeypatch
):
    """A raised live fetch (API down / bad resultCode) is a hard failure and is
    not masked by recent healthy collection history."""
    from app.services.koneps.collector import KonepsCollectorService
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    def _boom(self, req):
        raise ValueError("KONEPS OpenAPI HTTP 500")

    monkeypatch.setattr(KonepsCollectorService, "collect_notices", _boom)
    _make_crawl_job(test_db, result_count=300)  # healthy history present

    result = KonepsTelegramSmokeTestService()._phase_koneps_collect(test_db)

    assert result.passed is False
    assert "exception" in result.detail
    assert "ValueError" in result.detail


# ---------------------------------------------------------------------------
# Collaborator injection seams (PR-15): each phase resolves its collaborator
# from an injected instance when provided, else lazily builds the default at
# the same call site (per-call lifetime unchanged).
# ---------------------------------------------------------------------------
def test_koneps_collect_phase_uses_injected_collector():
    """An injected collector short-circuits the default KonepsCollectorService()."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _FakeCollector:
        def __init__(self):
            self.calls = 0

        def collect_notices(self, req):
            self.calls += 1
            return {"collected_count": 5, "items": [], "metadata": {}}

    fake = _FakeCollector()
    svc = KonepsTelegramSmokeTestService(koneps_collector=fake)
    result = svc._phase_koneps_collect(MagicMock())

    assert fake.calls == 1
    assert result.passed is True
    assert result.detail == "collected 5"


def test_predict_price_phase_uses_injected_cutoff_service(monkeypatch):
    """An injected cutoff service short-circuits the default BacktestCutoffService()."""
    import app.ai.business_group as bg_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _StubProject:
        id = 1
        budget_estimate = 100_000_000.0
        title = "t"
        description = ""
        requirements = ""
        category = "software"
        business_type_code = None
        issuing_agency = None
        demand_agency = None

    class _StubQuery:
        def filter(self, *a, **k):
            return self

        def one(self):
            return _StubProject()

    class _StubDB:
        def query(self, *a, **k):
            return _StubQuery()

    class _FakeCutoff:
        def __init__(self):
            self.cutoff_calls = 0
            self.history_calls = 0

        def resolve_data_cutoff_at(self, *a, **k):
            self.cutoff_calls += 1
            return None

        def load_price_history_at_cutoff(self, *a, **k):
            self.history_calls += 1
            return []

    class _RatePort:
        def predict_price(self, **kwargs):
            return {"predicted_bid_rate": 0.9, "predictor_name": "stub"}

    monkeypatch.setattr(bg_mod, "resolve_business_group", lambda code: None)
    cutoff = _FakeCutoff()
    svc = KonepsTelegramSmokeTestService(
        price_prediction_port=_RatePort(),
        backtest_cutoff_service=cutoff,
    )
    result = svc._phase_predict_price(_StubDB(), {"id": 1})

    assert cutoff.cutoff_calls == 1
    assert cutoff.history_calls == 1
    assert result.passed is True


def test_candidate_generation_phase_uses_injected_monitoring_service():
    """An injected monitoring service short-circuits the default StrategyMonitoringService()."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _FakeMonitor:
        def __init__(self):
            self.calls = 0

        def execute_monitoring(self, db, *, request, trigger_source):
            self.calls += 1
            return {
                "monitor_run_id": 77,
                "operator_id": 42,
                "current_operator_id": 42,
                "current_operator_username": "operator",
                "evaluated_project_count": 5,
                "selected_candidate_count": 0,
                "persisted_candidate_count": 0,
                "notification_count": 0,
                "new_candidate_count": 0,
            }

    fake = _FakeMonitor()
    svc = KonepsTelegramSmokeTestService(strategy_monitoring_service=fake)
    result = svc._phase_candidate_generation(MagicMock())

    assert fake.calls == 1
    assert result.passed is True
    assert result.data["monitor_run_id"] == 77


def test_telegram_ping_phase_uses_injected_telegram_service():
    """An injected telegram service short-circuits the default TelegramNotificationService()."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    class _FakeTelegram:
        def __init__(self):
            self.sent = []

        def is_configured(self):
            return True

        def send_message(self, text):
            self.sent.append(text)
            return {"sent": True, "status": "ok", "telegram_message_id": 99}

    fake = _FakeTelegram()
    svc = KonepsTelegramSmokeTestService(telegram_service=fake)
    report = SmokeTestReport()
    result = svc._phase_telegram_ping(report=report, prior_phases=[])

    assert len(fake.sent) == 1
    assert result.passed is True
    assert report.telegram_message_id == 99
