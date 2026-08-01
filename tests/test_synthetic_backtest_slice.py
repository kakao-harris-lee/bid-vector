"""Characterization for the settlement drilldown slice of the synthetic backtest.

CHARACTERIZATION, not correctness: every expectation below is the *current*
behaviour of ``_slice_settlement_item``, captured so the scalar-coercion
consolidation (local ``_int_or_none`` / ``_float_or_none`` -> the
``app.utils.numeric`` hub) can be proven behaviour-preserving. The interesting
rows are the coercion edges — ``"3.9"`` is a ``ValueError`` for ``int`` while
``3.9`` truncates, and every failure degrades to ``None`` rather than raising.
"""

from __future__ import annotations

import pytest

from app.services.synthetic_backtest import _slice_settlement_item


class _Unc:
    """A value neither ``int()`` nor ``float()`` accepts (raises TypeError)."""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param({}, None, id="missing-key"),
        pytest.param({"project_id": None}, None, id="none"),
        pytest.param({"project_id": 42}, 42, id="int"),
        pytest.param({"project_id": "42"}, 42, id="numeric-string"),
        pytest.param({"project_id": 3.9}, 3, id="float-truncates"),
        pytest.param({"project_id": "3.9"}, None, id="float-string-rejected"),
        pytest.param({"project_id": True}, 1, id="bool"),
        pytest.param({"project_id": "x"}, None, id="non-numeric-string"),
        pytest.param({"project_id": ""}, None, id="empty-string"),
        pytest.param({"project_id": _Unc()}, None, id="uncastable-object"),
    ],
)
def test_int_field_coercion(raw, expected) -> None:
    assert _slice_settlement_item(raw)["project_id"] == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param({}, None, id="missing-key"),
        pytest.param({"bid_amount": None}, None, id="none"),
        pytest.param({"bid_amount": 1000}, 1000.0, id="int"),
        pytest.param({"bid_amount": "1000.5"}, 1000.5, id="numeric-string"),
        pytest.param({"bid_amount": True}, 1.0, id="bool"),
        pytest.param({"bid_amount": "x"}, None, id="non-numeric-string"),
        pytest.param({"bid_amount": ""}, None, id="empty-string"),
        pytest.param({"bid_amount": _Unc()}, None, id="uncastable-object"),
    ],
)
def test_float_field_coercion(raw, expected) -> None:
    assert _slice_settlement_item(raw)["bid_amount"] == expected


def test_full_item_shape_is_stable() -> None:
    sliced = _slice_settlement_item(
        {
            "project_id": "7",
            "project_title": "합성 공고",
            "category": "construction",
            "paper_bid_id": 11,
            "decision_action": "bid",
            "bid_amount": "88000000",
            "winning_amount": 87500000.0,
            "absolute_bid_rate_error": "0.0057",
            "would_have_won": 1,
            "settled_at": "2026-01-05T00:00:00+00:00",
            "dropped_internal_field": {"not": "shipped"},
        }
    )
    assert sliced == {
        "project_id": 7,
        "project_title": "합성 공고",
        "category": "construction",
        "paper_bid_id": 11,
        "decision_action": "bid",
        "bid_amount": 88000000.0,
        "winning_amount": 87500000.0,
        "absolute_bid_rate_error": 0.0057,
        "would_have_won": True,
        "settled_at": "2026-01-05T00:00:00+00:00",
    }


def test_empty_item_degrades_to_declared_defaults() -> None:
    """Missing everything yields the same key set — nothing raises."""
    assert _slice_settlement_item({}) == {
        "project_id": None,
        "project_title": "",
        "category": None,
        "paper_bid_id": None,
        "decision_action": None,
        "bid_amount": None,
        "winning_amount": None,
        "absolute_bid_rate_error": None,
        "would_have_won": False,
        "settled_at": None,
    }


@pytest.mark.parametrize(
    ("raw_title", "expected"),
    [(None, ""), (0, ""), ("", ""), (123, "123"), ("제목", "제목")],
)
def test_title_falls_back_to_empty_string(raw_title, expected) -> None:
    assert _slice_settlement_item({"project_title": raw_title})["project_title"] == expected
