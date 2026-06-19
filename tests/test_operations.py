"""Tests for operations skeleton endpoints."""

from datetime import UTC, datetime, timedelta
import json

from app.core.config import settings
from app.core.single_user import ensure_operator_account
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    CompanyProfile,
    CrawlJob,
    HistoricalData,
    Notification,
    OperatorNotificationChannel,
    PricePrediction,
    Project,
    TenderResult,
    User,
)
from app.schemas.schemas import (
    BidDecisionSaveRequest,
    CrawlRequest,
    OpportunityAnalysisRequest,
)
from app.services.allocation import BidDecisionService
from app.services.classifier import NoticeClassifierService
from app.services.koneps.collector import KonepsCollectorService
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor
from app.services.opportunity_analysis import OpportunityAnalysisService


def test_crawl_skeleton(client):
    """Crawl endpoint should return a valid skeleton response."""
    response = client.post(
        "/api/v1/operations/crawl",
        json={"source": "koneps", "category": "software"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["job_status"] == "mock"
    assert data["source"] == "koneps"
    assert data["collected_count"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["notice_number"].startswith("KONEPS-")
    assert data["items"][0]["business_type"] == "software"
    assert data["items"][0]["metadata"]["mode"] == "mock"
    assert data["metadata"]["resolved_mode"] == "mock"


def test_openapi_crawl_collects_bid_public_info_and_persists_history(
    client,
    test_db,
    monkeypatch,
):
    """OpenAPI source should collect BidPublicInfoService rows through the crawl endpoint."""

    class FakeOpenApiResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                    "body": {
                        "items": {
                            "item": [
                                {
                                    "bidNtceNo": "R26BK01510407",
                                    "bidNtceOrd": "000",
                                    "bidNtceNm": "AI 소프트웨어 통합 구축",
                                    "ntceKindNm": "등록공고",
                                    "bidNtceDt": "2026-05-13 09:00:00",
                                    "bidBeginDt": "2026-05-13 10:00:00",
                                    "bidClseDt": "2026-05-20 10:00:00",
                                    "opengDt": "2026-05-20 11:00:00",
                                    "ntceInsttNm": "조달청",
                                    "dminsttNm": "서울특별시교육청",
                                    "bidMethdNm": "전자입찰",
                                    "cntrctCnclsMthdNm": "제한경쟁",
                                    "asignBdgtAmt": "125000000",
                                    "presmptPrce": "113636364",
                                    "bsnsDivNm": "용역",
                                    "prtcptLmtRgnNm": "서울특별시",
                                    "bidNtceDtlUrl": "https://www.g2b.go.kr/detail/R26BK01510407",
                                }
                            ]
                        },
                        "numOfRows": "5",
                        "pageNo": "1",
                        "totalCount": "1",
                    },
                }
            }

    captured = {}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeOpenApiResponse()

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr("app.services.koneps.collector.requests.get", fake_get)

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps-openapi",
            "category": "software",
            "target_date": "2026-05-13",
            "max_items": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_status"] == "completed"
    assert payload["metadata"]["resolved_mode"] == "openapi"
    assert payload["metadata"]["openapi_operation"] == "getBidPblancListInfoServc"
    assert payload["items"][0]["notice_number"] == "R26BK01510407"
    assert payload["items"][0]["base_amount"] == 125000000.0
    assert captured["params"]["ServiceKey"] == "test-service-key"
    assert captured["params"]["inqryBgnDt"] == "202605130000"
    assert captured["params"]["inqryEndDt"] == "202605132359"

    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510407")
        .one()
    )
    assert historical_record.category == "software"
    assert historical_record.base_amount == 125000000.0
    assert historical_record.predicted_price == 113636364.0
    assert historical_record.bid_rate == 0.0
    project = (
        test_db.query(Project).filter(Project.notice_number == "R26BK01510407").one()
    )
    assert historical_record.project_id == project.id
    assert project.issuing_agency == "조달청"
    assert project.demand_agency == "서울특별시교육청"


def test_scsbid_openapi_crawl_collects_awards_and_reserve_details(
    client,
    test_db,
    monkeypatch,
):
    """ScsbidInfoService rows should persist award rates and reserve patterns."""

    class FakeOpenApiResponse:
        status_code = 200
        text = "{}"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    captured = []

    def fake_get(url, params, timeout):
        captured.append({"url": url, "params": params, "timeout": timeout})
        if "getScsbidListSttusCnstwk" in url:
            return FakeOpenApiResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                        "body": {
                            "items": {
                                "item": [
                                    {
                                        "bidNtceNo": "R26BK01599999",
                                        "bidNtceOrd": "000",
                                        "bidClsfcNo": "0",
                                        "rbidNo": "0",
                                        "bidNtceNm": "테스트 공사 낙찰",
                                        "prtcptCnum": "12",
                                        "bidwinnrNm": "테스트 낙찰사",
                                        "bidwinnrBizno": "1234567890",
                                        "sucsfbidAmt": "88,123,000",
                                        "sucsfbidRate": "88.123",
                                        "rlOpengDt": "2026-05-13 11:00:00",
                                        "dminsttNm": "서울특별시",
                                        "rgstDt": "2026-05-13 12:00:00",
                                        "fnlSucsfDate": "2026-05-13",
                                    }
                                ]
                            },
                            "numOfRows": "5",
                            "pageNo": "1",
                            "totalCount": "1",
                        },
                    }
                }
            )
        if "getOpengResultListInfoCnstwkPreparPcDetail" in url:
            return FakeOpenApiResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                        "body": {
                            "items": {
                                "item": [
                                    {
                                        "bidNtceNo": "R26BK01599999",
                                        "bidNtceOrd": "000",
                                        "bidNtceNm": "테스트 공사 낙찰",
                                        "plnprc": "100000000",
                                        "bssamt": "100000000",
                                        "compnoRsrvtnPrceSno": "1",
                                        "bsisPlnprc": "99000000",
                                        "drwtYn": "Y",
                                    },
                                    {
                                        "bidNtceNo": "R26BK01599999",
                                        "bidNtceOrd": "000",
                                        "bidNtceNm": "테스트 공사 낙찰",
                                        "plnprc": "100000000",
                                        "bssamt": "100000000",
                                        "compnoRsrvtnPrceSno": "2",
                                        "bsisPlnprc": "100000000",
                                        "drwtYn": "N",
                                    },
                                    {
                                        "bidNtceNo": "R26BK01599999",
                                        "bidNtceOrd": "000",
                                        "bidNtceNm": "테스트 공사 낙찰",
                                        "plnprc": "100000000",
                                        "bssamt": "100000000",
                                        "compnoRsrvtnPrceSno": "4",
                                        "bsisPlnprc": "101000000",
                                        "drwtYn": "Y",
                                    },
                                ]
                            },
                            "numOfRows": "100",
                            "pageNo": "1",
                            "totalCount": "3",
                        },
                    }
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr("app.services.koneps.collector.requests.get", fake_get)

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps-scsbid",
            "category": "construction",
            "target_date": "2026-05-13",
            "max_items": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_status"] == "completed"
    assert payload["metadata"]["resolved_mode"] == "scsbid_openapi"
    assert payload["metadata"]["openapi_operation"] == "getScsbidListSttusCnstwk"
    assert payload["metadata"]["reserve_detail_collected_count"] == 1
    assert payload["items"][0]["notice_number"] == "R26BK01599999"
    assert payload["items"][0]["metadata"]["winning_company"] == "테스트 낙찰사"
    assert payload["items"][0]["metadata"]["winning_rate"] == 0.88123
    assert payload["items"][0]["metadata"]["reserve_prices"] == [
        99000000.0,
        100000000.0,
        101000000.0,
    ]
    assert payload["items"][0]["metadata"]["selected_numbers"] == [1, 4]
    assert captured[0]["params"]["ServiceKey"] == "test-service-key"
    assert captured[0]["params"]["inqryBgnDt"] == "202605130000"
    assert captured[1]["params"]["bidNtceNo"] == "R26BK01599999"

    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01599999")
        .one()
    )
    assert historical_record.category == "construction"
    assert historical_record.base_amount == 100000000.0
    assert historical_record.predicted_price == 100000000.0
    assert historical_record.bid_rate == 0.88123
    assert json.loads(historical_record.reserve_prices) == [
        99000000.0,
        100000000.0,
        101000000.0,
    ]
    assert json.loads(historical_record.selected_numbers) == [1, 4]

    tender_result = test_db.query(TenderResult).one()
    assert tender_result.winning_company == "테스트 낙찰사"
    assert tender_result.winning_amount == 88123000.0
    assert tender_result.winning_rate == 0.88123
    assert tender_result.result_status == "낙찰"


def test_live_crawl_parses_html(monkeypatch):
    """Live mode should parse KONEPS result rows when HTML is available."""
    service = KonepsCollectorService()
    sample_html = """
    <html>
        <body>
            <table id="mf_wfm_container_testTable">
                <tr>
                    <th>No</th><th>업무구분</th><th>공고번호</th><th>공고명</th><th></th><th>공고상태</th><th>국제여부</th>
                    <th>공고일시</th><th>개찰일시</th><th>입찰마감일시</th><th>공고기관</th><th>수요기관</th><th>계약방법</th><th>공도급여부</th><th>투찰</th>
                </tr>
                <tr>
                    <td>1</td>
                    <td>기술용역</td>
                    <td>R26BK01510407</td>
                    <td title="AI 소프트웨어 통합 구축"><label class="link_txt">AI 소프트웨어 통합 구축</label></td>
                    <td><a id="detail-row-1" href="javascript:void(null);">바로가기</a></td>
                    <td>등록공고</td>
                    <td>국내입찰</td>
                    <td>2026/05/08</td>
                    <td>2026/05/10 18:00</td>
                    <td>2026/05/10 18:00</td>
                    <td>조달청</td>
                    <td>서울특별시교육청</td>
                    <td>제한경쟁</td>
                    <td>아니오</td>
                    <td></td>
                </tr>
            </table>
        </body>
    </html>
    """
    detail_html = """
    <html>
        <body>
            <table>
                <tr><th>입찰공고번호</th><td>R26BK01510407</td></tr>
                <tr><th>입찰공고명</th><td>AI 소프트웨어 통합 구축</td></tr>
                <tr><th>입찰유형</th><td>기술용역</td></tr>
                <tr><th>입찰마감일시</th><td>2026.05.10 18:00:00</td></tr>
                <tr><th>개찰일시</th><td>2026.05.10 18:00:00</td></tr>
                <tr><th>기초금액</th><td>125,000,000 KRW</td></tr>
                <tr><th>제한지역</th><td>서울특별시</td></tr>
                <tr><th>면허제한</th><td>SW001</td></tr>
            </table>
        </body>
    </html>
    """

    monkeypatch.setattr(
        service,
        "_gather_live_page_snapshots",
        lambda request: [
            {
                "page_number": 1,
                "url": "https://www.g2b.go.kr/",
                "html": sample_html,
                "detail_pages": {
                    "detail-row-1": {
                        "url": "http://ebid.example.com/detail/R26BK01510407",
                        "html": detail_html,
                    }
                },
            }
        ],
    )

    response = service.collect_notices(
        CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        )
    )

    assert response["job_status"] == "completed"
    assert response["collected_count"] == 1
    assert response["items"][0]["notice_number"] == "R26BK01510407"
    assert response["items"][0]["title"] == "AI 소프트웨어 통합 구축"
    assert response["items"][0]["base_amount"] == 125000000.0
    assert response["items"][0]["region"] == "서울"
    assert (
        response["items"][0]["source_url"]
        == "http://ebid.example.com/detail/R26BK01510407"
    )
    assert response["items"][0]["license_codes"] == ["SW001"]
    assert response["items"][0]["metadata"]["detail_action_id"] == "detail-row-1"
    assert response["items"][0]["metadata"]["detail_collected"] is True
    assert response["items"][0]["metadata"]["mode"] == "live"
    assert response["metadata"]["resolved_mode"] == "live"
    assert response["metadata"]["page_count"] == 1


def test_live_parser_handles_generic_table_rows():
    """Generic table parsing should still work for unit test HTML without the KONEPS result table id."""
    service = KonepsCollectorService()
    sample_html = """
    <html>
        <body>
            <table>
                <tbody>
                    <tr>
                        <td>20260507-001</td>
                        <td>AI 소프트웨어 통합 구축</td>
                        <td>125,000,000</td>
                        <td>121,500,000</td>
                        <td>2026-05-10 18:00</td>
                        <td>서울</td>
                        <td><a href="https://www.g2b.go.kr/notice/20260507-001">상세보기</a></td>
                    </tr>
                </tbody>
            </table>
        </body>
    </html>
    """

    parsed = service._parse_live_html(
        html=sample_html,
        request=CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        ),
        page_url="https://www.g2b.go.kr/",
        page_number=1,
    )

    assert len(parsed) == 1
    assert parsed[0].notice_number == "20260507-001"
    assert parsed[0].title == "AI 소프트웨어 통합 구축"
    assert parsed[0].base_amount == 125000000.0


def test_live_crawl_falls_back_to_mock(monkeypatch):
    """Live mode should gracefully fall back to mock data on failure."""
    service = KonepsCollectorService()

    def fail_fetch(_: CrawlRequest):
        raise RuntimeError("browser not available")

    monkeypatch.setattr(service, "_gather_live_page_snapshots", fail_fetch)

    response = service.collect_notices(
        CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        )
    )

    assert response["job_status"] == "fallback_mock"
    assert response["collected_count"] == 2
    assert response["items"][0]["metadata"]["mode"] == "fallback_mock"
    assert (
        "browser not available" in response["items"][0]["metadata"]["fallback_reason"]
    )
    assert (
        response["items"][0]["metadata"]["fallback_failure_category"]
        == "browser_runtime"
    )
    assert response["metadata"]["resolved_mode"] == "fallback_mock"
    assert response["metadata"]["fallback_failure_category"] == "browser_runtime"
    assert response["metadata"]["fallback_failure_stage"] == "live_collection"
    assert response["metadata"]["fallback_retryable"] is False


def test_live_crawl_fallback_includes_retry_attempts(monkeypatch):
    """Live fallback metadata should preserve retry attempts for operations diagnostics."""
    service = KonepsCollectorService()
    timeout_error = TimeoutError("Timeout 30000ms exceeded while loading KONEPS")
    attempts = [
        service._build_live_retry_attempt(
            stage="notice_search",
            attempt_index=0,
            exc=timeout_error,
            final_attempt=False,
        ),
        service._build_live_retry_attempt(
            stage="notice_search",
            attempt_index=1,
            exc=timeout_error,
            final_attempt=True,
        ),
    ]

    def fail_collect(_: CrawlRequest):
        raise service._live_collection_error(
            stage="notice_search",
            attempts=attempts,
            original_error=timeout_error,
        )

    monkeypatch.setattr(service, "_collect_live_items", fail_collect)

    response = service.collect_notices(
        CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        )
    )

    assert response["job_status"] == "fallback_mock"
    assert response["metadata"]["fallback_failure_category"] == "timeout"
    assert response["metadata"]["fallback_failure_stage"] == "notice_search"
    assert response["metadata"]["fallback_retryable"] is True
    assert response["metadata"]["live_failure"]["attempt_count"] == 2
    assert (
        response["metadata"]["live_retry_attempts"][0]["next_retry_delay_seconds"]
        == 1.5
    )
    assert response["metadata"]["live_retry_attempts"][1]["final_attempt"] is True


