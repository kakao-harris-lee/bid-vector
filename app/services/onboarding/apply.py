"""사용자가 확정한 온보딩 후보를 CompanyProfile/OperatorStrategy 에 부분 반영한다.

설계 근거: ``docs/superpowers/specs/
2026-07-03-business-number-guided-onboarding-design.md`` §3 "사용자 확정 단계"
("확정된 값만 기존 API에 반영") 및 §"API 방향"의 apply 엔드포인트. GET
``onboarding-suggestions`` 후보(:mod:`app.services.onboarding.suggestions`)와
달리 이 모듈은 **쓰기 경계**다 — 클라이언트가 명시적으로 확정해 보낸 값만 반영한다.

정직 명세(§2)·설계 §2 규칙:
- 서버는 후보를 다시 계산하거나 신뢰하지 않는다. 클라이언트가 확정한 값이 진실이고,
  서버는 **타입/화이트리스트 검증**만 한다(§4.5.1 선언 화이트리스트). 넘어온(=accepted)
  필드만 반영하고 나머지 프로필/전략 필드는 불변이다(부분 업데이트).
- draft/pending 후보를 프로필에 쓰지 않는다 — 이 경로는 확정 결정만 받는다.

설계 규율:
- **선언적 dispatch**(§4.5.2): field → target(CompanyProfile/OperatorStrategy) 라우팅과
  값 종류별 정규화는 중첩 if-else 가 아니라 :data:`APPLYABLE_FIELDS` 룩업 + kind→coercer
  디스패치로 표현한다. 새 확정 필드는 코드 분기가 아니라 스펙 한 줄로 확장한다.
- **매직값 금지**(§4.5.1): 필드명은 :mod:`app.services.onboarding.suggestions` 의
  ``FIELD_*`` 단일 출처를 재사용한다(GET 후보와 정확히 같은 집합). business_type
  화이트리스트는 ``classification.taxonomy`` 의 canonical 집합을 재사용한다.
- **write 경로 재사용**(§4.6): 대상 행 확보와 다중값 텍스트 정규화는 기존 PUT
  ``/operator/profile`` · PUT ``/operator/strategy`` 가 쓰는 헬퍼
  (``ensure_operator_profile_for``/``ensure_operator_strategy_for``/
  ``join_multi_value_text``)를 그대로 재사용한다(복붙 금지). operator 스코프 해석은
  라우터의 ``resolve_write_operator``(self-only, 403) 가 담당한다 — 이 모듈은 이미
  스코프가 해석된 operator 의 자기 행에만 쓴다.
- **I/O 와 계산 분리**(§4.7.4): 값 검증/정규화(:func:`_coerce_*`)는 순수 함수이고
  DB write 는 :func:`apply_onboarding_decisions` 경계에서만 일어난다.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable, Union

from sqlalchemy.orm import Session

from app.core.single_user import (
    ensure_operator_profile_for,
    ensure_operator_strategy_for,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import User
from app.services.classification import taxonomy
from app.services.classification.text import normalize_business_type
from app.services.onboarding.suggestions import (
    FIELD_BUSINESS_TYPE,
    FIELD_FOCUS_CATEGORIES,
    FIELD_FOCUS_REGIONS,
    FIELD_LICENSE_CODES,
    FIELD_MAX_BUDGET,
    FIELD_MIN_BUDGET,
    FIELD_REGION_CODES,
)

# 확정 가능한 값 형태. GET ``OnboardingFieldSuggestion.value`` 와 동일 union 을 재사용해
# 요청/응답 계약을 대칭으로 유지한다(§4.6).
ApplyValue = Union[str, float, list[str]]

# business_type 허용 집합(선언 화이트리스트, §4.5.1/§4.5.3). classifier taxonomy 의
# canonical 업무구분 키를 단일 출처로 재사용한다. GET 후보는 항상
# ``normalize_business_type`` 로 canonical 값을 내므로 확정값도 이 집합 안에 든다.
VALID_BUSINESS_TYPES: frozenset[str] = frozenset(taxonomy.BUSINESS_TYPE_ALIASES)


class FieldTarget(str, enum.Enum):
    """확정 필드가 반영되는 대상 ORM 행."""

    PROFILE = "profile"  # CompanyProfile
    STRATEGY = "strategy"  # OperatorStrategy


class FieldKind(str, enum.Enum):
    """확정값의 종류(검증/정규화 방식 선택 키, §4.5.2)."""

    BUSINESS_TYPE = "business_type"  # 단일 문자열 + canonical 화이트리스트
    STRING_LIST = "string_list"  # 문자열 리스트 → 다중값 텍스트 저장
    NUMBER = "number"  # 0 이상 실수


@dataclass(frozen=True)
class ApplyFieldSpec:
    """확정 필드 하나의 라우팅/정규화 스펙(선언 데이터, §4.5.2)."""

    field: str
    target: FieldTarget
    attribute: str  # 대상 모델의 컬럼명
    kind: FieldKind


# field → 스펙 단일 룩업(§4.5.2). 키는 suggestions.FIELD_* 단일 출처를 재사용해 GET
# 후보와 정확히 같은 집합으로 고정한다. 선언 순서가 응답 ``applied`` 순서를 결정한다.
APPLYABLE_FIELDS: dict[str, ApplyFieldSpec] = {
    FIELD_BUSINESS_TYPE: ApplyFieldSpec(
        FIELD_BUSINESS_TYPE, FieldTarget.PROFILE, "business_type", FieldKind.BUSINESS_TYPE
    ),
    FIELD_LICENSE_CODES: ApplyFieldSpec(
        FIELD_LICENSE_CODES, FieldTarget.PROFILE, "license_codes", FieldKind.STRING_LIST
    ),
    FIELD_REGION_CODES: ApplyFieldSpec(
        FIELD_REGION_CODES, FieldTarget.PROFILE, "region_codes", FieldKind.STRING_LIST
    ),
    FIELD_FOCUS_CATEGORIES: ApplyFieldSpec(
        FIELD_FOCUS_CATEGORIES,
        FieldTarget.STRATEGY,
        "focus_categories",
        FieldKind.STRING_LIST,
    ),
    FIELD_FOCUS_REGIONS: ApplyFieldSpec(
        FIELD_FOCUS_REGIONS, FieldTarget.STRATEGY, "focus_regions", FieldKind.STRING_LIST
    ),
    FIELD_MIN_BUDGET: ApplyFieldSpec(
        FIELD_MIN_BUDGET, FieldTarget.STRATEGY, "min_budget_estimate", FieldKind.NUMBER
    ),
    FIELD_MAX_BUDGET: ApplyFieldSpec(
        FIELD_MAX_BUDGET, FieldTarget.STRATEGY, "max_budget_estimate", FieldKind.NUMBER
    ),
}


class OnboardingApplyError(ValueError):
    """확정값 검증 실패(알 수 없는 필드/타입/화이트리스트). 라우터가 422 로 매핑한다."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


