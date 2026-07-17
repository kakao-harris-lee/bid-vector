"""Red-line tests for the construction legal 낙찰하한 tier in guardrail_core.

The construction 적격심사 낙찰하한 (2026-01-30 +2%p) is folded into
``resolve_floor_bid_rate`` with ``max()`` only. These tests pin:

  (a) the tier value per bracket × effective date (예정가 → 기초금액 via E[사정률]),
  (b) the RED LINE — for EVERY tier × date the resolved floor is ≥ the pre-tier
      floor (flat 0.87), incl. old-rate tiers below 0.87 that the flat floor holds,
  (c) the E[사정률] conversion still leaves the new-rate minimum tier above 0.87
      (0.87495 × 0.9952 = 0.87075 > 0.87), so the tier itself binds — not just the
      flat floor,
  (d) non-construction categories are untouched (no cross-category impact),
  (e) a higher request-injected legal_floor still wins (max over all sources).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.ai.guardrail_core import GuardrailConfig, resolve_floor_bid_rate
from app.ai.price_prediction import predict_price

_EOK = 100_000_000.0
_OLD_DATE = date(2026, 1, 29)
_NEW_DATE = date(2026, 1, 30)
_ASSESSMENT = 0.9952  # E[사정률] sample from the marine postmortem notices


def _config(**overrides) -> GuardrailConfig:
    """Hermetic construction-focused guardrail config (flat floor 0.87)."""
    base = GuardrailConfig(
        bid_price_granularity=10,
        bid_price_granularity_min_budget=1_000_000.0,
        bid_price_rounding_mode="floor",
        floor_safety_margin_rate=0.001,
        business_group_calibration_enabled=True,
        category_minimum_bid_rates={"service": 0.87, "construction": 0.87},
        group_minimum_bid_rates={"service": 0.70, "construction": 0.87},
        default_minimum_bid_rate=0.0,
        agency_minimum_bid_rates=None,
        category_maximum_bid_rates={"service": 1.0, "construction": 0.93},
        group_maximum_bid_rates={"service": 1.0, "construction": 0.93},
        default_maximum_bid_rate=1.0,
        agency_maximum_bid_rates=None,
        agency_band_assessment_rates=None,
        default_band_assessment_rate=1.0,
    )
    return replace(base, **overrides)


# All (bracket, date) combinations and their raw 예정가-basis tier value.
_TIER_RAW = {
    (5 * _EOK, _NEW_DATE): 0.89745,
    (30 * _EOK, _NEW_DATE): 0.88745,
    (80 * _EOK, _NEW_DATE): 0.87495,
    (5 * _EOK, _OLD_DATE): 0.87745,
    (30 * _EOK, _OLD_DATE): 0.86745,
    (80 * _EOK, _OLD_DATE): 0.85495,
}


# ---------------------------------------------------------------------------
# (a) tier value with the default 사정률 (1.0 == no conversion)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key, raw", list(_TIER_RAW.items()))
def test_tier_floor_value_default_assessment(key, raw):
    amount, reference_date = key
    cfg = _config()  # default_band_assessment_rate == 1.0 → no conversion
    resolved = resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=amount, reference_date=reference_date
    )
    # floor = max(flat 0.87, raw × 1.0)
    assert resolved == pytest.approx(max(0.87, raw))


# ---------------------------------------------------------------------------
# (b) RED LINE — the tier can NEVER lower the floor (flat 0.87 preserved)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", list(_TIER_RAW))
@pytest.mark.parametrize("assessment", [1.0, _ASSESSMENT, 0.99, 1.01])
def test_red_line_floor_never_decreases(key, assessment):
    amount, reference_date = key
    cfg = _config(default_band_assessment_rate=assessment)
    before = resolve_floor_bid_rate(cfg, "construction")  # no tier
    after = resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=amount, reference_date=reference_date
    )
    assert before == pytest.approx(0.87)
    assert after >= before - 1e-12  # monotonic non-decreasing for every input


def test_old_rate_below_flat_is_held_by_flat_floor():
    # 구율 50억~100억 = 0.85495 < 0.87 → the flat category floor wins (status quo).
    cfg = _config()
    resolved = resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=80 * _EOK, reference_date=_OLD_DATE
    )
    assert resolved == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# (c) E[사정률] conversion still leaves the new-rate MINIMUM tier above 0.87
# ---------------------------------------------------------------------------
def test_new_rate_min_tier_binds_above_flat_after_assessment():
    cfg = _config(default_band_assessment_rate=_ASSESSMENT)
    resolved = resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=80 * _EOK, reference_date=_NEW_DATE
    )
    # 0.87495 × 0.9952 = 0.87075 — still ABOVE the flat 0.87, so the tier binds.
    assert resolved == pytest.approx(0.87495 * _ASSESSMENT)
    assert resolved > 0.87


# ---------------------------------------------------------------------------
# (d) non-construction categories are untouched
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category, expected", [("service", 0.87), ("goods", None)])
def test_non_construction_ignores_tier(category, expected):
    cfg = _config(category_minimum_bid_rates={"service": 0.87})
    resolved = resolve_floor_bid_rate(
        cfg, category, estimation_amount=30 * _EOK, reference_date=_NEW_DATE
    )
    if expected is None:
        assert resolved is None
    else:
        assert resolved == pytest.approx(expected)


def test_no_estimation_amount_preserves_flat_floor():
    cfg = _config()
    assert resolve_floor_bid_rate(cfg, "construction") == pytest.approx(0.87)
    # estimation without a date → tier not applied (leakage guard).
    assert resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=30 * _EOK
    ) == pytest.approx(0.87)


def test_hundred_eok_is_jonghsim_no_tier():
    cfg = _config()
    # 100억+ 종심제 → resolver None → flat floor preserved.
    assert resolve_floor_bid_rate(
        cfg, "construction", estimation_amount=150 * _EOK, reference_date=_NEW_DATE
    ) == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# (e) request-injected legal_floor wins when higher (end-to-end via predict_price)
# ---------------------------------------------------------------------------
def test_injected_legal_floor_wins_over_tier(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "PREDICTION_CATEGORY_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(
        settings, "PREDICTION_GROUP_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {})

    records = [{"bid_rate": 0.80, "base_amount": 100_000_000.0} for _ in range(20)]
    result = predict_price(
        budget=3_000_000_000.0,
        category="construction",
        description="OO 토목공사",
        historical_records=records,
        business_group="construction",
        legal_floor_bid_rate=0.95,  # higher than the 0.88745 tier
        estimation_amount=3_000_000_000.0,  # 30억 → 10~50억 신율 tier
        reference_date=_NEW_DATE,
    )
    assert result["floor_bid_rate"] == pytest.approx(0.95)


def test_tier_raises_floor_end_to_end(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "PREDICTION_CATEGORY_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(
        settings, "PREDICTION_GROUP_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {})

    records = [{"bid_rate": 0.80, "base_amount": 100_000_000.0} for _ in range(20)]
    # 5억 (<10억) 신율 tier = 0.89745. Without the tier the floor is the flat 0.87.
    result = predict_price(
        budget=500_000_000.0,
        category="construction",
        description="OO 토목공사",
        historical_records=records,
        business_group="construction",
        estimation_amount=500_000_000.0,
        reference_date=_NEW_DATE,
    )
    assert result["guardrail_applied"] is True
    assert result["floor_bid_rate"] == pytest.approx(0.89745)
    # every candidate lands at/above the tier floor — nothing prices below 낙찰하한.
    for candidate in result["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= 0.89745 - 1e-9, candidate