def test_live_crawl_records_opening_result_failure(monkeypatch):
    """Opening-result failures should not fail the notice crawl but should be classified."""
    service = KonepsCollectorService()
    sample_html = """
    <html>
        <body>
            <table>
                <tbody>
                    <tr>
                        <td>20260507-001</td>
                        <td>AI 소프트웨어 통합 구축</td>
                        <td>125,000,000</td>
                        <td>121,500,000</td>
                        <td>2026-05-10 18:00</td>
                        <td>서울</td>
                        <td><a href="https://www.g2b.go.kr/notice/20260507-001">상세보기</a></td>
                    </tr>
                </tbody>
            </table>
        </body>
    </html>
    """

    monkeypatch.setattr(
        service,
        "_gather_live_page_snapshots",
        lambda request: [
            {
                "page_number": 1,
                "url": "https://www.g2b.go.kr/",
                "html": sample_html,
            }
        ],
    )

    def fail_opening_results(_: CrawlRequest):
        raise ValueError("KONEPS opening-result menu could not be located")

    monkeypatch.setattr(service, "_collect_opening_result_rows", fail_opening_results)

    response = service.collect_notices(
        CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        )
    )

    assert response["job_status"] == "completed"
    assert response["metadata"]["opening_result_failure_category"] == "selector_drift"
    assert response["metadata"]["opening_result_failure_stage"] == "opening_result"
    assert response["metadata"]["opening_result_retryable"] is False
    assert "opening-result menu" in response["metadata"]["opening_result_error"]


def test_crawl_endpoint_persists_live_fallback_failure_category(
    client, test_db, monkeypatch
):
    """Persisted fallback crawl jobs should keep the classified live failure label."""

    def fail_fetch(self, request):
        raise ValueError("KONEPS public search button could not be located")

    monkeypatch.setattr(
        KonepsCollectorService, "_gather_live_page_snapshots", fail_fetch
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
            "max_items": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_status"] == "fallback_mock"
    assert payload["metadata"]["fallback_failure_category"] == "selector_drift"

    crawl_job = test_db.query(CrawlJob).one()
    assert crawl_job.status == "fallback_mock"
    assert crawl_job.result_count == 1
    assert "[live_collection/selector_drift]" in (crawl_job.error_message or "")


def test_live_crawl_enriches_with_opening_results(monkeypatch):
    """Live mode should merge opening-result data into collected notices when available."""
    service = KonepsCollectorService()
    sample_html = """
    <html>
        <body>
            <table id="mf_wfm_container_testTable">
                <tr>
                    <th>No</th><th>업무구분</th><th>공고번호</th><th>공고명</th><th></th><th>공고상태</th><th>국제여부</th>
                    <th>공고일시</th><th>개찰일시</th><th>입찰마감일시</th><th>공고기관</th><th>수요기관</th><th>계약방법</th><th>공도급여부</th><th>투찰</th>
                </tr>
                <tr>
                    <td>1</td>
                    <td>기술용역</td>
                    <td>R26BK01510407</td>
                    <td title="AI 소프트웨어 통합 구축"><label class="link_txt">AI 소프트웨어 통합 구축</label></td>
                    <td><a id="detail-row-1" href="javascript:void(null);">바로가기</a></td>
                    <td>등록공고</td>
                    <td>국내입찰</td>
                    <td>2026/05/08</td>
                    <td>2026/05/10 18:00</td>
                    <td>2026/05/10 18:00</td>
                    <td>조달청</td>
                    <td>서울특별시교육청</td>
                    <td>제한경쟁</td>
                    <td>아니오</td>
                    <td></td>
                </tr>
            </table>
        </body>
    </html>
    """
    detail_html = """
    <html>
        <body>
            <table>
                <tr><th>입찰공고번호</th><td>R26BK01510407</td></tr>
                <tr><th>입찰공고명</th><td>AI 소프트웨어 통합 구축</td></tr>
                <tr><th>입찰유형</th><td>기술용역</td></tr>
                <tr><th>입찰마감일시</th><td>2026.05.10 18:00:00</td></tr>
                <tr><th>개찰일시</th><td>2026.05.10 18:00:00</td></tr>
                <tr><th>기초금액</th><td>125,000,000 KRW</td></tr>
                <tr><th>제한지역</th><td>서울특별시</td></tr>
                <tr><th>면허제한</th><td>SW001</td></tr>
            </table>
        </body>
    </html>
    """
    opening_detail_html = """
    <html>
        <body>
            <table>
                <tr><th>복수예비가격</th><td>101,000,000 / 102,000,000 / 103,000,000 / 104,000,000 / 105,000,000 / 106,000,000 / 107,000,000 / 108,000,000 / 109,000,000 / 110,000,000 / 111,000,000 / 112,000,000 / 113,000,000 / 114,000,000 / 115,000,000</td></tr>
                <tr><th>선택번호</th><td>1, 4, 7, 12</td></tr>
                <tr><th>낙찰업체</th><td>주식회사 테스트</td></tr>
                <tr><th>낙찰금액</th><td>119,000,000 KRW</td></tr>
                <tr><th>낙찰률</th><td>95.2%</td></tr>
                <tr><th>개찰일시</th><td>2026.05.10 18:05:00</td></tr>
            </table>
        </body>
    </html>
    """

    monkeypatch.setattr(
        service,
        "_gather_live_page_snapshots",
        lambda request: [
            {
                "page_number": 1,
                "url": "https://www.g2b.go.kr/",
                "html": sample_html,
                "detail_pages": {
                    "detail-row-1": {
                        "url": "http://ebid.example.com/detail/R26BK01510407",
                        "html": detail_html,
                    }
                },
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "_collect_opening_result_rows",
        lambda request: [
            {
                "bidPbancNo": "R26BK01510407",
                "bidPbancOrd": "000",
                "bidPbancNoPbancOrd": "R26BK01510407-000",
                "bidPbancNm": "AI 소프트웨어 통합 구축",
                "bidClsfNo": "1",
                "bidPrgrsOrd": "000",
                "dmstGrpNm": "서울특별시교육청",
                "bidPgstCd": "개찰완료",
                "bizAmt": "119,000,000 KRW",
                "onbsPrnmntDt": "2026/05/10 18:05",
                "prcmBsneSeCd": "05",
                "detail_html": opening_detail_html,
            }
        ],
    )

    response = service.collect_notices(
        CrawlRequest(
            source="koneps", category="software", execution_mode="live", keyword="AI"
        )
    )

    assert response["job_status"] == "completed"
    assert response["metadata"]["opening_result_enriched_count"] == 1
    assert response["items"][0]["metadata"]["opening_status"] == "개찰완료"
    assert response["items"][0]["metadata"]["reserve_prices"] == [
        101000000.0,
        102000000.0,
        103000000.0,
        104000000.0,
        105000000.0,
        106000000.0,
        107000000.0,
        108000000.0,
        109000000.0,
        110000000.0,
        111000000.0,
        112000000.0,
        113000000.0,
        114000000.0,
        115000000.0,
    ]
    assert response["items"][0]["metadata"]["selected_numbers"] == [1, 4, 7, 12]
    assert response["items"][0]["metadata"]["winning_company"] == "주식회사 테스트"
    assert response["items"][0]["metadata"]["winning_amount"] == 119000000.0
    assert response["items"][0]["metadata"]["winning_rate"] == 95.2


def test_crawl_endpoint_persists_history_and_job(client, test_db, monkeypatch):
    """Crawl endpoint should persist CrawlJob and opening-related history records."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510407",
                "title": "AI 소프트웨어 통합 구축",
                "base_amount": 125000000.0,
                "estimated_amount": 121500000.0,
                "closing_at": "2026-05-10T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510407",
                "metadata": {
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_scheduled_at": "2026-05-10T18:05:00",
                    "opening_announced_at": "2026-05-10T18:05:00",
                    "reserve_prices": [101000000.0, 102000000.0, 103000000.0],
                    "selected_numbers": [1, 4, 7, 12],
                    "winning_company": "주식회사 테스트",
                    "winning_amount": 119000000.0,
                    "winning_rate": 95.2,
                },
            }
        ],
        "metadata": {
            "resolved_mode": "live",
        },
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["crawl_job_id"] >= 1

    crawl_job = test_db.query(CrawlJob).one()
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1

    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510407")
        .one()
    )
    assert historical_record.agency_name == "서울특별시교육청"
    assert historical_record.base_amount == 125000000.0
    assert json.loads(historical_record.reserve_prices) == [
        101000000.0,
        102000000.0,
        103000000.0,
    ]
    assert json.loads(historical_record.selected_numbers) == [1, 4, 7, 12]

    tender_result = test_db.query(TenderResult).one()
    assert tender_result.winning_company == "주식회사 테스트"
    assert tender_result.winning_amount == 119000000.0
    assert tender_result.winning_rate == 95.2
    assert tender_result.result_status == "개찰완료"


def test_crawl_endpoint_creates_project_and_links_history_feedback_records(
    client, test_db, monkeypatch
):
    """Crawled notices should auto-create a project and bind history/tender feedback rows to it."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510410",
                "title": "프로젝트 자동 연결 AI 통합 구축",
                "base_amount": 125000000.0,
                "estimated_amount": 121500000.0,
                "closing_at": "2026-05-13T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510410",
                "metadata": {
                    "issuing_agency": "조달청",
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "contract_method": "제한경쟁",
                    "opening_announced_at": "2026-05-13T18:05:00",
                    "winning_company": "주식회사 연결 테스트",
                    "winning_amount": 119500000.0,
                    "winning_rate": 95.6,
                },
            }
        ],
        "metadata": {
            "resolved_mode": "live",
        },
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200

    project = test_db.query(Project).one()
    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510410")
        .one()
    )
    tender_result = test_db.query(TenderResult).one()
    crawl_job = test_db.query(CrawlJob).one()

    assert project.title == "프로젝트 자동 연결 AI 통합 구축"
    assert project.category == "software"
    assert project.budget_estimate == 121500000.0
    assert project.status == "awarded"
    assert "공고번호: R26BK01510410" in project.description
    assert "수요기관: 서울특별시교육청" in project.description
    assert "면허요건: SW001" in project.requirements
    assert "지역요건: 서울" in project.requirements

    assert historical_record.project_id == project.id
    assert tender_result.project_id == project.id
    assert crawl_job.project_id == project.id


def test_crawl_endpoint_maps_cancelled_failed_and_re_notice_project_statuses(
    client, test_db, monkeypatch
):
    """Crawled notice lifecycle text should map to richer internal project statuses."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 3,
        "items": [
            {
                "notice_number": "R26BK01510412",
                "title": "재공고 AI 통합 구축",
                "base_amount": 111000000.0,
                "estimated_amount": 109000000.0,
                "closing_at": "2026-05-16T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510412",
                "metadata": {"opening_status": "재공고"},
            },
            {
                "notice_number": "R26BK01510413",
                "title": "유찰 AI 데이터 사업",
                "base_amount": 112000000.0,
                "estimated_amount": 110000000.0,
                "closing_at": "2026-05-09T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510413",
                "metadata": {"opening_status": "유찰"},
            },
            {
                "notice_number": "R26BK01510414",
                "title": "취소 AI 데이터 사업",
                "base_amount": 113000000.0,
                "estimated_amount": 111000000.0,
                "closing_at": "2026-05-16T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510414",
                "metadata": {"opening_status": "공고취소"},
            },
        ],
        "metadata": {"resolved_mode": "live"},
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    projects = {
        project.title: project.status for project in test_db.query(Project).all()
    }
    assert projects["재공고 AI 통합 구축"] == "re_notice"
    assert projects["유찰 AI 데이터 사업"] == "failed"
    assert projects["취소 AI 데이터 사업"] == "cancelled"


def test_crawl_endpoint_links_matching_manual_project_and_upserts_tender_result(
    client, test_db, monkeypatch
):
    """Matching manual projects should be reused and repeated crawls should not duplicate the same tender result."""
    existing_project = Project(
        title="수동 등록 AI 통합 구축",
        description="기존 메모",
        requirements="기존 요구사항",
        budget_estimate=120500000.0,
        category="software",
        deadline=datetime(2026, 5, 14, 18, 0, tzinfo=UTC),
        status="open",
    )
    test_db.add(existing_project)
    test_db.commit()
    test_db.refresh(existing_project)

    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510411",
                "title": "수동 등록 AI 통합 구축",
                "base_amount": 124000000.0,
                "estimated_amount": 121000000.0,
                "closing_at": "2026-05-14T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510411",
                "metadata": {
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_announced_at": "2026-05-14T18:05:00",
                    "winning_company": "주식회사 수동 연결 테스트",
                    "winning_amount": 118000000.0,
                    "winning_rate": 95.1,
                },
            }
        ],
        "metadata": {
            "resolved_mode": "live",
        },
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    for _ in range(2):
        response = client.post(
            "/api/v1/operations/crawl",
            json={
                "source": "koneps",
                "category": "software",
                "execution_mode": "live",
                "keyword": "AI",
            },
        )
        assert response.status_code == 200

    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510411")
        .one()
    )
    tender_results = test_db.query(TenderResult).all()
    projects = test_db.query(Project).order_by(Project.id.asc()).all()
    test_db.refresh(existing_project)

    assert len(projects) == 1
    assert historical_record.project_id == existing_project.id
    assert existing_project.status == "awarded"
    assert "기존 메모" in existing_project.description
    assert "공고번호: R26BK01510411" in existing_project.description
    assert "면허요건: SW001" in existing_project.requirements
    assert len(tender_results) == 1
    assert tender_results[0].project_id == existing_project.id


def test_crawl_endpoint_matches_existing_project_by_notice_number_and_source_url(
    client, test_db, monkeypatch
):
    """Explicit notice metadata should let crawled notices link even when titles differ."""
    existing_project = Project(
        title="내부 검토용 프로젝트명",
        description="기존 수동 메모",
        requirements="내부 요구사항",
        budget_estimate=121000000.0,
        category="software",
        notice_number="R26BK01510415",
        source_url="http://ebid.example.com/detail/R26BK01510415?from=manual",
        issuing_agency="조달청",
        demand_agency="서울특별시교육청",
        deadline=datetime(2026, 5, 15, 18, 0, tzinfo=UTC),
        status="open",
    )
    test_db.add(existing_project)
    test_db.commit()
    test_db.refresh(existing_project)

    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510415",
                "title": "실제 KONEPS 제목은 조금 다른 AI 통합 구축",
                "base_amount": 124000000.0,
                "estimated_amount": 121500000.0,
                "closing_at": "2026-05-15T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510415",
                "metadata": {
                    "issuing_agency": "조달청",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_status": "개찰완료",
                    "opening_announced_at": "2026-05-15T18:05:00",
                    "winning_company": "주식회사 메타매칭 테스트",
                    "winning_amount": 119000000.0,
                    "winning_rate": 95.3,
                },
            }
        ],
        "metadata": {"resolved_mode": "live"},
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    test_db.refresh(existing_project)
    projects = test_db.query(Project).all()
    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510415")
        .one()
    )
    tender_result = test_db.query(TenderResult).one()

    assert len(projects) == 1
    assert existing_project.title == "내부 검토용 프로젝트명"
    assert existing_project.notice_number == "R26BK01510415"
    assert historical_record.project_id == existing_project.id
    assert tender_result.project_id == existing_project.id


def test_crawl_endpoint_keeps_distinct_g2b_detail_links_separate(
    client, test_db, monkeypatch
):
    """G2B detail links carry the notice identity in query parameters."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps-openapi",
        "collected_count": 2,
        "items": [
            {
                "notice_number": "R26BK01522016",
                "title": "도로구조물 정기안전점검 보수공사",
                "base_amount": 161240000.0,
                "estimated_amount": 143970000.0,
                "closing_at": "2026-05-21T10:00:00",
                "business_type": "construction",
                "source_url": (
                    "https://www.g2b.go.kr/link/PNPE027_01/single/"
                    "?bidPbancNo=R26BK01522016&bidPbancOrd=000"
                ),
                "metadata": {"issuing_agency": "경기도 용인시 기흥구"},
            },
            {
                "notice_number": "R26BK01523768",
                "title": "남원태흥 공공주택 전기공사",
                "base_amount": 1868171000.0,
                "estimated_amount": 1579637951.0,
                "closing_at": "2026-06-02T10:00:00",
                "business_type": "construction",
                "source_url": (
                    "https://www.g2b.go.kr/link/PNPE027_01/single/"
                    "?bidPbancNo=R26BK01523768&bidPbancOrd=000"
                ),
                "metadata": {"issuing_agency": "제주특별자치도개발공사"},
            },
        ],
        "metadata": {"resolved_mode": "openapi"},
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps-openapi",
            "category": "construction",
            "target_date": "2026-05-15",
            "max_items": 2,
        },
    )

    assert response.status_code == 200
    projects = test_db.query(Project).order_by(Project.notice_number.asc()).all()
    historical_records = (
        test_db.query(HistoricalData).order_by(HistoricalData.notice_number.asc()).all()
    )

    assert [project.notice_number for project in projects] == [
        "R26BK01522016",
        "R26BK01523768",
    ]
    assert len({record.project_id for record in historical_records}) == 2


