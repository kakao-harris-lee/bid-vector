#!/usr/bin/env python3
"""Run a rolling holdout backtest for price predictors."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ai.predictor_backtest import build_predictor_backtest_report  # noqa: E402
from app.ai.predictors import (  # noqa: E402
    EnsembleBidRatePredictor,
    HistoricalStatisticalPredictor,
    LSTMBidRatePredictor,
    PricePredictionContext,
)
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.models import HistoricalData  # noqa: E402
from app.services.base_amount_basis import BASIS_CLEAN  # noqa: E402
from scripts._common import parse_datetime  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest price predictors over historical bid-rate rows.")
    parser.add_argument("--category", default="", help="Optional category filter, such as construction or service.")
    parser.add_argument("--start-date", help="Historical opened_at/created_at window start.")
    parser.add_argument("--end-date", help="Historical opened_at/created_at window end.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum chronological rows to evaluate.")
    parser.add_argument("--holdout-size", type=int, default=None, help="Override PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE.")
    parser.add_argument("--min-training-samples", type=int, default=None, help="Override PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES.")
    parser.add_argument(
        "--base-amount-basis",
        choices=[BASIS_CLEAN, "any"],
        default=BASIS_CLEAN,
        help="Base-amount provenance allowed in the holdout (default: clean).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Defaults to models/reports/price-backtest-<timestamp>.json.",
    )
    return parser


def load_records(
    db,
    *,
    category: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
    limit: int,
    base_amount_basis: str | None = BASIS_CLEAN,
) -> list[HistoricalData]:
    query = db.query(HistoricalData).filter(HistoricalData.bid_rate > 0, HistoricalData.base_amount > 0)
    if base_amount_basis:
        query = query.filter(HistoricalData.base_amount_basis == base_amount_basis)
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
            base_amount_basis=(
                None if args.base_amount_basis == "any" else args.base_amount_basis
            ),
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
    backtest = build_predictor_backtest_report(context, build_registry())
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "category": args.category.strip() or None,
        "record_count": len(records),
        "settings": {
            "holdout_size": settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE,
            "min_training_samples": settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES,
            "base_amount_basis": args.base_amount_basis,
        },
        "dataset_quality_status": "warning",
        "dataset_quality": {
            "status": "warning",
            "record_count": len(records),
            "base_amount_basis": args.base_amount_basis,
            "scope": "usable bid-rate and base-amount rows; no freshness audit",
        },
        **backtest,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "out": str(output_path),
                "record_count": len(records),
                "best_predictor_key": report.get("best_predictor_key"),
                "best_average_absolute_error_rate": report.get(
                    "best_average_absolute_error_rate"
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
