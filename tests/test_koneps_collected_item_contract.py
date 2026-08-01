"""수집 item DTO 의 구조 계약 (happy + sad) 과 best-effort 관용의 경계.

방어적 DTO Phase 3. 승격 지점마다 "무엇을 통과시키고 무엇을 거부하는가"를 고정한다.

* 생산자 승격(``build_openapi_notice_item`` / ``build_scsbid_award_item``): 필수 식별자
  (공고번호)가 없으면 ``None`` — **항목 단위 스킵**이고 run 은 계속된다(수집 best-effort).
* 영속화 승격(``persist_crawl_results`` 진입): dict payload 는 여기서 검증되고, 필수 필드
  결손은 ``ValidationError`` 로 **거부**된다(영속화는 best-effort 가 아니다).
* metadata 투영(``CrawlItemMetadataFacts``): 미지 키는 무시하고, 소비 대상 필드만 좁혀
  읽는다. 원본 bag 은 바뀌지 않는다(직렬화 산출 불변).
* 공개 표면 격리: 내부 전용 필드는 ``CrawlNoticeItem``(OpenAPI 스키마)에 새지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.models import CrawlJob
from app.schemas.crawl import CrawlNoticeItem
from app.schemas.koneps_items import (
    CrawlItemMetadataFacts,
    KonepsCollectedItem,
    ScsbidReserveDetail,
)
from app.schemas.schemas import CrawlRequest
from app.services.koneps import collection, openapi, persistence, scsbid


def _notice_request() -> CrawlRequest:
    return CrawlRequest(source="koneps-openapi", category="service")


def _award_request() -> CrawlRequest:
    return CrawlRequest(source="scsbid-openapi", category="construction")


# --------------------------------------------------------------------------- #
# 1. 생산자 승격 — happy
# --------------------------------------------------------------------------- #
def test_openapi_builder_returns_typed_item():
    item = openapi.build_openapi_notice_item(
        {
            "bidNtceNo": "R26BK01510407",
            "bidNtceNm": "테스트 용역",
            "asignBdgtAmt": "125,000,000",
            "bidClseDt": "2026-05-20 10:00:00",
            "sucsfbidLwltRate": "87.995",
        },
        request=_notice_request(),
        operation="getBidPblancListInfoServc",
    )

    assert isinstance(item, KonepsCollectedItem)
    assert item.notice_number == "R26BK01510407"
    assert item.base_amount == 125_000_000.0
    assert item.award_floor_rate == 0.87995
    assert item.closing_at == datetime(2026, 5, 20, 10, 0, tzinfo=UTC)


def test_scsbid_builder_returns_typed_item():
    item = scsbid.build_scsbid_award_item(
        {
            "bidNtceNo": "A-1",
            "bidNtceNm": "테스트 낙찰",
            "sucsfbidAmt": "88,000,000",
            "sucsfbidRate": "88.0",
        },
        detail=ScsbidReserveDetail(base_amount=100_000_000),
        request=_award_request(),
        operation="getScsbidListSttusCnstwk",
        category="construction",
    )

    assert isinstance(item, KonepsCollectedItem)
    assert item.base_amount == 100_000_000.0
    assert item.metadata["bid_rate"] == pytest.approx(0.88)


def test_mock_builder_returns_typed_items_not_dicts():
    """``build_mock_items`` 는 검증 직후 dict 로 강등하지 않는다(강등은 경계에서만)."""
    items = collection.build_mock_items(
        CrawlRequest(
            source="koneps",
            category="software",
            keyword="AI",
            target_date="2026-05-13",
        )
    )

    assert items
    assert all(isinstance(item, KonepsCollectedItem) for item in items)


# --------------------------------------------------------------------------- #
# 2. 생산자 승격 — sad (항목 단위 스킵: run 은 죽지 않는다)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw_item", [{}, {"bidNtceNo": ""}, {"bidNtceNm": "번호 없음"}]
)
def test_openapi_builder_skips_row_without_notice_number(raw_item):
    assert (
        openapi.build_openapi_notice_item(
            raw_item,
            request=_notice_request(),
            operation="getBidPblancListInfoServc",
        )
        is None
    )


@pytest.mark.parametrize("raw_item", [{}, {"bidNtceNo": "   "}])
def test_scsbid_builder_skips_row_without_notice_number(raw_item):
    assert (
        scsbid.build_scsbid_award_item(
            raw_item,
            detail=ScsbidReserveDetail(),
            request=_award_request(),
            operation="getScsbidListSttusCnstwk",
        )
        is None
    )


def test_producers_tolerate_unparseable_amounts_without_raising():
    """관용 경계 명시: 금액 텍스트가 해석 불가여도 item 은 살아남고 금액만 0.0 이 된다.

    수집은 best-effort 다 — 한 필드의 결손이 그 공고를 통째로 버리는 근거가 되지 않는다.
    (거부는 *식별자* 결손에서만 일어난다.)
    """
    item = openapi.build_openapi_notice_item(
        {
            "bidNtceNo": "R-TOLERANT",
            "bidNtceNm": "금액 미상 공고",
            "asignBdgtAmt": "협의",
            "presmptPrce": "-",
        },
        request=_notice_request(),
        operation="getBidPblancListInfoServc",
    )

    assert item is not None
    assert item.base_amount == 0.0
    assert item.estimated_amount == 0.0


# --------------------------------------------------------------------------- #
# 3. 영속화 승격 — happy / sad
# --------------------------------------------------------------------------- #
def _crawl_job(test_db) -> CrawlJob:
    crawl_job = CrawlJob(source="koneps-openapi", status="running", result_count=0)
    test_db.add(crawl_job)
    test_db.commit()
    test_db.refresh(crawl_job)
    return crawl_job


def test_persist_promotes_dict_items_to_dto(test_db):
    """손으로 만든 dict payload 도 진입 시 승격되어 ORM 대입까지 흐른다."""
    crawl_job = persistence.persist_crawl_results(
        test_db,
        _crawl_job(test_db),
        _notice_request(),
        {
            "job_status": "completed",
            "collected_count": 1,
            "items": [
                {
                    "notice_number": "PROMOTE-1",
                    "title": "승격 검증 공고",
                    "base_amount": 100_000_000.0,
                    "metadata": {"issuing_agency": "조달청"},
                }
            ],
            "metadata": {},
        },
        defer_embeddings=True,
    )

    assert crawl_job.status == "completed"
    assert crawl_job.result_count == 1


@pytest.mark.parametrize(
    "broken_item",
    [
        {"title": "번호 없음", "base_amount": 1.0},  # notice_number 결손
        {"notice_number": "N-1", "base_amount": 1.0},  # title 결손
        {"notice_number": "N-1", "title": "금액 결손"},  # base_amount 결손
        {"notice_number": "N-1", "title": "타입 불일치", "base_amount": "협의"},
        # 오타 키(선언되지 않은 필드) — extra="forbid" 로 거부한다.
        {
            "notice_number": "N-1",
            "title": "오타 키",
            "base_amount": 1.0,
            "award_floor": 0.87995,
        },
    ],
)
def test_persist_rejects_structurally_invalid_item(test_db, broken_item):
    """영속화는 best-effort 가 아니다 — 필수 필드 결손/타입 불일치는 거부한다.

    통과시키면 ORM 대입 단계에서 기본값(0.0 / "")이 조용히 저장되어, 나중에
    ``base_amount_basis`` 나 예측 입력을 오염시킨다.
    """
    with pytest.raises(ValidationError):
        persistence.persist_crawl_results(
            test_db,
            _crawl_job(test_db),
            _notice_request(),
            {"job_status": "completed", "collected_count": 1, "items": [broken_item]},
            defer_embeddings=True,
        )


def test_unknown_item_key_is_rejected_not_silently_dropped():
    """미지 키는 조용히 드롭되지 않고 거부된다(오타가 값 유실로 숨지 않게).

    부모(``CrawlNoticeItem``)를 그대로 상속하면 pydantic 기본값 ``extra="ignore"`` 라서
    ``award_floor_rate`` 오타(``award_floor``)가 통과하고 하한율만 조용히 사라진다.
    """
    with pytest.raises(ValidationError) as excinfo:
        KonepsCollectedItem.model_validate(
            {
                "notice_number": "N-1",
                "title": "오타 키",
                "base_amount": 1.0,
                "award_floor": 0.87995,
            }
        )

    assert "award_floor" in str(excinfo.value)


def test_public_crawl_notice_item_still_tolerates_extra_keys():
    """공개 표면(OpenAPI 스키마)의 관용은 바뀌지 않는다 — forbid 는 수집 내부 계약만.

    ``CrawlNoticeItem`` 은 HTTP 응답 스키마이므로 여기까지 좁히면 외부 계약이 흔들린다.
    """
    narrowed = CrawlNoticeItem.model_validate(
        {"notice_number": "N-1", "title": "t", "base_amount": 1.0, "unknown": 1}
    )

    assert not hasattr(narrowed, "unknown")


# --------------------------------------------------------------------------- #
# 4. metadata 읽기 투영
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, None),
        ("낙찰사", "낙찰사"),
        ("", ""),  # 빈 문자열은 보존(falsy 게이트 유지)
        (0, "0"),  # 스칼라는 기존처럼 문자열화
        (12, "12"),
        (1.5, "1.5"),
        (True, "True"),
        ({"name": "낙찰사"}, None),  # 비스칼라는 값 없음으로 접는다
        ([1, 2], None),
        ((1, 2), None),
    ],
)
def test_metadata_text_normalization_value_table(raw_value, expected):
    """``Text`` 정규화 값 테이블: 스칼라만 문자열화하고 비스칼라는 ``None``."""
    facts = CrawlItemMetadataFacts.model_validate({"winning_company": raw_value})

    assert facts.winning_company == expected


@pytest.mark.parametrize(
    ("metadata", "expected_signal"),
    [
        ({"winning_company": "낙찰사"}, True),
        ({"opening_status": "개찰완료"}, True),
        ({"winning_company": ""}, False),
        ({}, False),
        # 구조가 어긋난 값(중첩 객체/목록)은 개찰 흔적으로 세지 않는다: ``str({...})`` 가
        # truthy 라서 게이트를 통과하면 빈 TenderResult 가 생기고 파이썬 repr 이 저장된다.
        ({"winning_company": {"name": "낙찰사"}}, False),
        ({"opening_status": ["개찰완료"]}, False),
        # 같은 metadata 에 정상 스칼라 흔적이 있으면 게이트는 그대로 열린다.
        ({"winning_company": {"name": "x"}, "winning_amount": 88_000_000.0}, True),
    ],
)
def test_award_signal_gate_ignores_non_scalar_text(metadata, expected_signal):
    facts = CrawlItemMetadataFacts.model_validate(metadata)

    assert facts.has_award_signal() is expected_signal



def test_metadata_facts_ignores_unknown_keys_and_keeps_bag_intact():
    metadata = {
        "opening_demand_agency": "서울특별시교육청",
        "winning_amount": 88_000_000.0,
        "raw_openapi_item": {"bidNtceNo": "A-1"},
        "openapi_operation": "getScsbidListSttusCnstwk",
    }
    item = KonepsCollectedItem(
        notice_number="A-1", title="t", base_amount=1.0, metadata=metadata
    )

    facts = item.opening_facts()

    assert facts.resolved_agency_name() == "서울특별시교육청"
    assert facts.winning_amount == 88_000_000.0
    assert facts.has_award_signal() is True
    # 투영은 읽기 전용 — bag 은 provenance 키까지 값이 그대로 유지된다(얕은 복사이므로
    # 중첩된 원시 응답 객체도 동일 객체로 남아 직렬화 산출이 바뀌지 않는다).
    assert item.metadata == metadata
    assert item.metadata["raw_openapi_item"] is metadata["raw_openapi_item"]


def test_metadata_facts_agency_resolution_order():
    def _facts(**metadata) -> CrawlItemMetadataFacts:
        return CrawlItemMetadataFacts.model_validate(metadata)

    assert (
        _facts(
            opening_demand_agency="개찰수요",
            demand_agency="수요",
            issuing_agency="공고",
        ).resolved_agency_name()
        == "개찰수요"
    )
    assert (
        _facts(demand_agency="수요", issuing_agency="공고").resolved_agency_name()
        == "수요"
    )
    assert _facts(issuing_agency="공고").resolved_agency_name() == "공고"
    assert _facts().resolved_agency_name() == ""


def test_metadata_facts_demand_agency_prefers_opening_over_plain():
    """``resolved_demand_agency`` 는 개찰수요기관을 우선한다(순서 고정).

    골든 픽스처는 두 값이 같아서(생산자가 같은 ``dminsttNm`` 을 양쪽 키에 싣는다) 우선
    순위를 구분하지 못한다. 두 값을 다르게 준 단위 테스트로 순서를 못 박는다 —
    ``project.demand_agency`` 와 설명줄("수요기관: ...")이 이 순서에 의존한다.
    """

    def _facts(**metadata) -> CrawlItemMetadataFacts:
        return CrawlItemMetadataFacts.model_validate(metadata)

    assert (
        _facts(
            opening_demand_agency="개찰수요기관", demand_agency="공고수요기관"
        ).resolved_demand_agency()
        == "개찰수요기관"
    )
    # 개찰수요기관이 비면(결측/빈 문자열) 공고 수요기관으로 폴백한다.
    assert _facts(demand_agency="공고수요기관").resolved_demand_agency() == "공고수요기관"
    assert (
        _facts(
            opening_demand_agency="", demand_agency="공고수요기관"
        ).resolved_demand_agency()
        == "공고수요기관"
    )
    assert _facts().resolved_demand_agency() is None


def test_metadata_facts_has_no_award_signal_when_empty():
    assert CrawlItemMetadataFacts().has_award_signal() is False
    # 빈 문자열은 falsy 를 유지한다(개찰 결과 없음).
    assert (
        CrawlItemMetadataFacts.model_validate(
            {"opening_status": "", "winning_company": ""}
        ).has_award_signal()
        is False
    )


def test_reserve_detail_promotion_covers_empty_error_and_summary_shapes():
    """미조회 / 실패 / 정상 요약 세 형태가 같은 모델로 표현된다."""
    assert ScsbidReserveDetail.model_validate({}).reserve_prices == []
    failed = ScsbidReserveDetail.model_validate({"reserve_detail_error": "HTTP 429"})
    assert failed.reserve_detail_error == "HTTP 429"
    assert failed.reserve_prices == []
    summarized = ScsbidReserveDetail.model_validate(
        {
            "reserve_prices": [101.0, 102.0],
            "selected_numbers": [1],
            "planned_price": 100.0,
            "base_amount": 100.0,
            "reserve_detail_operation": "getOpengResultListInfoCnstwkPreparPcDetail",
        }
    )
    assert summarized.reserve_prices == [101.0, 102.0]
    assert summarized.planned_price == 100.0


# --------------------------------------------------------------------------- #
# 5. 공개 표면(OpenAPI 스키마) 격리
# --------------------------------------------------------------------------- #
def test_internal_fields_do_not_leak_into_public_crawl_notice_item():
    """``CrawlNoticeItem`` 은 OpenAPI 스펙 표면이므로 내부 전용 필드가 없어야 한다."""
    internal_only = set(KonepsCollectedItem.model_fields) - set(
        CrawlNoticeItem.model_fields
    )
    assert internal_only == {"award_floor_rate", "eligibility_raw"}


def test_boundary_serializer_narrows_items_to_json_values():
    """경계 직렬화는 DTO 를 순수 JSON 값으로 바꾼다(브로커/HTTP payload 계약)."""
    item = KonepsCollectedItem(
        notice_number="S-1",
        title="직렬화",
        base_amount=1.0,
        closing_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
    )

    payload = collection.serialize_collect_payload(
        {"job_status": "completed", "items": [item], "metadata": {"resolved_mode": "x"}}
    )

    assert payload["items"] == [
        {
            "notice_number": "S-1",
            "title": "직렬화",
            "base_amount": 1.0,
            "estimated_amount": None,
            "closing_at": "2026-05-20T10:00:00Z",
            "business_type": None,
            "business_type_code": None,
            "business_type_label": None,
            "region": None,
            "license_codes": [],
            "source_url": None,
            "metadata": {},
            "award_floor_rate": None,
            "eligibility_raw": None,
        }
    ]
    # 봉투의 나머지 키는 그대로 통과한다.
    assert payload["job_status"] == "completed"
    assert payload["metadata"] == {"resolved_mode": "x"}


def test_boundary_serializer_passes_through_plain_dict_items():
    """외부 호출부가 이미 dict 를 실어 보낸 payload 는 이중 변환하지 않는다."""
    payload = collection.serialize_collect_payload(
        {"items": [{"notice_number": "RAW-1"}]}
    )

    assert payload["items"] == [{"notice_number": "RAW-1"}]
