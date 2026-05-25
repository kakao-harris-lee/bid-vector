"""Per-synthetic-operator paper-bidding backtest aggregation.

Wraps :class:`PaperBiddingBacktestService` to run the same historical replay
for every synthetic operator seeded by
:mod:`scripts.seed_synthetic_operators`, then collapses the per-operator
summaries into a single comparison payload. Used by both
``scripts/backtest_synthetic_operators.py`` (CLI) and the
``/api/v1/synthetic/backtests/*`` HTTP endpoints (web dashboard).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app.models.models import CompanyProfile, OperatorStrategy, User
from app.services.paper_bidding_backtest import PaperBiddingBacktestService

SYNTHETIC_USERNAME_PREFIX = "synthetic-"


class SyntheticBacktestService:
    def __init__(self, backtest_service: PaperBiddingBacktestService | None = None) -> None:
        self.backtest_service = backtest_service or PaperBiddingBacktestService()

    # --- listing ---------------------------------------------------------------

    def list_operators(self, db: Session) -> list[dict[str, Any]]:
        users = (
            db.query(User)
            .filter(User.username.like(f"{SYNTHETIC_USERNAME_PREFIX}%"))
            .order_by(User.id.asc())
            .all()
        )
        rows: list[dict[str, Any]] = []
        for user in users:
            profile = (
                db.query(CompanyProfile)
                .filter(CompanyProfile.user_id == user.id)
                .first()
            )
            strategy = (
                db.query(OperatorStrategy)
                .filter(OperatorStrategy.user_id == user.id)
                .first()
            )
            slug = user.username[len(SYNTHETIC_USERNAME_PREFIX):]
            rows.append(
                {
                    "user_id": int(user.id),
                    "username": user.username,
                    "slug": slug,
                    "display_name": user.full_name or slug,
                    "company": user.company,
                    "business_type": profile.business_type if profile else None,
                    "annual_revenue": float(profile.annual_revenue or 0.0) if profile else 0.0,
                    "capacity_score": float(profile.capacity_score or 0.0) if profile else 0.0,
                    "bid_now_threshold": float(strategy.bid_now_threshold or 0.0) if strategy else 0.0,
                    "review_threshold": float(strategy.review_threshold or 0.0) if strategy else 0.0,
                }
            )
        return rows

    # --- run aggregation -------------------------------------------------------

    def run_for_all(
        self,
        db: Session,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        category: str | None = None,
        limit: int = 100,
        scenario: str = PaperBiddingBacktestService.DEFAULT_SCENARIO,
        slugs: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Run the historical backtest for every synthetic operator (or a slug subset).

        Returns the comparison payload — list of per-operator summaries plus the
        request envelope. Idempotent in the sense that calling it with the same
        inputs is safe; results are NOT persisted (frontend just caches the
        latest response).
        """
        operators = self.list_operators(db)
        if slugs:
            requested = set(slugs)
            operators = [op for op in operators if op["slug"] in requested]

        results: list[dict[str, Any]] = []
        for op in operators:
            summary = self._run_one(
                db,
                operator_id=op["user_id"],
                start_at=start_at,
                end_at=end_at,
                category=category,
                limit=limit,
                scenario=scenario,
            )
            results.append({**op, **summary})

        return {
            "operator_count": len(operators),
            "category": category,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "limit": limit,
            "scenario": scenario,
            "results": results,
        }

    def _run_one(
        self,
        db: Session,
        *,
        operator_id: int,
        start_at: datetime | None,
        end_at: datetime | None,
        category: str | None,
        limit: int,
        scenario: str,
    ) -> dict[str, Any]:
        result = self.backtest_service.run_historical_backtest(
            db,
            operator_id=operator_id,
            category=category,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            scenario=scenario,
            strategy_version="synthetic-backtest",
            model_version="current",
            persist=False,
        )
        summary = result.get("summary") or {}
        settled_count = int(result.get("settled_count") or 0)
        win_count = int(summary.get("would_have_won_price_only_count") or 0)
        win_rate_on_settled = (
            float(win_count) / float(settled_count) if settled_count else None
        )
        bid_submission_rate = summary.get("bid_submission_rate")
        return {
            "candidate_count": int(result.get("candidate_count") or 0),
            "paper_bid_count": int(result.get("paper_bid_count") or 0),
            "settled_count": settled_count,
            "would_have_won_count": win_count,
            "win_rate_on_settled": win_rate_on_settled,
            "bid_submission_rate": bid_submission_rate,
            "average_absolute_bid_rate_error": summary.get("average_absolute_bid_rate_error"),
        }


def serialize_synthetic_operators(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public helper for callers that already have the rows shape."""
    return list(rows)
