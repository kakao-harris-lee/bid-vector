"""KONEPS 수집 경로 산출 불변 characterization (골든 diff 0).

방어적 DTO 규율 Phase 3. ``openapi/scsbid/html 파싱 -> 수집 루프 -> persistence``
체인이 무타입 dict 릴레이에서 Pydantic DTO 체인으로 바뀌어도 **경계 산출은 동일**해야
한다. 그래서 리팩토링 *전* 코드에서 캡처한 골든을 저장하고, 리팩토링 후 같은 시나리오가
같은 값을 만드는지 비교한다.

고정 대상(시나리오):

* ``openapi_notice_collect`` — ``collection.collect_openapi_items`` 2페이지 수집
  (items + metadata, 계약 관찰기 요약 포함)
* ``scsbid_award_collect`` — ``_collect_scsbid_openapi_items`` 낙찰 sweep
  (복수예비가격 상세 있음/없음 두 공고 + reserve-detail 실패 1건)
* ``live_html_collect`` — ``collect_notices(execution_mode="live")`` 실 KONEPS
  결과테이블 파싱 + 상세팝업 병합 + 개찰결과 행 merge
* ``persistence_roundtrip`` — 위 openapi/scsbid 산출을 ``persist_crawl_results`` 로
  영속화한 뒤 되읽은 Project / HistoricalData / TenderResult / CrawlJob 투영

의도된 동작 변경 후에만 재생성한다::

    KONEPS_COLLECTION_GOLDEN_REGEN=1 pytest -q tests/test_koneps_collection_characterization.py

정규화 규칙(§ ``_canonical_item``): item 은 **선언된 필드 전체 집합**으로 투영한다.
dict 릴레이에서는 "키 부재"이고 DTO 에서는 "명시적 None" 인 차이는 소비자가 전부
``item.get(...)`` 로 읽으므로 의미상 동일하며, HTTP 경계(``CrawlResponse`` ->
``CrawlNoticeItem``)는 이미 None 으로 정규화해 내보낸다. 대신 **선언되지 않은 키가
새로 생기면 실패**시켜, DTO 가 조용히 버리는 필드를 잡는다.

시간 의존: persistence 는 ``utc_now()`` 로 ``completed_at`` / ``basis_checked_at`` 을
찍고 ``matching`` 은 마감시각과 현재를 비교해 status 를 정한다. 두 모듈의 시계를 고정
시각으로 패치해 결정성을 확보한다.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.koneps import collection, matching, persistence
from app.services.koneps.collector import KonepsCollectorService
from tests.support.koneps_openapi_fakes import (
    FakeOpenApiResponse,
    award_body,
    award_item,
    empty_reserve_body,
    openapi_body,
    reserve_detail_body,
)

GOLDEN_DIR = Path(__file__).parent / "goldens" / "koneps"
REGEN = os.environ.get("KONEPS_COLLECTION_GOLDEN_REGEN") == "1"

FROZEN_NOW = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
SERVICE_KEY = "test-service-key"

# 수집 item 의 선언 필드 전수. 생산자가 이 목록 밖의 키를 배출하면 골든 투영이 실패한다
# (DTO 가 조용히 버리는 필드를 잡는 가드).
ITEM_FIELDS: tuple[str, ...] = (
    "notice_number",
    "title",
    "base_amount",
    "estimated_amount",
    "closing_at",
    "business_type",
    "business_type_code",
    "business_type_label",
    "region",
    "license_codes",
    "source_url",
    "award_floor_rate",
    "eligibility_raw",
    "metadata",
)

PROJECT_COLUMNS = (
    "notice_number",
    "title",
    "description",
    "requirements",
    "category",
    "budget_estimate",
    "budget_min",
    "budget_max",
    "deadline",
    "status",
    "issuing_agency",
    "demand_agency",
    "source_url",
    "business_type_code",
    "business_type_label",
    "award_floor_rate",
    "eligibility_raw",
)
HISTORICAL_COLUMNS = (
    "notice_number",
    "agency_name",
    "category",
    "base_amount",
    "predicted_price",
    "bid_rate",
    "reserve_prices",
    "selected_numbers",
    "opened_at",
    "base_amount_estimated",
    "base_amount_basis",
    "basis_checked_at",
)
TENDER_COLUMNS = (
    "winning_company",
    "winning_amount",
    "winning_rate",
    "result_status",
    "announced_at",
)
CRAWL_JOB_COLUMNS = (
    "source",
    "status",
    "result_count",
    "error_message",
    "completed_at",
)


# --------------------------------------------------------------------------- #
# 골든 직렬화 / 비교
# --------------------------------------------------------------------------- #
def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(str(entry) for entry in value)
    return str(value)


def _canonical_instant(value: Any) -> Any:
    """datetime 과 ISO 문자열을 같은 표현으로 접는다.

    dict 릴레이 시절 일부 경로(live)는 item 을 만든 직후 ``model_dump(mode="json")`` 로
    강등해 ``closing_at`` 이 ``...Z`` 문자열로 실려 있었고, 다른 경로(openapi/scsbid)는
    ``datetime`` 객체를 실었다. 즉 **같은 순간이 경로마다 다른 표현**으로 흘렀다. 골든은
    순간(instant)의 동일성을 고정해야 하므로 두 표현을 하나로 접어 비교한다.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _canonical_item(item: Any) -> dict[str, Any]:
    """수집 item 을 선언 필드 전수로 투영한다(dict / DTO 양쪽 동일 결과)."""
    raw = item.model_dump(mode="python") if hasattr(item, "model_dump") else dict(item)
    unknown = sorted(set(raw) - set(ITEM_FIELDS))
    assert not unknown, f"ITEM_FIELDS 에 선언되지 않은 수집 item 필드: {unknown}"
    canonical = {name: raw.get(name) for name in ITEM_FIELDS}
    canonical["closing_at"] = _canonical_instant(canonical["closing_at"])
    return canonical


