from datetime import timedelta

from app.ai.bid_target import BidTargetSignals
from app.core.time import utc_now
from app.models.models import Project, TenderResult
from app.services.bid_target_signals import resolve_bid_target_signals


def _seed(db, agency, category, rates):
    now = utc_now()
    for i, rate in enumerate(rates):
        p = Project(title=f"n{i}", category=category, issuing_agency=agency, budget_estimate=1000.0)
        db.add(p)
        db.flush()
        db.add(TenderResult(
            project_id=p.id, winning_company="w", winning_amount=880.0,
            winning_rate=rate, announced_at=now - timedelta(days=10),
        ))
    db.commit()


def test_insufficient_samples_marks_not_sufficient(test_db):
    _seed(test_db, "한국수산자원공단동해본부", "service", [0.88, 0.89, 0.90])  # < min_samples
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert isinstance(sig, BidTargetSignals)
    assert sig.data_sufficient is False
    assert sig.win_rate_dispersion is None


def test_sufficient_samples_returns_stddev(test_db):
    rates = [0.86, 0.88, 0.90, 0.87, 0.89, 0.885, 0.895, 0.875, 0.905, 0.865]
    _seed(test_db, "한국수산자원공단동해본부", "service", rates)
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert sig.data_sufficient is True
    assert sig.win_rate_dispersion is not None and sig.win_rate_dispersion > 0


def test_other_agency_not_counted(test_db):
    _seed(test_db, "서울특별시", "service", [0.86, 0.88, 0.90, 0.87, 0.89, 0.885, 0.895, 0.875, 0.905, 0.865])
    sig = resolve_bid_target_signals(test_db, agency_name="한국수산자원공단동해본부", category="service")
    assert sig.data_sufficient is False
