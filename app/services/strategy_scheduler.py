"""Periodic operator strategy monitoring scheduler."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.inprocess_scheduler import BaseInProcessScheduler
from app.services.opportunity_monitoring import StrategyMonitoringService

logger = logging.getLogger(__name__)


class OperatorStrategyScheduler(BaseInProcessScheduler):
    """Run stored strategy monitoring on a fixed interval when enabled."""

    def is_enabled(self) -> bool:
        """Return whether periodic strategy monitoring is enabled."""
        return bool(settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED)

    def _task_name(self) -> str:
        return "operator_strategy_scheduler"

    def _enabled_log_message(self) -> str:
        return "Operator strategy scheduling is enabled; use Celery beat/worker for broker %s."

    def _started_log_message(self) -> str:
        return "Started in-process operator strategy scheduler (interval=%s minutes)."

    def _started_log_interval(self) -> Any:
        return settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES

    def _interval_seconds(self) -> int:
        return max(1, settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES) * 60

    def _run_on_startup(self) -> bool:
        return bool(settings.OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP)

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

    def build_payload(self) -> OperatorStrategyMonitorRequest:
        return self.build_request()

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
