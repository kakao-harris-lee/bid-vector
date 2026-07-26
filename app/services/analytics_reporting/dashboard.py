"""Top-level operations dashboard orchestration mixin."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import (
    CrawlJob,
    OperatorStrategyRun,
    User,
)


class _DashboardMixin:
    """Top-level operations dashboard orchestration and card assembly."""

    def build_operations_dashboard(
        self,
        db: Session,
        *,
        days: int = 30,
        recent_limit: int = 5,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return crawl, strategy, and task runtime summaries for one reporting window."""
        if operator is None:
            operator = ensure_operator_account(db)
        date_from = utc_now() - timedelta(days=days)
        crawl_jobs = (
            db.query(CrawlJob)
            .filter(CrawlJob.created_at >= date_from)
            .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
            .all()
        )
        strategy_runs = (
            db.query(OperatorStrategyRun)
            .filter(
                OperatorStrategyRun.operator_id == operator.id,
                OperatorStrategyRun.created_at >= date_from,
            )
            .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
            .all()
        )
        crawl_summary = self._build_crawl_summary(crawl_jobs, recent_limit=recent_limit)
        strategy_summary = self._build_strategy_summary(strategy_runs, recent_limit=recent_limit)
        task_summary = self._build_task_summary(crawl_jobs, strategy_runs, recent_limit=recent_limit)
        notification_summary = self._build_notification_summary(
            db,
            operator_id=int(operator.id),
            date_from=date_from,
            recent_limit=recent_limit,
        )
        ml_release_summary = self._build_ml_release_summary(recent_limit=recent_limit)
        smoke_test_summary = self._build_smoke_test_summary(
            db,
            days=days,
            recent_limit=recent_limit,
        )
        synthetic_validation_summary = self._build_synthetic_validation_summary(
            db,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        return {
            "operator_id": operator.id,
            "period_days": days,
            "crawl": crawl_summary,
            "strategy": strategy_summary,
            "tasks": task_summary,
            "notifications": notification_summary,
            "ml_release": ml_release_summary,
            "smoke_test": smoke_test_summary,
            "synthetic_validation": synthetic_validation_summary,
            "cards": self._build_cards(
                crawl_summary,
                strategy_summary,
                task_summary,
                notification_summary,
                ml_release_summary,
                smoke_test_summary,
                synthetic_validation_summary,
            ),
        }

    def _build_cards(
        self,
        crawl_summary: dict[str, Any],
        strategy_summary: dict[str, Any],
        task_summary: dict[str, Any],
        notification_summary: dict[str, Any],
        ml_release_summary: dict[str, Any],
        smoke_test_summary: dict[str, Any],
        synthetic_validation_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build dashboard card payloads from detailed summaries."""
        return [
            *self._crawl_dashboard_cards(crawl_summary),
            *self._strategy_dashboard_cards(strategy_summary),
            *self._task_dashboard_cards(task_summary),
            self._telegram_delivery_card(notification_summary),
            *self._ml_release_dashboard_cards(ml_release_summary),
            *self._smoke_test_dashboard_cards(smoke_test_summary),
            *self._synthetic_validation_dashboard_cards(synthetic_validation_summary),
        ]
