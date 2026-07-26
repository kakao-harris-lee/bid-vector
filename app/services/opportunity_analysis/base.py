"""Shared foundation for the integrated opportunity-analysis service.

Holds the ``_AnalysisScores`` / ``_AnalysisInputs`` value carriers and
``_OpportunityAnalysisBase`` -- the class constants and ``__init__`` shared by
every ``OpportunityAnalysisService`` mixin. Every member is moved verbatim from
the original single ``opportunity_analysis`` module; the split is a pure move,
so the honesty-spec caps, probability blend weights, and collaborator wiring are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.factory import build_bid_recommendation_port, build_price_prediction_port
from app.ai.service_interfaces import BidRecommendationPort, PricePredictionPort
from app.core.constants import ACTIVE_DECISION_STATUSES as _ACTIVE_DECISION_STATUSES
from app.models.models import Project
from app.services.allocation import BidDecisionService
from app.services.classifier import NoticeClassifierService
from app.services.prediction_dataset import PredictionDatasetService
from app.services.prediction_feedback import PredictionFeedbackService
from app.services.project_similarity import ProjectSimilarityService


@dataclass(frozen=True)
class _AnalysisScores:
    """Bundle of the per-analysis derived scores produced by ``_compute_scores``.

    A plain value carrier so ``analyze_project`` can keep its existing local
    variable names. Holds the exact float values the inline code produced — no
    additional rounding or transformation happens in this container.
    """

    competitiveness_score: float
    expected_margin_score: float
    execution_complexity_score: float


@dataclass(frozen=True)
class _AnalysisInputs:
    classification: dict
    similar_projects: list[Project]
    market_insights: dict
    user_historical_data: dict


class _OpportunityAnalysisBase:
    """Class constants and ``__init__`` shared by every
    ``OpportunityAnalysisService`` mixin. Members are moved verbatim from the
    original ``OpportunityAnalysisService`` body."""

    ACTIVE_DECISION_STATUSES = _ACTIVE_DECISION_STATUSES
    DEFAULT_SIMILARITY_SCORE = 0.35
    # Upper bound on probability_score for notices that did NOT match the
    # operator profile. This is the honesty-spec non-matched gate / invariant:
    # an unmatched notice can never present as a high-pursuit opportunity,
    # regardless of heuristic or calibrated inputs. Value is load-bearing — do
    # not change it; this only names the existing 0.49 literal.
    NON_MATCHED_PROBABILITY_CAP = 0.49
    # Weights that blend the main analysis signals into the pursuit
    # probability_score (see _estimate_probability_score). These weights are
    # specific to THIS module's probability_score composition and are unrelated
    # to the BidDecisionService opportunity-score weights in allocation.py or
    # the paper-bidding P(win) fallback weights — do not merge them. The six
    # weights sum to 1.0.
    PROBABILITY_BLEND_CLASSIFICATION_WEIGHT = 0.34
    PROBABILITY_BLEND_RECOMMENDATION_WEIGHT = 0.22
    PROBABILITY_BLEND_PRICE_WEIGHT = 0.14
    PROBABILITY_BLEND_COMPETITIVENESS_WEIGHT = 0.18
    PROBABILITY_BLEND_SIMILARITY_WEIGHT = 0.07
    PROBABILITY_BLEND_CAPACITY_WEIGHT = 0.05
    EXECUTION_COMPLEXITY_KEYWORDS = (
        "통합",
        "고도화",
        "운영",
        "유지관리",
        "24시간",
        "대규모",
        "다기관",
        "클라우드",
        "센터",
        "실시간",
        "연계",
        "보안",
        "이관",
        "플랫폼",
    )

    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
        bid_recommendation_port: BidRecommendationPort | None = None,
    ) -> None:
        self.classifier = NoticeClassifierService()
        self.dataset_service = PredictionDatasetService()
        self.decision_service = BidDecisionService()
        self.feedback_service = PredictionFeedbackService()
        self.similarity_service = ProjectSimilarityService()
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
        self.bid_recommendation_port = bid_recommendation_port or build_bid_recommendation_port()
