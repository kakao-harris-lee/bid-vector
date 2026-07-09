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
from app.core.database import SessionLocal  # noqa: E402
from app.core.time import to_kst  # noqa: E402
from app.models.models import Analytics  # noqa: E402
from app.services.g2_evidence_draft import build_daily_evidence_draft  # noqa: E402


def reconstruct_pass_draft(
    *,
    event_data: dict[str, Any],
    target_operator_ids: list[int],
    required_days: int,
    run_date_kst: str,
) -> dict[str, Any] | None:
    """Rebuild a ledger draft for one snapshot, or None if that day did not pass.

    Reuses ``build_daily_evidence_draft`` as the single source of truth for the
    daily pass rule: the draft is returned only when its ``daily_status`` verdict
    is ``pass`` (all targets present + ready + zero blocking gaps).
    """
    per_operator = {
        int(item["operator_id"]): item
        for item in event_data.get("per_operator") or []
        if item.get("operator_id") is not None
    }
    summaries: list[dict[str, Any]] = []
    for tid in target_operator_ids:
        snap = per_operator.get(int(tid))
        if snap is None:
            summaries.append(
                {"operator_id": int(tid), "username": None, "error": "missing_snapshot"}
            )
            continue
        if snap.get("error"):
            summaries.append(
                {
                    "operator_id": int(tid),
                    "username": snap.get("username"),
                    "error": str(snap.get("error")),
                }
            )
            continue
        # The stored compact carries blocking_gaps_count, not the gap list; a
        # count>0 must still block the day, so synthesize placeholder gap rows
        # that only ever appear on a non-pass day (which is dropped below).
        gap_count = int(snap.get("blocking_gaps_count") or 0)
        summaries.append(
            {
                "operator_id": int(tid),
                "username": snap.get("username"),
                "evidence_status": str(snap.get("evidence_status") or ""),
                "sections": dict(snap.get("sections") or {}),
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


def _passing_dates(
    db, *, target_operator_ids: list[int], required_days: int
) -> dict[str, dict[str, Any]]:
    """Map each qualifying KST date -> its reconstructed pass draft.

    A date qualifies if ANY of that day's ``collect_g2_evidence`` snapshots
    reconstructs to a pass; the latest passing snapshot for the date wins.
    """
    rows = (
        db.query(Analytics)
        .filter(Analytics.event_type == "collect_g2_evidence")
        .order_by(Analytics.timestamp.asc())
        .all()
    )
    drafts: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.timestamp is None:
            continue
        run_date_kst = to_kst(row.timestamp).date().isoformat()
        try:
            event_data = json.loads(row.event_data or "{}")
        except (TypeError, ValueError):
            continue
        draft = reconstruct_pass_draft(
            event_data=event_data,
            target_operator_ids=target_operator_ids,
            required_days=required_days,
            run_date_kst=run_date_kst,
        )
        if draft is not None:
            drafts[run_date_kst] = draft  # ascending order -> latest pass wins
    return drafts


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
