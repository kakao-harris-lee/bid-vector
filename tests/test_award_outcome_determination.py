"""Value-table tests for the pure award-outcome decision core (§4.7).

``determine_award_outcome`` judges an operator real bid as won/lost purely from
the operator 상호 vs the public 낙찰자 상호 — never from amount (the same 개찰 can
carry identical 투찰가 across distinct companies). It returns ``None`` whenever the
basis is unknown (§2 정직 명세 — 불확실하면 미기록). No DB/network is touched.
"""

from __future__ import annotations

import pytest

from app.services.award_verification import (
    AWARD_OUTCOME_LOST,
    AWARD_OUTCOME_WON,
    determine_award_outcome,
    normalize_company_name,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("주식회사 해담", "해담"),
        ("(주)해담", "해담"),
        ("（주）해담", "해담"),
        ("㈜해담", "해담"),
        ("유한회사 해담", "해담"),
        ("(유)해담", "해담"),
        ("  해담  건설 ", "해담건설"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_company_name_strips_legal_forms_and_whitespace(raw, expected):
    assert normalize_company_name(raw) == expected


def test_normalize_keeps_bare_char_that_is_not_a_legal_token():
    # "대주건설" must NOT lose its 주 — only the parenthesized/full-word legal
    # forms are stripped, not a bare 주 inside a real 상호.
    assert normalize_company_name("대주건설") == "대주건설"


# --------------------------------------------------------------------------- #
# determine_award_outcome value table
# --------------------------------------------------------------------------- #
def test_exact_name_match_is_won():
    assert (
        determine_award_outcome("해담건설", 89_000_000, "해담건설", 88_000_000)
        == AWARD_OUTCOME_WON
    )


def test_legal_prefix_variation_still_matches_won():
    # "주식회사 해담" (winner) vs "(주)해담" (operator) -> same 상호 -> won.
    assert (
        determine_award_outcome("주식회사 해담", 89_000_000, "(주)해담", 88_000_000)
        == AWARD_OUTCOME_WON
    )


def test_different_company_is_lost():
    assert (
        determine_award_outcome("다른건설", 89_000_000, "해담건설", 88_000_000)
        == AWARD_OUTCOME_LOST
    )


def test_same_amount_other_company_is_lost_amount_never_decides():
    # Identical 투찰가 to the winner but a different 상호 -> lost. This pins that
    # amount agreement never promotes a mismatched name to "won".
    assert (
        determine_award_outcome("다른건설", 88_000_000, "해담건설", 88_000_000)
        == AWARD_OUTCOME_LOST
    )


def test_no_public_winner_is_none_even_with_amount():
    # winning_amount alone (winner 상호 미공개) must not decide won/lost.
    assert (
        determine_award_outcome("", 88_000_000, "해담건설", 88_000_000) is None
    )
    assert (
        determine_award_outcome(None, 88_000_000, "해담건설", 88_000_000) is None
    )


def test_missing_operator_name_is_none():
    assert (
        determine_award_outcome("해담건설", 89_000_000, "", 88_000_000) is None
    )
    assert (
        determine_award_outcome("해담건설", 89_000_000, None, 88_000_000) is None
    )
