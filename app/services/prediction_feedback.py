"""Feedback analytics that compare predictions and bid decisions to actual tender results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, HistoricalData, PricePrediction, TenderResult, User


@dataclass
class _RecentWindow:
    """Memoized recent-window snapshot shared by candidates within one run.

    All collections are loaded once for the unfiltered window (operator + days).
    Category/agency filters are applied in memory on these snapshots so the
    underlying SQL is issued at most once per ``(operator_id, days)`` per run.
    """

    tender_results: list[TenderResult]
    latest_predictions: dict[int, PricePrediction]
    latest_histories: dict[int, HistoricalData]


class PredictionFeedbackService:
    """Build operator-facing accuracy summaries from linked tender results.

    Instance lifetime is assumed to equal a single monitor/analysis run:
    ``OpportunityAnalysisService`` owns one ``feedback_service`` per run and
    ``StrategyMonitoringService`` constructs a fresh ``OpportunityAnalysisService``
    each run. The recent-window memoization below is therefore scoped to that run
    -- it never spans runs and cannot go stale across runs. API handlers that
    require fresh reads simply construct a new instance per request.
    """

    MAX_ADJUSTMENT_RATE = 0.08

    def __init__(self) -> None:
        # Per-run cache of the unfiltered recent window keyed by
        # ``(resolved_operator_id, days)``. The category/agency filters are
        # applied in memory on the cached set so repeated candidates in one
        # monitor run never re-load the full TenderResult window (~26k rows).
        self._recent_window_cache: dict[tuple[int, int], "_RecentWindow"] = {}

    def build_feedback(
        self,
        db: Session,
        *,
        days: int = 90,
        limit: int = 20,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Compare the latest stored prediction/decision amounts against actual winning amounts."""
        if operator is None:
            operator = ensure_operator_account(db)

        window = self._get_recent_window(db, operator_id=operator.id, days=days)
        tender_results = window.tender_results
        project_ids = [int(result.project_id) for result in tender_results if result.project_id is not None]
        latest_predictions = window.latest_predictions
        latest_decisions = self._load_latest_decisions(db, operator_id=operator.id, project_ids=project_ids)

        items: list[dict[str, Any]] = []
        for tender_result in tender_results:
            if tender_result.project_id is None or tender_result.project is None:
                continue

            prediction = latest_predictions.get(int(tender_result.project_id))
            decision = latest_decisions.get(int(tender_result.project_id))
            if prediction is None and decision is None:
                continue

            items.append(self._serialize_feedback_item(tender_result, prediction=prediction, decision=decision))
            if len(items) >= limit:
                break

        prediction_error_rates = [item["prediction_error_rate"] for item in items if item["prediction_error_rate"] is not None]
        recommendation_error_rates = [item["recommendation_error_rate"] for item in items if item["recommendation_error_rate"] is not None]

        return {
            "operator_id": operator.id,
            "period_days": days,
            "result_count": len(items),
            "prediction_sample_count": len(prediction_error_rates),
            "recommendation_sample_count": len(recommendation_error_rates),
            "average_prediction_error_rate": self._average(prediction_error_rates),
            "average_recommendation_error_rate": self._average(recommendation_error_rates),
            "prediction_within_1_percent_count": sum(1 for value in prediction_error_rates if value <= 0.01),
            "prediction_within_3_percent_count": sum(1 for value in prediction_error_rates if value <= 0.03),
            "recommendation_within_1_percent_count": sum(1 for value in recommendation_error_rates if value <= 0.01),
            "recommendation_within_3_percent_count": sum(1 for value in recommendation_error_rates if value <= 0.03),
            "recommendation_better_than_prediction_count": sum(
                1
                for item in items
                if item.get("recommendation_improved_vs_prediction") is True
            ),
            "items": items,
        }

    def build_calibration_context(
        self,
        db: Session,
        *,
        operator_id: int | None = None,
        category: str | None = None,
        agency_name: str | None = None,
        days: int = 365,
        limit: int = 30,
    ) -> dict[str, Any] | None:
        """Build a signed error bias that can calibrate future price predictions."""
        resolved_operator_id = operator_id or ensure_operator_account(db).id

        window = self._get_recent_window(db, operator_id=resolved_operator_id, days=days)
        tender_results = window.tender_results
        if category:
            normalized_category = (category or "").strip().lower()
            tender_results = [
                result
                for result in tender_results
                if result.project is not None and (result.project.category or "").strip().lower() == normalized_category
            ]

        latest_predictions = window.latest_predictions
        latest_histories = window.latest_histories
        normalized_target_agency = self._normalize_text(agency_name)

        signed_error_rates: list[float] = []
        absolute_error_rates: list[float] = []
        weights: list[float] = []
        agency_match_sample_count = 0

        for tender_result in tender_results:
            if tender_result.project_id is None:
                continue
            prediction = latest_predictions.get(int(tender_result.project_id))
            if prediction is None or float(tender_result.winning_amount or 0.0) <= 0:
                continue

            winning_amount = float(tender_result.winning_amount or 0.0)
            signed_error_rate = (float(prediction.predicted_price or 0.0) - winning_amount) / winning_amount
            absolute_error_rate = abs(signed_error_rate)
            weight, matched_agency = self._resolve_agency_weight(
                latest_histories.get(int(tender_result.project_id)),
                normalized_target_agency,
            )
            if matched_agency:
                agency_match_sample_count += 1

            signed_error_rates.append(signed_error_rate)
            absolute_error_rates.append(absolute_error_rate)
            weights.append(weight)

            if len(signed_error_rates) >= limit:
                break

        if not signed_error_rates:
            return None

        weighted_signed_error = float(np.average(signed_error_rates, weights=weights))
        weighted_absolute_error = float(np.average(absolute_error_rates, weights=weights))
        confidence_factor = min(len(signed_error_rates) / 8, 1.0)
        applied_adjustment_rate = -weighted_signed_error * (0.55 + (0.45 * confidence_factor))
        if agency_match_sample_count > 0:
            applied_adjustment_rate *= 1.05
        applied_adjustment_rate = max(-self.MAX_ADJUSTMENT_RATE, min(self.MAX_ADJUSTMENT_RATE, applied_adjustment_rate))

        return {
            "sample_count": len(signed_error_rates),
            "agency_match_sample_count": agency_match_sample_count,
            "average_signed_error_rate": round(weighted_signed_error, 4),
            "average_absolute_error_rate": round(weighted_absolute_error, 4),
            "applied_adjustment_rate": round(applied_adjustment_rate, 4),
        }

    def _get_recent_window(self, db: Session, *, operator_id: int, days: int) -> _RecentWindow:
        """Load (or reuse) the unfiltered recent window for ``(operator_id, days)``.

        The first call within a run issues the bounded query set; subsequent
        candidates in the same run reuse the cached snapshot and only re-apply
        category/agency filters in memory. Cache scope is the instance (= one
        run), so it never spans runs and cannot go stale across runs.
        """
        cache_key = (int(operator_id), int(days))
        cached = self._recent_window_cache.get(cache_key)
        if cached is not None:
            return cached

        date_from = utc_now() - timedelta(days=days)
        tender_results = self._load_recent_tender_results(db, date_from=date_from)
        project_ids = [int(result.project_id) for result in tender_results if result.project_id is not None]
        window = _RecentWindow(
            tender_results=tender_results,
            latest_predictions=self._load_latest_predictions(db, operator_id=operator_id, project_ids=project_ids),
            latest_histories=self._load_latest_histories(db, project_ids=project_ids),
        )
        self._recent_window_cache[cache_key] = window
        return window

    def _load_recent_tender_results(self, db: Session, *, date_from: datetime) -> list[TenderResult]:
        """Return recent linked tender results ordered newest first.

        ``TenderResult.project`` is eager-loaded so downstream category access
        (``result.project.category``) does not trigger a per-row lazy SELECT
        (the previous N+1 over the full window).
        """
        results = (
            db.query(TenderResult)
            .options(joinedload(TenderResult.project))
            .filter(
                TenderResult.project_id.isnot(None),
                or_(
                    TenderResult.announced_at >= date_from,
                    and_(TenderResult.announced_at.is_(None), TenderResult.created_at >= date_from),
                ),
            )
            .all()
        )
        return sorted(results, key=lambda result: result.announced_at or result.created_at, reverse=True)

    def _load_latest_predictions(
        self,
        db: Session,
        *,
        operator_id: int,
        project_ids: list[int],
    ) -> dict[int, PricePrediction]:
        """Return the latest prediction row for each project."""
        if not project_ids:
            return {}

        predictions = (
            db.query(PricePrediction)
            .filter(
                PricePrediction.user_id == operator_id,
                PricePrediction.project_id.in_(project_ids),
            )
            .order_by(PricePrediction.project_id.asc(), PricePrediction.created_at.desc(), PricePrediction.id.desc())
            .all()
        )
        latest_by_project: dict[int, PricePrediction] = {}
        for prediction in predictions:
            latest_by_project.setdefault(int(prediction.project_id), prediction)
        return latest_by_project

    def _load_latest_histories(
        self,
        db: Session,
        *,
        project_ids: list[int],
    ) -> dict[int, HistoricalData]:
        """Return the latest linked historical row for each project when available."""
        if not project_ids:
            return {}

        histories = (
            db.query(HistoricalData)
            .filter(
                HistoricalData.project_id.isnot(None),
                HistoricalData.project_id.in_(project_ids),
            )
            .order_by(HistoricalData.project_id.asc(), HistoricalData.opened_at.desc(), HistoricalData.created_at.desc(), HistoricalData.id.desc())
            .all()
        )
        latest_by_project: dict[int, HistoricalData] = {}
        for history in histories:
            latest_by_project.setdefault(int(history.project_id), history)
        return latest_by_project

    def _load_latest_decisions(
        self,
        db: Session,
        *,
        operator_id: int,
        project_ids: list[int],
    ) -> dict[int, BidDecisionRecord]:
        """Return the latest bid-decision row for each project."""
        if not project_ids:
            return {}

        decisions = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.operator_id == operator_id,
                BidDecisionRecord.project_id.in_(project_ids),
            )
            .order_by(BidDecisionRecord.project_id.asc(), BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
            .all()
        )
        latest_by_project: dict[int, BidDecisionRecord] = {}
        for decision in decisions:
            latest_by_project.setdefault(int(decision.project_id), decision)
        return latest_by_project

    def _serialize_feedback_item(
        self,
        tender_result: TenderResult,
        *,
        prediction: PricePrediction | None,
        decision: BidDecisionRecord | None,
    ) -> dict[str, Any]:
        """Convert linked prediction/decision/result rows into one dashboard-friendly item."""
        winning_amount = float(tender_result.winning_amount or 0.0)
        predicted_price = float(prediction.predicted_price) if prediction is not None and prediction.predicted_price is not None else None
        recommended_amount = float(decision.recommended_amount) if decision is not None and decision.recommended_amount is not None else None

        prediction_delta_amount = self._compute_delta(predicted_price, winning_amount)
        recommendation_delta_amount = self._compute_delta(recommended_amount, winning_amount)
        prediction_error_rate = self._compute_error_rate(predicted_price, winning_amount)
        recommendation_error_rate = self._compute_error_rate(recommended_amount, winning_amount)

        improved = None
        if prediction_error_rate is not None and recommendation_error_rate is not None:
            improved = recommendation_error_rate <= prediction_error_rate

        return {
            "project_id": int(tender_result.project_id),
            "project_title": tender_result.project.title,
            "tender_result_id": int(tender_result.id),
            "result_status": str(tender_result.result_status or "pending"),
            "announced_at": tender_result.announced_at,
            "winning_amount": winning_amount,
            "winning_rate": float(tender_result.winning_rate or 0.0),
            "latest_prediction_id": int(prediction.id) if prediction is not None else None,
            "predicted_price": self._round_optional(predicted_price),
            "prediction_delta_amount": self._round_optional(prediction_delta_amount),
            "prediction_error_rate": self._round_optional(prediction_error_rate, digits=4),
            "latest_decision_record_id": int(decision.id) if decision is not None else None,
            "recommended_amount": self._round_optional(recommended_amount),
            "recommendation_delta_amount": self._round_optional(recommendation_delta_amount),
            "recommendation_error_rate": self._round_optional(recommendation_error_rate, digits=4),
            "recommendation_improved_vs_prediction": improved,
        }

    def _compute_delta(self, candidate_amount: float | None, winning_amount: float) -> float | None:
        """Return the signed amount difference versus the final winning amount."""
        if candidate_amount is None or winning_amount <= 0:
            return None
        return candidate_amount - winning_amount

    def _compute_error_rate(self, candidate_amount: float | None, winning_amount: float) -> float | None:
        """Return the absolute percentage error versus the final winning amount."""
        if candidate_amount is None or winning_amount <= 0:
            return None
        return abs(candidate_amount - winning_amount) / winning_amount

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average for summary metrics."""
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def _round_optional(self, value: float | None, *, digits: int = 2) -> float | None:
        """Round floats while preserving nulls."""
        if value is None:
            return None
        return round(float(value), digits)

    def _resolve_agency_weight(
        self,
        historical_record: HistoricalData | None,
        normalized_target_agency: str,
    ) -> tuple[float, bool]:
        """Weight calibration samples more heavily when the linked agency matches."""
        if historical_record is None or not normalized_target_agency:
            return 1.0, False

        normalized_record_agency = self._normalize_text(historical_record.agency_name)
        if not normalized_record_agency:
            return 1.0, False
        if normalized_record_agency == normalized_target_agency:
            return 2.5, True
        if normalized_target_agency in normalized_record_agency or normalized_record_agency in normalized_target_agency:
            return 1.5, False
        return 1.0, False

    def _normalize_text(self, value: str | None) -> str:
        """Normalize strings for fuzzy matching."""
        return "".join((value or "").strip().lower().split())
