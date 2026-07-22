"""Basis-conversion extraction: golden characterization + module unit tests.

Two jobs:

1. CHARACTERIZATION (golden diff 0): the extraction of the repeated
   ``clamp_bid_rate(X * resolve_band_assessment_rate(agency, config))`` (guardrail_core
   :234/:245/:299) into ``app.domain.basis_conversion.convert_yega_band_to_base`` MUST
   be behavior-preserving byte-for-byte. A representative sweep over category / group /
   agency / 추정가격 / 시행일 (exercising all three call sites: construction tier floor,
   agency floor, agency ceiling — incl. a 사정률 > 1 raise-path) is frozen as a golden
   JSON captured from the pre-extraction code. Post-refactor ``resolve_floor_bid_rate`` /
   ``resolve_ceiling_bid_rate`` reproduce it exactly.

   Regenerate ONLY after an intended behavior change:

       BASIS_GOLDEN_REGEN=1 pytest -q tests/test_basis_conversion.py

2. UNIT / RED LINE: the new pure module (money basis types + the single conversion
   function) is checked directly, including the guardrail red line — a converted agency
   band can never drag ``resolve_floor_bid_rate`` below the hard category floor.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from app.ai.guardrail_core import (
    GuardrailConfig,
    resolve_ceiling_bid_rate,
    resolve_floor_bid_rate,
)
from app.ai.predictors.historical import clamp_bid_rate
from app.domain.basis_conversion import (
    convert_yega_band_to_base,
    resolve_band_assessment_rate,
)
from app.domain.money import BaseAmount, Basis, YegaAmount

GOLDEN_PATH = Path(__file__).parent / "goldens" / "basis_conversion" / "floor_ceiling_sweep.json"
REGEN = os.environ.get("BASIS_GOLDEN_REGEN") == "1"

_MARINE = "한국수산자원공단"


def _config() -> GuardrailConfig:
    """Hermetic sweep config: two agencies with distinct 사정률 (one < 1, one > 1).

    조달청's 1.02 assessment exercises the conversion RAISE-path; 한국수산자원공단's
    0.9952 the LOWER-path. Values are frozen inputs, not tuned defaults.
    """
    return GuardrailConfig(
        bid_price_granularity=10,
        bid_price_granularity_min_budget=1_000_000.0,
        bid_price_rounding_mode="floor",
        floor_safety_margin_rate=0.001,
        business_group_calibration_enabled=True,
        category_minimum_bid_rates={"service": 0.87, "construction": 0.87, "goods": 0.85},
        group_minimum_bid_rates={"service": 0.70, "construction": 0.87},
        default_minimum_bid_rate=0.80,
        agency_minimum_bid_rates={_MARINE: 0.8806, "조달청": 0.90},
        category_maximum_bid_rates={"service": 1.0, "construction": 0.93, "goods": 1.1},
        group_maximum_bid_rates={"service": 1.0, "construction": 0.93},
        default_maximum_bid_rate=1.0,
        agency_maximum_bid_rates={_MARINE: 0.882, "조달청": 0.95},
        agency_band_assessment_rates={_MARINE: 0.9952, "조달청": 1.02},
        default_band_assessment_rate=1.0,
    )


_CATEGORIES = ["service", "construction", "goods", None, "unknown"]
_GROUPS = [None, "service", "construction"]
_AGENCIES = [None, "한국수산자원공단동해본부", "조달청", "서울시청"]
_EST_AMOUNTS = [None, 100_000_000, 3_000_000_000, 30_000_000_000]
_REF_DATES = [None, date(2026, 2, 1), date(2025, 1, 1)]


def _floor_cases() -> list[tuple[str, dict]]:
    cases: list[tuple[str, dict]] = []
    for cat in _CATEGORIES:
        for grp in _GROUPS:
            for ag in _AGENCIES:
                for est in _EST_AMOUNTS:
                    for rd in _REF_DATES:
                        label = f"{cat}|{grp}|{ag}|{est}|{rd.isoformat() if rd else None}"
                        cases.append(
                            (
                                label,
                                {
                                    "category": cat,
                                    "business_group": grp,
                                    "agency_name": ag,
                                    "estimation_amount": est,
                                    "reference_date": rd,
                                },
                            )
                        )
    return cases


def _ceiling_cases() -> list[tuple[str, dict]]:
    cases: list[tuple[str, dict]] = []
    for cat in _CATEGORIES:
        for grp in _GROUPS:
            for ag in _AGENCIES:
                label = f"{cat}|{grp}|{ag}"
                cases.append(
                    (label, {"category": cat, "business_group": grp, "agency_name": ag})
                )
    return cases


def _repr(value: float | None) -> str | None:
    return None if value is None else repr(float(value))


def _compute() -> dict[str, dict[str, str | None]]:
    config = _config()
    floor = {}
    for label, kwargs in _floor_cases():
        floor[label] = _repr(
            resolve_floor_bid_rate(
                config,
                kwargs["category"],
                kwargs["business_group"],
                kwargs["agency_name"],
                estimation_amount=kwargs["estimation_amount"],
                reference_date=kwargs["reference_date"],
            )
        )
    ceiling = {}
    for label, kwargs in _ceiling_cases():
        ceiling[label] = _repr(
            resolve_ceiling_bid_rate(
                config, kwargs["category"], kwargs["business_group"], kwargs["agency_name"]
            )
        )
    return {"floor": floor, "ceiling": ceiling}


def test_floor_ceiling_golden_diff_zero():
    """resolve_floor/ceiling reproduce the frozen pre-extraction golden byte-for-byte."""
    current = _compute()
    if REGEN:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("regenerated basis-conversion golden")

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    mismatches = []
    for section in ("floor", "ceiling"):
        assert set(current[section]) == set(golden[section]), (
            f"{section} case-set drifted from golden"
        )
        for label, value in current[section].items():
            if value != golden[section][label]:
                mismatches.append(f"{section}[{label}]: {golden[section][label]!r} -> {value!r}")
    assert not mismatches, "golden drift:\n" + "\n".join(mismatches)


# ---------------------------------------------------------------------------
# money.py — basis value types
# ---------------------------------------------------------------------------
def test_basis_enum_values_match_codebase_literals():
    assert Basis.PLANNED_PRICE.value == "planned_price"
    assert Basis.BASE_AMOUNT.value == "base_amount"
    assert Basis.WINNING_AMOUNT.value == "winning_amount"
    assert Basis.BUDGET_ESTIMATE.value == "budget_estimate"
    # str-mixed Enum: the member IS its value string for serialization/compare.
    assert Basis.BASE_AMOUNT == "base_amount"


def test_newtype_amounts_are_runtime_floats():
    base = BaseAmount(1_000_000.0)
    yega = YegaAmount(995_200.0)
    assert isinstance(base, float) and isinstance(yega, float)
    assert base == 1_000_000.0 and yega == 995_200.0


# ---------------------------------------------------------------------------
# basis_conversion.convert_yega_band_to_base — extraction equivalence + red line
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("agency", [None, "한국수산자원공단동해본부", "조달청", "서울시청"])
@pytest.mark.parametrize("rate", [0.70, 0.8806, 0.90, 1.0, 1.35, 2.0])
def test_convert_equals_inline_formula(agency, rate):
    """convert_yega_band_to_base == the exact pre-extraction inline expression."""
    config = _config()
    expected = clamp_bid_rate(rate * resolve_band_assessment_rate(agency, config))
    assert convert_yega_band_to_base(rate, agency, config) == expected


def test_convert_raise_path_when_assessment_above_one():
    """A 사정률 > 1 (조달청 1.02) RAISES the band (within the hard clamp)."""
    config = _config()
    raw = 0.90
    converted = convert_yega_band_to_base(raw, "조달청", config)
    assert converted == clamp_bid_rate(raw * 1.02)
    assert converted > raw  # 0.90 * 1.02 = 0.918, still inside [0.7, 1.4]


def test_convert_lower_path_when_assessment_below_one():
    config = _config()
    raw = 0.8806
    converted = convert_yega_band_to_base(raw, "한국수산자원공단동해본부", config)
    assert converted == clamp_bid_rate(raw * 0.9952)
    assert converted < raw


def test_convert_default_assessment_is_noop():
    """Non-matching agency -> default 사정률 1.0 -> conversion is just the clamp."""
    config = _config()
    assert convert_yega_band_to_base(0.90, "서울시청", config) == clamp_bid_rate(0.90)
    assert convert_yega_band_to_base(0.90, None, config) == clamp_bid_rate(0.90)


def test_convert_clamps_to_hard_band_bounds():
    """The [0.7, 1.4] clamp is preserved (guardrail 우회 금지 — band edge stays bounded)."""
    config = _config()
    assert convert_yega_band_to_base(5.0, "조달청", config) == 1.4
    assert convert_yega_band_to_base(0.1, None, config) == 0.7


def test_floor_never_undercuts_category_floor_red_line():
    """RED LINE: the converted agency band can never drag the floor below the hard
    category floor — resolve_floor_bid_rate re-applies max(category/group/legal)."""
    config = _config()
    category_floor = {"service": 0.87, "construction": 0.87, "goods": 0.85}
    for label, kwargs in _floor_cases():
        floor = resolve_floor_bid_rate(
            config,
            kwargs["category"],
            kwargs["business_group"],
            kwargs["agency_name"],
            estimation_amount=kwargs["estimation_amount"],
            reference_date=kwargs["reference_date"],
        )
        hard = category_floor.get(kwargs["category"])
        if hard is not None and floor is not None:
            assert floor >= hard - 1e-12, f"{label}: floor {floor} < category floor {hard}"
