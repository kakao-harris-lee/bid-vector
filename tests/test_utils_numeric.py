"""Characterization tests for the canonical scalar coercion helpers.

These pin the behavior that was consolidated into ``app/utils/numeric.py`` from
six copies (``optional_float``/``_safe_optional_int``/``_coerce_amount``/
``_coerce_float``/``_as_float``/``amount_float``). The two source algorithms
differed only in a redundant guard — the float copies bailed out on
``value in (None, "")`` while the others bailed out on ``value is None`` — and
the table below covers the inputs where that guard could have mattered.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from app.utils.numeric import coerce_or_none, optional_float, optional_int

# (input, expected float, expected int) — expectations are the pre-consolidation
# results of both source algorithms, verified to be identical for every row.
COERCION_CASES = [
    (None, None, None),
    ("", None, None),
    (" ", None, None),
    ("abc", None, None),
    ("0", 0.0, 0),
    ("42", 42.0, 42),
    (" 2.5 ", 2.5, None),  # int() rejects the fractional string, float() does not
    ("1e3", 1000.0, None),
    (0, 0.0, 0),  # falsy but not None — must survive coercion
    (0.0, 0.0, 0),
    (-1, -1.0, -1),
    (1.9, 1.9, 1),  # int() truncates toward zero
    (True, 1.0, 1),
    (False, 0.0, 0),
    (Decimal("1.25"), 1.25, 1),
    (b"3", 3.0, 3),
    (b"", None, None),
    ([], None, None),
    ({}, None, None),
    ((), None, None),
]


@pytest.mark.parametrize(("value", "expected_float", "expected_int"), COERCION_CASES)
class TestCoercionTable:
    def test_optional_float(self, value, expected_float, expected_int):
        assert optional_float(value) == expected_float

    def test_optional_int(self, value, expected_float, expected_int):
        assert optional_int(value) == expected_int

    def test_interpreter_matches_named_wrappers(
        self, value, expected_float, expected_int
    ):
        assert coerce_or_none(value, float) == expected_float
        assert coerce_or_none(value, int) == expected_int


class TestCoerceOrNone:
    def test_none_skips_the_cast_entirely(self):
        calls: list[object] = []

        def recording_cast(value: object) -> object:
            calls.append(value)
            return value

        assert coerce_or_none(None, recording_cast) is None
        assert calls == []

    def test_arbitrary_cast_is_supported(self):
        assert coerce_or_none("7", str) == "7"
        assert coerce_or_none(7, str) == "7"

    def test_only_type_and_value_errors_are_swallowed(self):
        def exploding_cast(value: object) -> object:
            raise KeyError("not swallowed")

        with pytest.raises(KeyError):
            coerce_or_none("x", exploding_cast)

    def test_non_finite_floats_pass_through(self):
        assert math.isnan(optional_float("nan"))
        assert math.isnan(optional_float(float("nan")))
        assert optional_float("inf") == math.inf
        assert optional_float(float("-inf")) == -math.inf

    def test_non_finite_floats_under_int(self):
        # NaN raises ValueError (swallowed); infinity raises OverflowError, which
        # the original copies did not catch either — it still propagates.
        assert optional_int(float("nan")) is None
        with pytest.raises(OverflowError):
            optional_int(float("inf"))


class TestConsolidatedCallPaths:
    """The former copies must keep their public names and their behavior."""

    @pytest.mark.parametrize(
        ("value", "expected_float", "expected_int"), COERCION_CASES
    )
    def test_guardrail_core_optional_float(self, value, expected_float, expected_int):
        from app.ai.guardrail_core import optional_float as guardrail_optional_float

        assert guardrail_optional_float(value) == expected_float

    @pytest.mark.parametrize(
        ("value", "expected_float", "expected_int"), COERCION_CASES
    )
    def test_sample_gap_safe_optional_int(self, value, expected_float, expected_int):
        from app.services.synthetic_experiment.sample_gap import _safe_optional_int

        assert _safe_optional_int(value) == expected_int

    def test_synthetic_experiment_package_reexport_is_intact(self):
        from app.services.synthetic_experiment import _safe_optional_int as reexported
        from app.services.synthetic_experiment.sample_gap import _safe_optional_int

        assert reexported is _safe_optional_int

    def test_legal_floor_resolver_still_coerces_its_amount(self):
        # Former ``legal_floor_spec._coerce_amount`` call path: the resolver takes
        # the estimation amount through the coercion before the 구간 lookup, so a
        # numeric string must resolve exactly like the float it denotes.
        from app.ai.predictors.legal_floor_spec import (
            resolve_construction_qualification_floor,
        )

        reference_date = date(2026, 1, 1)
        expected = resolve_construction_qualification_floor(
            5_000_000_000.0, reference_date
        )
        assert expected is not None
        assert (
            resolve_construction_qualification_floor("5000000000", reference_date)
            == expected
        )
        assert resolve_construction_qualification_floor(None, reference_date) is None
        assert resolve_construction_qualification_floor("abc", reference_date) is None
        assert resolve_construction_qualification_floor("", reference_date) is None

    def test_rate_to_fraction_still_coerces_its_input(self):
        # Former ``award_verification._coerce_float`` call path.
        from app.services.award_verification import _rate_to_fraction

        assert _rate_to_fraction("88.5") == _rate_to_fraction(88.5)
        assert _rate_to_fraction(None) is None
        assert _rate_to_fraction("") is None
        assert _rate_to_fraction("abc") is None
        assert _rate_to_fraction(0) is None  # non-positive stays rejected
