#!/usr/bin/env python3
"""Backfill HistoricalData.base_amount_basis / _estimated (provenance tagging).

Classifies every ``HistoricalData`` row's stored ``base_amount`` provenance
(``classify_base_basis``) and, for non-clean rows, records an estimated real
기초금액 from 복수예비가격 (``estimate_base_amount_from_reserves``). The original
``base_amount`` is NEVER overwritten (정직 명세 §2). Winning result inputs are
joined from ``TenderResult`` on ``project_id``, preferring a row with a real
``winning_amount > 0``; the notice's 추정가격 (``Project.budget_estimate``) is
joined from ``Project`` so the classifier can compare the two amounts of the SAME
notice — the only way to see a base that is a non-VAT multiple of the 추정가격.

Idempotent: rows already stamped with ``basis_checked_at`` are skipped unless
``--recheck`` is passed. Processing uses keyset (id-ordered) pagination with a
commit per chunk so a re-run resumes cleanly and never re-scans finished rows.
No external calls — DB only.

Usage:
    python scripts/backfill_base_amount_basis.py --dry-run          # default, counts only
    python scripts/backfill_base_amount_basis.py --apply
    python scripts/backfill_base_amount_basis.py --apply --chunk-size 2000
    python scripts/backfill_base_amount_basis.py --apply --recheck  # re-classify all
    # Correct rows mislabeled 'clean' (예정가-역산 base stamped clean before the
    # settled TenderResult join existed) -> re-tag to derived-yega, fill estimate.
    python scripts/backfill_base_amount_basis.py --reclassify-clean --dry-run
    python scripts/backfill_base_amount_basis.py --reclassify-clean --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.models.models import HistoricalData, Project, TenderResult  # noqa: E402
from app.services.base_amount_basis import (  # noqa: E402
    BASIS_CLEAN,
    classify_base_basis,
    estimate_base_amount_from_reserves,
    normalize_winning_rate,
)

# 관측 집계와 승인용 리포트는 형제 모듈이 소유한다 — 이 파일은 DB sweep(페이징·분류·
# 스탬프)만 맡는다. 테스트가 이 스크립트를 경로 로드해도 재노출된 이름으로 접근한다.
from scripts._backfill_basis_report import (  # noqa: E402
    NO_NOTICE_FACTS,
    UNKNOWN_BUCKET,
    BackfillStats,
    NoticeFacts,
    print_impact_report,
    record_reclassification,
)


def _load_result_map(
    db: Session, project_ids: list[int]
) -> dict[int, tuple[float, float]]:
    """Map project_id -> (winning_amount, normalized_rate), preferring a settled row.

    A project may have several ``TenderResult`` rows; pick one with a real
    ``winning_amount > 0`` when available (that is the row a 예정가 역산 would have
    used). The winning_rate is normalized to a fraction (mixed-scale column) so the
    derived-yega check matches the holdout path. ``project_ids`` is one chunk
    (<= chunk_size), so the IN() list stays well under the 65535-parameter limit.
    """
    if not project_ids:
        return {}
    rows = (
        db.query(
            TenderResult.project_id,
            TenderResult.winning_amount,
            TenderResult.winning_rate,
        )
        .filter(TenderResult.project_id.in_(project_ids))
        .all()
    )
    best: dict[int, tuple[float, float]] = {}
    for project_id, winning_amount, winning_rate in rows:
        if project_id is None:
            continue
        amount = float(winning_amount or 0.0)
        # Normalize the mixed-scale winning_rate to a fraction so percentage-form
        # rows (HTML parsing, e.g. 87.5) classify identically to the holdout path;
        # an unnormalized percent rate fails the derived-yega match and mislabels a
        # 예정가-역산 row as suspect-fractional.
        rate = normalize_winning_rate(winning_rate) or 0.0
        current = best.get(project_id)
        # Prefer the first row we see, but upgrade to a settled (amount>0) row.
        if current is None or (amount > 0 and current[0] <= 0):
            best[project_id] = (amount, rate)
    return best


def _load_notice_map(db: Session, project_ids: list[int]) -> dict[int, NoticeFacts]:
    """Map project_id -> :class:`NoticeFacts` for one chunk of rows.

    The 추정가격 feeds the classifier's ratio rule; status/category only feed the
    dry-run impact breakdown. Mirrors ``_load_result_map``'s chunk-scoped ``IN()``
    so the parameter list stays far below the 65535 limit. A ``HistoricalData`` row
    whose ``project_id`` has no ``Project`` row is simply absent from the map and
    falls back to :data:`NO_NOTICE_FACTS` (ratio rule not applied).
    """
    if not project_ids:
        return {}
    rows = (
        db.query(Project.id, Project.budget_estimate, Project.status, Project.category)
        .filter(Project.id.in_(project_ids))
        .all()
    )
    return {
        project_id: NoticeFacts(
            budget_estimate=float(budget_estimate or 0.0),
            status=status or UNKNOWN_BUCKET,
            category=category or UNKNOWN_BUCKET,
        )
        for project_id, budget_estimate, status, category in rows
        if project_id is not None
    }


def _classify_record(
    record: HistoricalData,
    result_map: dict[int, tuple[float, float]],
    notice_map: dict[int, NoticeFacts],
) -> tuple[str, float | None]:
    """Return (basis, estimated) for one row without touching the DB."""
    winning_amount, winning_rate = result_map.get(record.project_id, (0.0, 0.0))
    notice = notice_map.get(record.project_id, NO_NOTICE_FACTS)
    basis = classify_base_basis(
        record.base_amount, winning_amount, winning_rate, notice.budget_estimate
    )
    estimated = None
    if basis != BASIS_CLEAN:
        estimated = estimate_base_amount_from_reserves(record.reserve_prices)
    return basis, estimated


def _est_equals_base(record: HistoricalData, notice: NoticeFacts) -> bool:
    """추정가격이 base 와 정확히 같은가 — 비율 규칙이 구조적으로 못 보는 코호트.

    수집이 공고 추정가격을 얻지 못하면 ``matching.resolve_budget_estimate`` 가
    ``base_amount`` 를 그대로 ``Project.budget_estimate`` 로 쓴다. 그러면 두 금액이 같은
    값의 두 사본이라 비율이 항상 1.0 이고, base 가 아무리 오염돼도 이 규칙에 걸리지 않는다.
    "독립적인 두 번째 금액과의 모순"이라는 이 규칙의 근거가 성립하지 않는 행이므로,
    승인 자료에 검증 커버리지로 함께 낸다(정정 대상이 아니라 사각지대 표시다).
    """
    base = record.base_amount
    return bool(base) and notice.budget_estimate > 0 and float(base) == notice.budget_estimate


def _process_record(
    record: HistoricalData,
    *,
    stats: BackfillStats,
    result_map: dict[int, tuple[float, float]],
    notice_map: dict[int, NoticeFacts],
    apply: bool,
    stamp: datetime,
) -> None:
    """Classify one row, fold it into ``stats``, and (when applying) stamp it.

    A row counts as *moved* when it already carried a stored basis and the fresh
    verdict differs from it. That is deliberately not tied to ``basis_filter``: the
    pass that propagates a rule change across every row is ``--recheck``, and gating
    the evidence on the filter would leave exactly that pass with no record of what
    moved. A first-time tag (``previous_basis is None``) is not a move.
    """
    notice = notice_map.get(record.project_id, NO_NOTICE_FACTS)
    previous_basis = record.base_amount_basis
    basis, estimated = _classify_record(record, result_map, notice_map)
    stats.scanned += 1
    stats.by_basis[basis] += 1
    if _est_equals_base(record, notice):
        stats.est_equals_base += 1
    if basis != BASIS_CLEAN:
        if estimated is not None:
            stats.estimated_filled += 1
            stats.estimated_filled_by_status[notice.status] += 1
        else:
            stats.estimated_missing += 1
    if previous_basis is not None and basis != previous_basis:
        record_reclassification(
            stats,
            record,
            previous_basis=previous_basis,
            basis=basis,
            estimated=estimated,
            notice=notice,
        )
    if apply:
        record.base_amount_basis = basis
        record.base_amount_estimated = estimated
        record.basis_checked_at = stamp


def run_backfill(
    db: Session,
    *,
    apply: bool,
    recheck: bool = False,
    basis_filter: str | None = None,
    chunk_size: int = 1000,
    limit: int | None = None,
    progress: bool = False,
) -> BackfillStats:
    """Classify base_amount provenance in id-ordered chunks (commit per chunk).

    ``basis_filter`` targets a re-classification pass at rows whose *stored*
    ``base_amount_basis`` equals the given bucket (e.g. ``'clean'``), regardless of
    ``basis_checked_at``. This corrects rows that an earlier backfill mislabeled —
    a 예정가-역산 base that was stamped 'clean' before the settled ``TenderResult``
    join was available re-classifies to derived-yega here, and a base that is a
    non-VAT multiple of its notice's 추정가격 re-classifies to suspect-ratio now that
    the ``Project`` join supplies that second amount. The original ``base_amount`` is
    NEVER mutated; only the provenance tag / estimate move.
    """
    stats = BackfillStats(applied=apply, recheck=recheck, basis_filter=basis_filter)
    last_id = 0
    while True:
        remaining = None if limit is None else limit - stats.scanned
        if remaining is not None and remaining <= 0:
            break
        page_limit = chunk_size if remaining is None else min(chunk_size, remaining)

        query = db.query(HistoricalData).filter(HistoricalData.id > last_id)
        if basis_filter is not None:
            # Re-examine an existing basis bucket (ignore basis_checked_at — the
            # whole point is to revisit already-stamped rows in that bucket).
            query = query.filter(HistoricalData.base_amount_basis == basis_filter)
        elif not recheck:
            query = query.filter(HistoricalData.basis_checked_at.is_(None))
        chunk = query.order_by(HistoricalData.id.asc()).limit(page_limit).all()
        if not chunk:
            break

        project_ids = list({r.project_id for r in chunk if r.project_id is not None})
        result_map = _load_result_map(db, project_ids)
        notice_map = _load_notice_map(db, project_ids)
        stamp = utc_now()
        for record in chunk:
            last_id = record.id
            _process_record(
                record,
                stats=stats,
                result_map=result_map,
                notice_map=notice_map,
                apply=apply,
                stamp=stamp,
            )

        if apply:
            db.commit()
        else:
            db.rollback()
        # Free the identity map so a full 65k sweep does not accumulate rows.
        db.expunge_all()

        if progress:
            print(
                f"[backfill-base-basis] scanned={stats.scanned} last_id={last_id} "
                f"by_basis={dict(stats.by_basis)}",
                flush=True,
            )
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="Classify and count only; write nothing (default).",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Persist base_amount_basis / _estimated / basis_checked_at.",
    )
    parser.set_defaults(apply=False)
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-classify rows already stamped with basis_checked_at.",
    )
    parser.add_argument(
        "--reclassify-clean",
        action="store_true",
        help=(
            "Re-examine rows currently stored as base_amount_basis='clean' and "
            "correct any that are actually derived-yega/VAT/suspect (e.g. a 예정가-"
            "역산 base mislabeled 'clean' before the settled TenderResult join, or a "
            "base that is a non-VAT multiple of its notice's 추정가격 → suspect-ratio). "
            "base_amount is never mutated; only the provenance tag/estimate move."
        ),
    )
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N rows (for a small validation run).",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help="Optional path to write the run summary JSON.",
    )
    return parser


def summarize(stats: BackfillStats) -> dict[str, Any]:
    """Assemble the run summary this CLI prints and writes to ``--audit``.

    The JSON shape is this script's OUTPUT CONTRACT (an operator diffs two runs by
    it), so it is assembled here next to the CLI rather than on the accumulator in
    ``scripts/_backfill_basis_report.py`` — that module owns counting and the human
    report, not the file format.
    """
    result: dict[str, Any] = {
        "applied": stats.applied,
        "recheck": stats.recheck,
        "basis_filter": stats.basis_filter,
        "scanned": stats.scanned,
        "by_basis": stats.basis_counts(),
        # 비율(축소율)과 함께 **절대 잔여 행수**를 낸다 — 재캘리 게이트가 절대 표본 수를
        # 요구하므로 비율만으로는 승인 판단이 서지 않는다.
        "clean_remaining": stats.clean_remaining,
        "estimated_filled": stats.estimated_filled,
        "estimated_filled_by_status": dict(stats.estimated_filled_by_status),
        "estimated_missing": stats.estimated_missing,
        "est_equals_base": stats.est_equals_base,
        # 이동 증적은 어느 패스에서든 낸다(--recheck 포함) — ``BackfillStats.reclassified``
        # 주석 참조. 버킷 축소율만 ``basis_filter`` 가 있을 때 뜻이 서므로 그때만 낸다.
        "reclassified": stats.reclassified,
        "reclassified_by_status": dict(stats.reclassified_by_status),
        "reclassified_by_category": dict(stats.reclassified_by_category),
        "reclassified_with_reserve_estimate": stats.reclassified_with_reserve_estimate,
        "samples": stats.samples,
    }
    if stats.basis_filter is not None:
        result["bucket_shrink_ratio"] = stats.bucket_shrink_ratio
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    chunk_size = max(1, args.chunk_size)

    basis_filter = BASIS_CLEAN if args.reclassify_clean else None

    db = SessionLocal()
    try:
        stats = run_backfill(
            db,
            apply=args.apply,
            recheck=args.recheck,
            basis_filter=basis_filter,
            chunk_size=chunk_size,
            limit=args.limit,
            progress=True,
        )
    finally:
        db.close()

    summary = summarize(stats)
    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"[backfill-base-basis] {mode}")
    print_impact_report(stats)
    print(json.dumps(summary, ensure_ascii=False))
    if args.audit is not None:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
