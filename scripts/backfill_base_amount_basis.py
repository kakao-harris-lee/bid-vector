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
from collections import Counter
from dataclasses import dataclass, field
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
    ALL_BASES,
    BASIS_CLEAN,
    classify_base_basis,
    estimate_base_amount_from_reserves,
    normalize_winning_rate,
)

_UNKNOWN_BUCKET = "unknown"  # Project 행이 없거나 값이 비어 분해 키를 못 얻은 경우


@dataclass(frozen=True)
class NoticeFacts:
    """분류·분해에 필요한 공고(Project) 측 사실. Project 행이 없으면 기본값이 선다."""

    budget_estimate: float = 0.0
    status: str = _UNKNOWN_BUCKET
    category: str = _UNKNOWN_BUCKET


_NO_NOTICE_FACTS = NoticeFacts()


@dataclass
class BackfillStats:
    """Aggregate counts for one backfill run (dry-run or apply)."""

    applied: bool = False
    recheck: bool = False
    basis_filter: str | None = None
    scanned: int = 0
    by_basis: Counter = field(default_factory=Counter)
    estimated_filled: int = 0  # non-clean rows that got an estimate
    estimated_missing: int = 0  # non-clean rows without recoverable reserves
    # Rows whose freshly-computed basis differs from the ``basis_filter`` bucket
    # they were selected from (e.g. a row stored 'clean' that re-classifies as
    # derived-yega). Only meaningful when ``basis_filter`` is set.
    reclassified: int = 0
    # 이동 행을 공고 status / category 로 분해한다. 총량만으로는 재태깅이 열린 공고를
    # 건드리는지, 특정 카테고리에 몰렸는지 알 수 없어 승인 판단의 근거가 되지 못한다.
    reclassified_by_status: Counter = field(default_factory=Counter)
    reclassified_by_category: Counter = field(default_factory=Counter)
    samples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bucket_shrink_ratio(self) -> float:
        """선택한 버킷에서 빠져나가는 비율(이동 ÷ 스캔). 스캔 0 이면 0.0."""
        return round(self.reclassified / self.scanned, 4) if self.scanned else 0.0

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "applied": self.applied,
            "recheck": self.recheck,
            "basis_filter": self.basis_filter,
            "scanned": self.scanned,
            "by_basis": {
                basis: int(self.by_basis.get(basis, 0)) for basis in ALL_BASES
            },
            "estimated_filled": self.estimated_filled,
            "estimated_missing": self.estimated_missing,
        }
        if self.basis_filter is not None:
            result["reclassified"] = self.reclassified
            result["bucket_shrink_ratio"] = self.bucket_shrink_ratio
            result["reclassified_by_status"] = dict(self.reclassified_by_status)
            result["reclassified_by_category"] = dict(self.reclassified_by_category)
            result["samples"] = self.samples
        return result


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
    falls back to :data:`_NO_NOTICE_FACTS` (ratio rule not applied).
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
            status=status or _UNKNOWN_BUCKET,
            category=category or _UNKNOWN_BUCKET,
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
    notice = notice_map.get(record.project_id, _NO_NOTICE_FACTS)
    basis = classify_base_basis(
        record.base_amount, winning_amount, winning_rate, notice.budget_estimate
    )
    estimated = None
    if basis != BASIS_CLEAN:
        estimated = estimate_base_amount_from_reserves(record.reserve_prices)
    return basis, estimated


_MAX_SAMPLES = 12  # dry-run before/after evidence rows (per reclassify run)


def _record_reclassification(
    stats: BackfillStats,
    record: HistoricalData,
    *,
    previous_basis: str | None,
    basis: str,
    estimated: float | None,
    notice: NoticeFacts,
) -> None:
    """Count one bucket-leaving row and keep a bounded before/after sample.

    The sample carries BOTH amounts and their ratio because that ratio is the whole
    evidence for a ``suspect-ratio`` verdict — a reviewer must be able to see why a
    row moved without re-querying the DB.
    """
    stats.reclassified += 1
    stats.reclassified_by_status[notice.status] += 1
    stats.reclassified_by_category[notice.category] += 1
    if len(stats.samples) >= _MAX_SAMPLES:
        return
    base_amount = (
        float(record.base_amount) if record.base_amount is not None else None
    )
    ratio = (
        round(base_amount / notice.budget_estimate, 6)
        if base_amount and notice.budget_estimate > 0
        else None
    )
    stats.samples.append(
        {
            "id": record.id,
            "base_amount": base_amount,
            "budget_estimate": notice.budget_estimate or None,
            "base_to_estimate_ratio": ratio,
            "from_basis": previous_basis,
            "to_basis": basis,
            "estimated": estimated,
        }
    )


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
            previous_basis = record.base_amount_basis
            basis, estimated = _classify_record(record, result_map, notice_map)
            stats.scanned += 1
            stats.by_basis[basis] += 1
            if basis != BASIS_CLEAN:
                if estimated is not None:
                    stats.estimated_filled += 1
                else:
                    stats.estimated_missing += 1
            if basis_filter is not None and basis != basis_filter:
                _record_reclassification(
                    stats,
                    record,
                    previous_basis=previous_basis,
                    basis=basis,
                    estimated=estimated,
                    notice=notice_map.get(record.project_id, _NO_NOTICE_FACTS),
                )
            if apply:
                record.base_amount_basis = basis
                record.base_amount_estimated = estimated
                record.basis_checked_at = stamp

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


def print_impact_report(stats: BackfillStats) -> None:
    """Print the bucket-move impact in a form a reviewer reads before approving.

    The JSON summary already carries every number; this block exists because the
    approval question ("얼마나 많은 행이, 어느 공고 상태·카테고리에서 clean 버킷을
    떠나는가") should be answerable from the terminal without piping through ``jq``.
    Nothing here is a write — a dry-run prints exactly what an ``--apply`` would move.
    """
    if stats.basis_filter is None:
        return
    print(
        f"[backfill-base-basis] '{stats.basis_filter}' 버킷 {stats.scanned}행 중 "
        f"{stats.reclassified}행 이동 (축소율 {stats.bucket_shrink_ratio:.2%})"
    )
    for title, counter in (
        ("status", stats.reclassified_by_status),
        ("category", stats.reclassified_by_category),
    ):
        breakdown = ", ".join(
            f"{key}={count}" for key, count in sorted(counter.items())
        )
        print(f"  이동 {title}별: {breakdown or '없음'}")
    for sample in stats.samples:
        print(
            f"  샘플 id={sample['id']} {sample['from_basis']} → {sample['to_basis']} "
            f"base={sample['base_amount']} est={sample['budget_estimate']} "
            f"ratio={sample['base_to_estimate_ratio']}"
        )


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

    summary = stats.as_dict()
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