@dataclass(frozen=True)
class ApplyDecision:
    """서비스 입력 DTO — 스키마(Pydantic)와 분리해 값 테이블 테스트를 쉽게 한다.

    ``field`` 는 :data:`APPLYABLE_FIELDS` 의 키 문자열이다(라우터가 enum → str 로 언팩).
    """

    field: str
    value: ApplyValue


@dataclass(frozen=True)
class CoercedValue:
    """검증/정규화된 값 — ORM 저장 형태와 응답 표시 형태를 분리해 담는다."""

    orm_value: object  # 대상 컬럼에 setattr 되는 값(str/float)
    display_value: ApplyValue  # 응답에 노출되는 정규화 값(str/list/float)


@dataclass(frozen=True)
class AppliedField:
    """반영된 확정 필드 요약(어떤 필드가 어떤 값으로 갱신됐는지, 설계 §3)."""

    field: str
    target: str
    value: ApplyValue

    def to_dict(self) -> dict:
        return {"field": self.field, "target": self.target, "value": self.value}


@dataclass(frozen=True)
class IgnoredField:
    """반영되지 않은(무시된) 필드 + 사유(설계 §3 응답 요구)."""

    field: str
    reason: str

    def to_dict(self) -> dict:
        return {"field": self.field, "reason": self.reason}


@dataclass(frozen=True)
class ApplyResult:
    """apply 결과 묶음 — 반영/무시 목록."""

    applied: list[AppliedField]
    ignored: list[IgnoredField]


# --- 순수 검증/정규화 (kind → coercer 디스패치, §4.5.2) ------------------------


def _coerce_business_type(spec: ApplyFieldSpec, value: ApplyValue) -> CoercedValue:
    """business_type 확정값: 문자열 + canonical 화이트리스트 검증(§4.5.3)."""
    if not isinstance(value, str):
        raise OnboardingApplyError(spec.field, "문자열 값이 필요합니다.")
    normalized = normalize_business_type(value)
    if normalized is None or normalized not in VALID_BUSINESS_TYPES:
        raise OnboardingApplyError(spec.field, "허용되지 않은 업무구분 값입니다.")
    return CoercedValue(orm_value=normalized, display_value=normalized)


