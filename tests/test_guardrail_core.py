"""Value-table unit tests for the pure guardrail core (PR-16, CLAUDE.md §4.7.4).

These pin the arithmetic of ``app.ai.guardrail_core`` — the floor/ceiling/agency
band resolution, the safe-floor margin, and the price-granularity rounding — that
was extracted verbatim out of ``price_prediction`` when the inline
``settings.PREDICTION_*`` reads were collected into :class:`GuardrailConfig`.

The expected values are the ground truth captured from the pre-extraction code
running against the real settings defaults, so a byte-for-byte behaviour drift in
the core surfaces here as well as in the predictor golden suite.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.ai import guardrail_core as gc
from app.core.config import settings


@pytest.fixture()
def config() -> gc.GuardrailConfig:
    return gc.GuardrailConfig.from_settings(settings)


# ---------------------------------------------------------------------------
# from_settings boundary snapshot
# ---------------------------------------------------------------------------


def test_from_settings_normalizes_boundary_values(config):
    """from_settings applies the same normalization the inline reads used to."""
    assert config.bid_price_granularity == int(settings.PREDICTION_BID_PRICE_GRANULARITY or 0)
    assert config.bid_price_rounding_mode == str(
        settings.PREDICTION_BID_PRICE_ROUNDING_MODE or "floor"
    ).strip().lower()
    assert config.floor_safety_margin_rate == max(
        0.0, float(settings.PREDICTION_FLOOR_SAFETY_MARGIN_RATE or 0.0)
    )
    # frozen dataclass — no accidental mutation path.
    with pytest.raises(Exception):
        config.bid_price_granularity = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# floor resolution (category / group / default / agency)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category, business_group, agency_name, expected",
    [
        ("service", None, None, 0.87),            # category floor
        ("service", "service", None, 0.87),       # group 0.70 cannot undercut category 0.87
        ("construction", "construction", None, 0.87),
        ("goods", None, None, 0.84),
        ("nonexistent-cat", None, None, None),    # no category, default 0.0 -> None
        # agency raises floor above the service category floor(0.87), but the 예정가-basis
        # band is converted to a 기초금액 basis via E[사정률]=0.9952 (base 정합화 P0):
        # 0.8806 → 0.876373 (still > 0.87, so the agency edge binds). NOTE: for the
        # technical-service category (floor 0.88) the converted agency floor no longer
        # raises it — the category floor correctly dominates (guardrail red line).
        ("service", None, "한국수산자원공단동해본부", pytest.approx(0.8806 * 0.9952)),
    ],
)
def test_resolve_floor_bid_rate(config, category, business_group, agency_name, expected):
    assert gc.resolve_floor_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    ) == expected


# ---------------------------------------------------------------------------
# ceiling resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category, business_group, agency_name, expected",
    [
        ("construction", None, None, 0.93),
        ("service", None, None, 1.0),
        ("construction", "construction", None, 0.93),
        # agency lowers ceiling, converted to 기초금액 basis via E[사정률]=0.9952
        # (base 정합화 P0): 0.882 → 0.877766.
        ("technical-service", None, "한국수산자원공단", pytest.approx(0.882 * 0.9952)),
        ("nonexistent-cat", None, None, 1.0),                   # default max 1.0
    ],
)
def test_resolve_ceiling_bid_rate(config, category, business_group, agency_name, expected):
    assert gc.resolve_ceiling_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    ) == expected


# ---------------------------------------------------------------------------
# agency band substring match
# ---------------------------------------------------------------------------


def test_resolve_agency_bid_rate_substring_match():
    rate_map = {"한국수산자원공단": 0.8806}
    assert gc.resolve_agency_bid_rate("한국수산자원공단동해본부", rate_map) == 0.8806
    assert gc.resolve_agency_bid_rate("서울시청", rate_map) is None
    assert gc.resolve_agency_bid_rate(None, rate_map) is None
    assert gc.resolve_agency_bid_rate("한국수산자원공단", None) is None


# ---------------------------------------------------------------------------
# safe floor (floor + margin, clamped to ceiling, never below floor)
# ---------------------------------------------------------------------------


def test_resolve_safe_floor_bid_rate(config):
    # margin default 0.001; ceiling well above -> floor + margin
    assert gc.resolve_safe_floor_bid_rate(config, 0.87, ceiling_bid_rate=1.0) == 0.871
    # ceiling == floor -> margin is clamped away, safe floor stays at the floor
    assert gc.resolve_safe_floor_bid_rate(config, 0.87, ceiling_bid_rate=0.87) == 0.87
    # no floor -> None
    assert gc.resolve_safe_floor_bid_rate(config, None, ceiling_bid_rate=1.0) is None


# ---------------------------------------------------------------------------
# price granularity rounding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "price, mode, floor_price, ceiling_price, expected",
    [
        (12347.0, "floor", None, None, 12340.0),
        (12341.0, "ceil", None, None, 12350.0),
        (12343.0, "nearest", None, None, 12340.0),
        (12300.0, "floor", 12345.0, None, 12350.0),   # bumped up to ceil(floor_price)
        (99999.0, "floor", None, 12345.0, 12340.0),   # capped down to floor(ceiling_price)
    ],
)
def test_round_price_to_granularity(price, mode, floor_price, ceiling_price, expected):
    assert gc.round_price_to_granularity(
        price,
        granularity=10,
        rounding_mode=mode,
        floor_price=floor_price,
        ceiling_price=ceiling_price,
    ) == expected


def test_apply_bid_price_granularity_base_path(config):
    pred = {
        "predicted_price": 12347.0,
        "predicted_bid_rate": 0.617,
        "price_range_min": 12000.0,
        "price_range_max": 13000.0,
    }
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=2_000_000.0, config=config)
    assert rounded["predicted_price"] == 12340.0
    assert rounded["predicted_bid_rate"] == 0.00617
    assert rounded["price_range_min"] == 12000.0
    assert rounded["price_range_max"] == 13000.0
    assert rounded["bid_price_granularity"] == 10
    assert rounded["bid_price_rounding_mode"] == "floor"
    assert rounded["price_granularity_applied"] is True


def test_apply_bid_price_granularity_below_min_budget_quantizes_to_won(config):
    """투찰가 정수 정합: below min_budget no longer skips — it snaps to the 1원 unit.

    Previously a budget under PREDICTION_BID_PRICE_GRANULARITY_MIN_BUDGET returned the
    fractional price unchanged; the integer contract now floors 785.2 -> 785 원 and
    records the honest applied unit (1, not the configured 10).
    """
    pred = {"predicted_price": 785.2, "predicted_bid_rate": 0.617}
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=500.0, config=config)
    assert rounded["predicted_price"] == 785.0
    assert rounded["bid_price_granularity"] == 1
    assert rounded["price_granularity_applied"] is True


def test_apply_bid_price_granularity_zero_granularity_quantizes_to_won(config):
    """granularity=0 (currency snap disabled) still yields an integer 원 price."""
    zero_config = dataclasses.replace(config, bid_price_granularity=0)
    pred = {"predicted_price": 12347.6, "predicted_bid_rate": 0.617}
    # budget >= min_budget, but granularity<=1 -> effective unit is 1원.
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=2_000_000.0, config=zero_config)
    assert rounded["predicted_price"] == 12347.0
    assert rounded["bid_price_granularity"] == 1
    assert rounded["price_granularity_applied"] is True


def test_apply_bid_price_granularity_non_positive_budget_is_noop(config):
    """budget <= 0 remains the sole early return (the bid-rate divides by budget)."""
    pred = {"predicted_price": 785.2, "predicted_bid_rate": 0.617}
    assert gc.apply_bid_price_granularity(dict(pred), budget=0.0, config=config) == pred
    assert gc.apply_bid_price_granularity(dict(pred), budget=-5.0, config=config) == pred


def test_apply_bid_price_granularity_floor_protection_at_won_unit(config):
    """floor 보호(round_price_to_granularity clamp)가 unit=1 에서도 유지된다."""
    # budget < min_budget -> unit=1; price below the safe floor must be bumped UP to
    # the ceil of the floor (integer, never under the floor — guardrail red line).
    pred = {"predicted_price": 785.2, "floor_price": 790.5}
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=1_000.0, config=config)
    assert rounded["predicted_price"] == 791.0
    assert rounded["bid_price_granularity"] == 1


def test_apply_bid_price_granularity_candidate_path_below_min_budget(config):
    """Candidate path also snaps every fractional candidate to the 1원 unit."""
    pred = {
        "predicted_price": 785.2,
        "predicted_bid_rate": 0.617,
        "bid_rate_candidates": [
            {"label": "base", "bid_rate": 0.7852, "predicted_price": 785.2},
            {"label": "safe", "bid_rate": 0.9008, "predicted_price": 900.8},
        ],
    }
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=1_000.0, config=config)
    assert rounded["predicted_price"] == 785.0
    assert rounded["bid_price_granularity"] == 1
    base = rounded["bid_rate_candidates"][0]
    assert base["predicted_price"] == 785.0
    assert base["price_granularity_applied"] is True
    assert base["pre_granularity_price"] == 785.2
    assert rounded["bid_rate_candidates"][1]["predicted_price"] == 900.0


def test_apply_bid_price_granularity_candidate_path(config):
    pred = {
        "predicted_price": 12347.0,
        "predicted_bid_rate": 0.617,
        "price_range_min": 12000.0,
        "price_range_max": 13000.0,
        "bid_rate_candidates": [
            {"label": "base", "bid_rate": 0.006174, "predicted_price": 12347.0},
            {"label": "safe", "bid_rate": 0.0065, "predicted_price": 13000.0},
        ],
    }
    rounded = gc.apply_bid_price_granularity(dict(pred), budget=2_000_000.0, config=config)
    assert rounded["predicted_price"] == 12340.0
    assert rounded["price_range_min"] == 12340.0
    assert rounded["price_range_max"] == 13000.0
    base = rounded["bid_rate_candidates"][0]
    assert base["predicted_price"] == 12340.0
    assert base["price_granularity_applied"] is True
    assert base["pre_granularity_price"] == 12347.0
    assert rounded["bid_rate_candidates"][1]["price_granularity_applied"] is False
