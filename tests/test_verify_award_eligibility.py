"""Unit tests for scripts/verify_award_eligibility.verify_one and its CLI parsing.

Covers the post-개찰 적격/경쟁력 verdicts: not-settled, 적격+낙찰가능, and 낙하.
Reserve-detail fetching is injected (or the collector method is monkeypatched) so
no live KONEPS call happens.
"""

from __future__ import annotations

import pytest

import scripts.verify_award_eligibility as verify_mod
from app.models.models import Project, TenderResult
from scripts.verify_award_eligibility import (
    VERDICT_ELIGIBLE_OUTBID,
    VERDICT_ELIGIBLE_WINNABLE,
    VERDICT_NOT_SETTLED,
    VERDICT_UNDERCUT,
    VERDICT_UNDETERMINED,
    format_result,
    notify_telegram,
    parse_specs,
    strip_notice_suffix,
    verify_one,
)

NOTICE = "20260612345"


def _project(test_db, *, notice: str = NOTICE, category: str = "construction") -> Project:
    project = Project(
        title="검증 대상 공고",
        description="post-개찰 verification",
        category=category,
        notice_number=notice,
        status="awarded",
    )
    test_db.add(project)
    test_db.flush()
    return project


def _tender_result(
    test_db, project: Project, *, company: str, amount: float, rate: float
) -> TenderResult:
    result = TenderResult(
        project_id=project.id,
        winning_company=company,
        winning_amount=amount,
        winning_rate=rate,
        result_status="opened",
    )
    test_db.add(result)
    test_db.flush()
    return result


def _detail(**overrides):
    payload = {
        "reserve_prices": [],
        "selected_numbers": [],
        "planned_price": None,
        "base_amount": None,
        "reserve_detail_total_count": 0,
    }
    payload.update(overrides)
    return lambda notice, category: payload


def _settled_detail():
    return _detail(
        reserve_prices=[99_000_000, 101_000_000],
        selected_numbers=[2, 13],
        planned_price=100_000_000,
        base_amount=100_000_000,
        reserve_detail_total_count=15,
    )


# --------------------------------------------------------------------------- #
# (a) not settled
# --------------------------------------------------------------------------- #
def test_not_settled_reports_pre_opening(test_db):
    _project(test_db)
    result = verify_one(test_db, NOTICE, 88_000_000, 0.87745, fetch_detail=_detail())
    assert result["settled"] is False
    assert result["verdict"] == VERDICT_NOT_SETTLED
    lines = format_result(result)
    assert any("아직 개찰 전/미적재 (개찰결과 0건)" in line for line in lines)


# --------------------------------------------------------------------------- #
# (b) settled, floor given, bid >= floor and bid < winning -> 적격+낙찰가능
# --------------------------------------------------------------------------- #
def test_eligible_and_winnable(test_db):
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )
    result = verify_one(
        test_db, NOTICE, 88_000_000, 0.87745, fetch_detail=_settled_detail()
    )
    assert result["settled"] is True
    assert result["floor_price"] == pytest.approx(87_745_000.0)
    assert result["eligible"] is True
    assert result["eligibility_margin_won"] == pytest.approx(255_000.0)
    # bid is below the winning amount -> more competitive.
    assert result["competitiveness_won"] == pytest.approx(-1_000_000.0)
    assert result["verdict"] == VERDICT_ELIGIBLE_WINNABLE
    lines = format_result(result)
    assert any("적격" in line for line in lines)
    assert any(f"VERDICT: {VERDICT_ELIGIBLE_WINNABLE}" in line for line in lines)


# --------------------------------------------------------------------------- #
# (c) settled, floor given, bid < floor -> 낙하
# --------------------------------------------------------------------------- #
def test_undercut_below_floor(test_db):
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )
    result = verify_one(
        test_db, NOTICE, 87_000_000, 0.87745, fetch_detail=_settled_detail()
    )
    assert result["settled"] is True
    assert result["eligible"] is False
    assert result["eligibility_margin_won"] == pytest.approx(-745_000.0)
    assert result["verdict"] == VERDICT_UNDERCUT


