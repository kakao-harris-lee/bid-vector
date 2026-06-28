"""Tests for the shared stdlib-only script helpers in ``scripts/_common.py``.

These helpers were previously duplicated byte-for-byte across several script
entrypoints. The tests pin the behavior so the dedup stays behavior-preserving.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest

from scripts._common import parse_datetime, positive_int


class TestParseDatetime:
    def test_none_returns_none(self) -> None:
        assert parse_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_datetime("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert parse_datetime("   ") is None

    def test_date_only_defaults_to_start_of_day_utc(self) -> None:
        result = parse_datetime("2025-01-02")
        assert result == datetime(2025, 1, 2, 0, 0, 0, 0, tzinfo=UTC)
        assert result is not None
        assert result.tzinfo == UTC

    def test_date_only_end_of_day(self) -> None:
        result = parse_datetime("2025-01-02", end_of_day=True)
        assert result == datetime(2025, 1, 2, 23, 59, 59, 999999, tzinfo=UTC)

    def test_date_only_strips_surrounding_whitespace(self) -> None:
        assert parse_datetime("  2025-01-02  ") == datetime(
            2025, 1, 2, 0, 0, 0, 0, tzinfo=UTC
        )

    def test_z_suffix_is_treated_as_utc(self) -> None:
        result = parse_datetime("2025-03-04T05:06:07Z")
        assert result == datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC)

    def test_naive_datetime_is_assumed_utc(self) -> None:
        result = parse_datetime("2025-03-04T05:06:07")
        assert result == datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC)

    def test_aware_offset_is_converted_to_utc(self) -> None:
        # +09:00 -> 09:00 KST is 00:00 UTC.
        result = parse_datetime("2025-03-04T09:00:00+09:00")
        assert result == datetime(2025, 3, 4, 0, 0, 0, tzinfo=UTC)
        assert result is not None
        assert result.tzinfo == UTC


class TestPositiveInt:
    def test_valid_positive(self) -> None:
        assert positive_int("7") == 7

    def test_one_is_valid(self) -> None:
        assert positive_int("1") == 1

    def test_zero_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
            positive_int("0")

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
            positive_int("-3")

    def test_non_integer_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="not an integer"):
            positive_int("abc")

    def test_float_string_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="not an integer"):
            positive_int("1.5")
