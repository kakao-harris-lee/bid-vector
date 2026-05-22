"""Cutoff-aware historical data loading for backtests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.time import ensure_utc
from app.models.models import HistoricalData, Project, TenderResult

_CATEGORY_ALIASES = {
    "general-service": "service",
    "일반용역": "service",
    "service": "service",
    "technical-service": "technical-service",
    "기술용역": "technical-service",
    "construction": "construction",
    "공사": "construction",
    "software": "software",
    "소프트웨어": "software",
    "goods": "goods",
    "물품": "goods",
}

_RELATED_PRICE_HISTORY_CATEGORIES = {
    "technical-service": ("service",),
    "general-service": ("service",),
    "service": ("technical-service", "general-service"),
    "software": ("service", "technical-service"),
}

_AGENCY_STOP_WORDS = {
    "공사",
    "공단",
    "교육청",
    "지원청",
    "광역시",
    "특별시",
    "특별자치도",
    "시",
    "군",
    "구",
}


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
        seen_ids: set[int] = set()
        records: list[HistoricalData] = []
        category_scope = self._category_scope(category)

        scopes: list[tuple[list[str], list[str]]] = []
        if agency_name:
            scopes.append((category_scope[:1], [agency_name]))
            similar_terms = self._agency_similarity_terms(agency_name)
            if similar_terms:
                scopes.append((category_scope[:1], similar_terms))
        scopes.append((category_scope[:1], []))
        if len(category_scope) > 1:
            scopes.append((category_scope[1:], []))
        if not category_scope and agency_name:
            scopes.append(([], [agency_name]))
        if not scopes:
            scopes.append(([], []))

        for categories, agency_terms in scopes:
            if len(records) >= limit:
                break
            next_records = self._query_history_scope(
                db,
                categories=categories,
                agency_terms=agency_terms,
                cutoff_at=cutoff_at,
                exclude_project_id=exclude_project_id,
                explicit_bid_rate_only=explicit_bid_rate_only,
                exclude_historical_ids=seen_ids,
                limit=max(1, int(limit or 1)) - len(records),
            )
            for record in next_records:
                record_id = int(record.id or 0)
                if record_id in seen_ids:
                    continue
                records.append(record)
                seen_ids.add(record_id)

        return [self.serialize_historical_record(record) for record in records]

    def _query_history_scope(
        self,
        db: Session,
        *,
        categories: list[str],
        agency_terms: list[str],
        cutoff_at: datetime,
        exclude_project_id: int | None,
        explicit_bid_rate_only: bool,
        exclude_historical_ids: set[int],
        limit: int,
    ) -> list[HistoricalData]:
        """Fetch one scoped history slice for the fallback ladder."""
        cutoff_at = ensure_utc(cutoff_at)
        query = db.query(HistoricalData)
        if categories:
            query = query.filter(HistoricalData.category.in_(categories))
        cleaned_agency_terms = [term.strip() for term in agency_terms if term and term.strip()]
        if cleaned_agency_terms:
            query = query.filter(
                or_(
                    *[
                        HistoricalData.agency_name.ilike(f"%{term}%")
                        for term in cleaned_agency_terms
                    ]
                )
            )
        if exclude_project_id is not None:
            query = query.filter(
                or_(
                    HistoricalData.project_id.is_(None),
                    HistoricalData.project_id != int(exclude_project_id),
                )
            )
        if explicit_bid_rate_only:
            query = query.filter(HistoricalData.bid_rate > 0)
        if exclude_historical_ids:
            query = query.filter(HistoricalData.id.notin_(exclude_historical_ids))

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
        return records

    def _category_scope(self, category: str | None) -> list[str]:
        """Return the primary category plus price-history fallback categories."""
        normalized_category = self._normalize_category(category)
        if not normalized_category:
            return []

        categories = [normalized_category]
        for related_category in _RELATED_PRICE_HISTORY_CATEGORIES.get(normalized_category, ()):
            if related_category not in categories:
                categories.append(related_category)
        return categories

    def _normalize_category(self, category: str | None) -> str:
        normalized = str(category or "").strip().lower()
        return _CATEGORY_ALIASES.get(normalized, normalized)

    def _agency_similarity_terms(self, agency_name: str | None) -> list[str]:
        """Extract broad agency terms for a second-pass similar-agency lookup."""
        tokens = re.findall(r"[0-9A-Za-z가-힣]+", str(agency_name or ""))
        terms: list[str] = []
        for token in tokens:
            cleaned = token.strip()
            if len(cleaned) < 2 or cleaned in _AGENCY_STOP_WORDS:
                continue
            if cleaned not in terms:
                terms.append(cleaned)
        return terms[:3]

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
