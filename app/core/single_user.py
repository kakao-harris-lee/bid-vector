"""Single-user mode helpers.

This project is intentionally evolving toward a single-operator product.
The legacy database schema still uses `users` and `user_id` columns, so these
helpers centralize how the canonical operator account/profile are resolved.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.models import CompanyProfile, OperatorStrategy, User

DEFAULT_OPERATOR_USERNAME = "operator"
DEFAULT_OPERATOR_EMAIL = "operator@local.bid-vector"
DEFAULT_OPERATOR_FULL_NAME = "Primary Operator"
DEFAULT_OPERATOR_PASSWORD = "change-me-now"
DEFAULT_OPERATOR_BID_NOW_THRESHOLD = 0.7
DEFAULT_OPERATOR_REVIEW_THRESHOLD = 0.45


def split_multi_value_text(raw_value: str | None) -> list[str]:
    """Convert a stored comma-separated string into a normalized list."""
    if not raw_value:
        return []

    normalized = raw_value.replace(";", ",")
    return [value.strip() for value in normalized.split(",") if value.strip()]


def join_multi_value_text(values: list[str] | None) -> str:
    """Convert a list of values into the repository's compact text storage format."""
    if not values:
        return ""

    normalized: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped:
            normalized.append(stripped)
    return ", ".join(normalized)


def get_operator_account(db: Session) -> User | None:
    """Return the canonical operator account for single-user mode."""
    operator = db.query(User).filter(User.username == DEFAULT_OPERATOR_USERNAME).first()
    if operator:
        return operator

    operator = db.query(User).filter(User.is_active.is_(True)).order_by(User.id.asc()).first()
    if operator:
        return operator

    return db.query(User).order_by(User.id.asc()).first()


def ensure_operator_account(db: Session) -> User:
    """Ensure there is always exactly one practical operator account to work with."""
    operator = get_operator_account(db)
    if operator:
        return operator

    operator = User(
        username=DEFAULT_OPERATOR_USERNAME,
        email=DEFAULT_OPERATOR_EMAIL,
        full_name=DEFAULT_OPERATOR_FULL_NAME,
        company="",
        hashed_password=get_password_hash(DEFAULT_OPERATOR_PASSWORD),
        is_active=True,
    )
    db.add(operator)
    db.commit()
    db.refresh(operator)
    return operator


def get_operator_profile(db: Session, allow_fallback: bool = True) -> CompanyProfile | None:
    """Resolve the canonical company profile used for classification and planning."""
    operator = get_operator_account(db)
    if operator:
        profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == operator.id).first()
        if profile:
            return profile

    if not allow_fallback:
        return None

    profiles = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).limit(2).all()
    if len(profiles) == 1:
        return profiles[0]
    return None


def ensure_operator_profile(db: Session) -> CompanyProfile:
    """Ensure the operator has a company profile that can be edited from the UI."""
    operator = ensure_operator_account(db)

    direct_profile = get_operator_profile(db, allow_fallback=False)
    if direct_profile:
        return direct_profile

    fallback_profile = get_operator_profile(db, allow_fallback=True)
    if fallback_profile:
        if fallback_profile.user_id != operator.id:
            fallback_profile.user_id = operator.id
            db.commit()
            db.refresh(fallback_profile)
        return fallback_profile

    profile = CompanyProfile(
        user_id=operator.id,
        business_type="service",
        license_codes="",
        region_codes="",
        annual_revenue=0.0,
        capacity_score=0.0,
        total_awards=0,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_operator_strategy(db: Session, allow_fallback: bool = True) -> OperatorStrategy | None:
    """Resolve the canonical watch strategy used for operator monitoring."""
    operator = get_operator_account(db)
    if operator:
        strategy = db.query(OperatorStrategy).filter(OperatorStrategy.user_id == operator.id).first()
        if strategy:
            return strategy

    if not allow_fallback:
        return None

    strategies = db.query(OperatorStrategy).order_by(OperatorStrategy.id.asc()).limit(2).all()
    if len(strategies) == 1:
        return strategies[0]
    return None


def ensure_operator_strategy(db: Session) -> OperatorStrategy:
    """Ensure the operator has a persisted watch strategy that can be edited from the UI."""
    operator = ensure_operator_account(db)

    direct_strategy = get_operator_strategy(db, allow_fallback=False)
    if direct_strategy:
        return direct_strategy

    fallback_strategy = get_operator_strategy(db, allow_fallback=True)
    if fallback_strategy:
        if fallback_strategy.user_id != operator.id:
            fallback_strategy.user_id = operator.id
            db.commit()
            db.refresh(fallback_strategy)
        return fallback_strategy

    strategy = OperatorStrategy(
        user_id=operator.id,
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        required_keywords="",
        exclude_keywords="",
        min_budget_estimate=0.0,
        max_budget_estimate=0.0,
        minimum_match_score=0.6,
        minimum_probability_score=0.55,
        bid_now_threshold=DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
        review_threshold=DEFAULT_OPERATOR_REVIEW_THRESHOLD,
        auto_workload_penalty_multiplier=1.0,
        category_priority_overrides="{}",
        notify_only_high_priority=True,
        max_recommended_candidates=10,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy
