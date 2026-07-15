"""Unit tests for scripts/verify_award_eligibility.verify_one and its CLI parsing.

Covers the post-개찰 적격/경쟁력 verdicts: not-settled, 적격+낙찰가능, and 낙하.
Reserve-detail fetching is injected (or the collector method is monkeypatched) so
no live KONEPS call happens.
"""

from __future__ import annotations

import pytest

from app.models.models import Project, TenderResult
from scripts.verify_award_eligibility import (
    VERDICT_ELIGIBLE_OUTBID,
    VERDICT_ELIGIBLE_WINNABLE,
    VERDICT_NOT_SETTLED,
    VERDICT_UNDERCUT,
    VERDICT_UNDETERMINED,
    format_result,
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
