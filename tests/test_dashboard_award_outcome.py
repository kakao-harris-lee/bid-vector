"""Regression tests pinning the dashboard 낙찰 판정에 정본(canonical) 재사용.

``dashboard_summary._resolve_award_outcome`` 은 이제 상호 비교를
``award_verification.determine_award_outcome`` 에 위임한다(정규화 후 정확매치,
금액 미사용). 과거의 substring 매칭이 만들던 오탐(예: "몬딱솔류션" ⊃ "션")을
제거하고, 대시보드와 실투찰 트랙이 "우리가 이겼나"에 대해 어긋나지 않음을
고정한다. 순수 판정 경계라 detached ORM 인스턴스로 값-테이블 검증한다(§4.7).
"""

from __future__ import annotations

from app.models.models import Bid, TenderResult, User
from app.services.dashboard_summary import _resolve_award_outcome


def _result(*, winner: str | None, status: str = "낙찰") -> TenderResult:
    return TenderResult(
        winning_company=winner,
        winning_amount=88_000_000.0,
        result_status=status,
    )


def _bid(*, status: str = "submitted") -> Bid:
    return Bid(bid_amount=87_000_000.0, status=status)


def test_substring_false_positive_no_longer_wins():
    """정본 재사용 회귀: "몬딱솔류션" ⊃ "션" 같은 부분문자열 오탐이 사라진다.

    이 케이스가 라이브 60,639건 중 유일하게 old(substring)와 new(정확매치)가
    갈리던 행이며, 운영자는 해당 공고에 투찰하지 않았다(bid=None) → unknown.
    """
    operator = User(company="몬딱솔류션")
    result = _result(winner="션", status="낙찰")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "unknown"


def test_legal_suffix_variation_is_won():
    """"(주)해담" 운영자 vs "주식회사 해담" 낙찰자 → 정규화 후 동일 → won.

    substring 로직에서는 서로 포함하지 않아 놓치던 케이스."""
    operator = User(company="(주)해담")
    result = _result(winner="주식회사 해담")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "won"


def test_internal_whitespace_variation_is_won():
    operator = User(company="해담 건설")
    result = _result(winner="해담건설")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "won"


def test_exact_mismatch_with_bid_and_terminal_is_lost():
    operator = User(company="해담건설")
    result = _result(winner="다른건설", status="awarded")
    assert (
        _resolve_award_outcome(result, operator=operator, bid=_bid()) == "lost"
    )


def test_mismatch_without_bid_stays_unknown():
    """참여 게이트: 운영자가 투찰하지 않은 공고는 상호 불일치라도 unknown."""
    operator = User(company="해담건설")
    result = _result(winner="다른건설", status="awarded")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "unknown"


def test_mismatch_with_bid_but_non_terminal_status_stays_unknown():
    operator = User(company="해담건설")
    result = _result(winner="다른건설", status="낙찰")
    assert (
        _resolve_award_outcome(result, operator=operator, bid=_bid()) == "unknown"
    )


def test_accepted_bid_status_wins_regardless_of_name():
    """운영자가 명시적으로 확정(accepted)한 투찰은 상호 근거와 무관하게 won."""
    operator = User(company="해담건설")
    result = _result(winner="다른건설", status="awarded")
    assert (
        _resolve_award_outcome(result, operator=operator, bid=_bid(status="accepted"))
        == "won"
    )


def test_missing_winner_with_bid_and_terminal_is_lost():
    """낙찰자 상호 미상이라도 운영자가 투찰했고 개찰 종료면 lost(기존 동작 보존)."""
    operator = User(company="해담건설")
    result = _result(winner="", status="closed")
    assert (
        _resolve_award_outcome(result, operator=operator, bid=_bid()) == "lost"
    )


def test_missing_winner_without_bid_is_unknown():
    operator = User(company="해담건설")
    result = _result(winner="", status="awarded")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "unknown"


def test_missing_operator_company_is_unknown():
    operator = User(company=None)
    result = _result(winner="해담건설", status="awarded")
    assert _resolve_award_outcome(result, operator=operator, bid=None) == "unknown"