def _canonical_response(response: Any) -> dict[str, Any]:
    payload = dict(response)
    payload["items"] = [_canonical_item(item) for item in payload.get("items") or []]
    return payload


def _assert_golden(name: str, payload: Any) -> None:
    """골든과 비교한다. 기록은 ``REGEN`` 일 때만.

    골든이 없는데 조용히 만들어 두면 "현재 산출을 기록하고 그 기록과 비교"하는 자기봉인
    이 되어 어떤 회귀도 잡지 못한다(골든 파일을 지우면 항상 통과). 그래서 부재는
    **실패**로 다룬다.
    """
    path = GOLDEN_DIR / f"{name}.json"
    serialized = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, default=_encode
    )
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{serialized}\n", encoding="utf-8")
        pytest.skip(f"골든 재생성: {path.name}")
    if not path.exists():
        pytest.fail(
            f"골든 없음: {name} — 의도된 변경이면 KONEPS_COLLECTION_GOLDEN_REGEN=1"
        )
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(serialized) == expected


# --------------------------------------------------------------------------- #
# 공통 fixture 데이터
# --------------------------------------------------------------------------- #
def _pin_openapi_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_COLLECTION_PAGE_SIZE", 2)
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_MAX_ITEMS", 500)
    # 계약 관찰기는 관찰 전용이지만 metadata 요약을 싣는다 — 골든 결정성을 위해 고정.
    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", True)


