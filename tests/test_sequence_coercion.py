"""Unit tests for the canonical sequence coercion helpers.

These guard the byte-identical behavior consolidated from
``app/services/prediction_dataset.py``, ``app/services/backtest_cutoff.py`` and
``app/ai/predictors/historical.py``.
"""

from __future__ import annotations

from app.utils.sequence_coercion import (
    as_str_list,
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


class TestAsStrList:
    def test_none_returns_empty(self):
        assert as_str_list(None) == []

    def test_strings_kept_in_order(self):
        assert as_str_list(["실적 충족", "지역 가산"]) == ["실적 충족", "지역 가산"]

    def test_non_string_items_dropped_not_cast(self):
        assert as_str_list(["a", 1, 2.5, None, True, ["b"]]) == ["a"]

    def test_empty_strings_dropped(self):
        # 빈 문자열만 떨어진다 — "0"·공백은 non-empty 라 남는다.
        assert as_str_list(["", "0", " "]) == ["0", " "]

    def test_json_string_is_not_parsed(self):
        # ``coerce_sequence`` 와 다른 지점: 문자열 입력은 중첩 payload 가 아니라 데이터다.
        assert as_str_list('["a", "b"]') == []

    def test_non_list_returns_empty(self):
        assert as_str_list({"a": 1}) == []
        assert as_str_list(42) == []

    def test_returns_new_list(self):
        original = ["a"]
        assert as_str_list(original) is not original