def test_eligible_but_outbid(test_db):
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=88_500_000, rate=0.885
    )
    result = verify_one(
        test_db, NOTICE, 89_000_000, 0.87745, fetch_detail=_settled_detail()
    )
    assert result["eligible"] is True
    assert result["verdict"] == VERDICT_ELIGIBLE_OUTBID


def test_undetermined_without_floor_rate(test_db):
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )
    result = verify_one(test_db, NOTICE, 88_000_000, None, fetch_detail=_settled_detail())
    assert result["settled"] is True
    assert result["eligible"] is None
    assert result["verdict"] == VERDICT_UNDETERMINED
    lines = format_result(result)
    assert any("낙찰하한율 없이는" in line for line in lines)
    assert any("낙찰가보다 낮게 투찰" in line for line in lines)


def test_settled_via_tender_result_when_reserve_empty(test_db):
    """No reserve detail but a real TenderResult still counts as settled."""
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )
    result = verify_one(test_db, NOTICE, 88_000_000, None, fetch_detail=_detail())
    assert result["settled"] is True
    assert result["verdict"] == VERDICT_UNDETERMINED


def test_monkeypatched_collector_method_path(test_db, monkeypatch):
    """The default fetch path calls KonepsCollectorService._fetch_scsbid_reserve_detail."""
    from app.services.koneps.collector import KonepsCollectorService

    captured: dict[str, object] = {}

    def _fake(self, raw_item, *, category, service_key):
        captured["bidNtceNo"] = raw_item.get("bidNtceNo")
        captured["category"] = category
        captured["service_key"] = service_key
        return {
            "reserve_prices": [99_000_000],
            "selected_numbers": [7],
            "planned_price": 100_000_000,
            "base_amount": 100_000_000,
            "reserve_detail_total_count": 15,
        }

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _fake, raising=True
    )
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )
    result = verify_one(test_db, f"{NOTICE}-000", 88_000_000, 0.87745)
    assert result["verdict"] == VERDICT_ELIGIBLE_WINNABLE
    # Suffix stripped before hitting KONEPS + project category propagated.
    assert captured["bidNtceNo"] == NOTICE
    assert captured["category"] == "construction"


def test_reserve_fetch_error_falls_back_to_tender_result(test_db):
    project = _project(test_db)
    _tender_result(
        test_db, project, company="가상건설", amount=89_000_000, rate=0.89
    )

    def _boom(notice, category):
        raise RuntimeError("KONEPS 429")

    result = verify_one(test_db, NOTICE, 88_000_000, None, fetch_detail=_boom)
    assert result["reserve_error"] is not None
    assert "429" in result["reserve_error"]
    assert result["settled"] is True  # TenderResult keeps it settled
    lines = format_result(result)
    assert any("예비가격 상세 조회 실패" in line for line in lines)


# --------------------------------------------------------------------------- #
# CLI parsing
# --------------------------------------------------------------------------- #
def test_strip_notice_suffix():
    assert strip_notice_suffix("20260612345-000") == "20260612345"
    assert strip_notice_suffix("20260612345") == "20260612345"
    assert strip_notice_suffix("  20260612345-00 ") == "20260612345"


def test_parse_specs_repeatable_triples():
    specs = parse_specs(
        [
            "--notice", "A", "--bid", "88000000", "--floor-rate", "0.87745",
            "--notice", "B", "--bid", "120000000",
        ]
    )
    assert specs == [
        {"notice": "A", "bid": 88_000_000.0, "floor_rate": 0.87745},
        {"notice": "B", "bid": 120_000_000.0, "floor_rate": None},
    ]


def test_parse_specs_bid_before_notice_errors():
    with pytest.raises(SystemExit):
        parse_specs(["--bid", "100"])