def _notice_row(notice_number: str, *, floor_rate: str | None = None) -> dict[str, Any]:
    """BidPublicInfoService 공고 목록 한 행(실측 키 구성)."""
    row = {
        "bidNtceNo": notice_number,
        "bidNtceOrd": "000",
        "bidNtceNm": f"{notice_number} 통합 유지관리 용역",
        "ntceKindNm": "등록공고",
        "rgstTyNm": "일반",
        "bidNtceDt": "2026-05-13 09:00:00",
        "bidBeginDt": "2026-05-13 10:00:00",
        "bidClseDt": "2026-05-20 10:00:00",
        "opengDt": "2026-05-20 11:00:00",
        "ntceInsttNm": "조달청",
        "dminsttNm": "서울특별시교육청",
        "bidMethdNm": "전자입찰",
        "cntrctCnclsMthdNm": "제한경쟁",
        "asignBdgtAmt": "125,000,000",
        "presmptPrce": "113,636,364",
        "bsnsDivNm": "용역",
        "prtcptLmtRgnNm": "서울특별시",
        "indstrytyNm": "SW001 정보통신공사업",
        "refNo": f"REF-{notice_number}",
        "bidNtceDtlUrl": f"https://www.g2b.go.kr/detail/{notice_number}",
        # 자격 플래그(목록 응답에 실제로 오는 것) — eligibility_raw 는 배출되지 않아야 한다.
        "indstrytyLmtYn": "Y",
        "rgnLmtBidLocplcJdgmBssNm": "법인등기부상 본점",
    }
    if floor_rate is not None:
        row["sucsfbidLwltRate"] = floor_rate
    return row


def _openapi_request() -> CrawlRequest:
    return CrawlRequest(
        source="koneps-openapi",
        category="service",
        target_date="2026-05-13",
        execution_mode="live",
        max_items=10,
    )


def _scsbid_request() -> CrawlRequest:
    return CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=True,
        execution_mode="auto",
    )


# --------------------------------------------------------------------------- #
# 1. OpenAPI 공고 수집
# --------------------------------------------------------------------------- #
def _collect_openapi(monkeypatch) -> dict[str, Any]:
    _pin_openapi_settings(monkeypatch)
    pages = {
        1: [
            _notice_row("R26BK01510407", floor_rate="87.995"),
            _notice_row("R26BK01510408"),
        ],
        2: [_notice_row("R26BK01510409"), {"bidNtceNm": "공고번호 없는 행"}],
    }

    def fake_get(url, params, timeout):
        page_no = int(params["pageNo"])
        rows = pages.get(page_no, [])
        return FakeOpenApiResponse(
            openapi_body(rows, total_count=4, num_of_rows=2, page_no=page_no)
        )

    return collection.collect_openapi_items(_openapi_request(), http_get=fake_get)


def test_openapi_notice_collect_matches_golden(monkeypatch):
    result = _collect_openapi(monkeypatch)

    _assert_golden("openapi_notice_collect", _canonical_response(result))


def test_openapi_collect_skips_row_without_notice_number(monkeypatch):
    """수집은 best-effort: 공고번호 없는 행은 **항목 단위로만** 버려지고 run 은 계속된다.

    이 관용의 범위를 명시한다 — 필수 식별자(공고번호)가 없는 행만 스킵이고, 나머지 행은
    전부 수집된다(1건 불량이 run 전체를 죽이지 않는다).
    """
    result = _collect_openapi(monkeypatch)

    notice_numbers = [
        _canonical_item(item)["notice_number"] for item in result["items"]
    ]
    assert notice_numbers == ["R26BK01510407", "R26BK01510408", "R26BK01510409"]
    assert result["metadata"]["openapi_pages_fetched"] == 2


# --------------------------------------------------------------------------- #
# 2. scsbid 낙찰 sweep
# --------------------------------------------------------------------------- #
def _collect_scsbid(monkeypatch) -> dict[str, Any]:
    _pin_openapi_settings(monkeypatch)

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            notice_number = str(params.get("bidNtceNo"))
            if notice_number == "A-SETTLED":
                return FakeOpenApiResponse(reserve_detail_body())
            if notice_number == "C-BROKEN":
                raise ValueError("KONEPS ScsbidInfoService HTTP 429")
            return FakeOpenApiResponse(empty_reserve_body())
        return FakeOpenApiResponse(
            award_body(
                [
                    award_item(
                        "A-SETTLED", title="정산 완료 낙찰", amount="88,123,000"
                    ),
                    award_item(
                        "B-UNSETTLED", title="예비가격 미공개", amount="45,000,000"
                    ),
                    award_item("C-BROKEN", title="상세 조회 실패", amount="70,000,000"),
                ],
                total_count=3,
                num_of_rows=100,
            )
        )

    service = KonepsCollectorService(http_get=fake_get)
    return service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request())
    )


