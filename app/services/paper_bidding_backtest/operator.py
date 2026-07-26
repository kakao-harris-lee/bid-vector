"""Per-operator resolution (no silent canonical fallback)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile_for,
    ensure_operator_strategy_for,
)
from app.models.models import CompanyProfile, OperatorStrategy, User
from app.services.paper_bidding_backtest.base import (
    OperatorNotFoundError,
    _PaperBiddingBase,
)


class _OperatorResolutionMixin(_PaperBiddingBase):
    """Resolve the operator/strategy/profile that owns a backtest."""

    def _resolve_operator(self, db: Session, *, operator_id: int | None) -> User:
        """Resolve the operator that owns a backtest.

        ``operator_id is None`` keeps the single-operator default path and bootstraps
        the canonical operator. When an explicit ``operator_id`` is supplied it must
        resolve to a real row: a missing operator raises :class:`OperatorNotFoundError`
        rather than silently falling back to the canonical operator, which would let a
        non-existent (e.g. synthetic) operator pollute canonical paper-bid data.
        """
        if operator_id is None:
            return ensure_operator_account(db)
        operator = db.query(User).filter(User.id == int(operator_id)).first()
        if operator is None:
            raise OperatorNotFoundError(f"Operator {int(operator_id)} not found")
        return operator

    def _resolve_operator_strategy(
        self, db: Session, *, operator: User
    ) -> OperatorStrategy:
        """Return the strategy belonging to *operator*.

        Scoped to ``operator.id`` via :func:`ensure_operator_strategy_for`, which never
        reassigns the canonical operator's strategy. Synthetic operators already carry a
        strategy from seeding; if one is somehow missing a row is created for *that*
        operator (never the canonical one).
        """
        return ensure_operator_strategy_for(db, operator)

    def _resolve_operator_profile(
        self, db: Session, *, operator: User
    ) -> CompanyProfile | None:
        """Return the company profile belonging to *operator*.

        Mirrors :meth:`_resolve_operator_strategy` using
        :func:`ensure_operator_profile_for`, so each operator's
        license/region/budget/capability profile drives its own matched score and the
        canonical profile is never returned or mutated as a fallback.
        """
        return ensure_operator_profile_for(db, operator)
