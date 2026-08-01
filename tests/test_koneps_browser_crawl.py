"""Browser-free unit tests for the extracted KONEPS browser-crawl helpers.

These lock the live-crawl form/selector constants and the page-driving helpers
of ``app.services.koneps.browser_crawl`` after the pure relocation out of
``KonepsCollectorService``. They exercise everything that does *not* require a
real Playwright browser by driving the helpers with lightweight fake ``page`` /
``locator`` doubles that mirror the exact sync-API surface the code calls
(``evaluate`` / ``locator`` / ``count`` / ``click`` / ``wait_for_*`` / ``url`` /
``content``). The two ``sync_playwright``-launching functions are intentionally
not covered here (they need a live browser); the orchestrator
``collect_live_items`` is covered via a stub service that records the inner-step
dispatch so the monkeypatch-hook contract is locked.

Behavior must stay byte-identical to the original collector methods.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.koneps_items import OpeningResultRow
from app.schemas.schemas import CrawlRequest
from app.services.koneps import browser_crawl


def _request(**overrides: Any) -> CrawlRequest:
    base = {
        "source": "koneps",
        "category": "software",
        "execution_mode": "live",
        "keyword": "AI",
    }
    base.update(overrides)
    return CrawlRequest(**base)


class _FakeLocator:
    """Minimal stand-in for a Playwright sync Locator."""

    def __init__(self, recorder: list[str], selector: str, *, count: int = 1) -> None:
        self._recorder = recorder
        self._selector = selector
        self._count = count

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self._recorder.append(f"click:{self._selector}")


class _FakePage:
    """Minimal stand-in for a Playwright sync Page for non-browser helpers."""

    def __init__(
        self,
        *,
        evaluate_return: Any = None,
        evaluate_side_effect=None,
        locator_counts: dict[str, int] | None = None,
        url: str = "https://www.g2b.go.kr/",
        content_html: str = "<html></html>",
    ) -> None:
        self.evaluate_calls: list[tuple[str, Any]] = []
        self._evaluate_return = evaluate_return
        self._evaluate_side_effect = evaluate_side_effect
        self._locator_counts = locator_counts or {}
        self.actions: list[str] = []
        self.url = url
        self._content_html = content_html

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluate_calls.append((script, arg))
        if self._evaluate_side_effect is not None:
            return self._evaluate_side_effect(script, arg)
        return self._evaluate_return

    def locator(self, selector: str) -> _FakeLocator:
        count = self._locator_counts.get(selector, 1)
        return _FakeLocator(self.actions, selector, count=count)

    def wait_for_selector(self, selector: str) -> None:
        self.actions.append(f"wait_for_selector:{selector}")

    def wait_for_timeout(self, ms: int) -> None:
        self.actions.append(f"wait_for_timeout:{ms}")

    def content(self) -> str:
        return self._content_html


# --- Constant locks --------------------------------------------------------


def test_live_crawl_constants_are_relocated_verbatim():
    assert (
        browser_crawl.HOME_SEARCH_KEYWORD_ID
        == "mf_wfm_container_wq_uuid_925_wq_uuid_934_searchKeyword"
    )
    assert (
        browser_crawl.HOME_SEARCH_BUTTON_ID
        == "mf_wfm_container_wq_uuid_925_wq_uuid_934_btnBidPbancDtlSrch"
    )
    assert browser_crawl.HOME_SEARCH_PAGER_ID_PREFIX == "mf_wfm_container_pglList_page_"
    assert browser_crawl.HOME_SEARCH_DEFAULT_PAGE_SIZE == 10
    assert browser_crawl.OPENING_RESULT_SEARCH_BUTTON_ID == "mf_wfm_container_btnS0001"
    # Group ids enumerate every category radio (one "all" + five concrete).
    assert len(browser_crawl.HOME_SEARCH_CATEGORY_GROUP_IDS) == 6


def test_category_id_map_covers_korean_and_english_aliases():
    ids = browser_crawl.HOME_SEARCH_CATEGORY_IDS
    # Korean + english aliases resolve to the same WebSquare radio id.
    assert ids["goods"] == ids["물품"]
    assert ids["service"] == ids["일반용역"] == ids["general-service"]
    assert ids["construction"] == ids["공사"]
    assert ids["other"] == ids["기타"]
    assert ids.get("does-not-exist") is None


# --- DOM-id helpers (browser-free) -----------------------------------------


def test_set_input_value_passes_field_id_and_value_pair():
    page = _FakePage(evaluate_return=True)
    result = browser_crawl.set_input_value(page, "field-1", "hello")
    assert result is True
    assert page.evaluate_calls[0][1] == ["field-1", "hello"]


def test_set_input_value_returns_false_when_dom_node_missing():
    page = _FakePage(evaluate_return=False)
    assert browser_crawl.set_input_value(page, "missing", "x") is False


def test_set_checked_state_passes_id_and_checked_pair():
    page = _FakePage(evaluate_return=True)
    assert browser_crawl.set_checked_state(page, "radio-1", True) is True
    assert page.evaluate_calls[0][1] == ["radio-1", True]


def test_apply_business_type_filter_unknown_category_is_a_no_op():
    page = _FakePage(evaluate_return=True)
    browser_crawl.apply_business_type_filter(page, "no-such-category")
    # No evaluate calls because the category does not map to a known filter id.
    assert page.evaluate_calls == []


def test_apply_business_type_filter_clears_group_then_sets_target():
    page = _FakePage(evaluate_return=True)
    browser_crawl.apply_business_type_filter(page, "construction")
    # Six group ids cleared (checked=False) then the target set (checked=True).
    cleared = [arg for _, arg in page.evaluate_calls[:-1]]
    assert all(arg[1] is False for arg in cleared)
    assert len(cleared) == len(browser_crawl.HOME_SEARCH_CATEGORY_GROUP_IDS)
    target_id, target_checked = page.evaluate_calls[-1][1]
    assert target_id == browser_crawl.HOME_SEARCH_CATEGORY_IDS["construction"]
    assert target_checked is True


def test_go_to_result_page_returns_false_when_pager_absent():
    selector = f"#{browser_crawl.HOME_SEARCH_PAGER_ID_PREFIX}2"
    page = _FakePage(locator_counts={selector: 0})
    assert browser_crawl.go_to_result_page(page, 2) is False
    # No navigation actions when the pager is missing.
    assert page.actions == []


def test_go_to_result_page_clicks_pager_and_waits_when_present():
    selector = f"#{browser_crawl.HOME_SEARCH_PAGER_ID_PREFIX}3"
    page = _FakePage(locator_counts={selector: 1})
    assert browser_crawl.go_to_result_page(page, 3) is True
    assert f"click:{selector}" in page.actions


def test_read_opening_result_rows_filters_rows_without_notice_number():
    page = _FakePage(
        evaluate_return=[
            {"bidPbancNo": "R-1", "bidPbancNm": "정상"},
            {"bidPbancNo": "", "bidPbancNm": "공번호"},
        ]
    )
    rows = browser_crawl.read_opening_result_rows(page)
    notice_numbers = [row.notice_number for row in rows]
    assert "R-1" in notice_numbers
    # Empty bid number is dropped (no notice_number after normalization).
    assert all(nn for nn in notice_numbers)


def test_read_opening_result_rows_handles_empty_data_list():
    page = _FakePage(evaluate_return=[])
    assert browser_crawl.read_opening_result_rows(page) == []


def test_close_browser_context_swallows_close_errors():
    class _Boom:
        def close(self) -> None:
            raise RuntimeError("already closed")

    # Must not raise: best-effort cleanup.
    browser_crawl.close_browser_context(_Boom())


def test_collect_detail_page_snapshots_returns_empty_without_links():
    selector = (
        f"#{browser_crawl.html_parsing.HOME_SEARCH_RESULT_TABLE_ID} "
        "a[id$='btnOpenKonepsInfo']"
    )
    page = _FakePage(locator_counts={selector: 0})
    assert browser_crawl.collect_detail_page_snapshots(page) == {}


# --- Orchestrator dispatch (monkeypatch-hook contract) ---------------------


class _StubService:
    """Stub service that records inner-step dispatch from collect_live_items."""

    def __init__(self, snapshots, opening_rows=None, opening_exc=None) -> None:
        self._snapshots = snapshots
        self._opening_rows = opening_rows if opening_rows is not None else []
        self._opening_exc = opening_exc
        self.gather_calls = 0
        self.opening_calls = 0

    def _gather_live_page_snapshots(self, request):
        self.gather_calls += 1
        return self._snapshots

    def _collect_opening_result_rows(self, request):
        self.opening_calls += 1
        if self._opening_exc is not None:
            raise self._opening_exc
        return self._opening_rows


_SAMPLE_HTML = """
<html><body><table>
<tbody><tr>
<td>20260507-001</td>
<td>AI 소프트웨어 통합 구축</td>
<td>125,000,000</td>
<td>121,500,000</td>
<td>2026-05-10 18:00</td>
<td>서울</td>
<td><a href="https://www.g2b.go.kr/notice/20260507-001">상세보기</a></td>
</tr></tbody>
</table></body></html>
"""


def test_collect_live_items_dispatches_inner_steps_through_service():
    service = _StubService(
        snapshots=[
            {"page_number": 1, "url": "https://www.g2b.go.kr/", "html": _SAMPLE_HTML}
        ]
    )
    result = browser_crawl.collect_live_items(service, _request())

    # Both inner steps are dispatched back through the service (so a
    # monkeypatched hook on the service surface stays honored).
    assert service.gather_calls == 1
    assert service.opening_calls == 1
    assert result["metadata"]["resolved_mode"] == "live"
    assert result["metadata"]["page_count"] == 1
    assert result["items"][0].notice_number == "20260507-001"


def test_collect_live_items_raises_when_no_items_parsed():
    service = _StubService(
        snapshots=[
            {
                "page_number": 1,
                "url": "https://www.g2b.go.kr/",
                "html": "<html><body></body></html>",
            }
        ]
    )
    with pytest.raises(ValueError, match="No notice items could be parsed"):
        browser_crawl.collect_live_items(service, _request())


def test_collect_live_items_classifies_opening_result_failure_without_failing():
    service = _StubService(
        snapshots=[
            {"page_number": 1, "url": "https://www.g2b.go.kr/", "html": _SAMPLE_HTML}
        ],
        opening_exc=ValueError("KONEPS opening-result menu could not be located"),
    )
    result = browser_crawl.collect_live_items(service, _request())

    # Notice crawl still succeeds; opening-result failure is classified, not raised.
    assert result["items"][0].notice_number == "20260507-001"
    meta = result["metadata"]
    assert meta["opening_result_failure_category"] == "selector_drift"
    assert meta["opening_result_failure_stage"] == "opening_result"
    assert meta["opening_result_retryable"] is False
    assert "opening-result menu" in meta["opening_result_error"]


def test_collect_live_items_caps_items_at_max_items():
    rows = "".join(
        f"<tr><td>2026-{i:03d}</td><td>제목 {i}</td><td>1</td>"
        "<td>1</td><td>2026-05-10 18:00</td><td>서울</td>"
        f"<td><a href='https://www.g2b.go.kr/n/{i}'>상세</a></td></tr>"
        for i in range(5)
    )
    html = f"<html><body><table><tbody>{rows}</tbody></table></body></html>"
    service = _StubService(
        snapshots=[{"page_number": 1, "url": "https://www.g2b.go.kr/", "html": html}]
    )
    result = browser_crawl.collect_live_items(service, _request(max_items=2))
    assert len(result["items"]) == 2


# --- 개찰결과 행 승격 경계 (promote_opening_result_rows) --------------------


def test_promote_keeps_typed_rows_unchanged():
    """이미 ``OpeningResultRow`` 인 행은 재검증 없이 같은 객체로 통과한다(브라우저 경로)."""
    row = OpeningResultRow(notice_number="20260507-011", status="개찰완료")

    promoted = browser_crawl.promote_opening_result_rows([row])

    assert promoted[0] is row


def test_promote_validates_already_normalized_dict_rows_without_reparsing():
    """이미 내부 이름으로 온 dict 행은 승격만 한다(원시 키 재해석 없음).

    승격 경계가 dict 릴레이 시절의 분기 판정(``notice_number`` 유무)을 그대로 따르는지
    고정한다 — 여기서 정규화를 태우면 ``bidPbancNo`` 계열 폴백이 값을 덮어쓴다.
    """
    promoted = browser_crawl.promote_opening_result_rows(
        [
            {
                "notice_number": "20260507-010",
                "bidPbancNo": "무시되어야 하는 원시 키",
                "status": "개찰완료",
                "scheduled_at": datetime(2026, 5, 10, 18, 5, tzinfo=UTC),
            }
        ]
    )

    assert promoted[0].notice_number == "20260507-010"
    assert promoted[0].scheduled_at == datetime(2026, 5, 10, 18, 5, tzinfo=UTC)
    # 정규화를 타지 않았으므로 원시 키에서 만들어지는 full number 는 생기지 않는다.
    assert promoted[0].notice_full_number is None


def test_promote_normalizes_raw_grid_rows():
    """WebSquare 원시 키 행은 정규화를 태운다(내부 이름 부재 판정)."""
    promoted = browser_crawl.promote_opening_result_rows(
        [{"bidPbancNo": "20260507-020", "bidPbancOrd": "00", "prcmBsneSeCd": "07"}]
    )

    assert promoted[0].notice_number == "20260507-020"
    assert promoted[0].notice_full_number == "20260507-020-00"
    assert promoted[0].business_type == "공사"


def test_promote_rejects_unparseable_instant():
    """승격 경계에서 해석 불가 시각 토큰은 거부된다(소비 지점 AttributeError 대신).

    dict 릴레이 시절에는 문자열 ``scheduled_at`` 이 merge 안쪽까지 흘러가
    ``.isoformat()`` 호출에서 죽었다. 계약 위반은 경계에서 드러나야 한다.
    """
    with pytest.raises(ValidationError):
        browser_crawl.promote_opening_result_rows(
            [{"notice_number": "20260507-012", "scheduled_at": "2026/05/10 18:05"}]
        )
