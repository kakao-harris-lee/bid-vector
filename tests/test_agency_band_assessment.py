"""Characterization tests for the agency-band base 정합화 (P0).

INTENDED behavior change (not a refactor): the agency bid-rate band
(PREDICTION_AGENCY_*_BID_RATES) was calibrated as 낙찰가/예정가, but the price
guardrail multiplies it by the notice 사업금액(기초금액, #162). 예정가 sits a few
tenths of a percent BELOW 기초금액, so a 예정가-basis band applied to 기초금액 prices
~+0.5%p too high (postmortem: 52위/25위). The fix converts the band to a 기초금액
basis with E[사정률] = E[예정가/기초금액]:

    기초금액-기준 목표율 = 예정가-기준 밴드율 × E[사정률]

These tests pin the conversion, prove it only LOWERS (never RAISES) the agency
band, and prove the category/legal 낙찰하한 guardrail is UNTOUCHED (a converted
agency floor can never undercut the hard category floor).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai import guardrail_core
from app.ai.guardrail_core import (
    GuardrailConfig,
    resolve_band_assessment_rate,
    resolve_ceiling_bid_rate,
    resolve_floor_bid_rate,
)
from app.ai.price_prediction import predict_price

_MARINE = "한국수산자원공단"
_MARINE_MIN = 0.8806
_MARINE_MAX = 0.882
_MARINE_ASSESSMENT = 0.9952  # mean(99.782%, 99.260%) from the two postmortem notices


def _config(**overrides) -> GuardrailConfig:
    """A hermetic guardrail config keyed on the marine agency band."""
    base = GuardrailConfig(
        bid_price_granularity=10,
        bid_price_granularity_min_budget=1_000_000.0,
        bid_price_rounding_mode="floor",
        floor_safety_margin_rate=0.001,
        business_group_calibration_enabled=True,
        category_minimum_bid_rates={"service": 0.87, "construction": 0.87},
        group_minimum_bid_rates={"service": 0.70, "construction": 0.87},
        default_minimum_bid_rate=0.0,
        agency_minimum_bid_rates={_MARINE: _MARINE_MIN},
        category_maximum_bid_rates={"service": 1.0, "construction": 0.93},
        group_maximum_bid_rates={"service": 1.0, "construction": 0.93},
        default_maximum_bid_rate=1.0,
        agency_maximum_bid_rates={_MARINE: _MARINE_MAX},
        agency_band_assessment_rates={_MARINE: _MARINE_ASSESSMENT},
        default_band_assessment_rate=1.0,
    )
    return replace(base, **overrides)


# ---------------------------------------------------------------------------
# resolve_band_assessment_rate — the pure E[사정률] lookup
# ---------------------------------------------------------------------------
def test_assessment_rate_matches_configured_agency():
    assert resolve_band_assessment_rate(_MARINE, _config()) == _MARINE_ASSESSMENT


def test_assessment_rate_matches_regional_bureau_by_substring():
    # Regional bureaus inherit the HQ 사정률 via normalized substring match.
    assert resolve_band_assessment_rate("한국수산자원공단 동해본부", _config()) == _MARINE_ASSESSMENT


def test_assessment_rate_falls_back_to_default_noop():
    # An agency without a calibrated 사정률 uses the 1.0 default (no conversion).
    assert resolve_band_assessment_rate("제주대학교", _config()) == 1.0


def test_assessment_rate_none_agency_is_noop():
    assert resolve_band_assessment_rate(None, _config()) == 1.0


def test_assessment_rate_non_positive_collapses_to_one():
    # A misconfigured 0/negative default can never RAISE the band.
    cfg = _config(agency_band_assessment_rates={}, default_band_assessment_rate=0.0)
    assert resolve_band_assessment_rate("제주대학교", cfg) == 1.0


# ---------------------------------------------------------------------------
# resolve_floor / resolve_ceiling — the band edges are converted, not raw
# ---------------------------------------------------------------------------
def test_agency_floor_is_converted_to_business_amount_basis():
    resolved = resolve_floor_bid_rate(_config(), "service", agency_name=_MARINE)
    assert resolved == pytest.approx(_MARINE_MIN * _MARINE_ASSESSMENT)  # 0.876373
    # Strictly BELOW the raw band edge — the whole point of the alignment.
    assert resolved < _MARINE_MIN


def test_agency_ceiling_is_converted_to_business_amount_basis():
    resolved = resolve_ceiling_bid_rate(_config(), "service", agency_name=_MARINE)
    assert resolved == pytest.approx(_MARINE_MAX * _MARINE_ASSESSMENT)  # 0.877766
    assert resolved < _MARINE_MAX


def test_converted_floor_recovers_yega_basis_target():
    """Divided back by E[사정률] the converted floor equals the calibrated 예정가 target.

    That is the design invariant: the band MINIMUM 0.8806 was meant as
    "88.06% of 예정가" (the sweep sweet spot), and the conversion makes rate × 기초금액
    land there once the mean 사정률 is undone.
    """
    resolved = resolve_floor_bid_rate(_config(), "service", agency_name=_MARINE)
    assert resolved / _MARINE_ASSESSMENT == pytest.approx(_MARINE_MIN)


def test_non_configured_agency_band_is_unchanged():
    # 제주대학교 is not in the assessment map → default 1.0 → no conversion, so a
    # band configured for it would resolve verbatim.
    cfg = _config(
        agency_minimum_bid_rates={"제주대학교": 0.90},
        agency_maximum_bid_rates={"제주대학교": 0.93},
    )
    assert resolve_floor_bid_rate(cfg, "service", agency_name="제주대학교") == pytest.approx(0.90)
    assert resolve_ceiling_bid_rate(cfg, "service", agency_name="제주대학교") == pytest.approx(0.93)


# ---------------------------------------------------------------------------
# Guardrail red line: the conversion can never undercut the category floor
# ---------------------------------------------------------------------------
def test_conversion_never_undercuts_category_floor():
    # Category floor 0.88 sits ABOVE the converted agency floor 0.876373 — the hard
    # floor must win (max), so the alignment can never push a bid below 낙찰하한.
    cfg = _config(category_minimum_bid_rates={"service": 0.88})
    resolved = resolve_floor_bid_rate(cfg, "service", agency_name=_MARINE)
    assert resolved == pytest.approx(0.88)
    assert resolved > _MARINE_MIN * _MARINE_ASSESSMENT


def test_converted_floor_still_binds_above_default_category_floor():
    # With the real default service floor (0.87) the converted agency floor (0.876373)
    # still binds, so floor_from_agency attribution and the #164 menu keep working.
    resolved = resolve_floor_bid_rate(_config(), "service", agency_name=_MARINE)
    assert resolved > 0.87


# ---------------------------------------------------------------------------
# End-to-end predict_price on the real postmortem 사업금액
# ---------------------------------------------------------------------------
@pytest.fixture
def _pin_marine_band(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MINIMUM_BID_RATES", {_MARINE: _MARINE_MIN})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MAXIMUM_BID_RATES", {_MARINE: _MARINE_MAX})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {_MARINE: _MARINE_ASSESSMENT})
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_FLOOR_SAFETY_MARGIN_RATE", 0.001)
    monkeypatch.setattr(settings, "PREDICTION_BID_PRICE_GRANULARITY", 10)


def test_predict_price_lands_in_converted_band(_pin_marine_band):
    budget = 88_042_000.0  # real 사업금액 from the postmortem notice
    result = predict_price(
        budget=budget,
        category="service",
        description="어초어장 조성 관리 용역",
        historical_records=tuple({"bid_rate": 0.905, "base_amount": budget} for _ in range(20)),
        agency_name=_MARINE,
        business_group="service",
    )
    # Converted band edges, not the raw 0.8806/0.882.
    assert result["floor_bid_rate"] == pytest.approx(_MARINE_MIN * _MARINE_ASSESSMENT, abs=1e-6)
    assert result["ceiling_bid_rate"] == pytest.approx(_MARINE_MAX * _MARINE_ASSESSMENT, abs=1e-6)
    assert result["floor_from_agency"] is True
    assert result["ceiling_from_agency"] is True
    # The recommendation is strictly BELOW where the raw (unaligned) band would have
    # priced it — the fix direction for the 52위/25위 postmortem.
    raw_ceiling_price = _MARINE_MAX * budget
    assert result["predicted_price"] < raw_ceiling_price


def test_predict_price_guardrail_floor_still_protects(_pin_marine_band):
    """Even with the conversion, a legal floor ABOVE the converted band still binds
    — the guardrail 낙찰하한 red line is never weakened by base 정합화."""
    budget = 88_042_000.0
    result = predict_price(
        budget=budget,
        category="service",
        description="어초어장 조성 관리 용역",
        historical_records=tuple({"bid_rate": 0.905, "base_amount": budget} for _ in range(20)),
        agency_name=_MARINE,
        business_group="service",
        legal_floor_bid_rate=0.90,  # above the converted agency band
    )
    assert result["floor_bid_rate"] >= 0.90 - 1e-9
    for candidate in result["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= 0.90 - 1e-9, candidate


# ---------------------------------------------------------------------------
# The shim in price_prediction resolves through the same converted core
# ---------------------------------------------------------------------------
def test_price_prediction_shim_uses_converted_band(monkeypatch):
    from app.core.config import settings
    from app.ai import price_prediction

    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MINIMUM_BID_RATES", {_MARINE: _MARINE_MIN})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {_MARINE: _MARINE_ASSESSMENT})
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    resolved = price_prediction._resolve_floor_bid_rate("service", agency_name=_MARINE)
    assert resolved == pytest.approx(_MARINE_MIN * _MARINE_ASSESSMENT)
