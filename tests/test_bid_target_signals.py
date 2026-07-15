import math
from datetime import timedelta

import pytest

from app.ai.bid_target import (
    _BASE_ADJUSTMENT,
    _resolve_position_adjustment,
    BidTargetSignals,
)
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


def _pop_stddev(fractions):
    """Population stddev of already-fraction-scale winning rates (expected value)."""
    n = len(fractions)
    mean = sum(fractions) / n
    variance = sum((x - mean) ** 2 for x in fractions) / n
    return math.sqrt(variance)


# Fraction-scale reference distribution shared by the scale-normalization tests.
_FRACTIONS = [0.86, 0.88, 0.90, 0.87, 0.89, 0.885, 0.895, 0.875, 0.905, 0.865]


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


def test_percentage_scale_matches_fraction_scale_and_does_not_saturate(test_db):
    """Percentage-scale winning_rate (87.5, 88.5, …) must normalize to the same
    fraction-scale dispersion — never a blown-up ~1.4 that saturates the band."""
    percentages = [f * 100 for f in _FRACTIONS]
    _seed(test_db, "한국수산자원공단동해본부", "service", percentages)
    sig = resolve_bid_target_signals(
        test_db, agency_name="한국수산자원공단동해본부", category="service"
    )
    assert sig.data_sufficient is True
    expected = _pop_stddev(_FRACTIONS)
    assert sig.win_rate_dispersion == pytest.approx(expected, rel=1e-6, abs=1e-9)
    # Fraction-scale dispersion (~0.014) must NOT saturate the position to the
    # band midpoint (0.5); it should still lift above the neutral base anchor.
    adjustment = _resolve_position_adjustment(sig)
    assert adjustment < 0.5
    assert adjustment > _BASE_ADJUSTMENT


def test_mixed_scale_rows_yield_sane_fraction_dispersion(test_db):
    """Some fraction rows + some percentage rows for one agency must aggregate to
    the fraction-scale dispersion, not a garbage mixed-scale spread."""
    mixed = [0.86, 88.0, 0.90, 87.0, 0.89, 88.5, 0.895, 87.5, 0.905, 0.865]
    _seed(test_db, "한국수산자원공단동해본부", "service", mixed)
    sig = resolve_bid_target_signals(
        test_db, agency_name="한국수산자원공단동해본부", category="service"
    )
    assert sig.data_sufficient is True
    assert sig.win_rate_dispersion == pytest.approx(
        _pop_stddev(_FRACTIONS), rel=1e-6, abs=1e-9
    )
    # Sanity: a sane fraction-scale spread, nowhere near the raw-mix blow-up.
    assert sig.win_rate_dispersion < 0.05


def test_out_of_range_stray_row_excluded_from_aggregate(test_db):
    """A stray winning_rate (200.0 → normalized 2.0, outside [0.5, 1.5]) must be
    dropped by the normalized-range gate — counted neither in samples nor mean."""
    # 7 valid fraction rows (< default min_samples=8) plus one stray. If the stray
    # were counted the sample count would reach 8 and become sufficient.
    seven_valid = _FRACTIONS[:7]
    _seed(test_db, "한국수산자원공단동해본부", "service", seven_valid + [200.0])
    sig = resolve_bid_target_signals(
        test_db, agency_name="한국수산자원공단동해본부", category="service"
    )
    assert sig.data_sufficient is False
    assert sig.win_rate_dispersion is None


def test_stray_row_does_not_inflate_sufficient_aggregate(test_db):
    """With enough valid rows, an out-of-range stray leaves the dispersion intact."""
    eight_valid = _FRACTIONS[:8]
    _seed(test_db, "서울특별시", "service", eight_valid + [200.0])
    sig = resolve_bid_target_signals(
        test_db, agency_name="서울특별시", category="service"
    )
    assert sig.data_sufficient is True
    assert sig.win_rate_dispersion == pytest.approx(
        _pop_stddev(eight_valid), rel=1e-6, abs=1e-9
    )
