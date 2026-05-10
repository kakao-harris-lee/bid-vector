"""Periodic operator strategy monitoring scheduler."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring import StrategyMonitoringService

logger = logging.getLogger(__name__)


class OperatorStrategyScheduler:
    """Run stored strategy monitoring on a fixed interval when enabled."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def is_enabled(self) -> bool:
        """Return whether periodic strategy monitoring is enabled."""
        return bool(settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED)

    def should_run_inprocess(self) -> bool:
        """Use an in-process loop when the broker is in-memory and cannot back Celery beat/worker."""
        return self.is_enabled() and settings.CELERY_BROKER_URL.startswith("memory://")

    def build_request(self) -> OperatorStrategyMonitorRequest:
        """Build the configured periodic monitoring request payload."""
        return OperatorStrategyMonitorRequest(
            limit=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT,
            high_priority_only=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY,
            max_active_bids=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS,
            current_workload_score=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE,
            same_category_only=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY,
            similar_limit=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT,
            min_similarity=settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY,
        )

    async def start(self) -> None:
        """Start the in-process scheduler when the current runtime mode requires it."""
        if not self.should_run_inprocess():
            if self.is_enabled():
                logger.info(
                    "Operator strategy scheduling is enabled; use Celery beat/worker for broker %s.",
                    settings.CELERY_BROKER_URL,
                )
            return

        if self._task and not self._task.done():
            return

        self._task = asyncio.create_task(self._run_loop(), name="operator_strategy_scheduler")
        logger.info(
            "Started in-process operator strategy scheduler (interval=%s minutes).",
            settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES,
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
        """Sleep between runs and execute the monitor repeatedly."""
        interval_seconds = max(1, settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES) * 60

        if settings.OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP:
            await self._run_once()

        while True:
            await asyncio.sleep(interval_seconds)
            await self._run_once()

    async def _run_once(self) -> None:
        """Execute one scheduled monitoring cycle without blocking the event loop."""
        request = self.build_request()
        await asyncio.to_thread(self._run_once_sync, request)

    def _run_once_sync(self, request: OperatorStrategyMonitorRequest) -> None:
        """Run one scheduled cycle in a plain sync DB session."""
        db = SessionLocal()
        try:
            result = StrategyMonitoringService().execute_monitoring(
                db,
                request=request,
                trigger_source=StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE,
            )
            logger.info(
                "Scheduled operator strategy monitor finished: persisted=%s notifications=%s run_id=%s",
                result.get("persisted_candidate_count"),
                result.get("notification_count"),
                result.get("monitor_run_id"),
            )
        except Exception:
            logger.exception("Scheduled operator strategy monitor failed.")
        finally:
            db.close()


strategy_scheduler = OperatorStrategyScheduler()