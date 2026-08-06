"""Shared foundation for the historical paper-bidding backtest service.

Holds the ``CandidatePredictionContext`` / ``CandidateDecisionContext`` value
objects, the ``OperatorNotFoundError`` sentinel, and ``_PaperBiddingBase`` --
the class constants and ``__init__`` shared by every
``PaperBiddingBacktestService`` mixin. Every member is moved verbatim from the
original single ``paper_bidding_backtest`` module; the split is a pure move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ai.factory import build_price_prediction_port
from app.ai.service_interfaces import PricePredictionPort
from app.domain.money import BaseAmount
from app.services.allocation import BidDecisionService
from app.services.backtest_cutoff import BacktestCutoffService
from app.services.classifier import NoticeClassifierService

# --- 정산 오차 허용폭 (§4.5-1: 함수 안 리터럴 금지) ------------------------------
# 낙찰률 오차(절대값, base 기준 fraction)의 근접/경쟁 판정 임계. settlement.py 의
# ``price_close``/``price_competitive`` 와 summary.py 의 within_* 버킷이 같은 값을
# 따로 적어 두면 한쪽만 바뀌어 갈라지므로 여기가 단일 출처다.
PRICE_CLOSE_RATE_TOLERANCE = 0.003
PRICE_COMPETITIVE_RATE_TOLERANCE = 0.01
TIGHT_RATE_TOLERANCE = 0.001

# 요약의 within_* 카운트 = (필드명, 허용폭) 선언표. 버킷 추가는 이 표에 한 줄이다.
RATE_ERROR_BUCKETS: tuple[tuple[str, float], ...] = (
    ("within_0_1pct_count", TIGHT_RATE_TOLERANCE),
    ("within_0_3pct_count", PRICE_CLOSE_RATE_TOLERANCE),
    ("within_1pct_count", PRICE_COMPETITIVE_RATE_TOLERANCE),
)


@dataclass(frozen=True)
class CandidatePredictionContext:
    budget: float
    """추정가격(ex-VAT) — 리포팅 필드와 전략 예산밴드 필터가 쓰는 금액."""

    bid_base: BaseAmount
    """기초금액/사업금액 — 투찰율이 곱해지고 capture 비율의 분모가 되는 금액.

    ``budget`` 과 나뉘어 있는 이유: 과세 공고에서 둘은 ~10% 다른 금액이라, 백테스트의
    결정 분포가 그것이 측정하려는 라이브 경로와 비교 가능하려면 결정 점수도 라이브가
    쓰는 base 위에서 매겨져야 한다.
    """

    data_cutoff_at: datetime
    history: list[dict[str, Any]]
    business_group: str | None
    prediction: dict[str, Any]


@dataclass(frozen=True)
class CandidateDecisionContext:
    selected_scenario: dict[str, Any]
    paper_bid_amount: float
    paper_bid_rate: float
    matched_score: float
    match_reasons: list[str]
    match_source: str
    probability_score: float
    decision: dict[str, Any]
    action: str


class OperatorNotFoundError(ValueError):
    """Raised when a backtest is requested for an ``operator_id`` that does not exist.

    Per-operator backtests (e.g. synthetic operator comparisons) must never silently
    fall back to the canonical operator, since that would let a non-existent operator
    pollute the canonical operator's strategy/profile/paper-bid data. Callers that accept
    an ``operator_id`` (the API layer) translate this into a ``404``.
    """


class _PaperBiddingBase:
    """Class constants and ``__init__`` shared by every
    ``PaperBiddingBacktestService`` mixin. Members are moved verbatim from the
    original ``PaperBiddingBacktestService`` body."""

    DEFAULT_SETTLE_ACTIONS = ("bid_now",)
    DEFAULT_SCENARIO = "base"

    # Fallback heuristic weights for the backtest P(win) signal, used only when
    # no settlement-calibrated curve is published (see _estimate_win_probability).
    # These weights are specific to the backtest P(win) fallback and are
    # unrelated to the opportunity_analysis probability_score blend weights or
    # the allocation opportunity-score weights — do not merge them. They sum to
    # 1.0.
    WIN_PROBABILITY_MATCHED_WEIGHT = 0.38
    WIN_PROBABILITY_CONFIDENCE_WEIGHT = 0.42
    WIN_PROBABILITY_HISTORY_WEIGHT = 0.20

    # Competitiveness heuristic: a paper-bid rate closest to the target rate is
    # most competitive; the tolerance scales how quickly the score decays away
    # from the target. Specific to _estimate_competitiveness_score.
    COMPETITIVENESS_TARGET_RATE = 0.88
    COMPETITIVENESS_RATE_TOLERANCE = 0.15

    # Budget-tier execution-complexity scores: larger budgets map to higher
    # complexity. Thresholds are in KRW. Specific to
    # _estimate_execution_complexity_score.
    EXECUTION_COMPLEXITY_TIER1_BUDGET = 500_000_000
    EXECUTION_COMPLEXITY_TIER2_BUDGET = 200_000_000
    EXECUTION_COMPLEXITY_TIER3_BUDGET = 100_000_000
    EXECUTION_COMPLEXITY_TIER1_SCORE = 0.82
    EXECUTION_COMPLEXITY_TIER2_SCORE = 0.68
    EXECUTION_COMPLEXITY_TIER3_SCORE = 0.52
    EXECUTION_COMPLEXITY_DEFAULT_SCORE = 0.36

    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
    ) -> None:
        self.cutoff_service = BacktestCutoffService()
        self.decision_service = BidDecisionService()
        self.classifier = NoticeClassifierService()
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
