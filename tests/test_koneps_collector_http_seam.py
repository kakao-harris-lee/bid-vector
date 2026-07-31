"""KONEPS collector 의 HTTP 획득 seam **전달(threading)** 고정 테스트.

``KonepsCollectorService(http_get=...)`` 로 주입한 seam 이 하류 호출로 실제로 전달되는지
고정한다. 주입은 됐는데 전달 인자가 빠지면 그 경로는 조용히 기본 transport(= 실
``requests``)로 나가므로, 여기서는 기본 transport 를 **폭발 스텁**으로 치환해 "기본 경로가
불리면 곧바로 실패" 하게 만든다(라이브러리를 문자열 경로로 패치하지 않는다).

scsbid sweep / reserve-detail 경로는 defer·reuse·forward 스위트가 이미 같은 방식으로
가드한다. 이 파일은 그 스위트가 닿지 않는 두 전달 지점을 덮는다:

* ``collect_notices(source="koneps-openapi")`` -> ``collection.collect_openapi_items``
* ``fetch_detail_html_payload`` -> ``http_client.fetch_detail_html_payload``
"""

from __future__ import annotations

from app.core.config import settings
from app.schemas.schemas import CrawlRequest
from app.services.koneps import http_client
from app.services.koneps.collector import KonepsCollectorService
from tests.support.koneps_openapi_fakes import FakeOpenApiResponse, openapi_body


class _FakeHtmlResponse:
    """``requests.Response`` 최소 대역 — 상세 페이지 HTML 만 제공한다."""

    status_code = 200

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _explode(url, *, params, timeout):
    raise AssertionError(f"default transport must not run (url={url})")


def _notice_row(notice_number: str) -> dict:
    return {
        "bidNtceNo": notice_number,
        "bidNtceOrd": "000",
        "bidNtceNm": "주입 seam 공고",
        "bidNtceDt": "2026-05-13 09:00:00",
        "bidClseDt": "2026-05-20 10:00:00",
        "asignBdgtAmt": "125000000",
        "presmptPrce": "113636364",
        "bidNtceDtlUrl": "https://example.test/notice/1",
    }


def test_collect_notices_openapi_source_uses_injected_seam(monkeypatch):
    """koneps-openapi 수집이 주입된 seam 으로만 나간다(기본 transport 미사용)."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_ENCODED_SERVICE_KEY", "")
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(http_client, "_default_http_get", _explode)

    calls: list[str] = []

    def fake_get(url, params, timeout):
        calls.append(url)
        return FakeOpenApiResponse(
            openapi_body([_notice_row("SEAM-1")], total_count=1, num_of_rows=100)
        )

    service = KonepsCollectorService(http_get=fake_get)
    result = service.collect_notices(
        CrawlRequest(
            source="koneps-openapi",
            category="software",
            target_date="2026-05-13",
            max_items=1,
        )
    )

    assert result["job_status"] == "completed"
    assert result["metadata"]["resolved_mode"] == "openapi"
    assert [item.notice_number for item in result["items"]] == ["SEAM-1"]
    assert len(calls) == 1


def test_fetch_detail_html_payload_uses_injected_seam(monkeypatch):
    """상세 페이지 조회도 주입된 seam 으로만 나간다(기본 transport 미사용)."""
    monkeypatch.setattr(http_client, "_default_http_get", _explode)

    captured: dict = {}

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return _FakeHtmlResponse("<html><body>업종 없음</body></html>")

    service = KonepsCollectorService(http_get=fake_get)
    payload = service.fetch_detail_html_payload("https://example.test/detail/1")

    assert captured["url"] == "https://example.test/detail/1"
    assert captured["params"] is None
    assert captured["timeout"] >= 1
    assert set(payload) == {"business_type_code", "business_type_label"}
