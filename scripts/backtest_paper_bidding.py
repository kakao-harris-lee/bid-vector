#!/usr/bin/env python3
"""Run a historical paper-bidding backtest and optional DB persistence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import Base, SessionLocal, engine
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from scripts._common import parse_datetime


def parse_actions(value: str) -> tuple[str, ...]:
    actions = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return actions or ("bid_now",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical paper-bidding backtests.")
    parser.add_argument("--category", default="", help="Optional category filter, such as construction or service.")
    parser.add_argument("--start-date", help="Award window start date/datetime.")
    parser.add_argument("--end-date", help="Award window end date/datetime.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum awarded projects to replay.")
    parser.add_argument("--scenario", choices=["conservative", "base", "aggressive"], default="base")
    parser.add_argument("--history-limit", type=int, default=80)
    parser.add_argument("--cutoff-hours-before-deadline", type=int, default=2)
    parser.add_argument("--strategy-version", default="local-backtest")
    parser.add_argument("--model-version", default="current")
    parser.add_argument(
        "--settle-actions",
        default="bid_now",
        help="Comma-separated actions counted as virtual submissions. Default: bid_now.",
    )
    parser.add_argument("--persist", action="store_true", help="Persist paper_bid_* rows in the configured DB.")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Defaults to models/reports/paper-bidding-backtest-<timestamp>.json.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    category_slug = args.category.strip() or "all"
    output_path = Path(args.out or f"models/reports/paper-bidding-backtest-{category_slug}-{started_at}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.persist:
        Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        result = PaperBiddingBacktestService().run_historical_backtest(
            db,
            category=args.category.strip() or None,
            start_at=parse_datetime(args.start_date),
            end_at=parse_datetime(args.end_date, end_of_day=True),
            limit=args.limit,
            scenario=args.scenario,
            strategy_version=args.strategy_version,
            model_version=args.model_version,
            cutoff_hours_before_deadline=args.cutoff_hours_before_deadline,
            history_limit=args.history_limit,
            settle_actions=parse_actions(args.settle_actions),
            persist=bool(args.persist),
        )
    finally:
        db.close()

    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "completed",
                "out": str(output_path),
                "run_id": result.get("run_id"),
                "summary": result.get("summary", {}),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
