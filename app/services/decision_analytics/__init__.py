"""Decision analytics summaries for operator workflow tuning.

Public surface. ``DecisionAnalyticsService`` and the module-level
``parse_analytics_event_data`` keep their historical import path
(``from app.services.decision_analytics import ...``). The service body was
decomposed into responsibility mixins (base / insights / funnel /
recommendations / experiment_history / operations_kpi / feedback_labels); this
``__init__`` composes them. The split is a pure move — every method body is the
original ``DecisionAnalyticsService`` method, relocated verbatim, so behaviour
is unchanged.
"""

from __future__ import annotations

from app.services.decision_analytics.events import parse_analytics_event_data
from app.services.decision_analytics.experiment_history import (
    _ExperimentHistoryMixin,
)
from app.services.decision_analytics.feedback_labels import _FeedbackLabelsMixin
from app.services.decision_analytics.funnel import _FunnelMixin
from app.services.decision_analytics.insights import _InsightsMixin
from app.services.decision_analytics.operations_kpi import _OperationsKpiMixin
from app.services.decision_analytics.recommendations import _RecommendationsMixin

__all__ = ["DecisionAnalyticsService", "parse_analytics_event_data"]


class DecisionAnalyticsService(
    _InsightsMixin,
    _FunnelMixin,
    _RecommendationsMixin,
    _ExperimentHistoryMixin,
    _OperationsKpiMixin,
    _FeedbackLabelsMixin,
):
    """Build operator-facing analytics from persisted bid decision records."""
