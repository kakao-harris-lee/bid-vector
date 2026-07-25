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

# The base *open* Project status ONLY. This is deliberately NARROWER than
# ACTIVE_PROJECT_STATUSES below — it EXCLUDES "re_notice".
#
# Several read-only reporting / backtest scripts scan only freshly-open notices
# with ``Project.status == OPEN_PROJECT_STATUS``:
#   - scripts/report_license_eligibility.py
#   - scripts/report_license_gate_impact.py
#   - scripts/report_no_candidate_cause.py
#   - scripts/report_eligibility_segment_backtest.py
#   - scripts/measure_reliable_base_impact.py   (Python-level bucketing)
#   - scripts/backfill_award_floor_rate.py
#
# Kept SEPARATE from the wider set on purpose: the scripts and the services
# filter on DIFFERENT row sets. This is the single source for the singular
# literal; it does NOT unify the two scopes (see ACTIVE_PROJECT_STATUSES note).
OPEN_PROJECT_STATUS: str = "open"

# Project statuses that represent a currently *open / biddable* procurement
# notice: the base open notice PLUS a re-noticed one.
#
# This set is shared by:
#   - app/services/query_predicates.open_projects()
#     (consumed by backtest_data_audit.py + paper_bidding_backtest.py)
#   - app/services/opportunity_monitoring.StrategyMonitoringService
#     (ACTIVE_PROJECT_STATUSES class attribute)
#
# NOTE: This is WIDER than OPEN_PROJECT_STATUS above (it adds "re_notice") and is
# a different concept from ACTIVE_DECISION_STATUSES (which tracks the
# bid-decision lifecycle, not the procurement-notice status). Do not conflate the
# three.
ACTIVE_PROJECT_STATUSES: frozenset[str] = frozenset({"open", "re_notice"})

# Telegram delivery telemetry event types written to the ``analytics`` table.
#
# ``TELEGRAM_DELIVERY_EVENT_TYPE`` records every delivery attempt that reached
# the send boundary (sent / blocked / dry-run). ``TELEGRAM_DELIVERY_SUPPRESSED_
# EVENT_TYPE`` is deliberately a SEPARATE type: a fatigue suppression is not a
# failed delivery, so it must not enter the delivery success-rate denominator in
# app/services/analytics_reporting.py.
TELEGRAM_DELIVERY_EVENT_TYPE: str = "telegram.delivery"
TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE: str = "telegram.delivery.suppressed"

# Internal telemetry event types that operator-facing event *counts* exclude.
#
# This set is shared by:
#   - app/api/analytics.py  (INTERNAL_TELEMETRY_EVENT_TYPES)
#   - app/api/operator.py   (INTERNAL_OPERATOR_EVENT_TYPES)
#
# Both used to declare the same literal set independently; new internal event
# types are added here so neither counter silently starts reporting telemetry as
# operator activity.
INTERNAL_TELEMETRY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        TELEGRAM_DELIVERY_EVENT_TYPE,
        TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
        "telegram.strategy.pending_edit",
    }
)