def test_crawl_endpoint_matches_existing_project_by_agency_and_similar_title(
    client, test_db, monkeypatch
):
    """Agency metadata should help link near-identical titles that are not exact text matches."""
    existing_project = Project(
        title="서울 AI 데이터 통합 플랫폼",
        description="수동 등록 공고\n공고기관: 조달청\n수요기관: 서울특별시교육청",
        requirements="SW001 보유 업체\n서울 수행 가능",
        budget_estimate=123000000.0,
        category="software",
        issuing_agency="조달청",
        demand_agency="서울특별시교육청",
        deadline=datetime(2026, 5, 17, 18, 0, tzinfo=UTC),
        status="open",
    )
    test_db.add(existing_project)
    test_db.commit()
    test_db.refresh(existing_project)

    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510416",
                "title": "서울 AI 데이터 통합 플랫폼 구축",
                "base_amount": 125000000.0,
                "estimated_amount": 123500000.0,
                "closing_at": "2026-05-17T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510416",
                "metadata": {
                    "issuing_agency": "조달청",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_status": "재공고",
                },
            }
        ],
        "metadata": {"resolved_mode": "live"},
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    test_db.refresh(existing_project)
    projects = test_db.query(Project).all()
    historical_record = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "R26BK01510416")
        .one()
    )

    assert len(projects) == 1
    assert historical_record.project_id == existing_project.id
    assert existing_project.status == "re_notice"


