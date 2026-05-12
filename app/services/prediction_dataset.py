"""Historical dataset helpers for price prediction."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import HistoricalData, TenderResult


class PredictionDatasetService:
    """Build normalized historical bid-rate series for prediction logic."""

    VALID_BID_RATE_MIN = 0.5
    VALID_BID_RATE_MAX = 1.5

    def load_historical_series(
        self,
        db: Session,
        *,
        category: str | None = None,
        agency_name: str | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        """Load a normalized bid-rate series from stored historical rows."""
        query = db.query(HistoricalData)
        if category:
            query = query.filter(HistoricalData.category == category)
        if agency_name:
            query = query.filter(HistoricalData.agency_name.ilike(f"%{agency_name.strip()}%"))

        records = (
            query.order_by(HistoricalData.opened_at.desc(), HistoricalData.created_at.desc())
            .limit(limit)
            .all()
        )
        latest_results = self._load_latest_tender_results(
            db,
            project_ids={int(record.project_id) for record in records if record.project_id is not None},
        )

        series: list[dict[str, Any]] = []
        for record in records:
            tender_result = latest_results.get(int(record.project_id)) if record.project_id is not None else None
            normalized = self._serialize_series_point(record, tender_result=tender_result)
            if normalized is not None:
                series.append(normalized)
        return series

    def build_training_dataset(
        self,
        db: Session,
        *,
        category: str | None = None,
        agency_name: str | None = None,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Return a normalized training dataset plus lightweight quality summary."""
        series = self.load_historical_series(
            db,
            category=category,
            agency_name=agency_name,
            limit=limit,
        )
        opened_at_values = [item["opened_at"] for item in series if item.get("opened_at") is not None]
        linked_result_count = sum(1 for item in series if item.get("tender_result_status"))
        reserve_pattern_sample_count = sum(1 for item in series if item.get("reserve_prices"))

        return {
            "category": category,
            "agency_name": agency_name,
            "summary": {
                "sample_count": len(series),
                "project_count": len({item.get("project_id") for item in series if item.get("project_id") is not None}),
                "agency_count": len({item.get("agency_name") for item in series if item.get("agency_name")}),
                "linked_result_count": linked_result_count,
                "reserve_pattern_sample_count": reserve_pattern_sample_count,
                "started_at": min(opened_at_values) if opened_at_values else None,
                "ended_at": max(opened_at_values) if opened_at_values else None,
            },
            "series": series,
        }

    def _serialize_series_point(
        self,
        record: HistoricalData,
        *,
        tender_result: TenderResult | None,
    ) -> dict[str, Any] | None:
        """Normalize one historical row into a predictor-friendly dictionary."""
        bid_rate = self._resolve_bid_rate(record)
        if bid_rate is None:
            return None

        return {
            "historical_data_id": record.id,
            "project_id": record.project_id,
            "notice_number": record.notice_number,
            "category": record.category,
            "agency_name": record.agency_name,
            "base_amount": float(record.base_amount or 0.0),
            "predicted_price": float(record.predicted_price or 0.0),
            "bid_rate": bid_rate,
            "reserve_prices": self._coerce_numeric_list(record.reserve_prices),
            "selected_numbers": self._coerce_integer_list(record.selected_numbers),
            "opened_at": record.opened_at or record.created_at,
            "tender_result_id": tender_result.id if tender_result is not None else None,
            "winning_amount": float(tender_result.winning_amount or 0.0) if tender_result is not None else None,
            "winning_rate": float(tender_result.winning_rate or 0.0) if tender_result is not None else None,
            "tender_result_status": tender_result.result_status if tender_result is not None else None,
            "tender_result_announced_at": tender_result.announced_at if tender_result is not None else None,
        }

    def _resolve_bid_rate(self, record: HistoricalData) -> float | None:
        """Resolve a usable bid rate from the row's explicit or derived values."""
        bid_rate = float(record.bid_rate or 0.0)
        if bid_rate <= 0:
            predicted_price = float(record.predicted_price or 0.0)
            base_amount = float(record.base_amount or 0.0)
            if predicted_price > 0 and base_amount > 0:
                bid_rate = predicted_price / base_amount

        if not (self.VALID_BID_RATE_MIN <= bid_rate <= self.VALID_BID_RATE_MAX):
            return None
        return round(float(bid_rate), 6)

    def _load_latest_tender_results(
        self,
        db: Session,
        *,
        project_ids: set[int],
    ) -> dict[int, TenderResult]:
        """Load the latest tender result for each linked project."""
        if not project_ids:
            return {}

        results = (
            db.query(TenderResult)
            .filter(TenderResult.project_id.in_(sorted(project_ids)))
            .order_by(TenderResult.project_id.asc(), TenderResult.id.desc())
            .all()
        )
        latest_by_project: dict[int, TenderResult] = {}
        for result in results:
            project_id = int(result.project_id or 0)
            current = latest_by_project.get(project_id)
            if current is None or self._is_newer_result(result, current):
                latest_by_project[project_id] = result
        return latest_by_project

    def _is_newer_result(self, candidate: TenderResult, current: TenderResult) -> bool:
        """Prefer the latest announced result, with created id as a fallback."""
        candidate_announced_at = candidate.announced_at or candidate.created_at
        current_announced_at = current.announced_at or current.created_at
        if candidate_announced_at and current_announced_at and candidate_announced_at != current_announced_at:
            return candidate_announced_at > current_announced_at
        return int(candidate.id or 0) > int(current.id or 0)

    def _coerce_numeric_list(self, raw_value: Any) -> list[float]:
        """Coerce a JSON string or list of numbers into floats."""
        parsed = self._coerce_sequence(raw_value)
        numbers: list[float] = []
        for item in parsed:
            try:
                numbers.append(float(item))
            except (TypeError, ValueError):
                continue
        return numbers

    def _coerce_integer_list(self, raw_value: Any) -> list[int]:
        """Coerce a JSON string or list of numbers into integers."""
        parsed = self._coerce_sequence(raw_value)
        numbers: list[int] = []
        for item in parsed:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                continue
        return numbers

    def _coerce_sequence(self, raw_value: Any) -> list[Any]:
        """Parse list-like values coming from ORM rows."""
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return raw_value
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []
        return []
