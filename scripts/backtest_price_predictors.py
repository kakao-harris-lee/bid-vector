#!/usr/bin/env python3
"""Run a rolling holdout backtest for price predictors."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ai.predictor_backtest import build_predictor_backtest_report
from app.ai.predictors import EnsembleBidRatePredictor, HistoricalStatisticalPredictor, LSTMBidRatePredictor, PricePredictionContext
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import HistoricalData


def parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) == 10:
        parsed_date = datetime.fromisoformat(normalized).date()
        parsed_time = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest price predictors over historical bid-rate rows.")
    parser.add_argument("--category", default="", help="Optional category filter, such as construction or service.")
    parser.add_argument("--start-date", help="Historical opened_at/created_at window start.")
    parser.add_argument("--end-date", help="Historical opened_at/created_at window end.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum chronological rows to evaluate.")
    parser.add_argument("--holdout-size", type=int, default=None, help="Override PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE.")
    parser.add_argument("--min-training-samples", type=int, default=None, help="Override PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES.")
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Defaults to models/reports/price-backtest-<timestamp>.json.",
    )
    return parser


def load_records(db, *, category: str | None, start_at: datetime | None, end_at: datetime | None, limit: int) -> list[HistoricalData]:
    query = db.query(HistoricalData).filter(HistoricalData.bid_rate > 0, HistoricalData.base_amount > 0)
    if category:
        query = query.filter(HistoricalData.category == category)
    if start_at is not None:
        query = query.filter((HistoricalData.opened_at >= start_at) | ((HistoricalData.opened_at.is_(None)) & (HistoricalData.created_at >= start_at)))
    if end_at is not None:
        query = query.filter((HistoricalData.opened_at <= end_at) | ((HistoricalData.opened_at.is_(None)) & (HistoricalData.created_at <= end_at)))
    return (
        query.order_by(HistoricalData.opened_at.asc(), HistoricalData.created_at.asc(), HistoricalData.id.asc())
        .limit(max(1, int(limit or 1)))
        .all()
    )


def build_registry() -> dict[str, Any]:
    return {
        "historical": HistoricalStatisticalPredictor(),
        "lstm": LSTMBidRatePredictor(),
        "ensemble": EnsembleBidRatePredictor(),
    }


def main() -> int:
    args = build_parser().parse_args()
    if args.holdout_size is not None:
        settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE = max(1, int(args.holdout_size))
    if args.min_training_samples is not None:
        settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES = max(1, int(args.min_training_samples))

    started_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    category_slug = args.category.strip() or "all"
    output_path = Path(args.out or f"models/reports/price-backtest-{category_slug}-{started_at}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        records = load_records(
            db,
            category=args.category.strip() or None,
            start_at=parse_datetime(args.start_date),
            end_at=parse_datetime(args.end_date, end_of_day=True),
            limit=args.limit,
        )
    finally:
        db.close()

    budget = float(records[-1].base_amount or 0.0) if records else 0.0
    context = PricePredictionContext(
        budget=budget,
        category=args.category.strip() or "all",
        description=f"Rolling predictor backtest for {args.category.strip() or 'all categories'}",
        historical_records=tuple(records),
        agency_name=None,
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "category": args.category.strip() or None,
        "record_count": len(records),
        "settings": {
            "holdout_size": settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE,
            "min_training_samples": settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES,
        },
        "backtest": build_predictor_backtest_report(context, build_registry()),
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["backtest"]["status"],
                "out": str(output_path),
                "record_count": len(records),
                "best_predictor_key": report["backtest"].get("best_predictor_key"),
                "best_average_absolute_error_rate": report["backtest"].get("best_average_absolute_error_rate"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