def test_scsbid_award_collect_matches_golden(monkeypatch):
    result = _collect_scsbid(monkeypatch)

    _assert_golden("scsbid_award_collect", _canonical_response(result))


def test_scsbid_reserve_detail_failure_is_isolated_per_item(monkeypatch):
    """상세 조회 예외는 그 item 만 격리한다(에러 카운트 + 사유 보존, run 계속)."""
    result = _collect_scsbid(monkeypatch)

    items = {
        item["notice_number"]: item
        for item in (_canonical_item(entry) for entry in result["items"])
    }
    assert set(items) == {"A-SETTLED", "B-UNSETTLED", "C-BROKEN"}
    assert result["metadata"]["reserve_detail_error_count"] == 1
    assert result["metadata"]["reserve_detail_collected_count"] == 1
    assert "429" in items["C-BROKEN"]["metadata"]["reserve_detail_error"]
    # 실패한 item 은 예비가격 없이도 살아남아 영속화된다(기존 동작).
    assert items["C-BROKEN"]["metadata"]["reserve_prices"] == []


# --------------------------------------------------------------------------- #
# 3. live HTML 파싱 + 개찰결과 merge
# --------------------------------------------------------------------------- #
RESULT_TABLE_HTML = """
<html><body>
    <table id="mf_wfm_container_testTable">
        <tr>
            <th>No</th><th>업무구분</th><th>공고번호</th><th>공고명</th><th></th><th>공고상태</th>
            <th>국제여부</th><th>공고일시</th><th>개찰일시</th><th>입찰마감일시</th>
            <th>공고기관</th><th>수요기관</th><th>계약방법</th><th>공도급여부</th><th>투찰</th>
        </tr>
        <tr>
            <td>1</td>
            <td>0411 기술용역</td>
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
</body></html>
"""

DETAIL_HTML = """
<html><body><table>
    <tr><th>입찰공고번호</th><td>R26BK01510407</td></tr>
    <tr><th>입찰공고명</th><td>AI 소프트웨어 통합 구축</td></tr>
    <tr><th>입찰유형</th><td>0411 기술용역</td></tr>
    <tr><th>입찰마감일시</th><td>2026.05.10 18:00:00</td></tr>
    <tr><th>개찰일시</th><td>2026.05.10 18:00:00</td></tr>
    <tr><th>기초금액</th><td>125,000,000 KRW</td></tr>
    <tr><th>추정가격</th><td>113,636,364 KRW</td></tr>
    <tr><th>제한지역</th><td>서울특별시</td></tr>
    <tr><th>면허제한</th><td>SW001</td></tr>
</table></body></html>
"""


