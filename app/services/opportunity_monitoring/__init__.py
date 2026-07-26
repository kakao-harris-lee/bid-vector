"""Strategy-driven project monitoring for the singleton operator -- public surface.

``StrategyMonitoringService`` (and the ``has_watch_rules`` /
``matches_strategy_watch_rules`` gate, plus the ``StrategyFilterResult`` /
``StrategyCandidateEvaluation`` value objects) keep their historical import path
(``from app.services.opportunity_monitoring import ...``). The service body was
decomposed into responsibility mixins (base / orchestration / runs / candidates
/ filters / serialization) and the module-level watch-rule gate; this
``__init__`` composes them. The split is a pure move -- every method body is the
original ``StrategyMonitoringService`` member, relocated verbatim, so the watch
filters, license gate, scan bounds, and threshold logic are unchanged.

``settings`` and ``ensure_operator_profile_for`` are re-exported here because
tests monkeypatch them on this package namespace
(``opportunity_monitoring.settings`` / ``.ensure_operator_profile_for``); the
license-gate code in ``candidates`` resolves ``ensure_operator_profile_for``
through this module so that surface keeps working after the split.
"""

from __future__ import annotations

from app.core.config import settings  # noqa: F401 — re-exported monkeypatch surface
from app.core.single_user import (  # noqa: F401 — re-exported monkeypatch surface
    ensure_operator_profile_for,
)
from app.services.opportunity_monitoring.base import (
    StrategyCandidateEvaluation,
    StrategyFilterResult,
)
from app.services.opportunity_monitoring.candidates import _CandidateCollectionMixin
from app.services.opportunity_monitoring.filters import _WatchFilterMixin
from app.services.opportunity_monitoring.orchestration import _OrchestrationMixin
from app.services.opportunity_monitoring.runs import _RunLifecycleMixin
from app.services.opportunity_monitoring.serialization import _SerializationMixin

__all__ = [
    "StrategyCandidateEvaluation",
    "StrategyFilterResult",
    "StrategyMonitoringService",
    "WATCH_RULE_BUDGET_FIELDS",
    "WATCH_RULE_TEXT_FIELDS",
    "has_watch_rules",
    "matches_strategy_watch_rules",
]


class StrategyMonitoringService(
    _OrchestrationMixin,
    _RunLifecycleMixin,
    _CandidateCollectionMixin,
    _WatchFilterMixin,
    _SerializationMixin,
):
    """Evaluate open procurement notices against the operator's watch strategy."""


# Imported after the class definition: watch_rules only needs the composed
# service lazily (inside _watch_rule_service), so this order is not required, but
# it keeps the public gate re-export next to the class it delegates to.
from app.services.opportunity_monitoring.watch_rules import (  # noqa: E402
    WATCH_RULE_BUDGET_FIELDS,
    WATCH_RULE_TEXT_FIELDS,
    _watch_rule_service,  # noqa: F401 — re-exported private surface (opportunity_monitoring._watch_rule_service)
    has_watch_rules,
    matches_strategy_watch_rules,
)
