"""Cutoff-aware historical data loading for backtests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.time import ensure_utc
from app.models.models import HistoricalData, Project, TenderResult


class BacktestCutoffService:
    """Load only records that would have existed at a backtest decision time."""

    def resolve_data_cutoff_at(
        self,
        project: Project,
        *,
        tender_result: TenderResult | None = None,
        hours_before_deadline: int = 2,
    ) -> datetime:
        """Return the simulated decision cutoff for one project."""
        created_at = ensure_utc(project.created_at) if project.created_at else None
        deadline = ensure_utc(project.deadline) if project.deadline else None
        result_time = None
        if tender_result is not None:
            result_time = tender_result.announced_at or tender_result.created_at
            result_time = ensure_utc(result_time) if result_time else None

        if deadline is not None:
            cutoff = deadline - timedelta(hours=max(0, int(hours_before_deadline or 0)))
        elif created_at is not None:
            cutoff = created_at
        elif result_time is not None:
            cutoff = result_time - timedelta(days=1)
        else:
            raise ValueError("Cannot resolve backtest cutoff without project or result timestamps")

        if created_at is not None and cutoff < created_at:
            cutoff = created_at
        if result_time is not None and cutoff >= result_time:
            cutoff = result_time - timedelta(days=1)
            if created_at is not None and cutoff < created_at:
                cutoff = created_at
        return cutoff

    def load_price_history_at_cutoff(
        self,
        db: Session,
        *,
        category: str | None,
        cutoff_at: datetime,
        agency_name: str | None = None,
        exclude_project_id: int | None = None,
        limit: int = 80,
        explicit_bid_rate_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Load normalized historical rows visible before the cutoff."""
        cutoff_at = ensure_utc(cutoff_at)
        query = db.query(HistoricalData)
        if category:
            query = query.filter(HistoricalData.category == category)
        if agency_name:
            query = query.filter(HistoricalData.agency_name.ilike(f"%{agency_name.strip()}%"))
        if exclude_project_id is not None:
            query = query.filter(
                or_(
                    HistoricalData.project_id.is_(None),
                    HistoricalData.project_id != int(exclude_project_id),
                )
            )
        if explicit_bid_rate_only:
            query = query.filter(HistoricalData.bid_rate > 0)

        query = query.filter(
            or_(
                HistoricalData.opened_at < cutoff_at,
                and_(HistoricalData.opened_at.is_(None), HistoricalData.created_at < cutoff_at),
            )
        )

        records = (
            query.order_by(HistoricalData.opened_at.desc(), HistoricalData.created_at.desc(), HistoricalData.id.desc())
            .limit(max(1, int(limit or 1)))
            .all()
        )
        return [self.serialize_historical_record(record) for record in records]

    def serialize_historical_record(self, record: HistoricalData) -> dict[str, Any]:
        """Convert an ORM row into the predictor's lightweight record shape."""
        return {
            "historical_data_id": int(record.id),
            "project_id": int(record.project_id) if record.project_id is not None else None,
            "notice_number": record.notice_number,
            "category": record.category,
            "agency_name": record.agency_name,
            "base_amount": float(record.base_amount or 0.0),
            "predicted_price": float(record.predicted_price or 0.0),
            "bid_rate": float(record.bid_rate or 0.0),
            "reserve_prices": self._coerce_sequence(record.reserve_prices),
            "selected_numbers": self._coerce_sequence(record.selected_numbers),
            "opened_at": record.opened_at or record.created_at,
        }

    def _coerce_sequence(self, raw_value: Any) -> list[Any]:
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
