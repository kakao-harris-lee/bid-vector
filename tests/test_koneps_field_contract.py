"""KONEPS 필드 계약 순수 검증기의 값-테이블 테스트.

실 API 없이 fake item dict 로만 각 함정의 위반/정상 케이스를 검증한다:

- ``sucsfbidRate`` 범위/basis (#220: 낙찰가/예정가 사정률이지 기초금액 아님),
- ``bidNtceOrd`` 제로패딩/int-파괴 탐지 (#210),
- base 키 중첩(예정가 오염) 탐지 (#220),
- 계약상 실리는 계열 밖 필드 등장(목록에 없어야 할 상세) (#209),
- 미지 필드 리포트(신규 필드 소비 전 검토 트리거).

검증기는 순수 함수이므로 입력->출력만 확인한다(§4.7).
"""

from app.domain.money import Basis
from app.schemas.schemas import CrawlRequest
from app.services.koneps import field_contract as fc
from app.services.koneps import openapi
from app.services.koneps.field_contract import (
    OperationFamily,
    Severity,
    ViolationKind,
)

_SCSBID_OP = "getScsbidListSttusServc"
_NOTICE_OP = "getBidPblancListInfoServc"
_LICENSE_OP = "getBidPblancListInfoLicenseLimit"
_RESERVE_OP = "getOpengResultListInfoServcPreparPcDetail"
_OPENING_OP = "getOpengResultListInfoServc"

# openapi.build_openapi_notice_item 의 base_amount 후보 키 순서(openapi.py:436-444)를
# 그대로 옮긴 기대값. field_contract.BASE_RESOLUTION_ORDER 가 이것과 같아야 위반 detail
# 의 resolved_key·--dry-run 표시가 프로덕션이 실제 고른 키와 일치한다. 아래 행동 가드
# 테스트가 실제 build_openapi_notice_item 해석과 동치임을 실행으로 확인한다.
_PRODUCTION_BASE_ORDER = (
    "asignBdgtAmt",
    "bdgtAmt",
    "presmptPrce",
    "presmptAmt",
    "bssAmt",
    "bssamt",
    "bssAmtPurcnstcst",
)


def _kinds(violations):
    return [v.kind for v in violations]


# --- operation -> family classification --------------------------------------


def test_operation_family_classification():
    assert fc.operation_family(_NOTICE_OP) is OperationFamily.NOTICE_LIST
    # LicenseLimit shares the getBidPblancListInfo prefix but must win.
    assert fc.operation_family(_LICENSE_OP) is OperationFamily.LICENSE_LIMIT
    assert fc.operation_family(_SCSBID_OP) is OperationFamily.SCSBID_AWARD
    # PreparPcDetail shares the getOpengResultListInfo prefix but must win.
    assert fc.operation_family(_RESERVE_OP) is OperationFamily.RESERVE_DETAIL
    assert fc.operation_family(_OPENING_OP) is OperationFamily.OPENING_RESULT
    assert fc.operation_family("somethingElse") is OperationFamily.UNKNOWN
    assert fc.operation_family(None) is OperationFamily.UNKNOWN


# --- sucsfbidRate 범위/basis (#220) ------------------------------------------


def test_success_rate_in_range_ok():
    # "88.001" -> 0.88001 사정률, 정상.
    assert fc.validate_ranges({"sucsfbidRate": "88.001"}) == []
    # 이미 분수인 경우도 통과.
    assert fc.validate_ranges({"sucsfbidRate": 0.88}) == []


def test_success_rate_amount_in_rate_field_is_error():
    # 낙찰가(원)를 사정률 자리에 넣으면 범위 밖 -> ERROR.
    violations = fc.validate_ranges({"sucsfbidRate": 88000000})
    assert _kinds(violations) == [ViolationKind.FRACTION_OUT_OF_RANGE]
    assert violations[0].severity is Severity.ERROR


def test_success_rate_contract_declares_planned_price_basis():
    # 계약이 basis=예정가를 선언한다(기초금액과 교차 대입 금지의 단일 출처).
    contract = {c.raw_name: c for c in fc.FIELD_CONTRACTS}["sucsfbidRate"]
    assert contract.basis is Basis.PLANNED_PRICE
    # 범위 밖 위반 detail 에 기대 basis 가 노출돼 소비자 혼동을 줄인다.
    violations = fc.validate_ranges({"sucsfbidRate": 0.2})
    assert violations
    assert "planned_price" in violations[0].detail


def test_success_rate_absent_is_not_a_violation():
    assert fc.validate_ranges({}) == []
    assert fc.validate_ranges({"sucsfbidRate": None}) == []
    assert fc.validate_ranges({"sucsfbidRate": ""}) == []


# --- sucsfbidLwltRate 범위 ----------------------------------------------------


