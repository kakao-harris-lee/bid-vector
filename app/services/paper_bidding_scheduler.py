"""Periodic forward paper-bidding scheduler."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.inprocess_scheduler import BaseInProcessScheduler
from app.services.paper_bidding_backtest import PaperBiddingBacktestService

logger = logging.getLogger(__name__)


class PaperBiddingForwardScheduler(BaseInProcessScheduler):
    """Run forward paper-bidding on a fixed interval when enabled."""

    def is_enabled(self) -> bool:
        """Return whether periodic forward paper-bidding is enabled."""
        return bool(settings.PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED)

    def _task_name(self) -> str:
        return "paper_bidding_forward_scheduler"

    def _enabled_log_message(self) -> str:
        return "Forward paper-bidding scheduling is enabled; use Celery beat/worker for broker %s."

    def _started_log_message(self) -> str:
        return (
            "Started in-process forward paper-bidding scheduler (interval=%s minutes)."
        )

    def _started_log_interval(self) -> Any:
        return settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES

    def _interval_seconds(self) -> int:
        return max(1, int(settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES or 1)) * 60

    def _run_on_startup(self) -> bool:
        return bool(settings.PAPER_BIDDING_FORWARD_RUN_ON_STARTUP)

    def build_request_payload(self) -> dict:
        """Build the configured forward paper-bidding request payload."""
        category = (
            str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY or "").strip() or None
        )
        scenario = (
            str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO or "base").strip()
            or "base"
        )
        if scenario not in {"conservative", "base", "aggressive"}:
            scenario = "base"
        return {
            "category": category,
            "limit": max(1, int(settings.PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT or 1)),
            "scenario": scenario,
            "strategy_version": "scheduled-forward-paper",
            "model_version": "current",
            "history_limit": max(
                1, int(settings.PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT or 1)
            ),
            "persist": bool(settings.PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST),
        }

    def build_payload(self) -> dict:
        return self.build_request_payload()

    def _run_once_sync(self, payload: dict) -> None:
        db = SessionLocal()
        try:
            result = PaperBiddingBacktestService().run_forward_paper_bidding(
                db, **payload
            )
            logger.info(
                "Scheduled forward paper-bidding finished: run_id=%s candidates=%s paper_bids=%s",
                result.get("run_id"),
                result.get("summary", {}).get("candidate_count"),
                result.get("summary", {}).get("paper_bid_count"),
            )
        except Exception:
            logger.exception("Scheduled forward paper-bidding failed.")
        finally:
            db.close()


paper_bidding_forward_scheduler = PaperBiddingForwardScheduler()
