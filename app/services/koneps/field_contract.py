"""KONEPS 수집 필드의 **의미 계약**을 데이터로 선언하고 순수 검증한다.

반복 버그(#209 자격상세 목록 부재, #210 차수 int 파괴, #220 success_rate=예정가)는
전부 **신규 수집 필드를 소비 전에 실배치 검증하지 않아** 라이브에서 터졌다. 이 모듈은
그 함정 필드들의 계약(raw 이름·개념·basis·스케일·제로패딩·예상 범위·어느 오퍼레이션에
있는지)을 **선언적 데이터**로 모으고(§4.5.3 규칙=데이터), 코드는 그 데이터를 해석만 하는
순수 검증기(§4.7)만 유지한다. IO/DB 없음 — 응답 item dict를 받아 계약 위반 목록을 돌려준다.

이 모듈은 mypy strict 아일랜드다(과설계 금지: 전역 Money 래퍼가 아니라, 버그가 실제로
났던 좁은 도메인만). 무거운 의존을 끌지 않도록 ``app.domain.money.Basis``(순수 strict
아일랜드)만 재사용하고, 값 정규화는 얇은 로컬 순수 함수로 둔다(``parsing`` 모듈의 mutable
표면과 결합하지 않는다 — 정규화 계약은 아래 ``_as_fraction`` 주석에 명시).

온-디맨드 실배치 검증(실 KONEPS 응답 N건으로 assert)은
``scripts/verify_koneps_field_contract.py`` 가 이 순수 검증기를 소비한다.
"""

from __future__ import annotations

from collections.abc import Mapping
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


