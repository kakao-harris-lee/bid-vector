#!/usr/bin/env python3
"""Backfill HistoricalData.base_amount_basis / _estimated (provenance tagging).

Classifies every ``HistoricalData`` row's stored ``base_amount`` provenance
(``classify_base_basis``) and, for non-clean rows, records an estimated real
기초금액 from 복수예비가격 (``estimate_base_amount_from_reserves``). The original
``base_amount`` is NEVER overwritten (정직 명세 §2). Winning result inputs are
joined from ``TenderResult`` on ``project_id``, preferring a row with a real
``winning_amount > 0``.

Idempotent: rows already stamped with ``basis_checked_at`` are skipped unless
``--recheck`` is passed. Processing uses keyset (id-ordered) pagination with a
commit per chunk so a re-run resumes cleanly and never re-scans finished rows.
No external calls — DB only.

Usage:
    python scripts/backfill_base_amount_basis.py --dry-run          # default, counts only
    python scripts/backfill_base_amount_basis.py --apply
    python scripts/backfill_base_amount_basis.py --apply --chunk-size 2000
    python scripts/backfill_base_amount_basis.py --apply --recheck  # re-classify all
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
from app.models.models import HistoricalData, TenderResult  # noqa: E402
from app.services.base_amount_basis import (  # noqa: E402
    ALL_BASES,
    BASIS_CLEAN,
    classify_base_basis,
    estimate_base_amount_from_reserves,
    normalize_winning_rate,
)


@dataclass
class BackfillStats:
    """Aggregate counts for one backfill run (dry-run or apply)."""

    applied: bool = False
    recheck: bool = False
    scanned: int = 0
    by_basis: Counter = field(default_factory=Counter)
    estimated_filled: int = 0  # non-clean rows that got an estimate
    estimated_missing: int = 0  # non-clean rows without recoverable reserves

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "recheck": self.recheck,
            "scanned": self.scanned,
            "by_basis": {
                basis: int(self.by_basis.get(basis, 0)) for basis in ALL_BASES
            },
            "estimated_filled": self.estimated_filled,
            "estimated_missing": self.estimated_missing,
        }


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


def _classify_record(
    record: HistoricalData,
    result_map: dict[int, tuple[float, float]],
) -> tuple[str, float | None]:
    """Return (basis, estimated) for one row without touching the DB."""
    winning_amount, winning_rate = result_map.get(record.project_id, (0.0, 0.0))
    basis = classify_base_basis(record.base_amount, winning_amount, winning_rate)
    estimated = None
    if basis != BASIS_CLEAN:
        estimated = estimate_base_amount_from_reserves(record.reserve_prices)
    return basis, estimated


def run_backfill(
    db: Session,
    *,
    apply: bool,
    recheck: bool = False,
    chunk_size: int = 1000,
    limit: int | None = None,
    progress: bool = False,
) -> BackfillStats:
    """Classify base_amount provenance in id-ordered chunks (commit per chunk)."""
    stats = BackfillStats(applied=apply, recheck=recheck)
    last_id = 0
    while True:
        remaining = None if limit is None else limit - stats.scanned
        if remaining is not None and remaining <= 0:
            break
        page_limit = chunk_size if remaining is None else min(chunk_size, remaining)

        query = db.query(HistoricalData).filter(HistoricalData.id > last_id)
        if not recheck:
            query = query.filter(HistoricalData.basis_checked_at.is_(None))
        chunk = query.order_by(HistoricalData.id.asc()).limit(page_limit).all()
        if not chunk:
            break

        project_ids = list({r.project_id for r in chunk if r.project_id is not None})
        result_map = _load_result_map(db, project_ids)
        stamp = utc_now()
        for record in chunk:
            last_id = record.id
            basis, estimated = _classify_record(record, result_map)
            stats.scanned += 1
            stats.by_basis[basis] += 1
            if basis != BASIS_CLEAN:
                if estimated is not None:
                    stats.estimated_filled += 1
                else:
                    stats.estimated_missing += 1
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    chunk_size = max(1, args.chunk_size)

    db = SessionLocal()
    try:
        stats = run_backfill(
            db,
            apply=args.apply,
            recheck=args.recheck,
            chunk_size=chunk_size,
            limit=args.limit,
            progress=True,
        )
    finally:
        db.close()

    summary = stats.as_dict()
    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"[backfill-base-basis] {mode}")
    print(json.dumps(summary, ensure_ascii=False))
    if args.audit is not None:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
