"""verify_koneps_field_contract 스크립트의 순수 부분(리포트 빌더/페이지 슬라이스) 테스트.

실 API 는 호출하지 않는다: ``build_report`` 는 fake item 으로, ``fetch_items`` 는 주입된
가짜 fetch/sleep 로 검증한다(§4.7 seam). 라이브 호출은 운영자가 소량 승인 실행한다.
"""

from scripts.verify_koneps_field_contract import (
    build_report,
    fetch_items,
    render_report,
)
from app.services.koneps.field_contract import Severity

_SCSBID_OP = "getScsbidListSttusServc"
_NOTICE_OP = "getBidPblancListInfoServc"


def test_build_report_flags_errors_and_passes_clean_rows():
    items = [
        {"bidNtceNo": "A", "sucsfbidRate": "88.001", "bidNtceOrd": "000"},  # clean
        {"bidNtceNo": "B", "sucsfbidRate": 88000000, "bidNtceOrd": 0},  # 2 errors
    ]
    report = build_report(items, operation=_SCSBID_OP)
    assert report.family == "scsbid_award"
    assert report.item_count == 2
    assert report.error_count == 2  # amount-in-rate + ord-int
    assert report.passed is False


def test_build_report_clean_rows_pass():
    items = [{"bidNtceNo": "A", "bssAmt": "90000000", "bidNtceOrd": "000"}]
    report = build_report(items, operation=_NOTICE_OP)
    assert report.error_count == 0
    assert report.warn_count == 0
    assert report.passed is True


def test_build_report_collects_unknown_fields():
    items = [{"bidNtceNo": "A", "brandNewKoneps": "x"}]
    report = build_report(items, operation=_NOTICE_OP)
    assert report.unknown_fields == ["brandNewKoneps"]


def test_build_report_base_precedence_is_error_not_pass():
    items = [{"bidNtceNo": "A", "presmptPrce": "100000000", "bssAmt": "90000000"}]
    report = build_report(items, operation=_NOTICE_OP)
    assert report.error_count == 1
    assert report.passed is False
    # 렌더 출력에 시크릿이 아니라 위반 요약만 들어간다.
    text = "\n".join(render_report(report))
    assert "FAIL" in text
    assert "base_amount" in text


def test_fetch_items_slices_to_limit_without_network():
    calls = {"sleeps": 0}

    def fake_fetch():
        body_items = [{"bidNtceNo": str(i)} for i in range(10)]
        return {"response": {"body": {"items": {"item": body_items}}}}

    def fake_sleep(_seconds):
        calls["sleeps"] += 1

    items = fetch_items(fake_fetch, limit=3, delay=1.0, sleep_fn=fake_sleep)
    assert [i["bidNtceNo"] for i in items] == ["0", "1", "2"]
    assert calls["sleeps"] == 1  # throttle applied once after the single page call


def test_fetch_items_no_delay_skips_sleep():
    def fake_fetch():
        return {"response": {"body": {"items": {"item": [{"bidNtceNo": "1"}]}}}}

    sleeps = []
    fetch_items(fake_fetch, limit=5, delay=0.0, sleep_fn=lambda s: sleeps.append(s))
    assert sleeps == []


def test_report_warn_only_still_passes():
    # 예정가만 있는 공고: WARN(예정가 오염 인지)만, ERROR 없음 -> PASS.
    items = [{"bidNtceNo": "A", "presmptPrce": "100000000"}]
    report = build_report(items, operation=_NOTICE_OP)
    assert report.error_count == 0
    assert report.warn_count == 1
    assert report.passed is True
    assert report.items[0].violations[0].severity is Severity.WARN
