"""Shared foundation for the strategy-monitoring service.

Holds the ``StrategyFilterResult`` / ``StrategyCandidateEvaluation`` value
objects and ``_MonitoringBase`` -- the class constants and ``__init__`` shared
by every ``StrategyMonitoringService`` mixin. Every member is moved verbatim
from the original single ``opportunity_monitoring`` module; the split is a pure
move, so the watch-rule gate, scan bounds, and price/threshold logic are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.constants import ACTIVE_PROJECT_STATUSES as _ACTIVE_PROJECT_STATUSES
from app.core.database import SessionLocal
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService
from app.services.opportunity_analysis import OpportunityAnalysisService


@dataclass
class StrategyFilterResult:
    """Intermediate result for strategy pre-filter checks."""

    matched: bool
    reasons: list[str]


@dataclass
class StrategyCandidateEvaluation:
    """A strategy-filtered candidate, serialized at analysis time (slim).

    스캔 메모리 위생(설계 2026-07-30 §5 PR-A-1): 분석 직후 ``candidate``
    (preview 직렬화 dict)와 ``sort_key`` 만 보관하고 ORM ``Project`` / 전체
    analysis dict 참조는 즉시 해제한다. monitor 경로는 선택된 top-N 만
    ``project_id`` 로 재조회한다 (orchestration._process_monitor_evaluation).
    """

    project_id: int
    candidate: dict
    sort_key: tuple
    strategy_reasons: list[str]


class _MonitoringBase:
    """Class constants and ``__init__`` shared by every monitoring mixin."""

    # Sourced from the single constant in app/core/constants.py; kept as a class
    # attribute so the existing self.ACTIVE_PROJECT_STATUSES reference (and any
    # external one) stays intact. Value is unchanged: {"open", "re_notice"}.
    ACTIVE_PROJECT_STATUSES = _ACTIVE_PROJECT_STATUSES
    DEFAULT_LIMIT = 10
    DEFAULT_MAX_ACTIVE_BIDS = 3
    DEFAULT_SAME_CATEGORY_ONLY = True
    DEFAULT_SIMILAR_LIMIT = 3
    DEFAULT_MIN_SIMILARITY = 0.15
    PREVIEW_SCAN_MULTIPLIER = 12
    PREVIEW_SCAN_FLOOR = 30
    PREVIEW_SCAN_CEILING = 250
    # Scheduled monitor scan bound. The configured
    # OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT is clamped into this range and
    # also floored to resolved_limit x SCHEDULE_SCAN_MULTIPLIER so a small per-run
    # limit cannot starve filter-passing candidates while still keeping the
    # expensive per-candidate ML analysis count bounded (and the periodic task
    # well under the consumer timeout).
    SCHEDULE_SCAN_MULTIPLIER = 12
    SCHEDULE_SCAN_FLOOR = 30
    SCHEDULE_SCAN_CEILING = 400
    SYNC_TRIGGER_SOURCE = "manual_sync"
    ASYNC_TRIGGER_SOURCE = "manual_async"
    SCHEDULED_TRIGGER_SOURCE = "scheduled"

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self.analysis_service = OpportunityAnalysisService()
        self.decision_service = BidDecisionService()
        self.notification_service = OperatorNotificationService()
        self._session_factory = session_factory
