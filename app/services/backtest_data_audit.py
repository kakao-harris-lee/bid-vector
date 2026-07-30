"""Data readiness audit helpers for paper-bidding backtests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.models.models import Bid, BidDecisionRecord, HistoricalData, PricePrediction, Project, TenderResult
from app.schemas.paper_bidding import BacktestDataAuditResponse
from app.schemas.paper_bidding_audit import (
    BacktestDataAuditCategoryRow,
    BacktestDataAuditDateRange,
    BacktestDataAuditFilters,
    BacktestDataAuditTableCounts,
    BacktestDataAuditWindowCounts,
)
from app.services.query_predicates import open_projects, settled_with_amount


class BacktestDataAuditService:
    """Summarize whether the current DB can support backtest scenarios."""

    def build_report(
        self,
        db: Session,
        *,
        categories: Sequence[str] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> BacktestDataAuditResponse:
        """Return table counts and usable award coverage for a backtest window."""
        normalized_categories = self._normalize_categories(categories)
        start_at = ensure_utc(start_at) if start_at is not None else None
        end_at = ensure_utc(end_at) if end_at is not None else None

        usable_result_filters = self._usable_result_filters()
        result_window_filters = self._result_window_filters(start_at=start_at, end_at=end_at)
        category_filters = [Project.category.in_(normalized_categories)] if normalized_categories else []

        usable_result_query = (
            db.query(TenderResult)
            .join(Project, Project.id == TenderResult.project_id)
            .filter(*usable_result_filters, *result_window_filters, *category_filters)
        )
        pending_snapshot_query = (
            db.query(TenderResult)
            .join(Project, Project.id == TenderResult.project_id)
            .filter(
                TenderResult.project_id.isnot(None),
                or_(TenderResult.winning_amount.is_(None), TenderResult.winning_amount <= 0),
                *result_window_filters,
                *category_filters,
            )
        )

        return BacktestDataAuditResponse(
            generated_at=utc_now().isoformat(),
            filters=BacktestDataAuditFilters(
                categories=normalized_categories,
                start_at=start_at.isoformat() if start_at else None,
                end_at=end_at.isoformat() if end_at else None,
            ),
            table_counts=self._build_table_counts(db),
            window_counts=BacktestDataAuditWindowCounts(
                usable_award_count=usable_result_query.count(),
                pending_or_opening_snapshot_count=pending_snapshot_query.count(),
                distinct_project_count=usable_result_query.with_entities(func.count(func.distinct(TenderResult.project_id))).scalar() or 0,
            ),
            date_range=BacktestDataAuditDateRange(
                award_announced_min=self._iso_or_none(
                    usable_result_query.with_entities(func.min(TenderResult.announced_at)).scalar()
                ),
                award_announced_max=self._iso_or_none(
                    usable_result_query.with_entities(func.max(TenderResult.announced_at)).scalar()
                ),
            ),
            category_breakdown=self._build_category_breakdown(
                db,
                usable_result_filters=usable_result_filters,
                result_window_filters=result_window_filters,
                category_filters=category_filters,
            ),
        )

    def _build_table_counts(self, db: Session) -> BacktestDataAuditTableCounts:
        """백테스트가 의존하는 테이블별 전체 행 수(창 필터 없음)."""
        return BacktestDataAuditTableCounts(
            projects_total=self._count(db.query(Project.id)),
            projects_active_open_or_re_notice=self._count(
                db.query(Project.id).filter(open_projects())
            ),
            historical_total=self._count(db.query(HistoricalData.id)),
            historical_with_bid_rate=self._count(db.query(HistoricalData.id).filter(HistoricalData.bid_rate > 0)),
            historical_with_project_id=self._count(
                db.query(HistoricalData.id).filter(HistoricalData.project_id.isnot(None))
            ),
            tender_results_total=self._count(db.query(TenderResult.id)),
            tender_results_usable_awards=self._count(
                db.query(TenderResult.id).filter(*self._usable_result_filters())
            ),
            price_predictions_total=self._count(db.query(PricePrediction.id)),
            bid_decisions_total=self._count(db.query(BidDecisionRecord.id)),
            bid_decisions_submitted=self._count(
                db.query(BidDecisionRecord.id).filter(BidDecisionRecord.decision_status == "submitted")
            ),
            bids_total=self._count(db.query(Bid.id)),
        )

    def _build_category_breakdown(
        self,
        db: Session,
        *,
        usable_result_filters: list[Any],
        result_window_filters: list[Any],
        category_filters: list[Any],
    ) -> list[BacktestDataAuditCategoryRow]:
        rows = (
            db.query(
                Project.category,
                func.count(TenderResult.id),
                func.count(func.distinct(TenderResult.project_id)),
            )
            .join(TenderResult, TenderResult.project_id == Project.id)
            .filter(*usable_result_filters, *result_window_filters, *category_filters)
            .group_by(Project.category)
            .order_by(func.count(TenderResult.id).desc())
            .all()
        )
        return [
            BacktestDataAuditCategoryRow(
                category=category or "unknown",
                usable_award_count=int(usable_count or 0),
                distinct_project_count=int(project_count or 0),
            )
            for category, usable_count, project_count in rows
        ]

    def _usable_result_filters(self) -> list[Any]:
        return [
            TenderResult.project_id.isnot(None),
            settled_with_amount(),
        ]

    def _result_window_filters(self, *, start_at: datetime | None, end_at: datetime | None) -> list[Any]:
        filters: list[Any] = []
        if start_at is not None:
            filters.append(
                or_(
                    TenderResult.announced_at >= start_at,
                    and_(TenderResult.announced_at.is_(None), TenderResult.created_at >= start_at),
                )
            )
        if end_at is not None:
            filters.append(
                or_(
                    TenderResult.announced_at <= end_at,
                    and_(TenderResult.announced_at.is_(None), TenderResult.created_at <= end_at),
                )
            )
        return filters

    def _normalize_categories(self, categories: Sequence[str] | None) -> list[str]:
        normalized: list[str] = []
        for category in categories or []:
            value = str(category or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _count(self, query) -> int:
        return int(query.count() or 0)

    def _iso_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return ensure_utc(value).isoformat()
