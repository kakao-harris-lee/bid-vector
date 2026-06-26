"""Regression tests for the single-sourced ACTIVE_DECISION_STATUSES constant.

These guard the Tier 0 de-duplication: the open/actionable bid-decision status
set must have exactly one definition that every consumer references, and the
membership/value must stay {"planned", "reviewing"} (behavior-preserving).
"""

from app.api import dashboard
from app.core.constants import ACTIVE_DECISION_STATUSES
from app.services.allocation import BidDecisionService
from app.services.decision_analytics import DecisionAnalyticsService


def test_canonical_value_is_planned_and_reviewing():
    assert ACTIVE_DECISION_STATUSES == frozenset({"planned", "reviewing"})
    assert isinstance(ACTIVE_DECISION_STATUSES, frozenset)


def test_all_consumers_reference_the_single_source():
    # Same identity, not just equal value, proves there is one source of truth.
    assert BidDecisionService.ACTIVE_DECISION_STATUSES is ACTIVE_DECISION_STATUSES
    assert DecisionAnalyticsService.ACTIVE_DECISION_STATUSES is ACTIVE_DECISION_STATUSES
    assert dashboard._ACTIVE_OPPORTUNITY_STATUSES is ACTIVE_DECISION_STATUSES


def test_value_does_not_collide_with_experiment_statuses():
    # decision_experiments uses {"planned", "running"} for a different concept;
    # the de-duplication must not have merged them.
    assert "running" not in ACTIVE_DECISION_STATUSES