@dataclass(frozen=True)
class ContractViolation:
    """순수 검증기가 반환하는 단일 위반. IO 없음 — 값만 담는다."""

    field: str
    kind: ViolationKind
    severity: Severity
    detail: str


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
_TRUE_BASE_KEYS: tuple[str, ...] = ("bssAmt", "bssamt", "bssAmtPurcnstcst")
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
BASE_RESOLUTION_ORDER: tuple[str, ...] = _YEGA_OR_BUDGET_KEYS + _TRUE_BASE_KEYS
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
_KEY_BASIS: dict[str, Basis] = {
    **{key: Basis.BASE_AMOUNT for key in _TRUE_BASE_KEYS},
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
_CONTRACTS_BY_NAME: dict[str, FieldContract] = {
    contract.raw_name: contract for contract in FIELD_CONTRACTS
}


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


# ---------------------------------------------------------------------------
# 값 정규화 (얇은 순수 helper — I/O 없음)
# ---------------------------------------------------------------------------


def _as_fraction(value: object) -> float | None:
    """율 값을 분수로 정규화한다(백분율이면 /100). 파싱 불가면 None.

    ``parsing.normalize_bid_rate_value`` 의 계약을 의도적으로 미러한다(백분율>1.5 -> /100,
    6자리 반올림, 0 이하 -> None). 그 mutable 모듈에 결합하지 않고 이 strict 아일랜드를
    자립시키기 위해 얇게 재선언한다.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        text = value.replace(",", "").strip().rstrip("%").strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError:
            return None
    else:
        return None
    if numeric <= 0:
        return None
    if numeric > 1.5:
        numeric = numeric / 100.0
    return round(numeric, 6)


def _as_amount(value: object) -> float | None:
    """금액 값을 float 로 변환한다. 파싱 불가면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _is_present(value: object) -> bool:
    """비어있지 않은 값인지(None/빈문자열 제외)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


# ---------------------------------------------------------------------------
# 순수 검증기 (item dict -> 위반 목록). 각 검증기는 적용 대상을 스스로 판단한다.
# ---------------------------------------------------------------------------


def validate_ranges(item: Mapping[str, object]) -> list[ContractViolation]:
    """FRACTION 계약 필드가 존재하면 정규화 후 예상 범위 안인지 검증한다.

    데이터 주도: ``FIELD_CONTRACTS`` 의 FRACTION+범위 선언을 순회한다. 값이 범위를 벗어나면
    (예: success_rate 자리에 낙찰가/기초금액 기준값이 들어옴) ERROR. 값 부재는 위반 아님.
    """
    violations: list[ContractViolation] = []
    for contract in FIELD_CONTRACTS:
        if contract.scale is not Scale.FRACTION:
            continue
        if contract.expected_min is None or contract.expected_max is None:
            continue
        raw = item.get(contract.raw_name)
        if not _is_present(raw):
            continue
        fraction = _as_fraction(raw)
        if fraction is None:
            violations.append(
                ContractViolation(
                    field=contract.raw_name,
                    kind=ViolationKind.FRACTION_OUT_OF_RANGE,
                    severity=Severity.ERROR,
                    detail=(
                        f"{contract.raw_name}={raw!r} 를 분수 율로 정규화할 수 없음 "
                        f"({contract.concept})"
                    ),
                )
            )
            continue
        if fraction < contract.expected_min or fraction > contract.expected_max:
            basis_note = (
                f", 기대 basis={contract.basis.value}" if contract.basis else ""
            )
            violations.append(
                ContractViolation(
                    field=contract.raw_name,
                    kind=ViolationKind.FRACTION_OUT_OF_RANGE,
                    severity=Severity.ERROR,
                    detail=(
                        f"{contract.raw_name} 정규화값 {fraction} 이(가) 예상 범위 "
                        f"[{contract.expected_min}, {contract.expected_max}] 밖 — "
                        f"{contract.concept}{basis_note}. 다른 basis 값을 넣었을 가능성."
                    ),
                )
            )
    return violations


def validate_identifiers(item: Mapping[str, object]) -> list[ContractViolation]:
    """제로패딩 식별자(bidNtceOrd 등)가 문자열로 보존됐는지 검증한다.

    raw 값이 ``int``/``float`` 로 도착했다면 이미 제로패딩이 소실된 것(#210): int 변환된
    차수는 KONEPS 서브콜에서 빈 응답을 부른다. 올바른 상태는 raw 문자열('000')이다.
    """
    violations: list[ContractViolation] = []
    for contract in FIELD_CONTRACTS:
        if not contract.zero_padded:
            continue
        raw = item.get(contract.raw_name)
        if raw is None:
            continue
        if isinstance(raw, bool) or isinstance(raw, (int, float)):
            violations.append(
                ContractViolation(
                    field=contract.raw_name,
                    kind=ViolationKind.IDENTIFIER_NOT_STRING,
                    severity=Severity.ERROR,
                    detail=(
                        f"{contract.raw_name}={raw!r} 이(가) 문자열이 아닌 "
                        f"{type(raw).__name__} 로 도착 — 제로패딩 소실(#210). raw "
                        f"제로패딩 문자열로 보존해야 함(예 '000')."
                    ),
                )
            )
    return violations


def validate_base_basis(
    item: Mapping[str, object],
    family: OperationFamily,
) -> list[ContractViolation]:
    """공고(notice_list) item 의 base_amount 해석이 예정가로 오염될지 검증한다(#220).

    프로덕션 해석 순서(``BASE_RESOLUTION_ORDER``)로 base_amount 가 어느 키에서 해석되는지
    모사하고, 그 키의 basis 가 기초금액이 아니면 위반을 낸다:

    - 예정가/예산 키가 기초금액 키보다 먼저 선택됨(기초금액 키도 존재) -> ERROR(precedence).
    - 기초금액 키 자체가 부재해 base 가 예정가/예산 값이 됨 -> WARN(인지 필요).
    """
    if family is not OperationFamily.NOTICE_LIST:
        return []

    resolved_key: str | None = None
    for key in BASE_RESOLUTION_ORDER:
        amount = _as_amount(item.get(key))
        if amount is not None and amount > 0:
            resolved_key = key
            break
    if resolved_key is None:
        return []  # base 미상 — 위반 아님(soft fallback 이 처리)

    resolved_basis = _KEY_BASIS.get(resolved_key)
    if resolved_basis is Basis.BASE_AMOUNT:
        return []  # 기초금액 키에서 해석됨 — 정상

    has_true_base = any(
        (_as_amount(item.get(key)) or 0.0) > 0 for key in _TRUE_BASE_KEYS
    )
    basis_label = resolved_basis.value if resolved_basis else "unknown"
    if has_true_base:
        return [
            ContractViolation(
                field="base_amount",
                kind=ViolationKind.BASE_BASIS_PRECEDENCE,
                severity=Severity.ERROR,
                detail=(
                    f"base_amount 가 {resolved_key}({basis_label}) 에서 먼저 해석됨 — "
                    f"기초금액 키({', '.join(_TRUE_BASE_KEYS)}) 가 있는데도 예정가/예산이 "
                    f"우선 선택됨(#220 base==예정가 오염 precedence)."
                ),
            )
        ]
    return [
        ContractViolation(
            field="base_amount",
            kind=ViolationKind.BASE_BASIS_YEGA_ONLY,
            severity=Severity.WARN,
            detail=(
                f"base_amount 가 {resolved_key}({basis_label}) 에서 해석됨 — 기초금액 키 "
                f"부재로 base 가 예정가/예산 값이 됨. 소비 시 예정가 오염 인지 필요(#220)."
            ),
        )
    ]


def validate_field_placement(
    item: Mapping[str, object],
    family: OperationFamily,
) -> list[ContractViolation]:
    """계약 필드가 정당한 오퍼레이션 계열 밖에서 값과 함께 나타났는지 검증한다.

    예: 낙찰하한율(``sucsfbidLwltRate``)이 광범위 목록/개찰 응답에 실려오면(계약상
    표적조회/서브op 전용) 계약과 어긋난 것 — 소비 전 재확인이 필요하다는 WARN.
    """
    violations: list[ContractViolation] = []
    for contract in FIELD_CONTRACTS:
        raw = item.get(contract.raw_name)
        if not _is_present(raw):
            continue
        if family in contract.present_in:
            continue
        expected = ", ".join(sorted(member.value for member in contract.present_in))
        violations.append(
            ContractViolation(
                field=contract.raw_name,
                kind=ViolationKind.UNEXPECTED_FIELD_FOR_OPERATION,
                severity=Severity.WARN,
                detail=(
                    f"{contract.raw_name} 이(가) {family.value} 응답에 값과 함께 존재 — "
                    f"계약상 실리는 계열은 [{expected}]. 소비 전 의미 재확인 필요."
                ),
            )
        )
    return violations


def unknown_fields(item: Mapping[str, object]) -> list[str]:
    """소비 코드가 아직 다루지 않는 raw 키를 정렬해 반환한다(미지 필드 리포트).

    신규 필드를 소비하기 전에 사람이 검토(계약 등록)하도록 표면화하는 게 목적이다.
    """
    return sorted(key for key in item.keys() if key not in KNOWN_FIELDS)


def validate_item(
    item: Mapping[str, object],
    *,
    operation: str | None,
) -> list[ContractViolation]:
    """한 응답 item 에 적용 가능한 모든 순수 검증기를 돌려 위반 목록을 합친다.

    ``operation`` 이름으로 계열을 판정한 뒤 범위/식별자/base-basis/배치 검증을 순차 적용한다
    (검증 파이프라인). 미지 필드는 위반이 아니라 별도 리포트(``unknown_fields``)로 다룬다.
    """
    family = operation_family(operation)
    violations: list[ContractViolation] = []
    violations.extend(validate_ranges(item))
    violations.extend(validate_identifiers(item))
    violations.extend(validate_base_basis(item, family))
    violations.extend(validate_field_placement(item, family))
    return violations


# ---------------------------------------------------------------------------
# 사람이 읽는 계약 요약 (스크립트 --dry-run 이 소비)
# ---------------------------------------------------------------------------


def describe_contracts() -> list[str]:
    """선언된 함정 필드 계약을 사람이 읽는 줄로 렌더한다(시크릿 없음, 순수)."""
    lines: list[str] = ["[field-contract] 선언된 KONEPS 함정 필드 계약:"]
    for contract in FIELD_CONTRACTS:
        families = ", ".join(sorted(m.value for m in contract.present_in))
        basis_label = contract.basis.value if contract.basis else "-"
        rng = (
            f"[{contract.expected_min}, {contract.expected_max}]"
            if contract.expected_min is not None
            else "-"
        )
        lines.append(
            f"  - {contract.raw_name}: {contract.concept} "
            f"(scale={contract.scale.value}, basis={basis_label}, "
            f"zero_padded={contract.zero_padded}, range={rng}, "
            f"present_in=[{families}])"
        )
        lines.append(f"      실측 출처: {contract.provenance}")
    lines.append(
        "  - base_amount(키-집합 트랩): 해석 순서 "
        f"{list(BASE_RESOLUTION_ORDER)} — 예정가/예산 키가 기초금액 키보다 앞서면 "
        "base==예정가 오염(#220). 기초금액 키="
        f"{list(_TRUE_BASE_KEYS)}."
    )
    return lines
