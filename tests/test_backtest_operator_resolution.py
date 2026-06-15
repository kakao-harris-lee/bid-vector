"""Per-operator backtest resolution: no silent canonical fallback.

Regression guard for the ``fix/backtest-operator-no-silent-fallback`` change.

``PaperBiddingBacktestService._resolve_operator`` used to silently return the
canonical operator when an explicit ``operator_id`` did not resolve to a real
row, and ``_resolve_operator_strategy`` / ``_resolve_operator_profile`` used to
fall back to the canonical strategy/profile. That let a non-existent (e.g.
synthetic) operator pollute the canonical operator's data.

These tests assert:
  (a) a synthetic operator's *own* strategy drives candidate selection (its
      ``focus_categories`` filter), distinct from the canonical operator's, and
      the canonical strategy/profile rows are not mutated;
  (b) a non-existent ``operator_id`` raises ``OperatorNotFoundError`` (service)
      and maps to ``404`` (API);
  (c) ``operator_id=None`` keeps the canonical default path (regression).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.single_user import ensure_operator_account, ensure_operator_strategy
from app.models.models import (
    CompanyProfile,
    HistoricalData,
    OperatorStrategy,
    Project,
    TenderResult,
    User,
)
from app.services.paper_bidding_backtest import (
    OperatorNotFoundError,
    PaperBiddingBacktestService,
)


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _construction_project() -> Project:
    return Project(
        title="Road maintenance tender",
        description="Road maintenance project",
        requirements="standard qualification",
        budget_estimate=100_000_000,
        category="construction",
        status="awarded",
        issuing_agency="Seoul",
        created_at=_dt(2025, 2, 1),
        deadline=_dt(2025, 3, 10),
    )


def _historical_row(*, opened_at: datetime, bid_rate: float = 0.88) -> HistoricalData:
    return HistoricalData(
        project_id=None,
        notice_number=f"H-{opened_at:%Y%m%d%H}",
        agency_name="Seoul",
        category="construction",
        base_amount=100_000_000,
        predicted_price=100_000_000 * bid_rate,
        bid_rate=bid_rate,
        opened_at=opened_at,
    )


def _seed_construction_award(test_db) -> Project:
    target = _construction_project()
    test_db.add(target)
    test_db.flush()
    for index in range(8):
        test_db.add(_historical_row(opened_at=_dt(2025, 1, 1) + timedelta(days=index)))
    test_db.add(
        TenderResult(
            project_id=target.id,
            winning_company="Winner",
            winning_amount=88_000_000,
            winning_rate=0.88,
            result_status="awarded",
            announced_at=_dt(2025, 3, 11),
        )
    )
    test_db.commit()
    return target


def _make_synthetic_operator(test_db, *, slug: str, focus_categories: str) -> User:
    """Create a ``synthetic-<slug>`` operator with its own strategy + profile."""
    user = User(
        username=f"synthetic-{slug}",
        email=f"synthetic-{slug}@example.com",
        full_name=f"Synthetic {slug}",
        company=f"Synthetic {slug} Co",
        hashed_password="!unusable",
        is_active=True,
        is_admin=False,
    )
    test_db.add(user)
    test_db.flush()
    test_db.add(OperatorStrategy(user_id=user.id, focus_categories=focus_categories))
    test_db.add(CompanyProfile(user_id=user.id, business_type="service"))
    test_db.commit()
    test_db.refresh(user)
    return user


# --- (a) synthetic operator uses its own strategy, canonical untouched --------


def test_synthetic_operator_uses_own_strategy_and_leaves_canonical_intact(test_db):
    _seed_construction_award(test_db)

    # Canonical operator: empty focus_categories (matches everything).
    canonical = ensure_operator_account(test_db)
    canonical_strategy = ensure_operator_strategy(test_db)
    canonical_strategy.focus_categories = ""
    test_db.commit()
    canonical_strategy_id = canonical_strategy.id

    # Synthetic operator focuses on "service" only -> skips the construction award.
    synthetic = _make_synthetic_operator(
        test_db, slug="service-only", focus_categories="service"
    )

    service = PaperBiddingBacktestService()

    canonical_result = service.run_historical_backtest(
        test_db,
        operator_id=int(canonical.id),
        category=None,
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=False,
    )
    synthetic_result = service.run_historical_backtest(
        test_db,
        operator_id=int(synthetic.id),
        category=None,
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=False,
    )

    # The synthetic operator's own focus filter produces a *different* result.
    assert canonical_result["summary"]["candidate_count"] == 1
    assert synthetic_result["summary"]["candidate_count"] == 0
    assert synthetic_result["summary"]["skipped_by_strategy_count"] >= 1

    # Canonical strategy was never reassigned to the synthetic operator, and the
    # synthetic operator's own strategy row is the one that drove its result.
    test_db.expire_all()
    refreshed_canonical = (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.id == canonical_strategy_id)
        .one()
    )
    assert refreshed_canonical.user_id == canonical.id
    assert refreshed_canonical.focus_categories == ""

    synthetic_strategy = (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == synthetic.id)
        .one()
    )
    assert synthetic_strategy.focus_categories == "service"

    # No duplicate strategy/profile rows were created for either operator.
    assert (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == synthetic.id)
        .count()
        == 1
    )
    assert (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == synthetic.id)
        .count()
        == 1
    )


# --- (b) missing operator_id raises / 404 ------------------------------------


def test_missing_operator_id_raises_operator_not_found(test_db):
    ensure_operator_account(test_db)  # canonical exists but must not be returned.
    service = PaperBiddingBacktestService()
    missing_id = 999_999
    with pytest.raises(OperatorNotFoundError):
        service.run_historical_backtest(
            test_db,
            operator_id=missing_id,
            persist=False,
        )


def test_missing_operator_id_raises_for_forward_paper_bidding(test_db):
    ensure_operator_account(test_db)
    service = PaperBiddingBacktestService()
    with pytest.raises(OperatorNotFoundError):
        service.run_forward_paper_bidding(
            test_db,
            operator_id=999_999,
            persist=False,
        )


def test_backtests_api_returns_404_for_missing_operator(client, test_db, monkeypatch):
    # Authenticate as the canonical operator, but force the service to receive a
    # non-existent operator_id so the API translates OperatorNotFoundError -> 404.
    from app.services import paper_bidding_backtest as backtest_module

    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "backtest-404-operator",
            "email": "backtest-404@example.com",
            "full_name": "Backtest 404 Operator",
            "company": "Bid Vector Labs",
            "password": "password123",
        },
    )
    assert bootstrap.status_code == 200
    session = client.post(
        "/api/v1/auth/session",
        json={"username": "backtest-404-operator", "password": "password123"},
    )
    assert session.status_code == 200
    headers = {"Authorization": f"Bearer {session.json()['access_token']}"}

    original = backtest_module.PaperBiddingBacktestService.run_historical_backtest

    def _force_missing(self, db, *, operator_id=None, **kwargs):
        return original(self, db, operator_id=999_999, **kwargs)

    monkeypatch.setattr(
        backtest_module.PaperBiddingBacktestService,
        "run_historical_backtest",
        _force_missing,
    )

    response = client.post(
        "/api/v1/backtests/paper-bidding/runs",
        json={"category": "construction", "limit": 5, "persist": False},
        headers=headers,
    )
    assert response.status_code == 404, response.text


# --- (c) operator_id=None keeps canonical default (regression) ---------------


def test_none_operator_id_resolves_canonical_default(test_db):
    _seed_construction_award(test_db)
    canonical = ensure_operator_account(test_db)

    service = PaperBiddingBacktestService()
    result = service.run_historical_backtest(
        test_db,
        operator_id=None,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=True,
    )

    assert result["run_id"] is not None
    assert result["summary"]["candidate_count"] == 1

    from app.models.models import PaperBidRun

    run = test_db.query(PaperBidRun).one()
    assert run.operator_id == canonical.id