def test_floor_rate_in_range_ok_but_out_of_range_error():
    assert fc.validate_ranges({"sucsfbidLwltRate": "87.745"}) == []
    violations = fc.validate_ranges({"sucsfbidLwltRate": 0.1})
    assert _kinds(violations) == [ViolationKind.FRACTION_OUT_OF_RANGE]


# --- bidNtceOrd 제로패딩/int-파괴 (#210) -------------------------------------


def test_bid_notice_ord_zero_padded_string_ok():
    # 올바른 상태: 제로패딩 문자열.
    assert fc.validate_identifiers({"bidNtceOrd": "000"}) == []
    assert fc.validate_identifiers({"bidNtceOrd": "001"}) == []


def test_bid_notice_ord_int_is_error():
    # int 로 도착 = 제로패딩 이미 소실 -> ERROR ('000'->0 은 KONEPS 빈 응답).
    for coerced in (0, 3):
        violations = fc.validate_identifiers({"bidNtceOrd": coerced})
        assert _kinds(violations) == [ViolationKind.IDENTIFIER_NOT_STRING]
        assert violations[0].severity is Severity.ERROR


def test_bid_notice_ord_float_is_error():
    violations = fc.validate_identifiers({"bidNtceOrd": 1.0})
    assert _kinds(violations) == [ViolationKind.IDENTIFIER_NOT_STRING]


def test_bid_notice_ord_absent_is_ok():
    assert fc.validate_identifiers({}) == []
    assert fc.validate_identifiers({"bidNtceOrd": None}) == []


# --- base 키 중첩(예정가 오염) 탐지 (#220) -----------------------------------


def test_base_true_base_key_only_is_ok():
    item = {"bssAmt": "90000000"}
    assert fc.validate_base_basis(item, OperationFamily.NOTICE_LIST) == []


def test_base_yega_only_is_warn():
    # 기초금액 키 부재 + 예정가만 -> base 가 예정가 값이 됨(WARN, 인지 필요).
    item = {"presmptPrce": "100000000"}
    violations = fc.validate_base_basis(item, OperationFamily.NOTICE_LIST)
    assert _kinds(violations) == [ViolationKind.BASE_BASIS_YEGA_ONLY]
    assert violations[0].severity is Severity.WARN


def test_base_precedence_is_error():
    # 기초금액 키가 있는데도 해석 순서상 예정가가 먼저 선택됨 -> ERROR.
    item = {"presmptPrce": "100000000", "bssAmt": "90000000"}
    violations = fc.validate_base_basis(item, OperationFamily.NOTICE_LIST)
    assert _kinds(violations) == [ViolationKind.BASE_BASIS_PRECEDENCE]
    assert violations[0].severity is Severity.ERROR


def test_base_no_amounts_is_not_a_violation():
    assert fc.validate_base_basis({}, OperationFamily.NOTICE_LIST) == []
    # 0/음수 금액은 미상 취급(soft fallback).
    assert fc.validate_base_basis(
        {"presmptPrce": "0"}, OperationFamily.NOTICE_LIST
    ) == []


def test_base_basis_only_checks_notice_list_family():
    # scsbid/reserve 등은 자체 base 처리 경로가 있어 이 검증기 대상이 아니다.
    item = {"presmptPrce": "100000000"}
    assert fc.validate_base_basis(item, OperationFamily.SCSBID_AWARD) == []
    assert fc.validate_base_basis(item, OperationFamily.RESERVE_DETAIL) == []


# --- base 해석 순서 드리프트 가드 (프로덕션 충실성) --------------------------


def test_base_resolution_order_matches_documented_production_order():
    # openapi.py:436-444 후보 리스트를 그대로 옮긴 기대값과 정확히 일치해야 한다
    # (그룹 내부 순서 포함 — 예산 2키 다음 예정가 2키).
    assert fc.BASE_RESOLUTION_ORDER == _PRODUCTION_BASE_ORDER


def _drift_guard_request() -> CrawlRequest:
    return CrawlRequest(source="koneps-openapi", category="service")


def test_base_resolution_order_agrees_with_production_build():
    """행동 드리프트 가드: 실제 ``build_openapi_notice_item`` 이 base_amount 를 고르는
    키가 ``BASE_RESOLUTION_ORDER`` 예측과 일치하는지 실행으로 확인한다.

    각 접미(suffix) 케이스에서 ``order[i:]`` 키만 서로 다른 값으로 채우면 프로덕션은
    ``order[i]`` (첫 후보)를 골라야 한다. field_contract 순서가 openapi 순서와 어긋나면
    (향후 어느 쪽이 바뀌든) 이 assert 가 깨져 드리프트를 잡는다. 상수 추출 없이 실제
    프로덕션 함수를 실행하므로 openapi 인라인 리스트 변경도 포착한다.
    """
    request = _drift_guard_request()
    order = fc.BASE_RESOLUTION_ORDER
    for i, expected_key in enumerate(order):
        raw_item = {"bidNtceNo": "20260600001", "bidNtceNm": "드리프트 가드"}
        for offset, key in enumerate(order[i:]):
            # 값이 겹치지 않도록 각 키에 구별되는 양수를 준다.
            raw_item[key] = str(1_000_000 * (i + offset + 1))
        expected_value = float(raw_item[expected_key])
        built = openapi.build_openapi_notice_item(
            raw_item, request=request, operation=_NOTICE_OP
        )
        assert built is not None
        assert built["base_amount"] == expected_value, (
            f"suffix i={i}: 프로덕션이 {expected_key} 를 고르지 않음 — "
            f"BASE_RESOLUTION_ORDER 가 openapi 후보 순서와 어긋남"
        )


