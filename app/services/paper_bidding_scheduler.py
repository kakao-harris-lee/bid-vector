"""Periodic forward paper-bidding scheduler."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.paper_bidding_backtest import PaperBiddingBacktestService

logger = logging.getLogger(__name__)


class PaperBiddingForwardScheduler:
    """Run forward paper-bidding on a fixed interval when enabled."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def is_enabled(self) -> bool:
        """Return whether periodic forward paper-bidding is enabled."""
        return bool(settings.PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED)

    def should_run_inprocess(self) -> bool:
        """Use an in-process loop when the broker is in-memory and cannot back Celery beat."""
        return self.is_enabled() and settings.uses_in_memory_celery

    def build_request_payload(self) -> dict:
        """Build the configured forward paper-bidding request payload."""
        category = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY or "").strip() or None
        scenario = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO or "base").strip() or "base"
        if scenario not in {"conservative", "base", "aggressive"}:
            scenario = "base"
        return {
            "category": category,
            "limit": max(1, int(settings.PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT or 1)),
            "scenario": scenario,
            "strategy_version": "scheduled-forward-paper",
            "model_version": "current",
            "history_limit": max(1, int(settings.PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT or 1)),
            "persist": bool(settings.PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST),
        }

    async def start(self) -> None:
        """Start the in-process scheduler when the current runtime mode requires it."""
        if not self.should_run_inprocess():
            if self.is_enabled():
                logger.info(
                    "Forward paper-bidding scheduling is enabled; use Celery beat/worker for broker %s.",
                    settings.CELERY_BROKER_URL,
                )
            return

        if self._task and not self._task.done():
            return

        self._task = asyncio.create_task(self._run_loop(), name="paper_bidding_forward_scheduler")
        logger.info(
            "Started in-process forward paper-bidding scheduler (interval=%s minutes).",
            settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES,
        )

    async def stop(self) -> None:
        """Stop the in-process scheduler cleanly during app shutdown."""
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        interval_seconds = max(1, int(settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES or 1)) * 60

        if settings.PAPER_BIDDING_FORWARD_RUN_ON_STARTUP:
            await self._run_once()

        while True:
            await asyncio.sleep(interval_seconds)
            await self._run_once()

    async def _run_once(self) -> None:
        payload = self.build_request_payload()
        await asyncio.to_thread(self._run_once_sync, payload)

    def _run_once_sync(self, payload: dict) -> None:
        db = SessionLocal()
        try:
            result = PaperBiddingBacktestService().run_forward_paper_bidding(db, **payload)
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
