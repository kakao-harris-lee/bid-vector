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

from app.core.single_user import split_multi_value_text
from app.models.models import CompanyProfile, OperatorStrategy, User
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.utils.numeric import optional_float, optional_int

SYNTHETIC_USERNAME_PREFIX = "synthetic-"


class SyntheticBacktestService:
    def __init__(
        self, backtest_service: PaperBiddingBacktestService | None = None
    ) -> None:
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
            slug = user.username[len(SYNTHETIC_USERNAME_PREFIX) :]
            rows.append(
                {
                    "user_id": int(user.id),
                    "username": user.username,
                    "slug": slug,
                    # Custom companies (web-defined) carry the ``custom-`` slug
                    # prefix; presets do not. Sole ``is_custom`` predicate.
                    "is_custom": slug.startswith("custom-"),
                    "display_name": user.full_name or slug,
                    "company": user.company,
                    "business_type": profile.business_type if profile else None,
                    # Focus categories drive the per-operator award window so a
                    # focus-category operator (e.g. goods) is not starved by the
                    # global award pool. Internal-only: NOT part of the operator
                    # list response schema (consumers ignore the extra key).
                    "focus_categories": list(
                        split_multi_value_text(
                            getattr(strategy, "focus_categories", None)
                        )
                    ),
                    "annual_revenue": float(profile.annual_revenue or 0.0)
                    if profile
                    else 0.0,
                    "capacity_score": float(profile.capacity_score or 0.0)
                    if profile
                    else 0.0,
                    "bid_now_threshold": float(strategy.bid_now_threshold or 0.0)
                    if strategy
                    else 0.0,
                    "review_threshold": float(strategy.review_threshold or 0.0)
                    if strategy
                    else 0.0,
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
        cutoff_hours_before_deadline: int | None = None,
        history_limit: int | None = None,
        settle_actions: Sequence[str] | None = None,
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
                cutoff_hours_before_deadline=cutoff_hours_before_deadline,
                history_limit=history_limit,
                settle_actions=settle_actions,
                focus_categories=op.get("focus_categories") or None,
            )
            # ``focus_categories`` is an internal plumbing key, not a response
            # field -- drop it so the per-operator result matches the schema.
            op_public = {k: v for k, v in op.items() if k != "focus_categories"}
            results.append({**op_public, **summary})

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
        settlement_sample: int = 20,
        focus_categories: Sequence[str] | None = None,
        cutoff_hours_before_deadline: int | None = None,
        history_limit: int | None = None,
        settle_actions: Sequence[str] | None = None,
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
            cutoff_hours_before_deadline=(
                int(cutoff_hours_before_deadline)
                if cutoff_hours_before_deadline is not None
                else 2
            ),
            history_limit=int(history_limit) if history_limit is not None else 80,
            settle_actions=settle_actions,
            persist=False,
            # Scope the award window to the operator's focus categories so a
            # minority-category operator (goods) is not starved by the global
            # pool. ``run_historical_backtest`` falls back to strategy focus
            # categories itself, but passing them explicitly keeps this path
            # robust to future signature defaults.
            award_categories=list(focus_categories) if focus_categories else None,
        )
        summary = result.get("summary") or {}
        candidate_count = int(
            result.get("candidate_count") or summary.get("candidate_count") or 0
        )
        paper_bid_count = int(
            result.get("paper_bid_count") or summary.get("paper_bid_count") or 0
        )
        settled_count = int(
            result.get("settled_count") or summary.get("settled_count") or 0
        )
        win_count = int(summary.get("would_have_won_price_only_count") or 0)
        win_rate_on_settled = (
            float(win_count) / float(settled_count) if settled_count else None
        )
        bid_submission_rate = summary.get("bid_submission_rate")
        raw_settlements = result.get("settlements") or []
        settlement_items = [
            _slice_settlement_item(item) for item in raw_settlements[:settlement_sample]
        ]
        # Breakdown is computed over the FULL settlement set (not the truncated
        # ``settlement_sample``) so category / budget-band win rates reflect every
        # settled opportunity. Import locally to avoid a circular import with
        # ``synthetic_experiment`` (which imports this module).
        from app.services.synthetic_experiment import compute_breakdown

        breakdown = compute_breakdown(raw_settlements)
        return {
            "candidate_count": candidate_count,
            "paper_bid_count": paper_bid_count,
            "settled_count": settled_count,
            "would_have_won_count": win_count,
            "win_rate_on_settled": win_rate_on_settled,
            "bid_submission_rate": bid_submission_rate,
            "average_absolute_bid_rate_error": summary.get(
                "average_absolute_bid_rate_error"
            ),
            "settlement_sample_count": len(settlement_items),
            "settlement_items": settlement_items,
            "breakdown": breakdown,
        }


def _slice_settlement_item(item: dict[str, Any]) -> dict[str, Any]:
    """Pick a stable subset of settlement fields for the drilldown payload.

    Avoids shipping the full backtest internals to the dashboard; the few
    fields here are sufficient to render a settle list + bid-rate error
    histogram in the UI.
    """
    return {
        "project_id": optional_int(item.get("project_id")),
        "project_title": str(item.get("project_title") or ""),
        "category": item.get("category"),
        "paper_bid_id": optional_int(item.get("paper_bid_id")),
        "decision_action": item.get("decision_action"),
        "bid_amount": optional_float(item.get("bid_amount")),
        "winning_amount": optional_float(item.get("winning_amount")),
        "absolute_bid_rate_error": optional_float(item.get("absolute_bid_rate_error")),
        "would_have_won": bool(item.get("would_have_won")),
        "settled_at": item.get("settled_at"),
    }


def serialize_synthetic_operators(
    rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Public helper for callers that already have the rows shape."""
    return list(rows)
