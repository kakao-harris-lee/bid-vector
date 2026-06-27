"""Unit tests for the canonical sequence coercion helpers.

These guard the byte-identical behavior consolidated from
``app/services/prediction_dataset.py``, ``app/services/backtest_cutoff.py`` and
``app/ai/predictors/historical.py``.
"""

from __future__ import annotations

from app.utils.sequence_coercion import (
    coerce_integer_list,
    coerce_numeric_list,
    coerce_sequence,
)


class TestCoerceSequence:
    def test_none_returns_empty(self):
        assert coerce_sequence(None) == []

    def test_list_returned_as_is(self):
        value = [1, "two", 3.0]
        result = coerce_sequence(value)
        assert result == value
        # Same object is returned (no copy) — behavior preserved.
        assert result is value

    def test_valid_json_list_string(self):
        assert coerce_sequence("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_string_returns_empty(self):
        assert coerce_sequence("not json") == []

    def test_non_list_json_returns_empty(self):
        # Valid JSON but not a list (object / scalar) -> empty.
        assert coerce_sequence('{"a": 1}') == []
        assert coerce_sequence("42") == []
        assert coerce_sequence('"hello"') == []

    def test_unsupported_type_returns_empty(self):
        assert coerce_sequence(42) == []
        assert coerce_sequence({"a": 1}) == []


class TestCoerceNumericList:
    def test_none_returns_empty(self):
        assert coerce_numeric_list(None) == []

    def test_list_of_numbers(self):
        assert coerce_numeric_list([1, 2.5, "3"]) == [1.0, 2.5, 3.0]

    def test_json_string(self):
        assert coerce_numeric_list("[1, 2.5, 3]") == [1.0, 2.5, 3.0]

    def test_invalid_json_returns_empty(self):
        assert coerce_numeric_list("not json") == []

    def test_non_list_json_returns_empty(self):
        assert coerce_numeric_list('{"a": 1}') == []

    def test_non_numeric_items_skipped(self):
        assert coerce_numeric_list([1, "x", 3, None]) == [1.0, 3.0]


class TestCoerceIntegerList:
    def test_none_returns_empty(self):
        assert coerce_integer_list(None) == []

    def test_list_of_numbers(self):
        assert coerce_integer_list([1, 2, "3"]) == [1, 2, 3]

    def test_json_string(self):
        assert coerce_integer_list("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_returns_empty(self):
        assert coerce_integer_list("not json") == []

    def test_non_list_json_returns_empty(self):
        assert coerce_integer_list('{"a": 1}') == []

    def test_non_integer_items_skipped(self):
        # JSON floats are coerced with int() (truncation); non-numeric strings
        # raise ValueError and are skipped.
        assert coerce_integer_list([1, "x", 3.9, None]) == [1, 3]
