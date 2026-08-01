#!/usr/bin/env python3
"""Backfill daily G-2 ledger manifest drafts from stored evidence snapshots.

The ``collect_g2_evidence`` beat only began writing a daily ``manifest-draft.json``
after PR #155 deployed, so ``counted_days`` does not reflect the days the beat
already recorded a ledger snapshot (as ``collect_g2_evidence`` analytics events)
before that. This one-off backfill reconstructs a ledger draft for each past KST
date on which every target operator was ``evidence_status == "ready"`` with zero
blocking gaps in the stored snapshot — i.e. days that already qualified — and
writes them under ``reports/g2-evidence/daily/<YYYY-MM-DD>/`` so
``scripts/build_g2_exit_review.py`` counts them.

Honesty: this reuses the exact same ledger pass rule as the live beat draft
(``build_daily_evidence_draft`` is the single source of truth) and only writes
days that genuinely passed in the recorded ledger. Every entry carries the
``collect_g2_evidence_beat`` source stamp. The same rolling-window caveat applies
(``evidence_status`` is a 30-day trailing snapshot), so a backfilled
``counted_days`` means "the ledger was ready on N calendar dates", not "N days of
fresh forward activity". The exit stays human-approved.

Usage:
    python scripts/backfill_g2_daily_drafts.py --dry-run   # default: show only
    python scripts/backfill_g2_daily_drafts.py --apply      # write draft files
    python scripts/backfill_g2_daily_drafts.py --apply --overwrite
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.constants import COLLECT_G2_EVIDENCE_EVENT_TYPE  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.time import to_kst  # noqa: E402
from app.models.models import Analytics  # noqa: E402
from app.schemas.g2_evidence import (  # noqa: E402
    PersistedG2CollectEvidenceSummary,
)
from app.services.analytics_event_payload import load_analytics_event_as  # noqa: E402
from app.services.g2_evidence_draft import build_daily_evidence_draft  # noqa: E402

# A snapshot whose payload cannot be decoded at all: no per-operator cells, so
# every target reconstructs as ``missing_snapshot`` and the day cannot pass.
_UNREADABLE_SNAPSHOT = PersistedG2CollectEvidenceSummary()


def reconstruct_pass_draft(
    *,
    event_data: PersistedG2CollectEvidenceSummary,
    target_operator_ids: list[int],
    required_days: int,
    run_date_kst: str,
) -> dict[str, Any] | None:
    """Rebuild a ledger draft for one snapshot, or None if that day did not pass.

    Reuses ``build_daily_evidence_draft`` as the single source of truth for the
    daily pass rule: the draft is returned only when its ``daily_status`` verdict
    is ``pass`` (all targets present + ready + zero blocking gaps).

    ``event_data`` is the *restored* contract (:mod:`app.schemas.g2_evidence`), so
    a key the producer never wrote reads as ``None`` rather than ``0`` — and an
    unrecorded ``blocking_gaps_count`` is treated as an incomplete snapshot rather
    than as "no blocking gaps" (see the fail-closed branch below).
    """
    per_operator = {
        int(item.operator_id): item
        for item in event_data.per_operator or []
        if item.operator_id is not None
    }
    summaries: list[dict[str, Any]] = []
    for tid in target_operator_ids:
        snap = per_operator.get(int(tid))
        if snap is None:
            summaries.append(
                {"operator_id": int(tid), "username": None, "error": "missing_snapshot"}
            )
            continue
        if snap.error:
            summaries.append(
                {
                    "operator_id": int(tid),
                    "username": snap.username,
                    "error": str(snap.error),
                }
            )
            continue
        if snap.blocking_gaps_count is None:
            # Fail closed: a snapshot with no recorded gap count cannot certify
            # "zero blocking gaps". Reading the absence as ``0`` would fabricate a
            # counted day out of a row that never recorded one (§2 정직 명세). The
            # live producer has always written this key, so this only ever blocks
            # corrupted/legacy rows — it can lower counted_days, never raise it.
            summaries.append(
                {
                    "operator_id": int(tid),
                    "username": snap.username,
                    "error": "incomplete_snapshot",
                }
            )
            continue
        # The stored compact carries blocking_gaps_count, not the gap list; a
        # count>0 must still block the day, so synthesize placeholder gap rows
        # that only ever appear on a non-pass day (which is dropped below).
        gap_count = int(snap.blocking_gaps_count)
        summaries.append(
            {
                "operator_id": int(tid),
                "username": snap.username,
                "evidence_status": str(snap.evidence_status or ""),
                "sections": dict(snap.sections or {}),
                "blocking_gaps": [f"blocking gap #{i}" for i in range(1, gap_count + 1)],
            }
        )

    draft = build_daily_evidence_draft(
        operator_summaries=summaries,
        target_operator_ids=list(target_operator_ids),
        run_date_kst=run_date_kst,
        required_days=required_days,
    )
    return draft if draft["daily_status"][0]["status"] == "pass" else None


def select_pass_drafts(
    *,
    snapshots: list[tuple[Any, PersistedG2CollectEvidenceSummary]],
    target_operator_ids: list[int],
    required_days: int,
) -> dict[str, dict[str, Any]]:
    """Map each KST date -> reconstructed pass draft, mirroring live beat semantics.

    The live beat overwrites a KST day's draft on every run, so the *latest*
    snapshot of a day is what would sit on disk. This mirrors that exactly: pick
    the latest snapshot per KST date, and keep it only if that snapshot
    reconstructs to a pass. A day that flickered ready->insufficient by its last
    snapshot is therefore NOT counted — same as the live beat would have left it
    — which avoids over-counting an intra-day-only pass.
    """
    latest: dict[str, tuple[Any, PersistedG2CollectEvidenceSummary]] = {}
    for timestamp, event_data in snapshots:
        if timestamp is None:
            continue
        run_date_kst = to_kst(timestamp).date().isoformat()
        current = latest.get(run_date_kst)
        if current is None or timestamp >= current[0]:
            latest[run_date_kst] = (timestamp, event_data)

    drafts: dict[str, dict[str, Any]] = {}
    for run_date_kst, (_, event_data) in latest.items():
        draft = reconstruct_pass_draft(
            event_data=event_data,
            target_operator_ids=target_operator_ids,
            required_days=required_days,
            run_date_kst=run_date_kst,
        )
        if draft is not None:
            drafts[run_date_kst] = draft
    return drafts


def _passing_dates(
    db, *, target_operator_ids: list[int], required_days: int
) -> dict[str, dict[str, Any]]:
    """Load ``collect_g2_evidence`` snapshots and reconstruct per-day pass drafts."""
    rows = (
        db.query(Analytics)
        .filter(Analytics.event_type == COLLECT_G2_EVIDENCE_EVENT_TYPE)
        .order_by(Analytics.timestamp.asc())
        .all()
    )
    snapshots = [
        (
            row.timestamp,
            load_analytics_event_as(
                row.event_data,
                model=PersistedG2CollectEvidenceSummary,
                event_type=COLLECT_G2_EVIDENCE_EVENT_TYPE,
            )
            or _UNREADABLE_SNAPSHOT,
        )
        for row in rows
    ]
    return select_pass_drafts(
        snapshots=snapshots,
        target_operator_ids=target_operator_ids,
        required_days=required_days,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without writing (default).",
    )
    group.add_argument(
        "--apply", action="store_true", help="Write the reconstructed draft files."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing daily draft file for a date.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    apply = bool(args.apply)

    target_ids = settings.g2_evidence_target_operator_ids
    required_days = max(1, int(settings.G2_EVIDENCE_REQUIRED_DAYS))
    draft_root = REPO_ROOT / settings.G2_EVIDENCE_DAILY_DRAFT_DIR
    if not target_ids:
        print("No G2_EVIDENCE_TARGET_OPERATOR_IDS configured; nothing to backfill.")
        return 0

    db = SessionLocal()
    try:
        drafts = _passing_dates(
            db, target_operator_ids=target_ids, required_days=required_days
        )
    finally:
        db.close()

    written, skipped = [], []
    for run_date_kst in sorted(drafts):
        path = draft_root / run_date_kst / "manifest-draft.json"
        if path.exists() and not args.overwrite:
            skipped.append(run_date_kst)
            continue
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(drafts[run_date_kst], ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        written.append(run_date_kst)

    mode = "APPLIED" if apply else "DRY-RUN (use --apply to write)"
    print(f"[backfill-g2-daily-drafts] {mode} targets={target_ids}")
    print(f"  qualifying pass dates ({len(drafts)}): {sorted(drafts)}")
    print(f"  {'wrote' if apply else 'would write'} ({len(written)}): {written}")
    print(f"  skipped existing ({len(skipped)}): {skipped}")
    print(
        "  note: ledger-based (source=collect_g2_evidence_beat); rolling-window "
        "counted_days = dates the ledger was ready, not fresh-activity days."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
