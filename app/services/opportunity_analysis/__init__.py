"""Integrated opportunity analysis for award-oriented bidding decisions.

``OpportunityAnalysisService`` keeps its historical import path
(``from app.services.opportunity_analysis import ...``). The service body was
decomposed into responsibility mixins (orchestration / prediction_wiring /
scoring / workload / market / flags) over a shared ``_OpportunityAnalysisBase``;
this ``__init__`` composes them. The split is a pure move — every method body is
the original ``OpportunityAnalysisService`` member relocated verbatim, so the
honesty-spec caps, the #260 predict wiring, the published-floor path, and the
recommended_amount resolution are unchanged.

The module-level risk helpers, score tables, and value carriers keep their
historical import path via the re-exports below (see ``__all__``).
"""

from __future__ import annotations

from app.services.opportunity_analysis.base import (
    _AnalysisInputs,
    _AnalysisScores,
    _OpportunityAnalysisBase,
)
from app.services.opportunity_analysis.flags import _FlagsMixin
from app.services.opportunity_analysis.market import _MarketInputsMixin
from app.services.opportunity_analysis.orchestration import _OrchestrationMixin
from app.services.opportunity_analysis.prediction_wiring import _PredictionWiringMixin
from app.services.opportunity_analysis.risk_signals import (
    _CONSTRUCTION_RISK_PATTERNS,
    _NEGATION_NEAR_MATCH,
    _REGION_BONUS_RISK_MESSAGE,
    _detect_awarded_contract_limit_risks,
    _detect_construction_risk_reasons,
    _is_construction_project,
)
from app.services.opportunity_analysis.score_tables import (
    _BUDGET_COMPLEXITY_BANDS,
    _DEADLINE_COMPLEXITY_BANDS,
    _DEADLINE_MISSING_COMPLEXITY_SIGNAL,
    _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS,
    _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
    _WORKLOAD_COMPOSITE_WEIGHTS,
)
from app.services.opportunity_analysis.scoring import _ScoringMixin
from app.services.opportunity_analysis.workload import (
    OpportunityWorkloadContext,
    _WorkloadMixin,
)

__all__ = [
    "OpportunityAnalysisService",
    "OpportunityWorkloadContext",
    # Value carriers / base
    "_AnalysisInputs",
    "_AnalysisScores",
    "_OpportunityAnalysisBase",
    # Risk signals (imported by tests + _build_risk_flags)
    "_CONSTRUCTION_RISK_PATTERNS",
    "_NEGATION_NEAR_MATCH",
    "_REGION_BONUS_RISK_MESSAGE",
    "_detect_awarded_contract_limit_risks",
    "_detect_construction_risk_reasons",
    "_is_construction_project",
    # Score tables (imported by tests/test_opportunity_weight_tables.py)
    "_BUDGET_COMPLEXITY_BANDS",
    "_DEADLINE_COMPLEXITY_BANDS",
    "_DEADLINE_MISSING_COMPLEXITY_SIGNAL",
    "_EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS",
    "_EXPECTED_MARGIN_COMPOSITE_WEIGHTS",
    "_WORKLOAD_COMPOSITE_WEIGHTS",
]


class OpportunityAnalysisService(
    _OrchestrationMixin,
    _PredictionWiringMixin,
    _ScoringMixin,
    _WorkloadMixin,
    _MarketInputsMixin,
    _FlagsMixin,
):
    """Combine fit, price, market, similarity, and action guidance into one analysis."""