def _collect_live(monkeypatch) -> dict[str, Any]:
    service = KonepsCollectorService()
    monkeypatch.setattr(
        service,
        "_gather_live_page_snapshots",
        lambda request: [
            {
                "page_number": 1,
                "url": "https://www.g2b.go.kr/",
                "html": RESULT_TABLE_HTML,
                "detail_pages": {
                    "detail-row-1": {
                        "url": "http://ebid.example.com/detail/R26BK01510407",
                        "html": DETAIL_HTML,
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
                "notice_number": "R26BK01510407",
                "notice_full_number": "R26BK01510407-000",
                "status": "개찰완료",
                "business_type": "기술용역",
                "demand_agency": "서울특별시교육청",
                "opening_amount": 125000000.0,
                "reserve_prices": [124000000.0, 125000000.0, 126000000.0],
                "selected_numbers": [1, 4],
                "winning_company": "테스트 낙찰사",
                "winning_amount": 110000000.0,
                "winning_rate": 0.88,
            }
        ],
    )
    return service.collect_notices(
        CrawlRequest(
            source="koneps",
            category="technical-service",
            execution_mode="live",
            keyword="AI",
            target_date="2026-05-08",
            max_items=5,
        )
    )


def test_live_html_collect_matches_golden(monkeypatch):
    result = _collect_live(monkeypatch)

    _assert_golden("live_html_collect", _canonical_response(result))


# --------------------------------------------------------------------------- #
# 4. persistence 왕복
# --------------------------------------------------------------------------- #
def _row_projection(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: getattr(row, column) for column in columns}


def _db_projection(db) -> dict[str, Any]:
    return {
        "projects": [
            _row_projection(row, PROJECT_COLUMNS)
            for row in db.query(Project).order_by(Project.notice_number).all()
        ],
        "historical": [
            _row_projection(row, HISTORICAL_COLUMNS)
            for row in db.query(HistoricalData)
            .order_by(HistoricalData.notice_number)
            .all()
        ],
        "tender_results": [
            _row_projection(row, TENDER_COLUMNS)
            for row in db.query(TenderResult).order_by(TenderResult.id).all()
        ],
        "crawl_jobs": [
            _row_projection(row, CRAWL_JOB_COLUMNS)
            for row in db.query(CrawlJob).order_by(CrawlJob.id).all()
        ],
    }


def _persist(db, monkeypatch, request: CrawlRequest, response: dict[str, Any]) -> None:
    monkeypatch.setattr(persistence, "utc_now", lambda: FROZEN_NOW)
    monkeypatch.setattr(matching, "utc_now", lambda: FROZEN_NOW)
    crawl_job = persistence.create_crawl_job(db, request)
    persistence.persist_crawl_results(
        db,
        crawl_job,
        request,
        {
            "job_status": "completed",
            "collected_count": len(response["items"]),
            "items": response["items"],
            "metadata": response["metadata"],
        },
        # 임베딩은 지연시켜 sbert 추론 없이 결정적으로 돈다.
        defer_embeddings=True,
    )


def test_persistence_roundtrip_matches_golden(test_db, monkeypatch):
    openapi_response = _collect_openapi(monkeypatch)
    scsbid_response = _collect_scsbid(monkeypatch)

    _persist(test_db, monkeypatch, _openapi_request(), openapi_response)
    _persist(test_db, monkeypatch, _scsbid_request(), scsbid_response)

    test_db.expire_all()
    _assert_golden("persistence_roundtrip", _db_projection(test_db))


def test_persistence_preserves_existing_base_when_award_pass_has_none(
    test_db, monkeypatch
):
    """정합화 가드: 개찰 pass 의 base 미상(0.0)이 앞선 수집의 실 기초금액을 덮지 않는다.

    openapi 수집(base=125,000,000) 뒤 같은 공고번호의 scsbid 개찰 pass(base 미상)를
    영속화해도 base_amount 가 보존되는지 — 타입화 후에도 이 가드가 사는지 확인한다.
    """
    openapi_response = _collect_openapi(monkeypatch)
    _persist(test_db, monkeypatch, _openapi_request(), openapi_response)

    notice_number = "R26BK01510407"
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice_number)
        .one()
    )
    assert stored.base_amount == 125000000.0

    scsbid_response = _collect_scsbid(monkeypatch)
    award = scsbid_response["items"][1]  # B-UNSETTLED: 실 기초금액 없음 -> base 0.0
    canonical = _canonical_item(award)
    assert canonical["base_amount"] == 0.0
    # 같은 공고번호로 다시 흘려보낸다(개찰 pass 재수집 시나리오).
    if hasattr(award, "model_copy"):
        replayed = award.model_copy(update={"notice_number": notice_number})
    else:
        replayed = {**award, "notice_number": notice_number}
    _persist(
        test_db,
        monkeypatch,
        _scsbid_request(),
        {"items": [replayed], "metadata": scsbid_response["metadata"]},
    )

    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice_number)
        .one()
    )
    assert stored.base_amount == 125000000.0  # 0.0 으로 덮이지 않았다