def test_crawl_async_endpoint_returns_pollable_task(client, test_db, monkeypatch):
    """Async crawl endpoint should return a task id, poll URL, and persisted crawl job id."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510408",
                "title": "비동기 AI 소프트웨어 통합 구축",
                "base_amount": 127000000.0,
                "estimated_amount": 122000000.0,
                "closing_at": "2026-05-11T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510408",
                "metadata": {
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_announced_at": "2026-05-11T18:05:00",
                    "winning_company": "주식회사 비동기 테스트",
                    "winning_amount": 120000000.0,
                    "winning_rate": 94.4,
                },
            }
        ],
        "metadata": {
            "resolved_mode": "live",
        },
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    response = client.post(
        "/api/v1/operations/crawl/async",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_name"] == "jobs.collect_koneps_notices"
    assert payload["status"] == "completed"
    assert payload["task_id"]
    assert payload["crawl_job_id"] >= 1
    assert payload["poll_url"].endswith(payload["task_id"])

    crawl_job = (
        test_db.query(CrawlJob).filter(CrawlJob.id == payload["crawl_job_id"]).one()
    )
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1


def test_crawl_task_status_endpoint_returns_completed_result(
    client, test_db, monkeypatch
):
    """Crawl task status endpoint should expose the completed crawl response for eager/fallback tasks."""
    fake_response = {
        "job_status": "completed",
        "source": "koneps",
        "collected_count": 1,
        "items": [
            {
                "notice_number": "R26BK01510409",
                "title": "상태 조회 AI 소프트웨어 통합 구축",
                "base_amount": 129000000.0,
                "estimated_amount": 123000000.0,
                "closing_at": "2026-05-12T18:00:00",
                "business_type": "기술용역",
                "region": "서울",
                "license_codes": ["SW001"],
                "source_url": "http://ebid.example.com/detail/R26BK01510409",
                "metadata": {
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_announced_at": "2026-05-12T18:05:00",
                    "winning_company": "주식회사 상태 테스트",
                    "winning_amount": 121000000.0,
                    "winning_rate": 93.8,
                },
            }
        ],
        "metadata": {
            "resolved_mode": "live",
        },
    }

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None: fake_response,
    )

    kickoff = client.post(
        "/api/v1/operations/crawl/async",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    ).json()

    response = client.get(f"/api/v1/operations/crawl/tasks/{kickoff['task_id']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == kickoff["task_id"]
    assert payload["task_name"] == "jobs.collect_koneps_notices"
    assert payload["status"] == "completed"
    assert payload["raw_status"] == "SUCCESS"
    assert payload["ready"] is True
    assert payload["successful"] is True
    assert payload["error"] is None
    assert payload["crawl_job_id"] == kickoff["crawl_job_id"]
    assert payload["result"]["job_status"] == "completed"
    assert payload["result"]["collected_count"] == 1
    assert payload["result"]["metadata"]["crawl_job_id"] == kickoff["crawl_job_id"]

    crawl_job = (
        test_db.query(CrawlJob).filter(CrawlJob.id == kickoff["crawl_job_id"]).one()
    )
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1


def test_crawl_async_endpoint_reports_failed_task_when_collection_fails(
    client, test_db, monkeypatch
):
    """Async crawl kickoff should surface failure status when eager/fallback execution fails."""

    def raise_collection_error(self, request, db=None):
        raise RuntimeError("simulated crawl failure")

    monkeypatch.setattr(
        KonepsCollectorService, "collect_notices", raise_collection_error
    )

    response = client.post(
        "/api/v1/operations/crawl/async",
        json={
            "source": "koneps",
            "category": "software",
            "execution_mode": "live",
            "keyword": "AI",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_name"] == "jobs.collect_koneps_notices"
    assert payload["status"] == "failed"
    assert payload["crawl_job_id"] >= 1

    status_response = client.get(f"/api/v1/operations/crawl/tasks/{payload['task_id']}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "failed"
    assert status_payload["raw_status"] == "FAILURE"
    assert status_payload["ready"] is True
    assert status_payload["successful"] is False
    assert status_payload["result"] is None
    assert "simulated crawl failure" in status_payload["error"]

    crawl_job = (
        test_db.query(CrawlJob).filter(CrawlJob.id == payload["crawl_job_id"]).one()
    )
    assert crawl_job.status == "failed"
    assert "simulated crawl failure" in (crawl_job.error_message or "")


def test_classifier_service_returns_missing_profile_reason(test_db):
    """Classifier service should explain when a company profile is missing."""
    project = Project(
        title="AI 통합 플랫폼 구축",
        description="공공 데이터 통합 시스템 구축",
        requirements="서울특별시 소재 사업자 우대",
        budget_estimate=150000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()

    result = NoticeClassifierService().classify(project=project, profile=None)

    assert result["matched"] is False
    assert result["score"] == 0.0
    assert "업체 프로필" in result["reasons"][0]


def test_classify_endpoint_matches_company_profile(client, test_db):
    """Classification endpoint should return a positive rule-based match with reasons."""
    user = User(
        username="classifier-fit",
        email="classifier-fit@example.com",
        hashed_password="hashed",
        full_name="Classifier Fit",
        company="Fit Corp",
    )
    project = Project(
        title="서울 AI 통합 플랫폼 구축",
        description="공공기관 정보화 사업",
        requirements="서울특별시 소재 또는 수행 가능한 업체 참여 가능",
        budget_estimate=180000000.0,
        category="software",
    )
    test_db.add_all([user, project])
    test_db.commit()
    test_db.refresh(user)
    test_db.refresh(project)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        region_codes="서울특별시, 경기도",
        annual_revenue=720000000.0,
        capacity_score=0.92,
    )
    test_db.add(profile)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/classify",
        json={"project_id": project.id, "user_id": user.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert payload["score"] >= 0.8
    assert any("업무 구분" in reason for reason in payload["reasons"])
    assert any("지역" in reason for reason in payload["reasons"])
    assert any("예산" in reason or "연매출" in reason for reason in payload["reasons"])
    assert payload["criteria"]["business_type"]["passed"] is True
    assert payload["criteria"]["region"]["passed"] is True
    assert payload["score_breakdown"]["blocking_axes"] == []


def test_classify_endpoint_defaults_to_single_operator_profile(client, test_db):
    """Classification should work without an explicit user_id in single-user mode."""
    user = User(
        username="classifier-default-operator",
        email="classifier-default-operator@example.com",
        hashed_password="hashed",
        full_name="Classifier Default Operator",
        company="Default Operator Corp",
    )
    project = Project(
        title="전국 클라우드 보안 체계 고도화",
        description="공공기관 보안 운영 자동화 프로젝트",
        requirements="전국 수행 가능 업체 참여 가능",
        budget_estimate=220000000.0,
        category="software",
    )
    test_db.add_all([user, project])
    test_db.commit()
    test_db.refresh(user)
    test_db.refresh(project)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        region_codes="전국",
        annual_revenue=900000000.0,
        capacity_score=0.91,
        total_awards=8,
    )
    test_db.add(profile)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/classify",
        json={"project_id": project.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert payload["score"] >= 0.75


def test_classify_endpoint_rejects_region_mismatch(client, test_db):
    """Classification endpoint should fail the basic filter when project and profile regions do not overlap."""
    user = User(
        username="classifier-region-miss",
        email="classifier-region-miss@example.com",
        hashed_password="hashed",
        full_name="Classifier Region Miss",
        company="Miss Corp",
    )
    project = Project(
        title="서울 데이터 센터 고도화",
        description="서울특별시 교육청 대상 시스템 개선",
        requirements="서울특별시 소재 업체만 참여 가능",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add_all([user, project])
    test_db.commit()
    test_db.refresh(user)
    test_db.refresh(project)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        region_codes="부산광역시",
        annual_revenue=600000000.0,
        capacity_score=0.88,
    )
    test_db.add(profile)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/classify",
        json={"project_id": project.id, "user_id": user.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert payload["score"] < 0.6
    assert any(
        "제한지역" in reason or "수행지역" in reason for reason in payload["reasons"]
    )


def test_classify_endpoint_rejects_license_mismatch(client, test_db):
    """Classification endpoint should reject profiles missing required license codes."""
    user = User(
        username="classifier-license-miss",
        email="classifier-license-miss@example.com",
        hashed_password="hashed",
        full_name="Classifier License Miss",
        company="License Miss Corp",
    )
    project = Project(
        title="AI 관제 시스템 구축",
        description="정보통신 기반 통합 관제 플랫폼 구축 사업",
        requirements="필수 면허: SW001, NET001 보유 업체만 참여 가능",
        budget_estimate=130000000.0,
        category="software",
    )
    test_db.add_all([user, project])
    test_db.commit()
    test_db.refresh(user)
    test_db.refresh(project)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=500000000.0,
        capacity_score=0.9,
        total_awards=6,
    )
    test_db.add(profile)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/classify",
        json={"project_id": project.id, "user_id": user.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert any("면허" in reason for reason in payload["reasons"])
    assert any("NET001" in reason for reason in payload["reasons"])
    assert payload["criteria"]["license"]["passed"] is False
    assert "license" in payload["score_breakdown"]["blocking_axes"]


def test_classify_endpoint_rejects_insufficient_capability(client, test_db):
    """Classification endpoint should reject profiles whose capacity is too low for large complex projects."""
    user = User(
        username="classifier-capability-miss",
        email="classifier-capability-miss@example.com",
        hashed_password="hashed",
        full_name="Classifier Capability Miss",
        company="Capability Miss Corp",
    )
    project = Project(
        title="국가 단위 AI 통합 관제센터 고도화",
        description="다기관 클라우드 통합 운영 및 24시간 관제 체계 구축",
        requirements="필수 면허: SW001 보유 업체, 전국 수행 가능",
        budget_estimate=650000000.0,
        budget_max=700000000.0,
        category="software",
    )
    test_db.add_all([user, project])
    test_db.commit()
    test_db.refresh(user)
    test_db.refresh(project)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=2100000000.0,
        capacity_score=0.22,
        total_awards=0,
    )
    test_db.add(profile)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/classify",
        json={"project_id": project.id, "user_id": user.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is False
    assert any(
        "수행능력" in reason or "수행 범위" in reason for reason in payload["reasons"]
    )


def test_classifier_service_semantic_similarity_can_boost_borderline_match(monkeypatch):
    """High semantic similarity should lift borderline rule-based matches."""
    service = NoticeClassifierService()
    project = Project(
        title="AI 데이터 연계 플랫폼",
        description="공공기관 데이터 허브 구축",
        requirements="정보화 사업 경험 우대",
        budget_estimate=0.0,
        category="software",
    )
    profile = CompanyProfile(
        business_type="software",
        license_codes="",
        region_codes="",
        annual_revenue=0.0,
        capacity_score=0.35,
        total_awards=1,
    )

    monkeypatch.setattr(
        service,
        "_compute_semantic_similarity",
        lambda project_text, profile_text: (0.82, "mock-embedding"),
    )

    result = service.classify(project=project, profile=profile)

    assert result["matched"] is True
    assert result["score"] >= 0.65
    assert any("의미 유사도" in reason for reason in result["reasons"])


def test_classifier_service_semantic_similarity_can_reduce_false_positive(monkeypatch):
    """Low semantic similarity should lower an otherwise acceptable rule-based score."""
    service = NoticeClassifierService()
    project = Project(
        title="서울 공공 데이터 시각화 체계 구축",
        description="공공기관 정보화 사업",
        requirements="서울특별시 수행 가능 업체 참여 가능",
        budget_estimate=180000000.0,
        category="software",
    )
    profile = CompanyProfile(
        business_type="software",
        license_codes="",
        region_codes="서울특별시",
        annual_revenue=500000000.0,
        capacity_score=0.72,
        total_awards=4,
    )

    monkeypatch.setattr(
        service,
        "_compute_semantic_similarity",
        lambda project_text, profile_text: (0.02, "mock-embedding"),
    )

    result = service.classify(project=project, profile=profile)

    assert result["matched"] is False
    assert result["score"] < service.MATCH_THRESHOLD
    assert any(
        "false positive" in reason or "의미 유사도" in reason
        for reason in result["reasons"]
    )
    assert result["criteria"]["semantic_similarity"]["passed"] is False
    assert "semantic_similarity" in result["score_breakdown"]["blocking_axes"]


def test_classifier_service_uses_capacity_score_when_revenue_missing(monkeypatch):
    """Classifier should still score budget fitness conservatively when only capacity_score is available."""
    service = NoticeClassifierService()
    project = Project(
        title="AI 민원 데이터 플랫폼 구축",
        description="민원 데이터 분석과 시각화 기능을 포함한 플랫폼 구축",
        requirements="전국 수행 가능 업체 참여 가능",
        budget_estimate=95000000.0,
        category="software",
    )
    profile = CompanyProfile(
        business_type="software",
        license_codes="",
        region_codes="전국",
        annual_revenue=0.0,
        capacity_score=0.86,
        total_awards=3,
    )

    monkeypatch.setattr(
        service,
        "_compute_semantic_similarity",
        lambda project_text, profile_text: (0.72, "mock-embedding"),
    )

    result = service.classify(project=project, profile=profile)

    assert result["matched"] is True
    assert result["score"] >= service.MATCH_THRESHOLD
    assert any("capacity_score" in reason for reason in result["reasons"])


def test_bid_decision_skeleton(client):
    """Bid-decision endpoint should prioritize high-value single-user opportunities."""
    response = client.post(
        "/api/v1/operations/bid-decision",
        json={
            "project_id": 1,
            "recommended_amount": 12345.67,
            "probability_score": 0.88,
            "matched_score": 0.81,
            "deadline_hours_remaining": 6,
            "current_active_bids": 1,
            "max_active_bids": 3,
            "current_workload_score": 0.2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pursue_bid"] is True
    assert data["action"] == "bid_now"
    assert data["priority_score"] >= 0.7
    assert data["project_id"] == 1


def test_bid_decision_response_exposes_breakdown_and_budget_capture(client):
    """Bid-decision responses should expose the upgraded score breakdown for operator review."""
    response = client.post(
        "/api/v1/operations/bid-decision",
        json={
            "project_id": 11,
            "recommended_amount": 94000000.0,
            "budget_estimate": 100000000.0,
            "probability_score": 0.87,
            "matched_score": 0.8,
            "competitiveness_score": 0.82,
            "deadline_hours_remaining": 8,
            "current_active_bids": 1,
            "max_active_bids": 3,
            "current_workload_score": 0.15,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "bid_now"
    assert payload["workload_source"] == "provided"
    assert payload["competitiveness_score"] == 0.82
    assert payload["budget_capture_score"] == 0.94
    assert payload["expected_margin_score"] == 0.94
    assert payload["execution_complexity_score"] == 0.35
    assert payload["score_breakdown"]["competitiveness_signal"] == 0.82
    assert payload["score_breakdown"]["budget_capture_signal"] == 0.94
    assert payload["score_breakdown"]["expected_margin_signal"] == 0.94
    assert payload["score_breakdown"]["execution_complexity_signal"] == 0.35
    assert payload["score_breakdown"]["load_penalty"] > 0
    assert (
        payload["score_breakdown"]["total_penalty"]
        >= payload["score_breakdown"]["load_penalty"]
    )
    assert payload["score_breakdown"]["opportunity_score"] >= payload["priority_score"]


def test_bid_decision_high_execution_complexity_reduces_priority(client):
    """High execution complexity should materially lower priority even when other signals stay strong."""
    base_payload = {
        "project_id": 21,
        "recommended_amount": 118000000.0,
        "budget_estimate": 130000000.0,
        "probability_score": 0.86,
        "matched_score": 0.82,
        "competitiveness_score": 0.8,
        "deadline_hours_remaining": 12,
        "current_active_bids": 1,
        "max_active_bids": 3,
        "current_workload_score": 0.2,
        "expected_margin_score": 0.74,
    }

    low_complexity = client.post(
        "/api/v1/operations/bid-decision",
        json={**base_payload, "execution_complexity_score": 0.32},
    )
    high_complexity = client.post(
        "/api/v1/operations/bid-decision",
        json={**base_payload, "execution_complexity_score": 0.94},
    )

    assert low_complexity.status_code == 200
    assert high_complexity.status_code == 200

    low_payload = low_complexity.json()
    high_payload = high_complexity.json()
    assert low_payload["priority_score"] > high_payload["priority_score"]
    assert high_payload["score_breakdown"]["execution_complexity_penalty"] > 0
    assert (
        high_payload["score_breakdown"]["total_penalty"]
        > low_payload["score_breakdown"]["total_penalty"]
    )


def test_allocate_legacy_route_remains_available(client):
    """Legacy allocation route should remain as a compatibility alias."""
    response = client.post(
        "/api/v1/operations/allocate",
        json={
            "project_id": 2,
            "recommended_amount": 9000.0,
            "probability_score": 0.35,
            "matched_score": 0.4,
            "deadline_hours_remaining": 120,
            "current_active_bids": 4,
            "max_active_bids": 4,
            "current_workload_score": 0.9,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pursue_bid"] is False
    assert data["action"] == "skip"


def test_bid_decision_persistence_creates_record(client, test_db):
    """Persisted bid-decision endpoint should store a reusable decision record for the singleton operator."""
    project = Project(
        title="AI 입찰 추진 기록 생성",
        description="저장형 입찰 추진 결정 검증",
        requirements="신속 대응 필요",
        budget_estimate=98000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 96000000.0,
            "probability_score": 0.86,
            "matched_score": 0.8,
            "deadline_hours_remaining": 8,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.25,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == project.id
    assert payload["operator_id"] >= 1
    assert payload["decision_status"] == "planned"
    assert payload["action"] == "bid_now"
    assert payload["pursue_bid"] is True
    assert payload["initial_action"] == "bid_now"
    assert payload["initial_decision_status"] == "planned"
    assert payload["first_decided_at"] is not None
    assert payload["expected_margin_score"] > 0.0
    assert payload["execution_complexity_score"] >= 0.0
    assert (
        payload["score_breakdown"]["expected_margin_signal"]
        == payload["expected_margin_score"]
    )
    assert payload["workload_source"] == "provided"

    record = test_db.query(BidDecisionRecord).one()
    assert record.project_id == project.id
    assert record.operator_id == payload["operator_id"]
    assert record.decision_status == "planned"
    assert record.priority_score >= 0.7
    assert record.initial_action == "bid_now"
    assert record.initial_decision_status == "planned"
    assert record.first_decided_at is not None
    assert record.expected_margin_score == payload["expected_margin_score"]
    assert record.score_breakdown

    notification = test_db.query(Notification).one()
    assert notification.type == "recommendation"
    assert f"프로젝트 {project.id}" in notification.title
    assert "입찰 판단 알림" in notification.message
    assert "우선순위" in notification.message


def test_list_bid_decisions_includes_persisted_breakdown_metadata(client, test_db):
    """Listing persisted decisions should expose the stored score breakdown metadata."""
    project = Project(
        title="영속화 점수 메타데이터 공고",
        description="저장된 decision breakdown 반환 검증",
        requirements="즉시 추진 판단 근거를 남겨야 함",
        budget_estimate=102000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 95500000.0,
            "probability_score": 0.88,
            "matched_score": 0.82,
            "deadline_hours_remaining": 9,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.18,
        },
    )

    assert save_response.status_code == 200

    list_response = client.get(
        "/api/v1/operations/bid-decisions", params={"project_id": project.id}
    )
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["project_id"] == project.id
    assert payload[0]["expected_margin_score"] > 0.0
    assert payload[0]["execution_complexity_score"] >= 0.0
    assert (
        payload[0]["score_breakdown"]["expected_margin_signal"]
        == payload[0]["expected_margin_score"]
    )
    assert (
        payload[0]["score_breakdown"]["total_penalty"]
        >= payload[0]["score_breakdown"]["load_penalty"]
    )


def test_get_bid_decision_detail_returns_project_snapshot_and_timeline(client, test_db):
    """Decision detail endpoint should return the current record plus project-level decision history."""
    project = Project(
        title="상세 decision timeline 공고",
        description="decision detail 응답 검증",
        requirements="과거 판단 이력을 함께 조회해야 함",
        budget_estimate=112000000.0,
        category="software",
        notice_number="DETAIL-001",
        issuing_agency="조달청",
        demand_agency="서울특별시교육청",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    operator = ensure_operator_account(test_db)
    older_record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="submitted",
        recommended_amount=103000000.0,
        probability_score=0.78,
        matched_score=0.76,
        priority_score=0.74,
        urgency_score=0.6,
        competitiveness_score=0.67,
        budget_capture_score=0.92,
        expected_margin_score=0.71,
        execution_complexity_score=0.42,
        deadline_hours_remaining=30,
        current_active_bids=0,
        max_active_bids=3,
        current_workload_score=0.14,
        workload_source="provided",
        score_breakdown=json.dumps(
            {"expected_margin_signal": 0.71, "total_penalty": 0.08}, ensure_ascii=False
        ),
        reasoning="기존 제출 이력입니다.",
        created_at=datetime.now(UTC) - timedelta(days=2),
        updated_at=datetime.now(UTC) - timedelta(days=1),
    )
    test_db.add(older_record)
    test_db.commit()
    test_db.refresh(older_record)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 105500000.0,
            "probability_score": 0.89,
            "matched_score": 0.84,
            "deadline_hours_remaining": 7,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.18,
        },
    )

    assert save_response.status_code == 200
    decision_id = save_response.json()["id"]

    detail_response = client.get(
        f"/api/v1/operations/bid-decisions/{decision_id}",
        params={"timeline_limit": 10},
    )

    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["record"]["id"] == decision_id
    assert payload["project"]["id"] == project.id
    assert payload["project"]["title"] == project.title
    assert payload["project"]["notice_number"] == "DETAIL-001"
    assert payload["project"]["issuing_agency"] == "조달청"
    assert payload["timeline_count"] == 2
    assert payload["timeline_limit_applied"] == 10
    assert [item["id"] for item in payload["timeline"]] == [
        decision_id,
        older_record.id,
    ]
    assert (
        payload["timeline"][0]["score_breakdown"]["expected_margin_signal"]
        == payload["timeline"][0]["expected_margin_score"]
    )
    assert payload["timeline"][1]["decision_status"] == "submitted"


def test_project_bid_decision_timeline_returns_limited_recent_history(client, test_db):
    """Project timeline endpoint should report total history while limiting returned rows."""
    project = Project(
        title="프로젝트 기준 decision timeline 공고",
        description="timeline endpoint 검증",
        requirements="최근 판단 이력을 확인해야 함",
        budget_estimate=99000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    operator = ensure_operator_account(test_db)
    older_record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=False,
        action="skip",
        decision_status="skipped",
        recommended_amount=91000000.0,
        probability_score=0.31,
        matched_score=0.43,
        priority_score=0.28,
        urgency_score=0.25,
        competitiveness_score=0.45,
        budget_capture_score=0.92,
        expected_margin_score=0.48,
        execution_complexity_score=0.39,
        deadline_hours_remaining=96,
        current_active_bids=3,
        max_active_bids=3,
        current_workload_score=0.91,
        workload_source="provided",
        score_breakdown=json.dumps(
            {"expected_margin_signal": 0.48, "total_penalty": 0.29}, ensure_ascii=False
        ),
        reasoning="기존 보류 이력입니다.",
        created_at=datetime.now(UTC) - timedelta(days=3),
        updated_at=datetime.now(UTC) - timedelta(days=2),
    )
    newer_record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="review",
        decision_status="reviewing",
        recommended_amount=93500000.0,
        probability_score=0.63,
        matched_score=0.71,
        priority_score=0.59,
        urgency_score=0.55,
        competitiveness_score=0.61,
        budget_capture_score=0.94,
        expected_margin_score=0.64,
        execution_complexity_score=0.52,
        deadline_hours_remaining=24,
        current_active_bids=1,
        max_active_bids=3,
        current_workload_score=0.26,
        workload_source="auto",
        score_breakdown=json.dumps(
            {"expected_margin_signal": 0.64, "total_penalty": 0.12}, ensure_ascii=False
        ),
        reasoning="최신 검토 이력입니다.",
        created_at=datetime.now(UTC) - timedelta(days=1),
        updated_at=datetime.now(UTC) - timedelta(hours=3),
    )
    test_db.add_all([older_record, newer_record])
    test_db.commit()
    test_db.refresh(older_record)
    test_db.refresh(newer_record)

    response = client.get(
        f"/api/v1/operations/projects/{project.id}/bid-decision-timeline",
        params={"limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator.id
    assert payload["project"]["id"] == project.id
    assert payload["project"]["title"] == project.title
    assert payload["result_count"] == 2
    assert payload["limit_applied"] == 1
    assert payload["latest_decision_record_id"] == newer_record.id
    assert len(payload["timeline"]) == 1
    assert payload["timeline"][0]["id"] == newer_record.id
    assert payload["timeline"][0]["workload_source"] == "auto"


def test_bid_decision_persistence_updates_existing_active_record(client, test_db):
    """Saving the same project again should update the active decision record instead of duplicating it."""
    project = Project(
        title="AI 입찰 추진 기록 갱신",
        description="활성 결정 레코드 재사용 검증",
        requirements="추가 검토 후 즉시 판단",
        budget_estimate=88000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    first = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 82000000.0,
            "probability_score": 0.58,
            "matched_score": 0.58,
            "deadline_hours_remaining": 48,
            "current_active_bids": 1,
            "max_active_bids": 3,
            "current_workload_score": 0.2,
        },
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["decision_status"] == "reviewing"

    second = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 81500000.0,
            "probability_score": 0.9,
            "matched_score": 0.86,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 3,
            "current_workload_score": 0.1,
        },
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["id"] == first_payload["id"]
    assert second_payload["decision_status"] == "planned"
    assert second_payload["action"] == "bid_now"
    assert test_db.query(BidDecisionRecord).count() == 1


def test_list_bid_decisions_filters_persisted_records(client, test_db):
    """Persisted bid decisions should be listable and filterable by workflow status."""
    first_project = Project(
        title="보류 후보 공고",
        description="우선순위 낮은 공고",
        requirements="여유 있게 검토 가능",
        budget_estimate=45000000.0,
        category="software",
    )
    second_project = Project(
        title="즉시 제출 후보 공고",
        description="이미 제출 단계까지 올린 공고",
        requirements="즉시 추진",
        budget_estimate=150000000.0,
        category="software",
    )
    test_db.add_all([first_project, second_project])
    test_db.commit()
    test_db.refresh(first_project)
    test_db.refresh(second_project)

    skip_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": first_project.id,
            "recommended_amount": 43000000.0,
            "probability_score": 0.3,
            "matched_score": 0.35,
            "deadline_hours_remaining": 120,
            "current_active_bids": 3,
            "max_active_bids": 3,
            "current_workload_score": 0.95,
        },
    )
    assert skip_response.status_code == 200
    assert skip_response.json()["decision_status"] == "skipped"

    submitted_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": second_project.id,
            "recommended_amount": 144000000.0,
            "probability_score": 0.93,
            "matched_score": 0.89,
            "deadline_hours_remaining": 4,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.15,
            "decision_status": "submitted",
        },
    )
    assert submitted_response.status_code == 200
    assert submitted_response.json()["decision_status"] == "submitted"

    filtered = client.get(
        "/api/v1/operations/bid-decisions", params={"decision_status": "submitted"}
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert len(filtered_payload) == 1
    assert filtered_payload[0]["project_id"] == second_project.id
    assert filtered_payload[0]["decision_status"] == "submitted"


def test_opportunity_analysis_endpoint_returns_multi_angle_analysis(client, test_db):
    """Opportunity analysis should combine fit, market, similarity, and decision guidance in one response."""
    user = User(
        username="analysis-operator",
        email="analysis-operator@example.com",
        hashed_password="hashed",
        full_name="Analysis Operator",
        company="Analysis Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=1200000000.0,
        capacity_score=0.92,
        total_awards=6,
    )
    target_project = Project(
        title="AI 민원 데이터 분석 플랫폼 구축",
        description="민원 데이터 분석과 시각화, 대시보드 자동화가 포함된 플랫폼 구축",
        requirements="SW001 보유 업체, 전국 수행 가능, 운영지원 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=18),
    )
    similar_project = Project(
        title="AI 데이터 대시보드 구축",
        description="데이터 분석과 시각화 중심의 대시보드 시스템 개발",
        requirements="전국 수행 가능, 분석 보고서 자동화",
        budget_estimate=118000000.0,
        category="software",
    )
    second_similar_project = Project(
        title="공공기관 AI 리포트 자동화",
        description="리포트 자동화와 데이터 분석 기능을 포함한 정보화 사업",
        requirements="운영지원, 분석 대시보드",
        budget_estimate=125000000.0,
        category="software",
    )
    test_db.add_all([profile, target_project, similar_project, second_similar_project])
    test_db.commit()
    test_db.refresh(target_project)

    response = client.post(
        "/api/v1/operations/opportunity-analysis",
        json={
            "project_id": target_project.id,
            "current_workload_score": 0.2,
            "similar_limit": 3,
            "min_similarity": 0.15,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == target_project.id
    assert payload["project_title"] == target_project.title
    assert payload["classification"]["matched"] is True
    assert payload["matched"] is True
    assert payload["matched_score"] >= 0.65
    assert payload["price_prediction"]["predicted_price"] > 0
    assert payload["bid_recommendation"]["recommended_bid"] > 0
    assert payload["recommended_amount"] > 0
    assert payload["market_insights"]["competitiveness_score"] >= 0.0
    assert payload["similar_projects"]["result_count"] >= 1
    assert payload["decision"]["action"] in {"bid_now", "review"}
    assert payload["probability_score"] >= 0.6
    assert payload["analysis_summary"]
    assert payload["strengths"]


def test_opportunity_analysis_reflects_existing_active_bid_load(client, test_db):
    """Opportunity analysis should count existing active bid decisions and surface workload risks."""
    user = User(
        username="analysis-load-operator",
        email="analysis-load-operator@example.com",
        hashed_password="hashed",
        full_name="Analysis Load Operator",
        company="Analysis Load Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=900000000.0,
        capacity_score=0.88,
        total_awards=4,
    )
    target_project = Project(
        title="AI 통합 운영 대시보드 구축",
        description="공공기관 운영 데이터 통합 및 시각화 고도화",
        requirements="SW001 보유 업체, 전국 수행 가능",
        budget_estimate=140000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=5),
    )
    active_project = Project(
        title="기존 활성 입찰 프로젝트",
        description="이미 진행 중인 검토 건",
        requirements="운영지원",
        budget_estimate=80000000.0,
        category="software",
    )
    test_db.add_all([profile, target_project, active_project])
    test_db.commit()
    test_db.refresh(target_project)
    test_db.refresh(active_project)

    active_record = BidDecisionRecord(
        project_id=active_project.id,
        operator_id=user.id,
        pursue_bid=True,
        action="review",
        decision_status="reviewing",
        recommended_amount=76000000.0,
        probability_score=0.61,
        matched_score=0.7,
        priority_score=0.58,
        current_active_bids=1,
        max_active_bids=1,
        current_workload_score=0.7,
        reasoning="기존 활성 입찰 건입니다.",
    )
    similar_project = Project(
        title="AI 통합 현황판 구축",
        description="통합 현황판과 데이터 시각화 기능 개발",
        requirements="전국 수행 가능",
        budget_estimate=135000000.0,
        category="software",
    )
    test_db.add_all([active_record, similar_project])
    test_db.commit()

    response = client.post(
        "/api/v1/operations/opportunity-analysis",
        json={
            "project_id": target_project.id,
            "max_active_bids": 1,
            "current_workload_score": 0.95,
            "similar_limit": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_active_bids"] == 1
    assert any(
        "실행 부담" in item or "마감 시간" in item for item in payload["risk_flags"]
    )
    assert payload["decision"]["action"] in {"review", "skip"}
    assert payload["decision"]["priority_score"] <= 0.8


def test_opportunity_analysis_auto_computes_workload_when_omitted(client, test_db):
    """Opportunity analysis should derive workload from active bid decisions when the caller omits it."""
    user = User(
        username="analysis-auto-workload-operator",
        email="analysis-auto-workload-operator@example.com",
        hashed_password="hashed",
        full_name="Analysis Auto Workload Operator",
        company="Analysis Auto Workload Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=950000000.0,
        capacity_score=0.9,
        total_awards=5,
    )
    target_project = Project(
        title="AI 자동 workload 분석 대상 공고",
        description="전국 수행 가능한 AI 데이터 분석 플랫폼 구축",
        requirements="SW001 보유 업체, 전국 수행 가능, 데이터 분석 포함",
        budget_estimate=135000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=20),
    )
    active_project = Project(
        title="기존 검토 중인 활성 입찰",
        description="기존 검토 부하를 만드는 활성 입찰",
        requirements="SW001 보유 업체",
        budget_estimate=92000000.0,
        category="software",
    )
    similar_project = Project(
        title="AI 분석 자동화 유사 공고",
        description="AI 데이터 분석과 자동화 대시보드 구축",
        requirements="전국 수행 가능, SW001 보유 업체",
        budget_estimate=128000000.0,
        category="software",
    )
    test_db.add_all([profile, target_project, active_project, similar_project])
    test_db.commit()
    test_db.refresh(target_project)
    test_db.refresh(active_project)

    active_record = BidDecisionRecord(
        project_id=active_project.id,
        operator_id=user.id,
        pursue_bid=True,
        action="review",
        decision_status="reviewing",
        recommended_amount=87000000.0,
        probability_score=0.72,
        matched_score=0.74,
        priority_score=0.68,
        deadline_hours_remaining=12,
        current_active_bids=1,
        max_active_bids=2,
        current_workload_score=0.55,
        reasoning="기존 검토 건입니다.",
    )
    test_db.add(active_record)
    test_db.commit()

    response = client.post(
        "/api/v1/operations/opportunity-analysis",
        json={
            "project_id": target_project.id,
            "max_active_bids": 2,
            "similar_limit": 3,
            "min_similarity": 0.15,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_active_bids"] == 1
    assert payload["workload_source"] == "auto"
    assert payload["current_workload_score"] > 0.0
    assert payload["decision"]["workload_source"] == "auto"
    assert payload["decision"]["expected_margin_score"] > 0.0
    assert payload["decision"]["execution_complexity_score"] > 0.0
    assert payload["decision"]["score_breakdown"]["active_load_ratio"] == 0.5
    assert (
        payload["decision"]["score_breakdown"]["workload_score_used"]
        == payload["current_workload_score"]
    )
    assert payload["decision"]["score_breakdown"]["execution_complexity_penalty"] >= 0.0


def test_opportunity_analysis_uses_agency_weighted_price_history(client, test_db):
    """Opportunity analysis should forward agency context into price prediction and surface reserve-pattern metadata."""
    user = User(
        username="analysis-agency-operator",
        email="analysis-agency-operator@example.com",
        hashed_password="hashed",
        full_name="Analysis Agency Operator",
        company="Analysis Agency Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=1000000000.0,
        capacity_score=0.9,
        total_awards=5,
    )
    project = Project(
        title="AI 학교 데이터 통합 분석 플랫폼 구축",
        description="교육청 통합 데이터 분석 및 시각화 구축",
        requirements="SW001 보유 업체, 전국 수행 가능",
        budget_estimate=125000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=14),
    )
    test_db.add_all([profile, project])
    test_db.commit()
    test_db.refresh(project)

    test_db.add_all(
        [
            HistoricalData(
                notice_number="AN-AGENCY-1",
                agency_name="서울특별시교육청",
                category="software",
                base_amount=125000000.0,
                predicted_price=116250000.0,
                bid_rate=0.93,
                reserve_prices="[120000000.0, 121000000.0, 122000000.0]",
                selected_numbers="[1, 4, 7, 12]",
            ),
            HistoricalData(
                notice_number="AN-AGENCY-2",
                agency_name="서울특별시교육청",
                category="software",
                base_amount=125000000.0,
                predicted_price=115625000.0,
                bid_rate=0.925,
                reserve_prices="[119500000.0, 120500000.0, 121500000.0]",
                selected_numbers="[1, 5, 7, 11]",
            ),
            HistoricalData(
                notice_number="AN-AGENCY-3",
                agency_name="조달청",
                category="software",
                base_amount=125000000.0,
                predicted_price=121250000.0,
                bid_rate=0.97,
                reserve_prices="[118000000.0, 121000000.0, 123000000.0]",
                selected_numbers="[2, 4, 8, 12]",
            ),
        ]
    )
    test_db.commit()

    response = client.post(
        "/api/v1/operations/opportunity-analysis",
        json={
            "project_id": project.id,
            "agency_name": "서울특별시교육청",
            "current_workload_score": 0.2,
            "similar_limit": 3,
            "min_similarity": 0.15,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["price_prediction"]["pricing_mode"] == "historical_blend"
    assert payload["price_prediction"]["agency_match_sample_size"] == 2
    assert payload["price_prediction"]["reserve_price_context"]["sample_count"] == 3
    assert (
        1
        in payload["price_prediction"]["reserve_price_context"][
            "frequent_selected_numbers"
        ]
    )


def test_opportunity_analysis_applies_feedback_calibration_bias(client, test_db):
    """Opportunity analysis should surface feedback-derived calibration when past tender results exist."""
    user = User(
        username="analysis-feedback-operator",
        email="analysis-feedback-operator@example.com",
        hashed_password="hashed",
        full_name="Analysis Feedback Operator",
        company="Analysis Feedback Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=1100000000.0,
        capacity_score=0.91,
        total_awards=6,
    )
    source_project = Project(
        title="과거 피드백 학습용 프로젝트",
        description="최근 예측 오차를 학습하는 기준 프로젝트",
        requirements="SW001 보유 업체",
        budget_estimate=100000000.0,
        category="software",
    )
    target_project = Project(
        title="피드백 보정이 필요한 신규 프로젝트",
        description="과거 오차를 반영해 예측을 보정해야 하는 프로젝트",
        requirements="SW001 보유 업체, 전국 수행 가능",
        budget_estimate=100000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=20),
    )
    test_db.add_all([profile, source_project, target_project])
    test_db.commit()
    test_db.refresh(source_project)
    test_db.refresh(target_project)

    test_db.add_all(
        [
            PricePrediction(
                user_id=user.id,
                project_id=source_project.id,
                predicted_price=105000000.0,
                price_range_min=100000000.0,
                price_range_max=110000000.0,
                confidence_score=0.8,
                model_version="v1.1-historical",
            ),
            HistoricalData(
                project_id=source_project.id,
                notice_number="AN-CAL-1",
                agency_name="서울특별시교육청",
                category="software",
                base_amount=100000000.0,
                predicted_price=100000000.0,
                bid_rate=1.0,
            ),
            TenderResult(
                project_id=source_project.id,
                winning_company="피드백 학습 낙찰사",
                winning_amount=100000000.0,
                winning_rate=95.0,
                result_status="awarded",
            ),
        ]
    )
    test_db.commit()

    response = client.post(
        "/api/v1/operations/opportunity-analysis",
        json={
            "project_id": target_project.id,
            "agency_name": "서울특별시교육청",
            "current_workload_score": 0.15,
            "similar_limit": 3,
            "min_similarity": 0.15,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["price_prediction"]["feedback_calibration"]["sample_count"] == 1
    assert (
        payload["price_prediction"]["feedback_calibration"]["agency_match_sample_count"]
        == 1
    )
    assert (
        payload["price_prediction"]["feedback_calibration"]["applied_adjustment_rate"]
        < 0
    )
    assert payload["price_prediction"]["model_version"].endswith("+feedback")


def test_notify_telegram_skeleton(client):
    """Telegram skeleton endpoint should return configuration-aware response."""
    response = client.post(
        "/api/v1/operations/notify/telegram",
        json={
            "title": "신규 공고",
            "message": "AI 추천가가 준비되었습니다.",
            "url": "https://example.com/notices/1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_name"] == "send_telegram_notification"
    assert data["status"] in {"ready", "pending_configuration"}


def test_notify_telegram_endpoint_attempts_delivery_when_configured(
    client, monkeypatch
):
    """Manual Telegram notification endpoint should attempt delivery when configured."""
    deliveries: list[str] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test-bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(message)
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    response = client.post(
        "/api/v1/operations/notify/telegram",
        json={
            "title": "즉시 확인",
            "message": "새로운 고우선순위 공고가 감지되었습니다.",
            "url": "https://example.com/notices/high-priority",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert len(deliveries) == 1
    assert "즉시 확인" in deliveries[0]
    assert "Telegram delivery succeeded." in data["detail"]


def test_telegram_send_message_skips_api_in_test_environment(monkeypatch):
    """Configured Telegram delivery must still avoid external API calls in test."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")

    def fail_post(self, method_name: str, payload: dict[str, object]):
        raise AssertionError("ENVIRONMENT=test must not call Telegram Bot API")

    monkeypatch.setattr(TelegramNotificationService, "_post_json", fail_post)

    delivery = TelegramNotificationService().send_message("dry-run only")

    assert delivery["sent"] is False
    assert delivery["status"] == "skipped_test_environment"


