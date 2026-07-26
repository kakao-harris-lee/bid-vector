"""Derived-score computation for opportunity analysis.

Owns the three composite scores (competitiveness/margin/complexity), the pursuit
``probability_score`` blend + its calibration override chain, and the shared
capacity normalizer. Methods are moved verbatim from the original
``OpportunityAnalysisService`` body; the honesty-spec 0.49 non-matched cap, the
guardrail-fed floor headroom, and the weighted-sum order are unchanged.

``apply_probability_calibration`` is imported here (not via the package
``__init__``) so ``_apply_calibrated_or_heuristic_probability`` resolves it in
this module's namespace — tests patch it at
``app.services.opportunity_analysis.scoring``.
"""

from __future__ import annotations

import operator

from app.ai.bid_recommendation import calculate_competitiveness_score
from app.ai.predictors.historical import apply_probability_calibration
from app.core.bands import resolve_band
from app.models.models import Project
from app.services.opportunity_analysis.base import _AnalysisScores, _OpportunityAnalysisBase
from app.services.opportunity_analysis.score_tables import (
    _BUDGET_COMPLEXITY_BANDS,
    _DEADLINE_COMPLEXITY_BANDS,
    _DEADLINE_MISSING_COMPLEXITY_SIGNAL,
    _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS,
    _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
)