def _coerce_string_list(spec: ApplyFieldSpec, value: ApplyValue) -> CoercedValue:
    """면허/지역/카테고리 확정값: 문자열 리스트 → 다중값 텍스트로 정규화(write 경로 재사용)."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OnboardingApplyError(spec.field, "문자열 리스트 값이 필요합니다.")
    stored = join_multi_value_text(list(value))
    return CoercedValue(orm_value=stored, display_value=split_multi_value_text(stored))


def _coerce_number(spec: ApplyFieldSpec, value: ApplyValue) -> CoercedValue:
    """예산(min/max) 확정값: 0 이상 실수 검증. bool 은 숫자로 취급하지 않는다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OnboardingApplyError(spec.field, "숫자 값이 필요합니다.")
    number = float(value)
    if number < 0:
        raise OnboardingApplyError(spec.field, "0 이상의 숫자가 필요합니다.")
    return CoercedValue(orm_value=number, display_value=number)


_COERCERS: dict[FieldKind, Callable[[ApplyFieldSpec, ApplyValue], CoercedValue]] = {
    FieldKind.BUSINESS_TYPE: _coerce_business_type,
    FieldKind.STRING_LIST: _coerce_string_list,
    FieldKind.NUMBER: _coerce_number,
}


def _coerce(spec: ApplyFieldSpec, value: ApplyValue) -> CoercedValue:
    """스펙 kind 에 맞는 coercer 로 확정값을 검증/정규화한다(순수)."""
    return _COERCERS[spec.kind](spec, value)


# --- write 경계 (검증은 순수, 반영만 IO) -------------------------------------


def apply_onboarding_decisions(
    db: Session,
    *,
    operator: User,
    decisions: Sequence[ApplyDecision],
) -> ApplyResult:
    """확정된 온보딩 필드만 operator 자신의 프로필/전략에 부분 반영한다.

    ``operator`` 는 라우터의 ``resolve_write_operator`` 로 이미 self-only 스코프가
    해석된 계정이다 — 이 함수는 그 operator 의 자기 ``CompanyProfile``/
    ``OperatorStrategy`` 행에만 쓴다(canonical/synthetic 격리는 스코프 해석에서 보장).

    처리 순서:
    1. 모든 결정을 검증/정규화한다. 알 수 없는 필드/타입/화이트리스트 위반은
       :class:`OnboardingApplyError` (→422) 로 전체 요청을 거부한다.
    2. 같은 필드가 여러 번 오면 **마지막 값**을 적용하고 앞선 중복은 무시로 기록한다.
    3. 대상 행은 필요할 때만(lazy) 확보하고, 넘어온 필드만 갱신한 뒤 한 번 commit 한다.
       반영할 필드가 없으면(빈 요청) no-op 로 아무 것도 쓰지 않는다.
    """
    # 1. 검증/정규화 — 위치와 무관하게 잘못된 값이 하나라도 있으면 422.
    coerced: list[tuple[ApplyFieldSpec, CoercedValue]] = []
    for decision in decisions:
        spec = APPLYABLE_FIELDS.get(decision.field)
        if spec is None:
            raise OnboardingApplyError(decision.field, "알 수 없는 필드입니다.")
        coerced.append((spec, _coerce(spec, decision.value)))

    # 2. 중복 필드 dedup(last-wins) — 앞선 중복은 무시로 기록.
    final: dict[str, tuple[ApplyFieldSpec, CoercedValue]] = {}
    ignored: list[IgnoredField] = []
    for spec, value in coerced:
        if spec.field in final:
            ignored.append(
                IgnoredField(spec.field, "중복 필드: 마지막 값이 적용됩니다.")
            )
        final[spec.field] = (spec, value)

    # 3. 대상 행 lazy 확보 후 반영 — 선언 순서(APPLYABLE_FIELDS)로 결정적 출력.
    profile = None
    strategy = None
    applied: list[AppliedField] = []
    for field_name in APPLYABLE_FIELDS:
        if field_name not in final:
            continue
        spec, value = final[field_name]
        if spec.target is FieldTarget.PROFILE:
            if profile is None:
                profile = ensure_operator_profile_for(db, operator)
            setattr(profile, spec.attribute, value.orm_value)
        else:
            if strategy is None:
                strategy = ensure_operator_strategy_for(db, operator)
            setattr(strategy, spec.attribute, value.orm_value)
        applied.append(
            AppliedField(spec.field, spec.target.value, value.display_value)
        )

    if applied:
        db.commit()

    return ApplyResult(applied=applied, ignored=ignored)


__all__ = [
    "ApplyValue",
    "VALID_BUSINESS_TYPES",
    "FieldTarget",
    "FieldKind",
    "ApplyFieldSpec",
    "APPLYABLE_FIELDS",
    "OnboardingApplyError",
    "ApplyDecision",
    "AppliedField",
    "IgnoredField",
    "ApplyResult",
    "apply_onboarding_decisions",
]
