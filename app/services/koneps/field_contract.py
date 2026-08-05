"""KONEPS 수집 필드의 **의미 계약**을 순수 검증한다(선언은 ``field_contract_spec``).

반복 버그(#209 자격상세 목록 부재, #210 차수 int 파괴, #220 success_rate=예정가)는
전부 **신규 수집 필드를 소비 전에 실배치 검증하지 않아** 라이브에서 터졌다. 그 함정 필드의
계약(raw 이름·개념·basis·스케일·제로패딩·예상 범위·어느 오퍼레이션에 있는지)은
``field_contract_spec`` 이 **선언적 데이터**로 모으고(§4.5.3 규칙=데이터), 이 모듈은 그
데이터를 해석만 하는 순수 검증기(§4.7)를 소유한다. IO/DB 없음 — 응답 item dict를 받아
계약 위반 목록을 돌려준다. 두 모듈을 가른 이유는 파일 크기 한도(§4.5-4)이며, 선언 표면은
여기서 **전부** 다시 노출하므로(``field_contract.FIELD_CONTRACTS`` 등) 기존 호출부 참조는
그대로다. 새 코드는 선언을 소유 모듈(``field_contract_spec``)에서 직접 참조한다 — 예:
``openapi`` 는 base/추정가 해석 순서를 소유 모듈에서 읽는다.

이 모듈은 mypy strict 아일랜드다(과설계 금지: 전역 Money 래퍼가 아니라, 버그가 실제로
났던 좁은 도메인만). 무거운 의존을 끌지 않도록 순수 strict 아일랜드만 재사용한다:
``app.domain.money.Basis`` + ``app.domain.rate_normalization.to_bid_rate_fraction``
(percent↔fraction 스케일 규칙 단일 출처). ``parsing`` 모듈의 mutable 표면에는 결합하지
않는다 — 값 coercion은 얇은 로컬 순수 함수(``_as_fraction``)로 유지하고 스케일 판별만 위임한다.

온-디맨드 실배치 검증(실 KONEPS 응답 N건으로 assert)은
``scripts/verify_koneps_field_contract.py`` 가 이 순수 검증기를 소비한다. 역할 분리(방어적 DTO
Phase 3): **구조 계약**(필드 존재·타입)은 ``app/schemas/koneps_items`` 의 Pydantic DTO 가 승격
지점에서 ``ValidationError`` 로 **거부**하고, **의미 계약**(basis/스케일/제로패딩)은 이 모듈이
**경고·집계로만** 남긴다(KONEPS 의미 드리프트는 통제 불가 — 관측기는 차단기가 되지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.money import Basis
from app.domain.rate_normalization import to_bid_rate_fraction

# 선언 표면 **전체**를 여기서 다시 노출한다: ``field_contract.X`` 로 참조하던 기존 호출부
# (스크립트/테스트)가 분해 후에도 그대로 동작해야 한다. 검증기가 직접 쓰는 이름은 그 사용
# 자체로 재수출되고, 이 모듈이 쓰지 않는 두 이름(``ESTIMATED_RESOLUTION_ORDER`` /
# ``FieldContract``)은 PEP 484 의 명시적 재수출 형식(``X as X``)으로 남긴다 — 빠뜨리면
# "참조 경로는 그대로"라는 위 주장이 그 이름에서만 거짓이 된다.
from app.services.koneps.field_contract_spec import (
    BASE_RESOLUTION_ORDER,
    ESTIMATED_RESOLUTION_ORDER as ESTIMATED_RESOLUTION_ORDER,
    FIELD_CONTRACTS,
    KEY_BASIS,
    KNOWN_FIELDS,
    TRUE_BASE_KEYS,
    FieldContract as FieldContract,
    OperationFamily,
    Scale,
    Severity,
    ViolationKind,
    operation_family,
)


@dataclass(frozen=True)
class ContractViolation:
    """순수 검증기가 반환하는 단일 위반. IO 없음 — 값만 담는다.

    ``resolved_key`` 는 base-basis 트랩처럼 "어느 raw 키에서 해석됐는가"가 위반의 성격을
    가르는 경우에만 채워지는 구조적 메타데이터다(그 외 None). 소비자가 detail 문자열을
    파싱하지 않고 known-benign 여부(``is_known_benign``)를 데이터로 분류하게 한다.
    """

    field: str
    kind: ViolationKind
    severity: Severity
    detail: str
    resolved_key: str | None = None


# ---------------------------------------------------------------------------
# 알려진 양성(known-benign) 위반 시그니처 (선언 데이터 — 코드 분기 금지, §4.5.3)
# ---------------------------------------------------------------------------
# 계약 의미론은 **불변**이다: asignBdgtAmt(배정예산액)는 여전히 BUDGET_ESTIMATE basis 이고
# validate_base_basis 는 양수 기초금액 키가 없을 때 여전히 BASE_BASIS_YEGA_ONLY WARN 을 낸다.
# 다만 대다수 service 공고는 기초금액 키(bssAmt*) 없이 배정예산(asignBdgtAmt)만 싣고,
# 이 배정예산은 실무상 기초금액에 근사해 현 실투찰이 정상 작동함이 확인됐다(#228). 그래서
# 이 WARN 은 매 수집 배치마다 전 service 공고에서 발화해 요약을 도배한다. 라이브 관찰기가
# 이 시그니처를 **별도 버킷(benign_suppressed)** 으로 분리하도록 데이터로만 표시한다 —
# 재분류가 아니라 요약 집계의 노이즈 억제다. presmptPrce/presmptAmt(예정가) 에서 해석된
# 진짜 #220 오염은 이 집합에 없으므로 계속 실 위반으로 표면화된다.
KNOWN_BENIGN_SIGNATURES: frozenset[tuple[ViolationKind, str]] = frozenset(
    {
        (ViolationKind.BASE_BASIS_YEGA_ONLY, "asignBdgtAmt"),
    }
)


def is_known_benign(violation: ContractViolation) -> bool:
    """위반이 선언된 known-benign 시그니처(``(kind, resolved_key)``)에 해당하는지 판정한다.

    순수 — ``resolved_key`` 가 채워진 위반(base-basis 트랩)만 대상이고, 그 외에는 항상
    False 다. 요약 억제는 데이터(``KNOWN_BENIGN_SIGNATURES``)로만 결정되며 계약 의미는
    바꾸지 않는다.
    """
    if violation.resolved_key is None:
        return False
    return (violation.kind, violation.resolved_key) in KNOWN_BENIGN_SIGNATURES


# ---------------------------------------------------------------------------
# 값 정규화 (얇은 순수 helper — I/O 없음)
# ---------------------------------------------------------------------------


def _as_fraction(value: object) -> float | None:
    """율 값을 분수로 정규화한다(백분율이면 /100). 파싱 불가면 None.

    스케일 판별(백분율>1.5 -> /100)은 ``app.domain.rate_normalization`` 단일 출처에
    위임하고, 이 strict 아일랜드는 입력 coercion(bool 거부·콤마·`%` strip·0 이하 -> None·
    6자리 반올림)만 담당한다. ``parsing`` mutable 표면에는 결합하지 않는다.
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
    return round(to_bid_rate_fraction(numeric), 6)


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
    모사하고(0/미상 후보는 건너뛴다 — 프로덕션의 positive-only 규칙과 동일), 그 키의 basis
    가 기초금액이 아니면 위반을 낸다:

    - 예정가/예산 키가 기초금액 키보다 먼저 선택됨(기초금액 키도 존재) -> ERROR(precedence).
    - 기초금액 키 자체가 부재해 base 가 예정가/예산 값이 됨 -> WARN(인지 필요).

    해석 순서가 기초금액 키 우선으로 교정된 뒤로 첫 케이스는 **현 상수에서 발생할 수 없다**
    (기초금액 키가 양수면 그 키가 먼저 해석된다). 판정을 하드코딩하지 않고 상수에서 파생하므로
    이 분기는 순서가 예산·예정가 우선으로 되돌아가는 드리프트를 잡는 감지기로 남는다 —
    상수만 되돌려도 라이브 관찰기가 즉시 ERROR 로 표면화한다.
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

    resolved_basis = KEY_BASIS.get(resolved_key)
    if resolved_basis is Basis.BASE_AMOUNT:
        return []  # 기초금액 키에서 해석됨 — 정상

    has_true_base = any(
        (_as_amount(item.get(key)) or 0.0) > 0 for key in TRUE_BASE_KEYS
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
                    f"기초금액 키({', '.join(TRUE_BASE_KEYS)}) 가 있는데도 예정가/예산이 "
                    f"우선 선택됨(#220 base==예정가 오염 precedence)."
                ),
                resolved_key=resolved_key,
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
            resolved_key=resolved_key,
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
        f"{list(BASE_RESOLUTION_ORDER)} — 기초금액 키{list(TRUE_BASE_KEYS)} 가 앞서고, "
        "그중 어느 것도 양수로 실리지 않았을 때만 예정가/예산으로 폴백한다"
        "(그 경우 base 는 기초금액이 아님). "
        "예정가/예산 키가 앞으로 되돌아가면 base==예정가 오염(#220)이라 ERROR 로 잡는다."
    )
    return lines
