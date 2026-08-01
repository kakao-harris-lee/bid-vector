"""Characterization tests for the canonical text cleanup helpers.

These pin the behavior that was consolidated into ``app/utils/textfmt.py`` from
``_normalize_category`` (backtest_cutoff · prediction_dataset) and
``_clean_category_name``/``_clean_optional`` (decision_experiments ·
ml_training). The table below is the differential input set that was run
against every pre-consolidation copy; every row produced the identical result
in all of them.
"""

from __future__ import annotations

import pytest

from app.services.allocation import BidDecisionService
from app.services.backtest_cutoff import BacktestCutoffService
from app.services.decision_experiments.application import _ApplicationMixin
from app.services.ml_training.helpers import HelpersMixin
from app.services.prediction_dataset import PredictionDatasetService
from app.utils.textfmt import (
    append_unique_note,
    clean_text,
    normalize_lookup_key,
    optional_text,
)

ALIASES = {
    "general-service": "service",
    "일반용역": "service",
    "공사": "construction",
}

# (input, cleaned text, alias-resolved lookup key)
TEXT_CASES = [
    (None, "", ""),
    ("", "", ""),
    (" ", "", ""),
    ("\t\n ", "", ""),
    ("공사", "공사", "construction"),
    (" 공사 ", "공사", "construction"),
    ("General-Service", "General-Service", "service"),
    (" GENERAL-SERVICE ", "GENERAL-SERVICE", "service"),
    ("일반용역", "일반용역", "service"),
    ("unknown-category", "unknown-category", "unknown-category"),
    ("  Mixed Case Label  ", "Mixed Case Label", "mixed case label"),
    (0, "", ""),  # falsy non-string collapses to "" rather than "0"
    (0.0, "", ""),
    (False, "", ""),
    ([], "", ""),
    ({}, "", ""),
    (True, "True", "true"),
    (5, "5", "5"),
    (-1, "-1", "-1"),
    (b"", "", ""),
]


@pytest.mark.parametrize(("value", "expected_text", "expected_key"), TEXT_CASES)
class TestTextCleanupTable:
    def test_clean_text(self, value, expected_text, expected_key):
        assert clean_text(value) == expected_text

    def test_optional_text(self, value, expected_text, expected_key):
        assert optional_text(value) == (expected_text or None)

    def test_normalize_lookup_key(self, value, expected_text, expected_key):
        assert normalize_lookup_key(value, ALIASES) == expected_key

    def test_normalize_lookup_key_without_matching_alias(
        self, value, expected_text, expected_key
    ):
        # An empty alias table degrades to the casefolded key — unknown labels
        # stay visible instead of collapsing into a default bucket.
        assert normalize_lookup_key(value, {}) == expected_text.lower()


# --------------------------------------------------------------------------- #
# Caller paths — the category normalizers keep resolving the project aliases.
# --------------------------------------------------------------------------- #
CATEGORY_CASES = [
    (None, ""),
    ("", ""),
    (" 공사 ", "construction"),
    ("CONSTRUCTION", "construction"),
    ("일반용역", "service"),
    ("general-service", "service"),
    ("기술용역", "technical-service"),
    ("소프트웨어", "software"),
    ("물품", "goods"),
    ("unknown-category", "unknown-category"),
]


@pytest.mark.parametrize(("category", "expected"), CATEGORY_CASES)
def test_backtest_cutoff_normalize_category(category, expected):
    service = BacktestCutoffService()
    assert service._normalize_category(category) == expected


@pytest.mark.parametrize(("category", "expected"), CATEGORY_CASES)
def test_prediction_dataset_normalize_category(category, expected):
    service = PredictionDatasetService()
    assert service._normalize_category(category) == expected


# --------------------------------------------------------------------------- #
# Caller paths — blank-to-``None`` cleanup keeps its "missing" contract.
# --------------------------------------------------------------------------- #
OPTIONAL_CASES = [
    (None, None),
    ("", None),
    ("   ", None),
    (0, None),  # falsy non-string reads as missing, not as "0"
    (False, None),
    ("공사", "공사"),
    ("  release-tag  ", "release-tag"),
    (5, "5"),
]


@pytest.mark.parametrize(("value", "expected"), OPTIONAL_CASES)
def test_decision_experiment_clean_category_name(value, expected):
    mixin = _ApplicationMixin()
    assert mixin._clean_category_name(value) == expected


@pytest.mark.parametrize(("value", "expected"), OPTIONAL_CASES)
def test_ml_training_clean_optional(value, expected):
    mixin = HelpersMixin()
    assert mixin._clean_optional(value) == expected


# --------------------------------------------------------------------------- #
# Caller path — reasoning notes stay idempotent and skip redundant writes.
# --------------------------------------------------------------------------- #
# (existing reasoning, note, resulting reasoning, attribute writes)
NOTE_CASES = [
    (None, "메모", "메모", 1),
    ("", "메모", "메모", 1),
    ("기존", "메모", "기존 메모", 1),
    ("기존 메모", "메모", "기존 메모", 0),  # already present — no rewrite
    ("메모", "메모", "메모", 0),
    ("기존", "", "기존", 0),  # empty note is a no-op
    (None, "", None, 0),
    ("기존 ", "메모", "기존  메모", 1),  # only the ends are stripped, not the seam
    ("기존", "메모 ", "기존 메모", 1),
    ("메", "메모", "메 메모", 1),  # substring of the note is not a match
]


class _ReasoningRecord:
    """Stand-in for ``BidDecisionRecord`` counting writes to ``reasoning``.

    The write count is part of the contract: re-assigning an unchanged value
    would mark the ORM row dirty and emit a redundant UPDATE.
    """

    def __init__(self, reasoning: str | None) -> None:
        self.writes = 0
        self.reasoning = reasoning

    def __setattr__(self, name: str, value: object) -> None:
        if name == "reasoning" and "reasoning" in self.__dict__:
            object.__setattr__(self, "writes", self.writes + 1)
        object.__setattr__(self, name, value)


@pytest.mark.parametrize(("existing", "note", "expected", "writes"), NOTE_CASES)
def test_append_unique_note(existing, note, expected, writes):
    assert append_unique_note(existing, note) == expected


@pytest.mark.parametrize(("existing", "note", "expected", "writes"), NOTE_CASES)
def test_allocation_append_reasoning_note(existing, note, expected, writes):
    record = _ReasoningRecord(existing)
    BidDecisionService()._append_reasoning_note(record, note)
    assert record.reasoning == expected
    assert record.writes == writes