def test_telegram_callback_updates_bid_decision_state(client, test_db, monkeypatch):
    """Telegram callback endpoint should update the persisted bid decision and acknowledge the action."""
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )

    project = Project(
        title="Telegram Callback Project",
        description="Verify inline callback processing",
        requirements="React to Telegram actions",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 115000000.0,
            "probability_score": 0.91,
            "matched_score": 0.84,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )
    assert save_response.status_code == 200
    decision_id = save_response.json()["id"]

    callback_response = client.post(
        "/api/v1/operations/telegram/callback",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "callback-1",
                "data": f"bid-decision:{decision_id}:review",
                "message": {
                    "message_id": 100,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "processed"
    assert payload["action"] == "review"
    assert payload["decision_status"] == "reviewing"
    assert acknowledgements == [("callback-1", "검토 처리 완료")]

    record = (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.id == decision_id)
        .one()
    )
    assert record.action == "review"
    assert record.decision_status == "reviewing"
    assert "텔레그램에서 검토 버튼" in record.reasoning


def _run_telegram_callback_confirmation_case(
    client,
    test_db,
    monkeypatch,
    *,
    callback_action: str,
    expected_status: str,
    expected_ack: str,
    expected_guidance_fragment: str,
):
    """Drive an inline callback action and capture chat confirmation deliveries."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(
            {"message": message, "chat_id": chat_id, "reply_markup": reply_markup}
        )
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )

    project = Project(
        title="Telegram Callback Confirmation Project",
        description="Verify inline callback chat confirmation",
        requirements="React to Telegram actions",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 115000000.0,
            "probability_score": 0.91,
            "matched_score": 0.84,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )
    assert save_response.status_code == 200
    decision_id = save_response.json()["id"]

    callback_response = client.post(
        "/api/v1/operations/telegram/callback",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "callback-confirm",
                "data": f"bid-decision:{decision_id}:{callback_action}",
                "message": {
                    "message_id": 100,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "processed"
    assert payload["decision_status"] == expected_status

    # Toast acknowledgement still fires for the inline button.
    assert acknowledgements == [("callback-confirm", expected_ack)]

    # The regression fix: a persistent chat confirmation must be delivered too.
    assert deliveries, "expected a chat confirmation message to be sent"
    confirmation = deliveries[-1]
    assert confirmation["chat_id"] == "1594710346"
    assert "입찰 판단 처리" in confirmation["message"]
    assert expected_ack in confirmation["message"]
    assert expected_guidance_fragment in confirmation["message"]

    return decision_id, deliveries


def test_telegram_callback_submit_sends_chat_confirmation(client, test_db, monkeypatch):
    """Tapping the inline 투찰 button should send a persistent chat confirmation."""
    _run_telegram_callback_confirmation_case(
        client,
        test_db,
        monkeypatch,
        callback_action="submit",
        expected_status="submitted",
        expected_ack="투찰 처리 완료",
        expected_guidance_fragment="실제 나라장터 투찰서 제출은 직접 진행하세요",
    )


def test_telegram_callback_review_sends_chat_confirmation(client, test_db, monkeypatch):
    """Tapping the inline 검토 button should send a persistent chat confirmation."""
    _run_telegram_callback_confirmation_case(
        client,
        test_db,
        monkeypatch,
        callback_action="review",
        expected_status="reviewing",
        expected_ack="검토 처리 완료",
        expected_guidance_fragment="검토 상태로 기록되었습니다",
    )


def test_telegram_callback_skip_sends_chat_confirmation(client, test_db, monkeypatch):
    """Tapping the inline 보류 button should send a persistent chat confirmation."""
    _run_telegram_callback_confirmation_case(
        client,
        test_db,
        monkeypatch,
        callback_action="skip",
        expected_status="skipped",
        expected_ack="보류 처리 완료",
        expected_guidance_fragment="보류(스킵)로 기록되었습니다",
    )


def test_telegram_callback_chat_confirmation_failure_does_not_break_processing(
    client,
    test_db,
    monkeypatch,
):
    """A failed chat confirmation send must not break the applied decision response."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def boom_send(self, message: str, reply_markup=None, chat_id=None):
        raise RuntimeError("Telegram delivery exploded")

    def fake_answer(self, callback_query_id: str, text: str):
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", boom_send)
    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )

    project = Project(
        title="Telegram Callback Confirmation Failure Project",
        description="Verify resilience when chat confirmation send fails",
        requirements="React to Telegram actions",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 115000000.0,
            "probability_score": 0.91,
            "matched_score": 0.84,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )
    assert save_response.status_code == 200
    decision_id = save_response.json()["id"]

    callback_response = client.post(
        "/api/v1/operations/telegram/callback",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "callback-fail",
                "data": f"bid-decision:{decision_id}:submit",
                "message": {
                    "message_id": 100,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "processed"
    assert payload["decision_status"] == "submitted"

    record = (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.id == decision_id)
        .one()
    )
    assert record.action == "bid_now"
    assert record.decision_status == "submitted"


def test_telegram_callback_unauthorized_chat_is_ignored(client, test_db, monkeypatch):
    """Inline callbacks from an unauthorized chat must not apply or confirm anything."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id})
        return {"sent": True, "status": "sent", "detail": "ok"}

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {"sent": True, "status": "sent", "detail": "ok"}

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )

    project = Project(
        title="Telegram Unauthorized Callback Project",
        description="Verify unauthorized chat gating",
        requirements="React to Telegram actions",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 115000000.0,
            "probability_score": 0.91,
            "matched_score": 0.84,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )
    assert save_response.status_code == 200
    saved = save_response.json()
    decision_id = saved["id"]
    saved_status = saved["decision_status"]

    # Saving a high-priority decision may itself emit the original Telegram
    # alert. Ignore anything sent before the callback; we only assert that the
    # unauthorized callback produces no *additional* delivery.
    deliveries.clear()

    callback_response = client.post(
        "/api/v1/operations/telegram/callback",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "callback-unauth",
                "data": f"bid-decision:{decision_id}:submit",
                "message": {
                    "message_id": 100,
                    "chat": {"id": 9999999},
                },
            },
        },
    )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "ignored"
    assert payload["detail"] == "unauthorized chat"
    # No confirmation, no toast, no state mutation for unauthorized chats.
    assert deliveries == []
    assert acknowledgements == []

    record = (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.id == decision_id)
        .one()
    )
    assert record.decision_status == saved_status


def test_telegram_callback_owner_route_cannot_cross_operator(
    client,
    test_db,
    monkeypatch,
):
    """Decision callback payloads cannot use one operator route to mutate another operator's record."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id})
        return {"sent": True, "status": "sent", "detail": "ok"}

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {"sent": True, "status": "sent", "detail": "ok"}

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(
        TelegramNotificationService,
        "answer_callback_query",
        fake_answer,
    )

    canonical = _create_operator_user(test_db, username="operator")
    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    _canonical_record = _seed_action_decision(test_db, operator_id=canonical.id)
    synthetic_record = _seed_action_decision(test_db, operator_id=synthetic.id)

    callback_payloads = [
        f"bid-decision:{canonical.id}:{synthetic_record.id}:submit",
        f"bid-decision:{synthetic.id}:{synthetic_record.id}:submit",
        f"bid-decision:{synthetic_record.id}:submit",
    ]
    for index, callback_data in enumerate(callback_payloads, start=1):
        response = client.post(
            "/api/v1/operations/telegram/callback",
            json={
                "update_id": index,
                "callback_query": {
                    "id": f"callback-cross-{index}",
                    "data": callback_data,
                    "message": {
                        "message_id": 100 + index,
                        "chat": {"id": 1594710346},
                    },
                },
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "ignored"
        assert "Bid decision record not found" in payload["detail"]

    assert deliveries == []
    assert acknowledgements == [
        ("callback-cross-1", "처리할 입찰 판단을 찾지 못했습니다"),
        ("callback-cross-2", "처리할 입찰 판단을 찾지 못했습니다"),
        ("callback-cross-3", "처리할 입찰 판단을 찾지 못했습니다"),
    ]

    test_db.refresh(synthetic_record)
    assert synthetic_record.decision_status == "reviewing"
    assert synthetic_record.action == "review"


def test_telegram_text_action_updates_latest_active_bid_decision(
    client,
    test_db,
    monkeypatch,
):
    """Plain Telegram action text should apply to the latest active bid decision."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(
            {"message": message, "chat_id": chat_id, "reply_markup": reply_markup}
        )
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    operator = ensure_operator_account(test_db)
    project = Project(
        title="Telegram Text Action Project",
        description="Verify text action processing",
        requirements="React to Telegram text actions",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        recommended_amount=115000000.0,
        probability_score=0.91,
        matched_score=0.84,
        priority_score=0.82,
        reasoning="high-priority text action test",
    )
    test_db.add(record)
    test_db.commit()
    test_db.refresh(record)

    response = client.post(
        "/api/v1/operations/telegram/webhook",
        json={
            "update_id": 601,
            "message": {
                "message_id": 101,
                "text": "✅ 투찰",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processed"
    assert payload["processed_count"] == 1
    assert payload["processed_update_ids"] == [601]

    test_db.refresh(record)
    assert record.action == "bid_now"
    assert record.decision_status == "submitted"
    assert "텔레그램에서 투찰 버튼" in record.reasoning
    assert deliveries[-1]["chat_id"] == "1594710346"
    assert "투찰 처리 완료" in deliveries[-1]["message"]


def test_telegram_callback_missing_decision_is_ignored_with_ack(
    client,
    monkeypatch,
):
    """Stale callback data should not crash webhook processing."""
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(
        TelegramNotificationService,
        "answer_callback_query",
        fake_answer,
    )

    response = client.post(
        "/api/v1/operations/telegram/webhook",
        json={
            "update_id": 602,
            "callback_query": {
                "id": "callback-stale",
                "data": "bid-decision:999999:submit",
                "message": {
                    "message_id": 102,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ignored"
    assert payload["processed_count"] == 0
    assert "Bid decision record not found" in payload["detail"]
    assert acknowledgements == [
        ("callback-stale", "처리할 입찰 판단을 찾지 못했습니다")
    ]


def test_telegram_callback_ack_failure_does_not_fail_webhook(client, monkeypatch):
    """Expired Telegram callback query ids should not make webhook processing fail."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    def fake_answer(self, callback_query_id: str, text: str):
        raise RuntimeError("Telegram callback acknowledgement failed")

    monkeypatch.setattr(
        TelegramNotificationService,
        "answer_callback_query",
        fake_answer,
    )

    response = client.post(
        "/api/v1/operations/telegram/webhook",
        json={
            "update_id": 603,
            "callback_query": {
                "id": "callback-expired",
                "data": "bid-decision:999999:submit",
                "message": {
                    "message_id": 103,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ignored"
    assert "Bid decision record not found" in payload["detail"]


def test_telegram_webhook_processes_start_message(client, monkeypatch):
    """Webhook endpoint should process `/start` and reply with chat-id guidance."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-token")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id})
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 501,
            "message": {
                "message_id": 99,
                "text": "/start",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processed"
    assert payload["processed_count"] == 1
    assert payload["processed_update_ids"] == [501]
    assert payload["known_chat_ids"] == ["******0346"]
    assert len(deliveries) == 1
    assert deliveries[0]["chat_id"] == "1594710346"
    assert "감지된 chat id: 1594710346" in deliveries[0]["message"]


def test_telegram_webhook_updates_operator_strategy_from_text_command(client, monkeypatch):
    """Telegram strategy commands should update stored watch rules without leaving the chat."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-token")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id, "reply_markup": reply_markup})
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    update_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 601,
            "message": {
                "message_id": 100,
                "text": (
                    "/strategy_set categories=software,security regions=서울특별시 "
                    "keywords=AI,데이터 min_budget=90000000 max_budget=180000000 "
                    "match=0.66 probability=0.61 bid_now=0.77 review=0.52 "
                    "high_priority=false limit=6"
                ),
                "chat": {"id": 1594710346},
            },
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["status"] == "processed"
    strategy_response = client.get("/api/v1/operator/strategy")
    assert strategy_response.status_code == 200
    strategy = strategy_response.json()
    assert strategy["focus_categories"] == ["software", "security"]
    assert strategy["focus_regions"] == ["서울특별시"]
    assert strategy["required_keywords"] == ["AI", "데이터"]
    assert strategy["min_budget_estimate"] == 90000000.0
    assert strategy["max_budget_estimate"] == 180000000.0
    assert strategy["minimum_match_score"] == 0.66
    assert strategy["minimum_probability_score"] == 0.61
    assert strategy["bid_now_threshold"] == 0.77
    assert strategy["review_threshold"] == 0.52
    assert strategy["notify_only_high_priority"] is False
    assert strategy["max_recommended_candidates"] == 6
    assert deliveries[-1]["chat_id"] == "1594710346"
    assert "전략이 업데이트되었습니다." in deliveries[-1]["message"]

    clear_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 602,
            "message": {
                "message_id": 101,
                "text": "/strategy_clear categories budget",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert clear_response.status_code == 200
    strategy = client.get("/api/v1/operator/strategy").json()
    assert strategy["focus_categories"] == []
    assert strategy["min_budget_estimate"] == 0.0
    assert strategy["max_budget_estimate"] == 0.0
    assert strategy["focus_regions"] == ["서울특별시"]
    assert "전략 항목을 초기화했습니다." in deliveries[-1]["message"]


def test_telegram_strategy_buttons_apply_step_edit_after_confirmation(client, monkeypatch):
    """Strategy edit buttons should stage changes until the operator confirms them."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-token")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id, "reply_markup": reply_markup})
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(TelegramNotificationService, "answer_callback_query", fake_answer)

    strategy_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 611,
            "message": {
                "message_id": 110,
                "text": "/strategy",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert strategy_response.status_code == 200
    assert strategy_response.json()["status"] == "processed"
    button_payloads = {
        button["callback_data"]
        for row in deliveries[-1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert {
        "strategy-edit:categories",
        "strategy-edit:regions",
        "strategy-edit:keywords",
        "strategy-edit:budget",
        "strategy-edit:thresholds",
        "strategy-edit:notification",
        "strategy-edit:limit",
    }.issubset(button_payloads)

    select_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 612,
            "callback_query": {
                "id": "strategy-callback-1",
                "data": "strategy-edit:categories",
                "message": {
                    "message_id": 111,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert select_response.status_code == 200
    assert select_response.json()["status"] == "processed"
    assert "업종 새 값을 입력하세요." in deliveries[-1]["message"]
    assert deliveries[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "strategy-edit:cancel"
    TelegramStrategyCommandProcessor.PENDING_EDITS.clear()

    value_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 613,
            "message": {
                "message_id": 112,
                "text": "software,security",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert value_response.status_code == 200
    assert value_response.json()["status"] == "processed"
    assert "적용 전 확인" in deliveries[-1]["message"]
    confirm_payloads = [
        button["callback_data"]
        for row in deliveries[-1]["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert confirm_payloads == ["strategy-edit:apply", "strategy-edit:cancel"]
    assert client.get("/api/v1/operator/strategy").json()["focus_categories"] == []

    apply_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 614,
            "callback_query": {
                "id": "strategy-callback-2",
                "data": "strategy-edit:apply",
                "message": {
                    "message_id": 113,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )

    assert apply_response.status_code == 200
    assert client.get("/api/v1/operator/strategy").json()["focus_categories"] == ["software", "security"]
    assert "전략이 업데이트되었습니다." in deliveries[-1]["message"]
    assert acknowledgements == [
        ("strategy-callback-1", "전략 수정 처리 완료"),
        ("strategy-callback-2", "전략 수정 처리 완료"),
    ]


def test_telegram_strategy_step_rejects_invalid_value_without_mutation(client, monkeypatch):
    """Invalid step values should keep the stored strategy unchanged and show an example."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-token")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "chat_id": chat_id, "reply_markup": reply_markup})
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    def fake_answer(self, callback_query_id: str, text: str):
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(TelegramNotificationService, "answer_callback_query", fake_answer)

    client.put(
        "/api/v1/operator/strategy",
        json={"bid_now_threshold": 0.7, "review_threshold": 0.45},
    )
    client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 621,
            "callback_query": {
                "id": "strategy-callback-3",
                "data": "strategy-edit:thresholds",
                "message": {
                    "message_id": 120,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )
    invalid_response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 622,
            "message": {
                "message_id": 121,
                "text": "bid_now=0.60 review=0.80",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert invalid_response.status_code == 200
    assert "처리 실패:" in deliveries[-1]["message"]
    assert "올바른 예시:" in deliveries[-1]["message"]
    strategy = client.get("/api/v1/operator/strategy").json()
    assert strategy["bid_now_threshold"] == 0.7
    assert strategy["review_threshold"] == 0.45
    client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-token"},
        json={
            "update_id": 623,
            "callback_query": {
                "id": "strategy-callback-4",
                "data": "strategy-edit:cancel",
                "message": {
                    "message_id": 122,
                    "chat": {"id": 1594710346},
                },
            },
        },
    )


def test_telegram_webhook_rejects_invalid_secret(client, monkeypatch):
    """Webhook endpoint should reject requests with the wrong Telegram secret header when configured."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-token")

    response = client.post(
        "/api/v1/operations/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        json={
            "update_id": 1,
            "message": {
                "message_id": 1,
                "text": "/start",
                "chat": {"id": 1594710346},
            },
        },
    )

    assert response.status_code == 403
    assert "Invalid Telegram webhook secret" in response.json()["detail"]


def test_telegram_sync_processes_pending_updates_and_acknowledges_offset(
    client, test_db, monkeypatch
):
    """Manual Telegram sync should process getUpdates results and advance the offset."""
    acknowledgements: list[tuple[str, str]] = []
    get_updates_calls: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    def fake_get_updates(self, offset=None, limit=None, timeout_seconds=None):
        get_updates_calls.append(
            {
                "offset": offset,
                "limit": limit,
                "timeout_seconds": timeout_seconds,
            }
        )
        if offset is None:
            return [
                {
                    "update_id": 42,
                    "callback_query": {
                        "id": "sync-callback-1",
                        "data": f"bid-decision:{decision_id}:review",
                        "message": {
                            "message_id": 100,
                            "chat": {"id": 1594710346},
                        },
                    },
                }
            ]
        return []

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )
    monkeypatch.setattr(TelegramNotificationService, "get_updates", fake_get_updates)

    project = Project(
        title="Telegram Sync Project",
        description="Verify manual polling sync",
        requirements="React to Telegram updates",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    save_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project.id,
            "recommended_amount": 115000000.0,
            "probability_score": 0.91,
            "matched_score": 0.84,
            "deadline_hours_remaining": 5,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )
    assert save_response.status_code == 200
    decision_id = save_response.json()["id"]

    response = client.post(
        "/api/v1/operations/telegram/sync",
        params={"limit": 10, "timeout_seconds": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processed"
    assert payload["processed_count"] == 1
    assert payload["processed_update_ids"] == [42]
    assert payload["known_chat_ids"] == ["******0346"]
    assert get_updates_calls[0] == {"offset": None, "limit": 10, "timeout_seconds": 0}
    assert get_updates_calls[1] == {"offset": 43, "limit": 1, "timeout_seconds": 0}
    assert acknowledgements == [("sync-callback-1", "검토 처리 완료")]

    record = (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.id == decision_id)
        .one()
    )
    assert record.action == "review"
    assert record.decision_status == "reviewing"


def test_telegram_status_reports_webhook_and_chat_diagnostics(client, monkeypatch):
    """Status endpoint should expose webhook and recently observed chat diagnostics."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    monkeypatch.setattr(
        TelegramNotificationService,
        "get_updates",
        lambda self, offset=None, limit=None, timeout_seconds=None: [
            {"update_id": 1, "message": {"chat": {"id": 1594710346}}},
            {"update_id": 2, "message": {"chat": {"id": 987654321}}},
        ],
    )
    monkeypatch.setattr(
        TelegramNotificationService,
        "get_webhook_info",
        lambda self: {
            "ok": True,
            "result": {
                "url": "https://example.com/api/v1/operations/telegram/webhook",
                "pending_update_count": 2,
                "has_custom_certificate": False,
            },
        },
    )

    response = client.get("/api/v1/operations/telegram/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["status"] == "healthy"
    assert payload["detail"] == "Telegram is configured."
    assert payload["delivery_chat_id"] == "******0346"
    assert payload["pending_update_count"] == 2
    assert payload["webhook_url"].endswith("/telegram/webhook")
    assert payload["has_custom_certificate"] is False
    assert payload["known_chat_ids"] == ["*****4321", "******0346"]


def test_telegram_status_treats_placeholder_settings_as_unconfigured(client, monkeypatch):
    """Placeholder Telegram secrets should not trigger Telegram API calls."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "replace-with-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "replace-with-target-chat-id")

    def fail_get_updates(self, offset=None, limit=None, timeout_seconds=None):
        raise AssertionError("placeholder Telegram settings should not call getUpdates")

    monkeypatch.setattr(TelegramNotificationService, "get_updates", fail_get_updates)

    response = client.get("/api/v1/operations/telegram/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["status"] == "watch"
    assert payload["detail"] == "Telegram is not configured."
    assert payload["delivery_chat_id"] is None
    assert payload["pending_update_count"] == 0
    assert payload["webhook_url"] == ""
    assert payload["known_chat_ids"] == []


def test_update_bid_decision_status_endpoint_transitions_decision(client):
    """PATCH /bid-decisions/{id}/status는 decision_status를 안전하게 전이시킨다."""
    project = client.post(
        "/api/v1/projects/",
        json={
            "title": "전이 테스트 공고",
            "description": "status PATCH 검증",
            "requirements": "n/a",
            "budget_estimate": 50_000_000.0,
            "category": "software",
        },
    ).json()

    record = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project["id"],
            "recommended_amount": 45_000_000.0,
            "probability_score": 0.7,
            "matched_score": 0.7,
            "decision_status": "planned",
        },
    ).json()

    patch = client.patch(
        f"/api/v1/operations/bid-decisions/{record['id']}/status",
        json={"decision_status": "submitted"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["decision_status"] == "submitted"

    missing = client.patch(
        "/api/v1/operations/bid-decisions/9999999/status",
        json={"decision_status": "submitted"},
    )
    assert missing.status_code == 404


def test_save_decision_merges_reasons_into_score_breakdown(client, test_db):
    """save_decision should persist strengths/risk_flags into score_breakdown without dropping signals."""
    project = Project(
        title="사유 영속화 검증 공고",
        description="강점/리스크 사유를 결정 레코드에 함께 저장",
        requirements="즉시 추진 판단 근거 보존",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    record = BidDecisionService().save_decision(
        test_db,
        BidDecisionSaveRequest(
            project_id=project.id,
            recommended_amount=115000000.0,
            probability_score=0.88,
            matched_score=0.82,
            deadline_hours_remaining=10,
            current_active_bids=1,
            max_active_bids=4,
            current_workload_score=0.2,
            strengths=["업체 자격·지역·역량 기준을 전반적으로 충족합니다."],
            risk_flags=["자격·지역·면허 조건이 완전히 맞지 않아 수주 리스크가 있습니다."],
        ),
    )

    stored = json.loads(record.score_breakdown)
    # Existing signal keys must survive the merge.
    assert "probability_signal" in stored
    assert "opportunity_score" in stored
    # Reason lists are merged under dedicated keys.
    assert stored["strengths"] == ["업체 자격·지역·역량 기준을 전반적으로 충족합니다."]
    assert stored["risk_flags"] == [
        "자격·지역·면허 조건이 완전히 맞지 않아 수주 리스크가 있습니다."
    ]

    # And they round-trip through the response schema (list endpoint).
    listed = client.get(
        "/api/v1/operations/bid-decisions", params={"project_id": project.id}
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert payload[0]["strengths"] == ["업체 자격·지역·역량 기준을 전반적으로 충족합니다."]
    assert payload[0]["risk_flags"] == [
        "자격·지역·면허 조건이 완전히 맞지 않아 수주 리스크가 있습니다."
    ]
    # The signal breakdown remains intact alongside the reasons.
    assert payload[0]["score_breakdown"]["probability_signal"] == stored["probability_signal"]


def test_save_decision_without_reasons_stays_backward_compatible(client, test_db):
    """Records persisted without reasons must serialize empty lists and omit the keys in storage."""
    project = Project(
        title="사유 없는 하위호환 공고",
        description="강점/리스크 미전달 시 빈 리스트로 안전 직렬화",
        requirements="기존 동작 보존",
        budget_estimate=90000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    record = BidDecisionService().save_decision(
        test_db,
        BidDecisionSaveRequest(
            project_id=project.id,
            recommended_amount=88000000.0,
            probability_score=0.6,
            matched_score=0.62,
            deadline_hours_remaining=40,
        ),
    )

    stored = json.loads(record.score_breakdown)
    # Empty reason lists are not written into the blob (keeps legacy shape).
    assert "strengths" not in stored
    assert "risk_flags" not in stored

    detail = client.get(f"/api/v1/operations/bid-decisions/{record.id}")
    assert detail.status_code == 200
    record_payload = detail.json()["record"]
    assert record_payload["strengths"] == []
    assert record_payload["risk_flags"] == []


def test_legacy_score_breakdown_serializes_reasons_as_empty_lists(client, test_db):
    """A pre-existing record whose score_breakdown lacks reason keys stays safe to serialize."""
    operator = ensure_operator_account(test_db)
    project = Project(
        title="레거시 점수 분해 공고",
        description="기존 score_breakdown에 사유 키가 없는 레코드",
        requirements="하위호환 직렬화 검증",
        budget_estimate=70000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    legacy = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="review",
        decision_status="reviewing",
        recommended_amount=68000000.0,
        probability_score=0.6,
        matched_score=0.61,
        priority_score=0.55,
        current_active_bids=0,
        max_active_bids=3,
        current_workload_score=0.0,
        score_breakdown=json.dumps({"probability_signal": 0.6, "opportunity_score": 0.55}),
        reasoning="레거시 결정 레코드입니다.",
    )
    test_db.add(legacy)
    test_db.commit()
    test_db.refresh(legacy)

    listed = client.get(
        "/api/v1/operations/bid-decisions", params={"project_id": project.id}
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert payload[0]["strengths"] == []
    assert payload[0]["risk_flags"] == []
    assert payload[0]["score_breakdown"]["probability_signal"] == 0.6


def test_opportunity_analysis_reasons_persist_through_save_decision(client, test_db):
    """Reasons computed by opportunity analysis flow into the persisted decision record and response."""
    user = User(
        username="reason-persist-operator",
        email="reason-persist-operator@example.com",
        hashed_password="hashed",
        full_name="Reason Persist Operator",
        company="Reason Persist Corp",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="software",
        license_codes="SW001",
        region_codes="서울특별시",
        annual_revenue=1200000000.0,
        capacity_score=0.92,
        total_awards=6,
    )
    matched_project = Project(
        title="AI 민원 데이터 분석 플랫폼 구축",
        description="민원 데이터 분석과 시각화, 대시보드 자동화가 포함된 플랫폼 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 운영지원 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=18),
    )
    similar_project = Project(
        title="AI 데이터 대시보드 구축",
        description="데이터 분석과 시각화 중심의 대시보드 시스템 개발",
        requirements="서울특별시 수행 가능, 분석 보고서 자동화",
        budget_estimate=118000000.0,
        category="software",
    )
    mismatch_project = Project(
        title="부산 토목 시설 유지보수",
        description="부산 지역 토목 구조물 점검 및 보수 공사",
        requirements="토목시공 면허 보유, 부산광역시 수행 필수",
        budget_estimate=140000000.0,
        category="construction",
    )
    test_db.add_all([profile, matched_project, similar_project, mismatch_project])
    test_db.commit()
    test_db.refresh(matched_project)
    test_db.refresh(mismatch_project)

    analysis_service = OpportunityAnalysisService()
    decision_service = BidDecisionService()

    def _persist(project):
        analysis = analysis_service.analyze_project(
            test_db,
            project,
            OpportunityAnalysisRequest(project_id=project.id, similar_limit=3, min_similarity=0.15),
        )
        record = decision_service.save_decision(
            test_db,
            BidDecisionSaveRequest(
                project_id=project.id,
                recommended_amount=float(analysis["recommended_amount"]),
                probability_score=float(analysis["probability_score"]),
                matched_score=float(analysis["matched_score"]),
                deadline_hours_remaining=analysis.get("deadline_hours_remaining"),
                current_active_bids=int(analysis.get("current_active_bids") or 0),
                max_active_bids=int(analysis.get("max_active_bids") or 3),
                current_workload_score=float(analysis.get("current_workload_score") or 0.0),
                budget_estimate=float(project.budget_estimate or 0.0),
                strengths=list(analysis.get("strengths") or []),
                risk_flags=list(analysis.get("risk_flags") or []),
            ),
        )
        return analysis, record

    matched_analysis, matched_record = _persist(matched_project)
    mismatch_analysis, mismatch_record = _persist(mismatch_project)

    assert matched_analysis["matched"] is True
    assert matched_analysis["strengths"]
    stored_matched = json.loads(matched_record.score_breakdown)
    assert stored_matched["strengths"] == matched_analysis["strengths"]

    assert mismatch_analysis["matched"] is False
    assert any(
        "자격" in flag or "지역" in flag or "면허" in flag
        for flag in mismatch_analysis["risk_flags"]
    )
    stored_mismatch = json.loads(mismatch_record.score_breakdown)
    assert stored_mismatch["risk_flags"] == mismatch_analysis["risk_flags"]

    detail = client.get(f"/api/v1/operations/bid-decisions/{mismatch_record.id}")
    assert detail.status_code == 200
    detail_record = detail.json()["record"]
    assert any(
        "자격" in flag or "지역" in flag or "면허" in flag
        for flag in detail_record["risk_flags"]
    )


# ---------------------------------------------------------------------------
# POST /bid-decisions/{id}/actions — dashboard inline actions
# ---------------------------------------------------------------------------


def _create_operator_user(
    test_db,
    *,
    username: str,
    password: str = "password123",
    is_admin: bool = False,
) -> User:
    """Create a User + CompanyProfile + OperatorStrategy so the user is fully usable."""
    from app.core.security import get_password_hash
    from app.models.models import OperatorStrategy

    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=f"{username} Co",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=is_admin,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    test_db.add(
        CompanyProfile(
            user_id=user.id,
            business_type="software",
            license_codes="",
            region_codes="",
            annual_revenue=0.0,
            capacity_score=0.0,
            total_awards=0,
        )
    )
    test_db.add(
        OperatorStrategy(
            user_id=user.id,
            focus_categories="",
            bid_now_threshold=0.7,
            review_threshold=0.45,
        )
    )
    test_db.commit()
    return user


def _login_operator(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_action_decision(
    test_db,
    *,
    operator_id: int,
    action: str = "review",
    decision_status: str = "reviewing",
    title: str = "Inline action test project",
) -> BidDecisionRecord:
    project = Project(
        title=title,
        description="dashboard inline action",
        requirements="n/a",
        budget_estimate=50_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator_id,
        pursue_bid=True,
        action=action,
        decision_status=decision_status,
        initial_action=action,
        initial_decision_status=decision_status,
        recommended_amount=45_000_000.0,
        probability_score=0.7,
        matched_score=0.7,
        priority_score=0.6,
        urgency_score=0.4,
        competitiveness_score=0.5,
        budget_capture_score=0.5,
        expected_margin_score=0.5,
        execution_complexity_score=0.4,
        current_active_bids=0,
        max_active_bids=3,
        current_workload_score=0.2,
        workload_source="provided",
        score_breakdown="{}",
        reasoning="seed reasoning",
    )
    test_db.add(record)
    test_db.commit()
    test_db.refresh(record)
    return record


def test_canonical_operator_notification_uses_owner_routed_callback_payload(
    test_db,
    monkeypatch,
):
    """Canonical Telegram notifications include the operator owner in callback routing keys."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(
            {"message": message, "reply_markup": reply_markup, "chat_id": chat_id}
        )
        return {"sent": True, "status": "sent", "detail": "ok"}

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(
        test_db,
        operator_id=canonical.id,
        action="bid_now",
        decision_status="planned",
    )
    record.priority_score = 0.99
    record.probability_score = 0.99
    test_db.commit()
    test_db.refresh(record)

    notification = OperatorNotificationService().create_bid_decision_notification(
        test_db,
        operator_id=canonical.id,
        project=record.project,
        decision_record=record,
    )

    assert notification.user_id == canonical.id
    assert deliveries, "canonical operator notification should still deliver to Telegram"
    reply_markup = deliveries[-1]["reply_markup"]
    submit_callback = reply_markup["inline_keyboard"][0][0]["callback_data"]
    assert submit_callback == f"bid-decision:{canonical.id}:{record.id}:submit"


def test_synthetic_operator_notification_records_dry_run_without_telegram_send(
    test_db,
    monkeypatch,
):
    """Synthetic operator notifications stay in-app and record Telegram dry-run evidence."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fail_send(self, message: str, reply_markup=None, chat_id=None):
        raise AssertionError("synthetic operator notifications must not call Telegram")

    monkeypatch.setattr(TelegramNotificationService, "send_message", fail_send)

    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    record = _seed_action_decision(
        test_db,
        operator_id=synthetic.id,
        action="bid_now",
        decision_status="planned",
    )
    record.priority_score = 0.99
    record.probability_score = 0.99
    test_db.commit()
    test_db.refresh(record)

    notification = OperatorNotificationService().create_bid_decision_notification(
        test_db,
        operator_id=synthetic.id,
        project=record.project,
        decision_record=record,
    )

    assert notification.user_id == synthetic.id
    event = (
        test_db.query(Analytics)
        .filter(
            Analytics.user_id == synthetic.id,
            Analytics.event_type == "telegram.delivery",
        )
        .one()
    )
    event_data = json.loads(event.event_data)
    assert event_data["operator_id"] == synthetic.id
    assert event_data["notification_id"] == notification.id
    assert event_data["sent"] is False
    assert event_data["status"] == "skipped_synthetic_operator"
    assert event_data["channel_type"] == "telegram"
    assert event_data["channel_id"] is None
    assert event_data["route_key"] == f"operator:{synthetic.id}:telegram:unconfigured"
    assert event_data["channel_source"] == "missing_channel"
    assert event_data["channel_active"] is False
    assert event_data["dry_run_only"] is True


def test_inactive_operator_telegram_channel_records_dry_run_evidence(
    test_db,
    monkeypatch,
):
    """Inactive per-operator channels should never send but should leave route evidence."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fail_send(self, message: str, reply_markup=None, chat_id=None):
        raise AssertionError("inactive operator channels must not call Telegram")

    monkeypatch.setattr(TelegramNotificationService, "send_message", fail_send)

    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    channel = OperatorNotificationChannel(
        operator_id=synthetic.id,
        channel_type="telegram",
        route_key="telegram:synthetic-sw-small-seoul",
        target_label="chat ********0346",
        is_active=False,
        dry_run_only=False,
    )
    test_db.add(channel)
    test_db.commit()
    test_db.refresh(channel)

    record = _seed_action_decision(
        test_db,
        operator_id=synthetic.id,
        action="bid_now",
        decision_status="planned",
    )
    record.priority_score = 0.99
    record.probability_score = 0.99
    test_db.commit()
    test_db.refresh(record)

    notification = OperatorNotificationService().create_bid_decision_notification(
        test_db,
        operator_id=synthetic.id,
        project=record.project,
        decision_record=record,
    )

    assert notification.user_id == synthetic.id
    event = (
        test_db.query(Analytics)
        .filter(
            Analytics.user_id == synthetic.id,
            Analytics.event_type == "telegram.delivery",
        )
        .one()
    )
    event_data = json.loads(event.event_data)
    assert event_data["sent"] is False
    assert event_data["status"] == "telegram_channel_inactive"
    assert event_data["channel_id"] == channel.id
    assert event_data["route_key"] == "telegram:synthetic-sw-small-seoul"
    assert event_data["target_label"] == "chat ********0346"
    assert event_data["channel_active"] is False
    assert event_data["dry_run_only"] is False


def test_non_canonical_telegram_delivery_plan_distinguishes_channel_states(
    test_db,
    monkeypatch,
):
    """Synthetic Telegram plans clearly separate missing, inactive, dry-run, and active-risk states."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    missing = _create_operator_user(test_db, username="synthetic-plan-missing")
    inactive = _create_operator_user(test_db, username="synthetic-plan-inactive")
    dry_run = _create_operator_user(test_db, username="synthetic-plan-dry-run")
    active = _create_operator_user(test_db, username="synthetic-plan-active")

    test_db.add_all(
        [
            OperatorNotificationChannel(
                operator_id=inactive.id,
                channel_type="telegram",
                route_key="telegram:inactive-synthetic",
                target_label="chat 1594710346",
                is_active=False,
                dry_run_only=False,
            ),
            OperatorNotificationChannel(
                operator_id=dry_run.id,
                channel_type="telegram",
                route_key="telegram:dry-run-synthetic",
                target_label="chat 1594710346",
                is_active=True,
                dry_run_only=True,
            ),
            OperatorNotificationChannel(
                operator_id=active.id,
                channel_type="telegram",
                route_key=TelegramNotificationService.LEGACY_CONFIGURED_CHAT_ROUTE_KEY,
                target_label="chat 1594710346",
                is_active=True,
                dry_run_only=False,
            ),
        ]
    )
    test_db.commit()

    service = OperatorNotificationService()
    plans = {
        "missing": service.build_telegram_delivery_plan(test_db, operator_id=missing.id),
        "inactive": service.build_telegram_delivery_plan(test_db, operator_id=inactive.id),
        "dry_run": service.build_telegram_delivery_plan(test_db, operator_id=dry_run.id),
        "active": service.build_telegram_delivery_plan(test_db, operator_id=active.id),
    }

    assert plans["missing"]["status"] == "skipped_synthetic_operator"
    assert plans["missing"]["channel_source"] == "missing_channel"
    assert plans["missing"]["channel_active"] is False
    assert plans["missing"]["dry_run_only"] is True

    assert plans["inactive"]["status"] == "telegram_channel_inactive"
    assert plans["inactive"]["channel_source"] == "operator_notification_channels"
    assert plans["inactive"]["channel_active"] is False
    assert plans["inactive"]["dry_run_only"] is False

    assert plans["dry_run"]["status"] == "telegram_channel_dry_run"
    assert plans["dry_run"]["channel_active"] is True
    assert plans["dry_run"]["dry_run_only"] is True

    assert plans["active"]["status"] == "telegram_route_non_canonical"
    assert plans["active"]["route_key"] == "telegram:legacy-configured-chat"
    assert plans["active"]["channel_active"] is True
    assert plans["active"]["dry_run_only"] is False

    for plan in plans.values():
        assert plan["target_label"] is None or "1594710346" not in plan["target_label"]
        assert plan["route_send_allowed"] is False
        assert plan["can_send"] is False
        assert plan["telegram_configured"] is True


def test_non_canonical_delivery_telemetry_masks_raw_route_targets(
    test_db,
    monkeypatch,
):
    """Delivery telemetry must not persist raw chat ids from route_key or target_label."""
    raw_chat_id = "1594710346"
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", raw_chat_id)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fail_send(self, message: str, reply_markup=None, chat_id=None):
        raise AssertionError("non-canonical active channels must not call Telegram")

    monkeypatch.setattr(TelegramNotificationService, "send_message", fail_send)

    synthetic = _create_operator_user(test_db, username="synthetic-raw-route")
    test_db.add(
        OperatorNotificationChannel(
            operator_id=synthetic.id,
            channel_type="telegram",
            route_key=f"telegram:{raw_chat_id}",
            target_label=f"chat_id={raw_chat_id}",
            is_active=True,
            dry_run_only=False,
        )
    )
    test_db.commit()

    record = _seed_action_decision(
        test_db,
        operator_id=synthetic.id,
        action="bid_now",
        decision_status="planned",
    )
    record.priority_score = 0.99
    record.probability_score = 0.99
    test_db.commit()
    test_db.refresh(record)

    OperatorNotificationService().create_bid_decision_notification(
        test_db,
        operator_id=synthetic.id,
        project=record.project,
        decision_record=record,
    )

    event = (
        test_db.query(Analytics)
        .filter(
            Analytics.user_id == synthetic.id,
            Analytics.event_type == "telegram.delivery",
        )
        .one()
    )
    event_data = json.loads(event.event_data)
    assert raw_chat_id not in json.dumps(event_data, ensure_ascii=False)
    assert event_data["status"] == "telegram_route_non_canonical"
    assert event_data["route_key"] == "telegram:******0346"
    assert event_data["target_label"] == "chat_id=******0346"
    assert event_data["route_send_allowed"] is False
    assert event_data["can_send"] is False
    assert event_data["telegram_configured"] is True


def test_bid_decision_notification_rejects_mismatched_owner(test_db):
    """Notification creation must not bind one operator to another operator's decision."""
    import pytest

    canonical = _create_operator_user(test_db, username="operator")
    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    synthetic_record = _seed_action_decision(test_db, operator_id=synthetic.id)

    with pytest.raises(ValueError, match="Notification owner"):
        OperatorNotificationService().create_bid_decision_notification(
            test_db,
            operator_id=canonical.id,
            project=synthetic_record.project,
            decision_record=synthetic_record,
        )

    assert test_db.query(Notification).count() == 0


def test_apply_bid_decision_action_submit_transitions_to_submitted(client, test_db):
    """submit 액션은 record를 submit/submitted/pursue=True로 일관 전이시킨다."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(
        test_db, operator_id=canonical.id, action="review", decision_status="reviewing"
    )

    headers = _login_operator(client, "operator")
    response = client.post(
        f"/api/v1/operations/bid-decisions/{record.id}/actions",
        json={"action": "submit"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == record.id
    assert payload["action"] == "bid_now"
    assert payload["decision_status"] == "submitted"
    assert payload["pursue_bid"] is True
    assert "텔레그램에서 투찰 버튼" in payload["reasoning"]

    test_db.refresh(record)
    assert record.action == "bid_now"
    assert record.decision_status == "submitted"
    assert record.pursue_bid is True


def test_apply_bid_decision_action_review_transitions_to_reviewing(client, test_db):
    """review 액션은 record를 review/reviewing/pursue=True로 일관 전이시킨다."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(
        test_db, operator_id=canonical.id, action="bid_now", decision_status="planned"
    )

    headers = _login_operator(client, "operator")
    response = client.post(
        f"/api/v1/operations/bid-decisions/{record.id}/actions",
        json={"action": "review"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "review"
    assert payload["decision_status"] == "reviewing"
    assert payload["pursue_bid"] is True
    assert "텔레그램에서 검토 버튼" in payload["reasoning"]


def test_apply_bid_decision_action_skip_transitions_to_skipped(client, test_db):
    """skip 액션은 record를 skip/skipped/pursue=False로 일관 전이시킨다."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(
        test_db, operator_id=canonical.id, action="review", decision_status="reviewing"
    )

    headers = _login_operator(client, "operator")
    response = client.post(
        f"/api/v1/operations/bid-decisions/{record.id}/actions",
        json={"action": "skip"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "skip"
    assert payload["decision_status"] == "skipped"
    assert payload["pursue_bid"] is False
    assert "텔레그램에서 보류 버튼" in payload["reasoning"]


def test_apply_bid_decision_action_canonical_can_target_synthetic(client, test_db):
    """canonical bearer + ?operator_id=synthetic → 해당 회사 record에 액션 가능."""
    canonical = _create_operator_user(test_db, username="operator")
    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    canonical_record = _seed_action_decision(
        test_db, operator_id=canonical.id, title="canonical-owned"
    )
    synthetic_record = _seed_action_decision(
        test_db, operator_id=synthetic.id, title="synthetic-owned"
    )

    headers = _login_operator(client, "operator")

    # canonical bearer scoping to synthetic can act on synthetic-owned record.
    response = client.post(
        f"/api/v1/operations/bid-decisions/{synthetic_record.id}/actions",
        params={"operator_id": synthetic.id},
        json={"action": "submit"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == synthetic_record.id
    assert payload["operator_id"] == synthetic.id
    assert payload["decision_status"] == "submitted"

    # But scoping to synthetic must NOT allow touching a canonical-owned record.
    cross = client.post(
        f"/api/v1/operations/bid-decisions/{canonical_record.id}/actions",
        params={"operator_id": synthetic.id},
        json={"action": "submit"},
        headers=headers,
    )
    assert cross.status_code == 404


def test_apply_bid_decision_action_non_privileged_cannot_cross_operator(client, test_db):
    """비-canonical/비-admin이 다른 operator의 record에 액션 시도 → 403."""
    _create_operator_user(test_db, username="operator")
    synthetic = _create_operator_user(test_db, username="synthetic-sw-small-seoul")
    other = _create_operator_user(test_db, username="other-operator")
    synthetic_record = _seed_action_decision(test_db, operator_id=synthetic.id)

    headers = _login_operator(client, "other-operator")
    response = client.post(
        f"/api/v1/operations/bid-decisions/{synthetic_record.id}/actions",
        params={"operator_id": synthetic.id},
        json={"action": "submit"},
        headers=headers,
    )
    assert response.status_code == 403

    # And without operator_id, non-privileged caller acts on their own scope —
    # synthetic_record is owned by synthetic, so other-operator gets 404.
    own_scope = client.post(
        f"/api/v1/operations/bid-decisions/{synthetic_record.id}/actions",
        json={"action": "submit"},
        headers=headers,
    )
    assert own_scope.status_code == 404

    # Original synthetic record must remain unchanged.
    test_db.refresh(synthetic_record)
    assert synthetic_record.decision_status != "submitted"


def test_apply_bid_decision_action_404_for_unknown_record(client, test_db):
    """존재하지 않는 record_id → 404."""
    _create_operator_user(test_db, username="operator")
    headers = _login_operator(client, "operator")
    response = client.post(
        "/api/v1/operations/bid-decisions/9999999/actions",
        json={"action": "submit"},
        headers=headers,
    )
    assert response.status_code == 404


def test_apply_bid_decision_action_422_for_invalid_action(client, test_db):
    """잘못된 action 문자열은 Pydantic Literal 검증으로 422."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(test_db, operator_id=canonical.id)

    headers = _login_operator(client, "operator")
    response = client.post(
        f"/api/v1/operations/bid-decisions/{record.id}/actions",
        json={"action": "bid_now"},  # not in {submit, review, skip}
        headers=headers,
    )
    assert response.status_code == 422


def test_apply_bid_decision_action_requires_bearer(client, test_db):
    """bearer 토큰 없이 요청하면 401."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(test_db, operator_id=canonical.id)

    response = client.post(
        f"/api/v1/operations/bid-decisions/{record.id}/actions",
        json={"action": "submit"},
    )
    assert response.status_code == 401


def test_apply_bid_decision_action_does_not_regress_patch_status(client, test_db):
    """신규 endpoint 도입 후에도 기존 PATCH /status 호출자는 무손상."""
    canonical = _create_operator_user(test_db, username="operator")
    record = _seed_action_decision(
        test_db, operator_id=canonical.id, action="review", decision_status="planned"
    )

    # PATCH /status still works without bearer (legacy behavior preserved).
    patch = client.patch(
        f"/api/v1/operations/bid-decisions/{record.id}/status",
        json={"decision_status": "submitted"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["decision_status"] == "submitted"


def test_apply_telegram_action_service_contract_unchanged(test_db):
    """텔레그램 콜백이 의존하는 apply_telegram_action 시그니처/시멘틱 무손상.

    신규 endpoint가 이 메서드를 그대로 재사용하므로, 별도 변경이 없는지
    회귀 가드. submit/review/skip 각각에 대한 전이가 텔레그램 경로와 동일하게
    동작해야 한다.
    """
    operator = ensure_operator_account(test_db)
    record = _seed_action_decision(
        test_db, operator_id=operator.id, action="review", decision_status="reviewing"
    )

    service = BidDecisionService()
    updated = service.apply_telegram_action(
        test_db, decision_record_id=record.id, requested_action="submit"
    )
    assert updated.action == "bid_now"
    assert updated.decision_status == "submitted"
    assert updated.pursue_bid is True

    # Unsupported action still raises ValueError (defensive contract).
    import pytest

    with pytest.raises(ValueError):
        service.apply_telegram_action(
            test_db, decision_record_id=record.id, requested_action="nope"
        )
