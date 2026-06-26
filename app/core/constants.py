"""Tier 0 shared domain constants.

Single source of truth for small, cross-module domain value sets that were
previously duplicated as literals across services and routers. Keeping the
exact same string values here preserves runtime behavior; this module exists
only to remove the duplication, not to change any semantics.
"""

from __future__ import annotations

# Decision statuses that represent an *open / actionable* bid opportunity for
# the single operator. A record in one of these statuses has been planned or is
# under review but has not yet been submitted or skipped.
#
# This set is shared by:
#   - app/services/allocation.py           (BidDecisionService.ACTIVE_DECISION_STATUSES)
#   - app/services/decision_analytics.py   (DecisionAnalyticsService.ACTIVE_DECISION_STATUSES)
#   - app/services/opportunity_analysis.py (OpportunityAnalysisService.ACTIVE_DECISION_STATUSES)
#   - app/api/dashboard.py                 (_ACTIVE_OPPORTUNITY_STATUSES)
#   - app/api/operator.py                  (active_decision_count .in_() filter)
#
# NOTE: This is intentionally distinct from experiment lifecycle statuses such
# as {"planned", "running"} used in app/services/decision_experiments.py — do
# not merge the two; "running" is not a bid-decision status.
ACTIVE_DECISION_STATUSES: frozenset[str] = frozenset({"planned", "reviewing"})