# --- 계열 밖 필드 배치 (#209) -------------------------------------------------


def test_floor_rate_in_scsbid_is_unexpected_placement():
    # 낙찰하한율은 목록/개찰 응답에 없어야 한다(표적조회/서브op 전용) -> WARN.
    violations = fc.validate_field_placement(
        {"sucsfbidLwltRate": "0.877"}, OperationFamily.SCSBID_AWARD
    )
    assert _kinds(violations) == [ViolationKind.UNEXPECTED_FIELD_FOR_OPERATION]
    assert violations[0].severity is Severity.WARN


def test_success_rate_in_notice_list_is_unexpected_placement():
    violations = fc.validate_field_placement(
        {"sucsfbidRate": "0.88"}, OperationFamily.NOTICE_LIST
    )
    assert _kinds(violations) == [ViolationKind.UNEXPECTED_FIELD_FOR_OPERATION]


def test_field_in_declared_family_is_ok():
    # success_rate 는 scsbid 계열에서 정당 -> 배치 위반 없음.
    assert (
        fc.validate_field_placement(
            {"sucsfbidRate": "0.88"}, OperationFamily.SCSBID_AWARD
        )
        == []
    )
    # 차수는 notice_list 계열에서 정당.
    assert (
        fc.validate_field_placement(
            {"bidNtceOrd": "000"}, OperationFamily.NOTICE_LIST
        )
        == []
    )


# --- 미지 필드 리포트 ---------------------------------------------------------


def test_unknown_fields_reports_undeclared_keys():
    item = {"bidNtceNo": "20260612345", "brandNewField": "x", "anotherNew": 1}
    assert fc.unknown_fields(item) == ["anotherNew", "brandNewField"]


def test_known_fields_are_not_reported_unknown():
    item = {
        "bidNtceNo": "x",
        "sucsfbidRate": "0.88",
        "bidNtceOrd": "000",
        "bssAmt": "1",
    }
    assert fc.unknown_fields(item) == []


# --- validate_item 통합 (계열 판정 + 파이프라인) ------------------------------


def test_validate_item_scsbid_success_rate_out_of_range():
    item = {"bidNtceNo": "x", "sucsfbidRate": 88000000, "bidNtceOrd": "000"}
    violations = fc.validate_item(item, operation=_SCSBID_OP)
    assert ViolationKind.FRACTION_OUT_OF_RANGE in _kinds(violations)


def test_validate_item_scsbid_ord_int_break():
    item = {"bidNtceNo": "x", "bidNtceOrd": 0}
    violations = fc.validate_item(item, operation=_SCSBID_OP)
    assert ViolationKind.IDENTIFIER_NOT_STRING in _kinds(violations)


def test_validate_item_notice_base_precedence():
    item = {"bidNtceNo": "x", "presmptPrce": "100000000", "bssAmt": "90000000"}
    violations = fc.validate_item(item, operation=_NOTICE_OP)
    assert ViolationKind.BASE_BASIS_PRECEDENCE in _kinds(violations)


def test_validate_item_clean_scsbid_row_has_no_violations():
    # 정상 개찰행: 사정률 분수 + 제로패딩 차수 -> 위반 0.
    item = {
        "bidNtceNo": "20260612345",
        "bidNtceNm": "테스트 공고",
        "sucsfbidRate": "88.001",
        "sucsfbidAmt": "88000000",
        "bidNtceOrd": "000",
        "bidwinnrNm": "테스트업체",
    }
    assert fc.validate_item(item, operation=_SCSBID_OP) == []


def test_validate_item_clean_notice_row_has_no_violations():
    item = {
        "bidNtceNo": "20260612345",
        "bidNtceNm": "테스트 공고",
        "bssAmt": "90000000",
        "bidNtceOrd": "000",
    }
    assert fc.validate_item(item, operation=_NOTICE_OP) == []


# --- describe_contracts 요약 (스크립트 --dry-run 소비) ------------------------


def test_describe_contracts_lists_every_trap_field():
    text = "\n".join(fc.describe_contracts())
    for name in ("sucsfbidRate", "sucsfbidLwltRate", "bidNtceOrd", "base_amount"):
        assert name in text
    # 실측 출처(이슈 번호)가 사람이 읽는 요약에 남아있다.
    assert "#220" in text
    assert "#210" in text
    assert "#209" in text
