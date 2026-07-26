"""Public watch-rule gate (cheap filters only, no ML).

Callers outside the monitor (e.g. backfill target prioritisation) need the same
"is this notice in the operator's watch scope?" answer WITHOUT the per-candidate
ML analysis, which is far too slow to run over the whole open-notice set. These
thin delegators expose the existing filters; the rules themselves stay defined
once, in ``_apply_strategy_filters``.

Moved verbatim from the original ``opportunity_monitoring`` module. The
``StrategyMonitoringService`` import is deferred to call time to avoid a package
import cycle (this module is imported by the package ``__init__`` that defines
the composed service class).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.single_user import split_multi_value_text
from app.models.models import OperatorStrategy, Project

if TYPE_CHECKING:
    from app.services.opportunity_monitoring import StrategyMonitoringService

# The strategy fields _apply_strategy_filters can actually narrow on, declared as
# data so "does this strategy define a gate at all?" cannot drift from the filter.
WATCH_RULE_TEXT_FIELDS = (
    "focus_categories",
    "focus_regions",
    "exclude_regions",
    "required_keywords",
    "exclude_keywords",
)
WATCH_RULE_BUDGET_FIELDS = ("min_budget_estimate", "max_budget_estimate")


@lru_cache(maxsize=1)
def _watch_rule_service() -> StrategyMonitoringService:
    """Reusable service instance used only for its cheap watch-rule filters."""
    from app.services.opportunity_monitoring import StrategyMonitoringService

    return StrategyMonitoringService()


def has_watch_rules(strategy: OperatorStrategy | None) -> bool:
    """Whether the strategy declares any rule that can narrow the notice set.

    A strategy with every watch field empty passes *every* notice through
    ``matches_strategy_watch_rules``, so it is not a usable gate — callers that
    prioritise by watch scope should treat this as "no gate" rather than "all
    notices match".
    """
    if strategy is None:
        return False
    if any(
        split_multi_value_text(getattr(strategy, field, None))
        for field in WATCH_RULE_TEXT_FIELDS
    ):
        return True
    return any(
        float(getattr(strategy, field, 0.0) or 0.0) > 0
        for field in WATCH_RULE_BUDGET_FIELDS
    )


def matches_strategy_watch_rules(project: Project, strategy: OperatorStrategy) -> bool:
    """Whether ``project`` passes the operator's cheap watch-rule filters.

    Same semantics as the monitor's pre-filter pass (focus categories/regions,
    exclude regions, required/exclude keywords, budget bounds) — no scoring, no
    ML, no DB access.
    """
    return _watch_rule_service()._apply_strategy_filters(project, strategy).matched
