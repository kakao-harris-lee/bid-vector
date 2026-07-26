"""Domain logic for the mobile dashboard API (summary + list serialization).

Routers in :mod:`app.api.dashboard` stay thin and delegate the data collection,
serialization, and payload assembly to the helpers below. Behavior here mirrors
the previous inline router logic exactly; this module only relocates it.

This package was decomposed from a single module; the import surface
(``from app.services.dashboard_summary import ...``) is preserved verbatim via
the re-exports below.
"""

from __future__ import annotations

from .collectors import (
    _summarize_active_bids,
    _summarize_active_opportunity_counts,
    _summarize_due_opportunities,
    _summarize_operational_metrics,
    _summarize_recent_opportunities,
    _summarize_recent_results,
)
from .constants import (
    _ACTIVE_OPPORTUNITY_STATUSES,
    _BID_STATUSES,
    _DEFAULT_PAPER_OPPORTUNITY_ACTIONS,
    _OPPORTUNITY_STATUSES,
    _PAPER_ACTION_STATUS,
    _TERMINAL_RESULT_STATUSES,
)
from .lookups import (
    _load_latest_bid_map,
    _load_latest_decision_map,
    _load_latest_prediction_map,
    _load_latest_result_map,
)
from .metric_cards import (
    _serialize_operational_status,
    _serialize_paper_backtest_metric,
)
from .normalizers import (
    _compute_delta,
    _compute_error_rate,
    _hours_until,
    _normalize_action,
    _normalize_bid_status,
    _normalize_opportunity_status,
    _paper_status_from_action,
    _project_brief,
    _round_optional,
)
from .payload import (
    build_dashboard_summary,
    build_metric_cards,
    build_sections,
    build_work_items,
)
from .queries import (
    _build_opportunity_items,
    _count_paper_opportunities,
    _latest_paper_run,
    _load_latest_result_rows,
    _load_opportunity_records,
    _load_paper_opportunities,
    _paper_actions_for_statuses,
    _paper_opportunity_query,
)
from .serializers import (
    _resolve_award_outcome,
    _serialize_bid,
    _serialize_opportunity,
    _serialize_paper_opportunity,
    _serialize_result,
)

__all__ = [
    "_ACTIVE_OPPORTUNITY_STATUSES",
    "_BID_STATUSES",
    "_DEFAULT_PAPER_OPPORTUNITY_ACTIONS",
    "_OPPORTUNITY_STATUSES",
    "_PAPER_ACTION_STATUS",
    "_TERMINAL_RESULT_STATUSES",
    "_build_opportunity_items",
    "_compute_delta",
    "_compute_error_rate",
    "_count_paper_opportunities",
    "_hours_until",
    "_latest_paper_run",
    "_load_latest_bid_map",
    "_load_latest_decision_map",
    "_load_latest_prediction_map",
    "_load_latest_result_map",
    "_load_latest_result_rows",
    "_load_opportunity_records",
    "_load_paper_opportunities",
    "_normalize_action",
    "_normalize_bid_status",
    "_normalize_opportunity_status",
    "_paper_actions_for_statuses",
    "_paper_opportunity_query",
    "_paper_status_from_action",
    "_project_brief",
    "_resolve_award_outcome",
    "_round_optional",
    "_serialize_bid",
    "_serialize_operational_status",
    "_serialize_opportunity",
    "_serialize_paper_backtest_metric",
    "_serialize_paper_opportunity",
    "_serialize_result",
    "_summarize_active_bids",
    "_summarize_active_opportunity_counts",
    "_summarize_due_opportunities",
    "_summarize_operational_metrics",
    "_summarize_recent_opportunities",
    "_summarize_recent_results",
    "build_dashboard_summary",
    "build_metric_cards",
    "build_sections",
    "build_work_items",
]
