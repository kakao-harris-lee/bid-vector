"""Tests for operations skeleton endpoints."""

import json

from app.core.config import settings
from app.models.models import BidDecisionRecord, CompanyProfile, CrawlJob, HistoricalData, Notification, Project, TenderResult, User
from app.schemas.schemas import CrawlRequest
from app.services.classifier import NoticeClassifierService
from app.services.koneps.collector import KonepsCollectorService
from app.services.notifications.telegram import TelegramNotificationService


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
        lambda request: [{
            "page_number": 1,
            "url": "https://www.g2b.go.kr/",
            "html": sample_html,
            "detail_pages": {
                "detail-row-1": {
                    "url": "http://ebid.example.com/detail/R26BK01510407",
                    "html": detail_html,
                }
            },
        }],
    )

    response = service.collect_notices(
        CrawlRequest(source="koneps", category="software", execution_mode="live", keyword="AI")
    )

    assert response["job_status"] == "completed"
    assert response["collected_count"] == 1
    assert response["items"][0]["notice_number"] == "R26BK01510407"
    assert response["items"][0]["title"] == "AI 소프트웨어 통합 구축"
    assert response["items"][0]["base_amount"] == 125000000.0
    assert response["items"][0]["region"] == "서울"
    assert response["items"][0]["source_url"] == "http://ebid.example.com/detail/R26BK01510407"
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
        request=CrawlRequest(source="koneps", category="software", execution_mode="live", keyword="AI"),
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
        CrawlRequest(source="koneps", category="software", execution_mode="live", keyword="AI")
    )

    assert response["job_status"] == "fallback_mock"
    assert response["collected_count"] == 2
    assert response["items"][0]["metadata"]["mode"] == "fallback_mock"
    assert "browser not available" in response["items"][0]["metadata"]["fallback_reason"]
    assert response["metadata"]["resolved_mode"] == "fallback_mock"


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
        lambda request: [{
            "page_number": 1,
            "url": "https://www.g2b.go.kr/",
            "html": sample_html,
            "detail_pages": {
                "detail-row-1": {
                    "url": "http://ebid.example.com/detail/R26BK01510407",
                    "html": detail_html,
                }
            },
        }],
    )
    monkeypatch.setattr(
        service,
        "_collect_opening_result_rows",
        lambda request: [{
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
        }],
    )

    response = service.collect_notices(
        CrawlRequest(source="koneps", category="software", execution_mode="live", keyword="AI")
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

    monkeypatch.setattr(KonepsCollectorService, "collect_notices", lambda self, request: fake_response)

    response = client.post(
        "/api/v1/operations/crawl",
        json={"source": "koneps", "category": "software", "execution_mode": "live", "keyword": "AI"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["crawl_job_id"] >= 1

    crawl_job = test_db.query(CrawlJob).one()
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1

    historical_record = test_db.query(HistoricalData).filter(HistoricalData.notice_number == "R26BK01510407").one()
    assert historical_record.agency_name == "서울특별시교육청"
    assert historical_record.base_amount == 125000000.0
    assert json.loads(historical_record.reserve_prices) == [101000000.0, 102000000.0, 103000000.0]
    assert json.loads(historical_record.selected_numbers) == [1, 4, 7, 12]

    tender_result = test_db.query(TenderResult).one()
    assert tender_result.winning_company == "주식회사 테스트"
    assert tender_result.winning_amount == 119000000.0
    assert tender_result.winning_rate == 95.2
    assert tender_result.result_status == "개찰완료"


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

    monkeypatch.setattr(KonepsCollectorService, "collect_notices", lambda self, request: fake_response)

    response = client.post(
        "/api/v1/operations/crawl/async",
        json={"source": "koneps", "category": "software", "execution_mode": "live", "keyword": "AI"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_name"] == "jobs.collect_koneps_notices"
    assert payload["status"] == "completed"
    assert payload["task_id"]
    assert payload["crawl_job_id"] >= 1
    assert payload["poll_url"].endswith(payload["task_id"])

    crawl_job = test_db.query(CrawlJob).filter(CrawlJob.id == payload["crawl_job_id"]).one()
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1


def test_crawl_task_status_endpoint_returns_completed_result(client, test_db, monkeypatch):
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

    monkeypatch.setattr(KonepsCollectorService, "collect_notices", lambda self, request: fake_response)

    kickoff = client.post(
        "/api/v1/operations/crawl/async",
        json={"source": "koneps", "category": "software", "execution_mode": "live", "keyword": "AI"},
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

    crawl_job = test_db.query(CrawlJob).filter(CrawlJob.id == kickoff["crawl_job_id"]).one()
    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1


def test_crawl_async_endpoint_reports_failed_task_when_collection_fails(client, test_db, monkeypatch):
    """Async crawl kickoff should surface failure status when eager/fallback execution fails."""

    def raise_collection_error(self, request):
        raise RuntimeError("simulated crawl failure")

    monkeypatch.setattr(KonepsCollectorService, "collect_notices", raise_collection_error)

    response = client.post(
        "/api/v1/operations/crawl/async",
        json={"source": "koneps", "category": "software", "execution_mode": "live", "keyword": "AI"},
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

    crawl_job = test_db.query(CrawlJob).filter(CrawlJob.id == payload["crawl_job_id"]).one()
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
    assert any("제한지역" in reason or "수행지역" in reason for reason in payload["reasons"])


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
    assert any("수행능력" in reason or "수행 범위" in reason for reason in payload["reasons"])


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

    monkeypatch.setattr(service, "_compute_semantic_similarity", lambda project_text, profile_text: (0.82, "mock-embedding"))

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

    monkeypatch.setattr(service, "_compute_semantic_similarity", lambda project_text, profile_text: (0.02, "mock-embedding"))

    result = service.classify(project=project, profile=profile)

    assert result["matched"] is False
    assert result["score"] < service.MATCH_THRESHOLD
    assert any("false positive" in reason or "의미 유사도" in reason for reason in result["reasons"])


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

    monkeypatch.setattr(service, "_compute_semantic_similarity", lambda project_text, profile_text: (0.72, "mock-embedding"))

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

    record = test_db.query(BidDecisionRecord).one()
    assert record.project_id == project.id
    assert record.operator_id == payload["operator_id"]
    assert record.decision_status == "planned"
    assert record.priority_score >= 0.7

    notification = test_db.query(Notification).one()
    assert notification.type == "recommendation"
    assert f"프로젝트 {project.id}" in notification.title
    assert "입찰 판단 알림" in notification.message
    assert "우선순위" in notification.message


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

    filtered = client.get("/api/v1/operations/bid-decisions", params={"decision_status": "submitted"})
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert len(filtered_payload) == 1
    assert filtered_payload[0]["project_id"] == second_project.id
    assert filtered_payload[0]["decision_status"] == "submitted"


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


def test_notify_telegram_endpoint_attempts_delivery_when_configured(client, monkeypatch):
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
    monkeypatch.setattr(TelegramNotificationService, "answer_callback_query", fake_answer)

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
                }
            }
        },
    )

    assert callback_response.status_code == 200
    payload = callback_response.json()
    assert payload["status"] == "processed"
    assert payload["action"] == "review"
    assert payload["decision_status"] == "reviewing"
    assert acknowledgements == [("callback-1", "검토 처리 완료")]

    record = test_db.query(BidDecisionRecord).filter(BidDecisionRecord.id == decision_id).one()
    assert record.action == "review"
    assert record.decision_status == "reviewing"
    assert "텔레그램에서 검토 버튼" in record.reasoning


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
    assert payload["known_chat_ids"] == [1594710346]
    assert len(deliveries) == 1
    assert deliveries[0]["chat_id"] == "1594710346"
    assert "감지된 chat id: 1594710346" in deliveries[0]["message"]


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


def test_telegram_sync_processes_pending_updates_and_acknowledges_offset(client, test_db, monkeypatch):
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
        get_updates_calls.append({
            "offset": offset,
            "limit": limit,
            "timeout_seconds": timeout_seconds,
        })
        if offset is None:
            return [{
                "update_id": 42,
                "callback_query": {
                    "id": "sync-callback-1",
                    "data": f"bid-decision:{decision_id}:review",
                    "message": {
                        "message_id": 100,
                        "chat": {"id": 1594710346},
                    },
                },
            }]
        return []

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)
    monkeypatch.setattr(TelegramNotificationService, "answer_callback_query", fake_answer)
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
    assert payload["known_chat_ids"] == [1594710346]
    assert get_updates_calls[0] == {"offset": None, "limit": 10, "timeout_seconds": 0}
    assert get_updates_calls[1] == {"offset": 43, "limit": 1, "timeout_seconds": 0}
    assert acknowledgements == [("sync-callback-1", "검토 처리 완료")]

    record = test_db.query(BidDecisionRecord).filter(BidDecisionRecord.id == decision_id).one()
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
    assert payload["delivery_chat_id"] == "1594710346"
    assert payload["pending_update_count"] == 2
    assert payload["webhook_url"].endswith("/telegram/webhook")
    assert payload["has_custom_certificate"] is False
    assert payload["known_chat_ids"] == [987654321, 1594710346]