def test_parse_specs_missing_bid_errors():
    with pytest.raises(SystemExit):
        parse_specs(["--notice", "A"])


# --------------------------------------------------------------------------- #
# --telegram alarm gate (no real network: an injected recorder is used)
# --------------------------------------------------------------------------- #
class _RecordingSender:
    """Test double for TelegramNotificationService.send_message."""

    def __init__(self):
        self.messages: list[str] = []

    def send_message(self, message: str) -> dict[str, object]:
        self.messages.append(message)
        return {"sent": True, "status": "sent"}


def _not_settled_result(notice: str = "A") -> dict[str, object]:
    return {
        "notice": notice,
        "settled": False,
        "verdict": VERDICT_NOT_SETTLED,
        "bid": 88_000_000,
        "reserve_error": None,
    }


def _settled_result(notice: str = NOTICE) -> dict[str, object]:
    return {
        "notice": notice,
        "settled": True,
        "verdict": VERDICT_ELIGIBLE_WINNABLE,
        "bid": 88_000_000,
        "planned_price": 100_000_000,
        "reserve_price_count": 2,
        "selected_numbers": [2, 13],
        "base_amount": 100_000_000,
        "winning_company": "가상건설",
        "winning_amount": 89_000_000,
        "winning_rate": 0.89,
        "floor_price": 87_745_000.0,
        "eligible": True,
        "eligibility_margin_won": 255_000.0,
        "eligibility_margin_pp": 0.255,
        "competitiveness_won": -1_000_000.0,
        "competitiveness_pp": -1.0,
        "reserve_error": None,
    }


# (a) all notices not-settled + --telegram -> send_message NOT called.
def test_notify_telegram_skips_when_all_not_settled():
    sender = _RecordingSender()
    outcome = notify_telegram(
        [_not_settled_result("A"), _not_settled_result("B")], sender=sender
    )
    assert sender.messages == []
    assert outcome["status"] == "skipped"
    assert outcome["sent"] is False


# (b) at least one settled + --telegram -> send_message called once with verdict + notice.
def test_notify_telegram_sends_when_a_notice_is_settled():
    sender = _RecordingSender()
    outcome = notify_telegram(
        [_not_settled_result("A"), _settled_result(NOTICE)], sender=sender
    )
    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert VERDICT_ELIGIBLE_WINNABLE in message
    assert NOTICE in message
    assert outcome["status"] == "sent"
    assert outcome["settled_count"] == 1


def test_notify_telegram_swallows_sender_error():
    class _BoomSender:
        def send_message(self, message: str) -> dict[str, object]:
            raise RuntimeError("Telegram API down")

    outcome = notify_telegram([_settled_result(NOTICE)], sender=_BoomSender())
    assert outcome["status"] == "error"
    assert "Telegram API down" in outcome["error"]


def _patch_main_deps(monkeypatch, sender, result):
    """Stub SessionLocal + verify_one + default sender so main() stays offline."""

    class _FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(verify_mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        verify_mod, "verify_one", lambda db, notice, bid, floor_rate: result
    )
    monkeypatch.setattr(verify_mod, "_default_sender", lambda: sender)


# main() with --telegram + a settled notice actually reaches the sender.
def test_main_with_telegram_sends_for_settled(monkeypatch):
    sender = _RecordingSender()
    _patch_main_deps(monkeypatch, sender, _settled_result(NOTICE))
    assert verify_mod.main(["--notice", NOTICE, "--bid", "88000000", "--telegram"]) == 0
    assert len(sender.messages) == 1
    assert NOTICE in sender.messages[0]


# (c) without --telegram -> send_message never called (regression).
def test_main_without_telegram_never_sends(monkeypatch):
    sender = _RecordingSender()
    _patch_main_deps(monkeypatch, sender, _settled_result(NOTICE))
    assert verify_mod.main(["--notice", NOTICE, "--bid", "88000000"]) == 0
    assert sender.messages == []