class _ScoringMixin(_OpportunityAnalysisBase):
    """Composite-score and probability-blend computations."""

    def _compute_scores(
        self,
        *,
        project: Project,
        classification: dict,
        price_prediction: dict,
        recommended_amount: float,
        market_insights: dict,
        capacity_score: float,
        deadline_hours_remaining: int | None,
        current_active_bids: int,
        max_active_bids: int,
    ) -> _AnalysisScores:
        """Compute the competitiveness, expected-margin, and execution-complexity scores.

        Pure extraction of the three derived score computations from
        ``analyze_project``. Competitiveness is computed first and writes its
        rounded value back into ``market_insights`` exactly as the inline code
        did (so the market block in the response is unchanged), then margin and
        complexity reuse the existing ``_estimate_*`` helpers with identical
        arguments. No values are altered here.
        """
        competitiveness_score = calculate_competitiveness_score(
            recommended_amount,
            project_data={
                "budget": float(project.budget_estimate or 0.0),
                "category": project.category or "other",
            },
            market_data=market_insights,
        )
        market_insights["competitiveness_score"] = round(float(competitiveness_score), 4)

        expected_margin_score = self._estimate_expected_margin_score(
            project=project,
            recommended_amount=recommended_amount,
            price_prediction=price_prediction,
            competitiveness_score=float(competitiveness_score),
            capacity_score=capacity_score,
        )
        execution_complexity_score = self._estimate_execution_complexity_score(
            project=project,
            classification=classification,
            deadline_hours_remaining=deadline_hours_remaining,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
            capacity_score=capacity_score,
        )
        return _AnalysisScores(
            competitiveness_score=competitiveness_score,
            expected_margin_score=expected_margin_score,
            execution_complexity_score=execution_complexity_score,
        )

    def _resolve_final_probability_score(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        bid_recommendation: dict,
        similar_projects: dict,
        competitiveness_score: float,
        capacity_score: float,
        current_active_bids: int,
        max_active_bids: int,
        business_group: str | None,
        category_priority_override: float,
    ) -> float:
        """Resolve the final pursuit ``probability_score`` through its full override chain.

        Pure extraction of the probability_score reassignment chain from
        ``analyze_project``. The reassignment order, operations, and honesty-spec
        gates are byte-identical to the inline code:

          1. heuristic blend via ``_estimate_probability_score``
          2. calibrated-or-heuristic override via
             ``_apply_calibrated_or_heuristic_probability`` (preserves the 0.49
             non-matched cap internally)
          3. category-priority offset via ``_apply_category_priority_override``
          4. non-matched 0.49 cap re-applied last

        The ``matched_score`` override (which uses ``override * 0.5`` and is
        independent of this chain) stays in ``analyze_project``.
        """
        probability_score = self._estimate_probability_score(
            classification=classification,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            capacity_score=capacity_score,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
        )
        # When a settlement-calibrated curve is published, replace the heuristic
        # P(낙찰) with the calibrated value; otherwise keep the heuristic. The
        # non-matched 0.49 gate is preserved either way. (Offline / fresh
        # environments fall back to the heuristic unchanged.)
        probability_score = self._apply_calibrated_or_heuristic_probability(
            heuristic_probability=probability_score,
            classification=classification,
            price_prediction=price_prediction,
            business_group=business_group,
        )
        probability_score = self._apply_category_priority_override(
            probability_score,
            category_priority_override,
        )
        if not classification.get("matched", False):
            probability_score = min(probability_score, self.NON_MATCHED_PROBABILITY_CAP)
        return probability_score

    def _estimate_probability_score(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        bid_recommendation: dict,
        similar_projects: dict,
        competitiveness_score: float,
        capacity_score: float,
        current_active_bids: int,
        max_active_bids: int,
    ) -> float:
        """Blend the main analysis signals into one pursuit probability score."""
        similarity_scores = [float(item.get("similarity_score", 0.0) or 0.0) for item in similar_projects.get("results", [])]
        similarity_signal = (
            sum(similarity_scores) / len(similarity_scores)
            if similarity_scores
            else self.DEFAULT_SIMILARITY_SCORE
        )
        normalized_capacity = self._normalize_capacity_score(capacity_score)

        probability_score = (
            float(classification.get("score", 0.0)) * self.PROBABILITY_BLEND_CLASSIFICATION_WEIGHT
            + float(bid_recommendation.get("confidence_score", 0.0)) * self.PROBABILITY_BLEND_RECOMMENDATION_WEIGHT
            + float(price_prediction.get("confidence_score", 0.0)) * self.PROBABILITY_BLEND_PRICE_WEIGHT
            + float(competitiveness_score) * self.PROBABILITY_BLEND_COMPETITIVENESS_WEIGHT
            + similarity_signal * self.PROBABILITY_BLEND_SIMILARITY_WEIGHT
            + normalized_capacity * self.PROBABILITY_BLEND_CAPACITY_WEIGHT
        )

        if not classification.get("matched", False):
            probability_score = min(probability_score, self.NON_MATCHED_PROBABILITY_CAP)

        if current_active_bids >= max_active_bids:
            probability_score -= 0.05

        return round(max(0.0, min(1.0, probability_score)), 2)

    def _apply_calibrated_or_heuristic_probability(
        self,
        *,
        heuristic_probability: float,
        classification: dict,
        price_prediction: dict,
        business_group: str | None,
    ) -> float:
        """Override the heuristic P(낙찰) with the calibrated curve when published.

        Uses the SAME inference-time signals (confidence/matched) the backtest path
        feeds the curve. When no calibration artifact exists, the heuristic is kept
        unchanged. The non-matched 0.49 gate is enforced here so calibration can
        never let an unmatched notice present as a high-pursuit opportunity.
        """
        probability = float(heuristic_probability)
        calibrated = apply_probability_calibration(
            {
                "confidence_score": float(
                    price_prediction.get("confidence_score", 0.0) or 0.0
                ),
                "matched_score": float(classification.get("score", 0.0) or 0.0),
                "business_group": business_group,
            }
        )
        if calibrated is not None:
            probability = calibrated
        if not classification.get("matched", False):
            probability = min(probability, self.NON_MATCHED_PROBABILITY_CAP)
        return round(max(0.0, min(1.0, probability)), 2)

    def _apply_category_priority_override(self, score: float, override: float) -> float:
        """Apply a bounded category priority offset to an analysis score."""
        return round(max(0.0, min(1.0, float(score) + float(override))), 2)

    def _estimate_expected_margin_score(
        self,
        *,
        project: Project,
        recommended_amount: float,
        price_prediction: dict,
        competitiveness_score: float,
        capacity_score: float,
    ) -> float:
        """Estimate a profitability proxy from budget retention, floor headroom, and execution confidence."""
        budget_estimate = float(project.budget_estimate or 0.0)
        if budget_estimate <= 0:
            return 0.5

        recommended_rate = max(0.0, min(1.0, float(recommended_amount or 0.0) / budget_estimate))
        floor_bid_rate = max(0.0, min(1.0, float(price_prediction.get("floor_bid_rate", 0.0) or 0.0)))
        predicted_bid_rate = max(
            0.0,
            min(1.0, float(price_prediction.get("predicted_bid_rate", recommended_rate) or recommended_rate)),
        )
        price_confidence = max(0.0, min(1.0, float(price_prediction.get("confidence_score", 0.0) or 0.0)))
        normalized_capacity = self._normalize_capacity_score(capacity_score)

        if floor_bid_rate > 0:
            floor_headroom = max(0.0, min(1.0, (recommended_rate - floor_bid_rate) / max(1e-6, 1.0 - floor_bid_rate)))
        else:
            floor_headroom = recommended_rate

        prediction_alignment = max(0.0, 1.0 - min(abs(recommended_rate - predicted_bid_rate) / 0.12, 1.0))

        weights = _EXPECTED_MARGIN_COMPOSITE_WEIGHTS
        expected_margin_score = (
            recommended_rate * weights["recommended_rate"]
            + floor_headroom * weights["floor_headroom"]
            + prediction_alignment * weights["prediction_alignment"]
            + price_confidence * weights["price_confidence"]
            + normalized_capacity * weights["normalized_capacity"]
        )
        return round(max(0.0, min(1.0, expected_margin_score)), 2)

    def _estimate_execution_complexity_score(
        self,
        *,
        project: Project,
        classification: dict,
        deadline_hours_remaining: int | None,
        current_active_bids: int,
        max_active_bids: int,
        capacity_score: float,
    ) -> float:
        """Estimate delivery complexity from project scale, wording, schedule pressure, and current capacity."""
        project_budget = max(float(project.budget_estimate or 0.0), float(project.budget_max or 0.0))
        budget_signal = resolve_band(project_budget, _BUDGET_COMPLEXITY_BANDS)

        project_text = " ".join(part for part in [project.title or "", project.description or "", project.requirements or ""] if part).lower()
        keyword_hits = sum(1 for keyword in self.EXECUTION_COMPLEXITY_KEYWORDS if keyword in project_text)
        keyword_signal = min(1.0, 0.24 + (keyword_hits * 0.08))

        deadline_signal = (
            _DEADLINE_MISSING_COMPLEXITY_SIGNAL
            if deadline_hours_remaining is None
            else resolve_band(
                deadline_hours_remaining,
                _DEADLINE_COMPLEXITY_BANDS,
                compare=operator.le,
            )
        )

        active_load_ratio = min(1.0, current_active_bids / max(1, max_active_bids))
        match_friction = max(0.0, min(1.0, 1.0 - float(classification.get("score", 0.0) or 0.0)))
        capacity_friction = max(0.0, min(1.0, 1.0 - self._normalize_capacity_score(capacity_score)))

        weights = _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS
        complexity_score = (
            budget_signal * weights["budget_signal"]
            + keyword_signal * weights["keyword_signal"]
            + deadline_signal * weights["deadline_signal"]
            + active_load_ratio * weights["active_load_ratio"]
            + match_friction * weights["match_friction"]
            + capacity_friction * weights["capacity_friction"]
        )
        return round(max(0.0, min(1.0, complexity_score)), 2)

    def _normalize_capacity_score(self, value: float | None) -> float:
        """Normalize capacity scores that may arrive on either 0-1 or 0-100 scales."""
        if value is None:
            return 0.0

        normalized = float(value)
        if normalized > 1:
            normalized /= 100.0
        return max(0.0, min(1.0, normalized))
