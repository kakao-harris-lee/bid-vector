"""Value-table tests for the base_amount provenance classifier + estimator."""
from __future__ import annotations

import json

import pytest

from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_VAT,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    RESERVE_PRICE_COUNT,
    classify_base_basis,
    estimate_base_amount_from_reserves,
    normalize_winning_rate,
)

# Real postmortem case: 43,996,200 ÷ 0.88035 = 49,975,805.0775 (예정가-basis 오염).
_YEGA_BASE = 43_996_200 / 0.88035
# 45,000,001 (VAT-inclusive, not ÷11) ÷ 1.1 = non-integer whose ×1.1 is integer.
_VAT_BASE = 45_000_001 / 1.1


@pytest.mark.parametrize(
    "base, winning_amount, winning_rate, expected",
    [
        # clean: strictly positive exact integer 원화
        (43_996_200.0, 0, 0, BASIS_CLEAN),
        (43_996_200, None, None, BASIS_CLEAN),
        # integer wins even when a winning result is present
        (43_996_200.0, 43_000_000, 0.977, BASIS_CLEAN),
        # derived-yega: base × winning_rate == winning_amount
        (_YEGA_BASE, 43_996_200, 0.88035, BASIS_DERIVED_YEGA),
        # derived-vat: base × 1.1 lands on an integer, no winning result
        (_VAT_BASE, 0, 0, BASIS_DERIVED_VAT),
        # suspect-fractional: fractional, not yega, not vat
        (12_345_678.4321, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        # guards: winning present but rate 0/None ⇒ yega skipped (no crash)
        (_YEGA_BASE, 43_996_200, 0, BASIS_SUSPECT_FRACTIONAL),
        (_YEGA_BASE, 43_996_200, None, BASIS_SUSPECT_FRACTIONAL),
        # guards: rate present but winning missing ⇒ yega skipped
        (_YEGA_BASE, None, 0.88035, BASIS_SUSPECT_FRACTIONAL),
        (_YEGA_BASE, 0, 0.88035, BASIS_SUSPECT_FRACTIONAL),
        # guards: missing / non-positive / non-numeric base ⇒ NOT clean
        (None, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        (0.0, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        (-5.0, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        ("not-a-number", 0, 0, BASIS_SUSPECT_FRACTIONAL),
    ],
)
def test_classify_base_basis(base, winning_amount, winning_rate, expected):
    assert classify_base_basis(base, winning_amount, winning_rate) == expected


def test_classify_defaults_missing_winning_args():
    """winning_amount/winning_rate default to None without raising."""
    assert classify_base_basis(43_996_200.0) == BASIS_CLEAN
    assert classify_base_basis(_YEGA_BASE) == BASIS_SUSPECT_FRACTIONAL


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.88035, 0.88035),  # already a fraction (scsbid) — passthrough
        (88.035, 0.88035),  # percentage (HTML parsing) — divided by 100
        (0.4, 0.4),  # genuinely low fraction — no range gate
        (150.0, 1.5),  # percentage boundary case
        (1.5, 1.5),  # at threshold — not divided
        (0, None),  # non-positive → None
        (-5.0, None),
        (None, None),
        ("x", None),  # non-numeric → None
    ],
)
def test_normalize_winning_rate(value, expected):
    result = normalize_winning_rate(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def _reserves(base: float, count: int = RESERVE_PRICE_COUNT) -> list[float]:
    """Build ``count`` reserve prices straddling ``base`` by ~±2.5%."""
    spread = base * 0.025
    step = (2 * spread) / (count - 1)
    return [round(base - spread + step * i) for i in range(count)]


def test_estimate_from_15_reserves_returns_midpoint():
    base = 1_000_000_000.0
    reserves = _reserves(base)
    expected = round((min(reserves) + max(reserves)) / 2)
    assert estimate_base_amount_from_reserves(reserves) == expected
    # midpoint recovers base to within the ±2.5% band's rounding
    assert abs(estimate_base_amount_from_reserves(reserves) - base) <= 1


def test_estimate_accepts_json_string_column():
    reserves = _reserves(500_000_000.0)
    from_list = estimate_base_amount_from_reserves(reserves)
    from_json = estimate_base_amount_from_reserves(json.dumps(reserves))
    assert from_json == from_list
    assert from_json is not None


def test_estimate_requires_full_15():
    reserves = _reserves(1_000_000.0)[:14]
    assert estimate_base_amount_from_reserves(reserves) is None


@pytest.mark.parametrize("raw", [None, "", "[]", "not json", "{}", 12345])
def test_estimate_unrecoverable_inputs_return_none(raw):
    assert estimate_base_amount_from_reserves(raw) is None


def test_estimate_drops_nonpositive_and_nonnumeric_below_threshold():
    """A list padded with junk/zeros that leaves < 15 valid reserves ⇒ None."""
    reserves = _reserves(2_000_000.0)[:14] + [0, -1, "x", None]
    assert estimate_base_amount_from_reserves(reserves) is None
