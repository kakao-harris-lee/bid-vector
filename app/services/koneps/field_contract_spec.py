"""KONEPS 수집 필드 **의미 계약의 선언**(어휘 · 계열 분류표 · 함정 필드 계약 · 기지 필드).

``field_contract`` 가 한도(500줄) 경계까지 자라 여유가 0줄이었다. 그래서 그 모듈을 책임
단위로 갈랐다: 여기는 **무엇이 계약인가**(§4.5-3 규칙=데이터)만 선언하고, 그 계약을 실제
응답 값에 적용하는 **순수 검증기**는 ``field_contract`` 가 계속 소유한다(검증기 테스트도
그 자리에 남는다). 새 함정 필드 등록은 코드가 아니라 이 표에 한 항목 추가로 끝난다.

여기에는 검증 로직도 IO 도 없다. 유일한 함수 ``operation_family`` 는 마커 표를 읽는 얇은
해석기다(순수). ``field_contract`` 가 필요한 이름을 import 해 다시 노출하므로
(``field_contract.FIELD_CONTRACTS`` 등) 기존 호출부 참조 경로는 그대로다.

mypy strict 아일랜드(``field_contract`` 와 동일 정책): 무거운 의존을 끌지 않고 순수 strict
아일랜드 ``app.domain.money.Basis`` 만 재사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.money import Basis


class OperationFamily(str, Enum):
    """KONEPS 오퍼레이션을 응답 스키마가 같은 계열로 묶는 단일 어휘.

    필드가 "어느 응답에 정당하게 실리는가"를 계약이 참조한다. 값은 로그/리포트에 그대로
    쓸 수 있게 문자열 혼합 Enum이다.
    """

    NOTICE_LIST = "notice_list"  # getBidPblancListInfo* (공고 목록/표적조회)
    LICENSE_LIMIT = "license_limit"  # getBidPblancListInfoLicenseLimit (자격 상세 서브op)
    SCSBID_AWARD = "scsbid_award"  # getScsbidListSttus* (개찰/낙찰 목록)
    RESERVE_DETAIL = "reserve_detail"  # getOpengResultListInfo*PreparPcDetail (예비가격)
    OPENING_RESULT = "opening_result"  # getOpengResultListInfo* (개찰 1위 목록)
    UNKNOWN = "unknown"


class Scale(str, Enum):
    """필드 값의 축(스케일) — 어떤 정규화·범위 검증을 적용하는지 결정한다."""

    FRACTION = "fraction"  # 0~1.x 비율(율). 백분율이면 /100 정규화 후 범위 검증
    AMOUNT = "amount"  # 원 단위 금액
    IDENTIFIER = "identifier"  # 식별자 문자열(제로패딩 가능 — int 변환 금지)


class Severity(str, Enum):
    """계약 위반 심각도. ERROR=명백히 틀림, WARN=모호/인지 필요."""

    ERROR = "error"
    WARN = "warn"


class ViolationKind(str, Enum):
    """위반 종류(매직 문자열 대신 선언). 리포트·테스트가 이 키로 분류한다."""

    FRACTION_OUT_OF_RANGE = "fraction_out_of_range"
    IDENTIFIER_NOT_STRING = "identifier_not_string"
    BASE_BASIS_PRECEDENCE = "base_basis_precedence"
    BASE_BASIS_YEGA_ONLY = "base_basis_yega_only"
    UNEXPECTED_FIELD_FOR_OPERATION = "unexpected_field_for_operation"


@dataclass(frozen=True)
class FieldContract:
    """한 함정 필드의 의미 계약(선언 데이터). 실측 출처를 ``provenance``에 남긴다."""

    raw_name: str  # KONEPS 응답의 raw 키 이름
    concept: str  # 개념/의미(한글)
    basis: Basis | None  # 금액/율의 기준(basis). 비금액이면 None
    scale: Scale
    zero_padded: bool  # 제로패딩 식별자면 True (int 변환 시 파괴 — #210)
    present_in: frozenset[OperationFamily]  # 정당하게 실리는 오퍼레이션 계열
    provenance: str  # 실측 출처 주석(#209/#210/#220)
    expected_min: float | None = None  # FRACTION 하한(포함)
    expected_max: float | None = None  # FRACTION 상한(포함)


# ---------------------------------------------------------------------------
# 오퍼레이션 -> 계열 분류 (데이터 규칙, 코드는 해석만)
# ---------------------------------------------------------------------------
# 더 구체적인 마커를 먼저 둔다: LicenseLimit / PreparPcDetail 은 각각 NOTICE_LIST /
# OPENING_RESULT 접두를 공유하므로 순서가 중요하다(첫 매칭이 이긴다).
_FAMILY_MARKERS: tuple[tuple[str, OperationFamily], ...] = (
    ("LicenseLimit", OperationFamily.LICENSE_LIMIT),
    ("PreparPcDetail", OperationFamily.RESERVE_DETAIL),
    ("getScsbidListSttus", OperationFamily.SCSBID_AWARD),
    ("getOpengResultListInfo", OperationFamily.OPENING_RESULT),
    ("getBidPblancListInfo", OperationFamily.NOTICE_LIST),
)


def operation_family(operation: str | None) -> OperationFamily:
    """오퍼레이션 이름을 응답 계열로 분류한다(순수, 첫 매칭 우선)."""
    name = str(operation or "")
    for marker, family in _FAMILY_MARKERS:
        if marker in name:
            return family
    return OperationFamily.UNKNOWN


# ---------------------------------------------------------------------------
# 기초금액(base) 후보 키의 basis 선언 (#220: base==예정가 오염 근본원인)
# ---------------------------------------------------------------------------
# 엔트리포인트(openapi.build_openapi_notice_item)가 base_amount 를 해석하는 후보 키는
# 예산/예정가/기초금액이 뒤섞여 있고, 현재 해석 순서(``BASE_RESOLUTION_ORDER``)는 예산·
# 예정가 키를 기초금액 키보다 **먼저** 시도한다. 그래서 예정가(presmptPrce)만 있고
# 기초금액 키가 없으면 base==예정가 오염이, 둘 다 있으면 예정가가 먼저 선택되는 함정이
# 생긴다(#220). 각 키의 실제 basis 를 데이터로 선언해 검증기가 해석만 한다.
TRUE_BASE_KEYS: tuple[str, ...] = ("bssAmt", "bssamt", "bssAmtPurcnstcst")
# 이 순서가 base_amount 해석의 **단일 출처**다: openapi.build_openapi_notice_item 이
# ``BASE_RESOLUTION_ORDER`` 를 import 해 base_amount 후보로 그대로 소비한다(더는 인라인
# 리스트를 두지 않는다). 그룹 내부 순서는 예산 2키(asignBdgtAmt, bdgtAmt) 다음 예정가
# 2키(presmptPrce, presmptAmt) — 여기서 정한 순서를 프로덕션이 곧 따른다. 같은 상수를
# 공유하므로 위반 detail 의 resolved_key·--dry-run 표시와 실제 해석은 드리프트할 수 없다.
# tests/test_koneps_field_contract.py 의 드리프트 가드가 실제 build_openapi_notice_item
# 해석과 동치임을 실행으로 확인한다.
_YEGA_OR_BUDGET_KEYS: tuple[str, ...] = (
    "asignBdgtAmt",  # 배정예산액 — 기초금액 아님
    "bdgtAmt",  # 예산금액 — 기초금액 아님
    "presmptPrce",  # 추정가격(부가세 포함) — 기초금액 아님
    "presmptAmt",  # 추정금액 — 기초금액 아님
)
# 예산·예정가 키가 기초금액 키보다 앞선다는 사실 자체가 #220 함정이며, 이 검증기가
# 그것을 표면화한다. openapi 가 이 상수를 소비하므로 여기가 그 순서의 유일한 정의처다.
BASE_RESOLUTION_ORDER: tuple[str, ...] = _YEGA_OR_BUDGET_KEYS + TRUE_BASE_KEYS
# 추정가격(estimated_amount) 해석 순서 — base 와 **별도** 후보 집합(단일 출처). base 와
# 달리 추정가격은 추정가격 키(presmptPrce/presmptAmt)를 **먼저** 시도하고 예산 키로만
# 폴백하며, 기초금액 키(bssAmt*)는 포함하지 않는다: 추정가격의 basis 는 BUDGET_ESTIMATE
# 라 기초금액과 섞지 않는다(#162). openapi.build_openapi_notice_item 의 estimated_amount
# 후보가 이 상수를 그대로 소비한다(인라인 리스트 제거). 후보 전부 BUDGET_ESTIMATE basis
# 라 base 와 달리 기초금액-우선 재정렬 대상이 아니다.
ESTIMATED_RESOLUTION_ORDER: tuple[str, ...] = (
    "presmptPrce",  # 추정가격(부가세 포함)
    "presmptAmt",  # 추정금액
    "asignBdgtAmt",  # 배정예산액(폴백)
    "bdgtAmt",  # 예산금액(폴백)
)
# 검증기 전용 룩업: base 가 어느 키에서 해석됐는지를 basis 로 분류한다(해석 **순서**는 위
# 두 상수가 소유하고 프로덕션이 그것을 소비한다 — 이 표는 판정에만 쓰인다).
KEY_BASIS: dict[str, Basis] = {
    **{key: Basis.BASE_AMOUNT for key in TRUE_BASE_KEYS},
    "presmptPrce": Basis.BUDGET_ESTIMATE,
    "presmptAmt": Basis.BUDGET_ESTIMATE,
    "asignBdgtAmt": Basis.BUDGET_ESTIMATE,
    "bdgtAmt": Basis.BUDGET_ESTIMATE,
}


# ---------------------------------------------------------------------------
# 함정 필드 계약 (단일 필드 트랩: 율/식별자). base 는 키-집합 트랩이라 별도 검증기.
# ---------------------------------------------------------------------------
FIELD_CONTRACTS: tuple[FieldContract, ...] = (
    FieldContract(
        raw_name="sucsfbidRate",
        concept="낙찰가/예정가 사정률(성공사정률) — 기초금액 기준 아님",
        basis=Basis.PLANNED_PRICE,
        scale=Scale.FRACTION,
        zero_padded=False,
        present_in=frozenset({OperationFamily.SCSBID_AWARD}),
        provenance=(
            "#220: 낙찰가/success_rate=예정가이므로 base 로 쓰면 base==예정가 오염. "
            "실측 범위 대략 0.5~1.0(사정률)."
        ),
        expected_min=0.5,
        expected_max=1.0,
    ),
    FieldContract(
        raw_name="sucsfbidLwltRate",
        concept="낙찰하한율 — 목록엔 없고 표적조회/license-limit 서브op만",
        basis=None,
        scale=Scale.FRACTION,
        zero_padded=False,
        present_in=frozenset(
            {OperationFamily.NOTICE_LIST, OperationFamily.LICENSE_LIMIT}
        ),
        provenance=(
            "#209: 광범위 목록 피드엔 자격/하한 상세가 없다. 표적조회(inqryDiv=2)와 "
            "license-limit 서브op만 값을 싣는다. 값은 하한율(분수)."
        ),
        expected_min=0.5,
        expected_max=1.0,
    ),
    FieldContract(
        raw_name="bidNtceOrd",
        concept="공고 차수 — 제로패딩 문자열('000'), int 변환 금지",
        basis=None,
        scale=Scale.IDENTIFIER,
        zero_padded=True,
        present_in=frozenset(
            {
                OperationFamily.NOTICE_LIST,
                OperationFamily.LICENSE_LIMIT,
                OperationFamily.SCSBID_AWARD,
                OperationFamily.OPENING_RESULT,
            }
        ),
        provenance=(
            "#210: int 변환 시 '000'->0 으로 KONEPS 가 빈 응답(totalCount=0). raw "
            "제로패딩 문자열로 보존해야 한다."
        ),
    ),
)


# ---------------------------------------------------------------------------
# 소비 코드가 이미 다루는 raw 키(미지 필드 탐지의 기준 집합).
# ---------------------------------------------------------------------------
# 아래에 없는 키가 응답에 나오면 "미지 필드"로 리포트한다 — 신규 필드를 소비 전에
# 사람이 검토(계약 등록)하게 만드는 게 목적이다. 출처는 실제 소비 코드(openapi.py /
# scsbid.py / collection.py)의 raw_item.get(...) 호출부.
KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        # notice_list (build_openapi_notice_item)
        "bidNtceNo",
        "bidPbancNo",
        "bfSpecRgstNo",
        "bidNtceNm",
        "ntceNm",
        "asignBdgtAmt",
        "bdgtAmt",
        "presmptPrce",
        "presmptAmt",
        "bssAmt",
        "bssamt",
        "bssAmtPurcnstcst",
        "bsnsDivNm",
        "prcmBsneSeCd",
        "dminsttNm",
        "ntceInsttNm",
        "opengDt",
        "opengDate",
        "bidOpenDt",
        "bidClseDt",
        "bidNtceDt",
        "bidBeginDt",
        "bidNtceDtlUrl",
        "ntceSpecDocUrl1",
        "indstrytyCd",
        "indstrytyNm",
        "prtcptLmtRgnNm",
        "sucsfbidLwltRate",
        "bidNtceOrd",
        "ntceKindNm",
        "rgstTyNm",
        "bidMethdNm",
        "cntrctCnclsMthdNm",
        "refNo",
        # eligibility flags (ELIGIBILITY_RAW_KEYS)
        "indstrytyLmtYn",
        "bidPrtcptLmtYn",
        "prdctClsfcLmtYn",
        "cmmnSpldmdCorpRgnLmtYn",
        "rgnLmtBidLocplcJdgmBssCd",
        "rgnLmtBidLocplcJdgmBssNm",
        # license_limit (LICENSE_LIMIT_ITEM_KEYS)
        "lcnsLmtNm",
        "permsnIndstrytyList",
        "lmtGrpNo",
        "lmtSno",
        # scsbid_award (build_scsbid_award_item)
        "sucsfbidAmt",
        "sucsfbidRate",
        "rlOpengDt",
        "fnlSucsfDate",
        "rgstDt",
        "bidClsfcNo",
        "rbidNo",
        "prtcptCnum",
        "bidwinnrNm",
        "bidwinnrBizno",
        # reserve_detail (summarize_scsbid_reserve_detail)
        "plnprc",
        "compnoRsrvtnPrceSno",
        "bsisPlnprc",
        "drwtYn",
        # opening_result (build_opening_result_summary)
        "opengCorpInfo",
        "progrsDivCdNm",
    }
)
