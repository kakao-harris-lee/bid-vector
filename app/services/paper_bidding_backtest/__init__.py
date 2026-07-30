"""Historical paper-bidding backtest service -- public surface.

``PaperBiddingBacktestService`` and ``OperatorNotFoundError`` keep their
historical import path (``from app.services.paper_bidding_backtest import ...``).
The service body was decomposed into responsibility mixins (base / orchestration
/ forward_run / candidates / settlement / forward_settlement / persistence /
summary / scoring / operator); this ``__init__`` composes them. The split is a
pure move -- every method body is the original ``PaperBiddingBacktestService``
member, relocated verbatim, so the win definition, eligibility gate, and price
math are unchanged.
"""

from __future__ import annotations

from app.services.paper_bidding_backtest.base import (
    CandidateDecisionContext,
    CandidatePredictionContext,
    OperatorNotFoundError,
)
from app.services.paper_bidding_backtest.candidates import _CandidateMixin
from app.services.paper_bidding_backtest.forward_run import _ForwardRunMixin
from app.services.paper_bidding_backtest.forward_settlement import (
    _ForwardSettlementMixin,
)
from app.services.paper_bidding_backtest.operator import _OperatorResolutionMixin
from app.services.paper_bidding_backtest.orchestration import _BacktestRunMixin
from app.services.paper_bidding_backtest.persistence import _PersistenceMixin
from app.services.paper_bidding_backtest.scoring import _ScoringMixin
from app.services.paper_bidding_backtest.settlement import _SettlementMixin
from app.services.paper_bidding_backtest.summary import _SummaryMixin

__all__ = [
    "CandidateDecisionContext",
    "CandidatePredictionContext",
    "OperatorNotFoundError",
    "PaperBiddingBacktestService",
]


class PaperBiddingBacktestService(
    _BacktestRunMixin,
    _ForwardRunMixin,
    _CandidateMixin,
    _SettlementMixin,
    _ForwardSettlementMixin,
    _PersistenceMixin,
    _SummaryMixin,
    _ScoringMixin,
    _OperatorResolutionMixin,
):
    """Replay historical awards as paper-bidding opportunities."""
