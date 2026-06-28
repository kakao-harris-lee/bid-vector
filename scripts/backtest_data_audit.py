#!/usr/bin/env python3
"""Generate a DB readiness audit for paper-bidding backtests."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal
from app.services.backtest_data_audit import BacktestDataAuditService
from scripts._common import parse_datetime


def parse_categories(values: list[str] | None) -> list[str]:
    categories: list[str] = []
    for raw_value in values or []:
        for item in raw_value.split(","):
            category = item.strip()
            if category and category not in categories:
                categories.append(category)
    return categories


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit DB readiness for paper-bidding backtests.")
    parser.add_argument("--category", action="append", help="Category filter. May be repeated or comma-separated.")
    parser.add_argument("--start-date", help="Award window start date/datetime.")
    parser.add_argument("--end-date", help="Award window end date/datetime.")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Defaults to models/reports/backtest-data-audit-<timestamp>.json.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.out or f"models/reports/backtest-data-audit-{started_at}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        report = BacktestDataAuditService().build_report(
            db,
            categories=parse_categories(args.category),
            start_at=parse_datetime(args.start_date),
            end_at=parse_datetime(args.end_date, end_of_day=True),
        )
    finally:
        db.close()

    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "out": str(output_path), "window_counts": report["window_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
