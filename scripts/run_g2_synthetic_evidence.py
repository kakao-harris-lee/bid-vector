#!/usr/bin/env python3
"""Plan or enqueue G-2 synthetic evidence runs from sample-gap candidates.

Default mode is dry-run: it reads recent completed synthetic experiment summaries
and prints the repeatable execution plan without saving experiments or enqueueing
backtests.

Use --write only after operator approval. --write persists the selected
experiment plan when needed and enqueues an asynchronous synthetic experiment
run; it does not run the backtest inline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.synthetic_experiment import SyntheticExperimentService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="write",
        action="store_false",
        default=False,
        help="Print the plan only. This is the default.",
    )
    mode.add_argument(
        "--write",
        dest="write",
        action="store_true",
        help=(
            "Persist the selected experiment plan and enqueue the async run. "
            "Requires explicit operator approval."
        ),
    )
    parser.add_argument(
        "--preset",
        help=(
            "Preferred sample-gap preset name or saved experiment id, for "
            "example g1-software-base-12m or 12. Required for --write unless "
            "--dimension/--key are supplied."
        ),
    )
    parser.add_argument(
        "--dimension",
        choices=["preset", "category", "business_type", "budget_band"],
        help="Exact sample-gap dimension to select.",
    )
    parser.add_argument("--key", help="Exact sample-gap key to select.")
    parser.add_argument("--action-code", help="Override the recommended action.")
    parser.add_argument(
        "--max-runs",
        type=int,
        default=20,
        help="Number of recent completed runs to scan for gaps.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of ranked gaps to include in dry-run context.",
    )
    return parser


def _json_dump(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _select_gap(
    plan: dict[str, Any],
    *,
    preset: str | None,
    dimension: str | None,
    key: str | None,
) -> dict[str, Any] | None:
    gaps = plan.get("gaps") or []
    if dimension or key:
        if not (dimension and key):
            raise ValueError("--dimension and --key must be supplied together.")
        return next(
            (
                gap
                for gap in gaps
                if gap.get("dimension") == dimension and gap.get("key") == key
            ),
            None,
        )
    if preset:
        preset_id = int(preset) if preset.isdigit() else None
        return next(
            (
                gap
                for gap in gaps
                if gap.get("recommendation", {}).get("preset_name") == preset
                or preset in (gap.get("related_preset_names") or [])
                or (
                    preset_id is not None
                    and any(
                        int(run.get("experiment_id") or 0) == preset_id
                        for run in (gap.get("related_runs") or [])
                        if isinstance(run, dict)
                    )
                )
            ),
            None,
        )
    return gaps[0] if gaps else None


def main() -> int:
    args = build_parser().parse_args()
    if args.write and not (args.preset or (args.dimension and args.key)):
        sys.stderr.write(
            "--write requires --preset or an exact --dimension/--key target.\n"
        )
        return 2

    db = SessionLocal()
    try:
        service = SyntheticExperimentService(db)
        plan = service.build_sample_gap_plan(max_runs=args.max_runs)
        try:
            gap = _select_gap(
                plan,
                preset=args.preset,
                dimension=args.dimension,
                key=args.key,
            )
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        if gap is None:
            _json_dump(
                {
                    "status": "no_gap",
                    "mode": "write" if args.write else "dry_run",
                    "message": "No matching sample-gap candidate was found.",
                    "plan_summary": {
                        "gap_count": plan.get("gap_count", 0),
                        "source_run_count": plan.get("source_run_count", 0),
                        "warnings": plan.get("warnings", []),
                    },
                }
            )
            return 2

        dimension = str(gap["dimension"])
        key = str(gap["key"])
        if args.write:
            result = service.materialize_sample_gap_candidate_run(
                dimension=dimension,
                key=key,
                max_runs=args.max_runs,
                action_code=args.action_code,
            )
            if result is None:
                _json_dump({"status": "not_found", "dimension": dimension, "key": key})
                return 2
            if result.get("status") == "blocked":
                _json_dump(
                    {
                        "status": "blocked",
                        "message": (
                            "Run was not enqueued because the candidate is blocked. "
                            "Resolve warnings first."
                        ),
                        "candidate": result.get("candidate"),
                    }
                )
                return 3
            _json_dump(
                {
                    "status": "queued",
                    "mode": "write",
                    "approval_required": True,
                    "write_performed": True,
                    "message": (
                        "DB writes were performed and the async synthetic evidence "
                        "run was enqueued."
                    ),
                    **result,
                }
            )
            return 0

        candidate = service.build_sample_gap_run_candidate(
            dimension=dimension,
            key=key,
            max_runs=args.max_runs,
            action_code=args.action_code,
        )
        _json_dump(
            {
                "status": "planned",
                "mode": "dry_run",
                "write_performed": False,
                "approval_required_for_write": True,
                "message": (
                    "Dry run only. Re-run with --write after approval to save the "
                    "plan and enqueue the asynchronous evidence run."
                ),
                "selected_gap": {"dimension": dimension, "key": key},
                "candidate": candidate,
                "ranked_gaps": (plan.get("gaps") or [])[: max(0, int(args.top or 0))],
                "plan_warnings": plan.get("warnings", []),
            }
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
