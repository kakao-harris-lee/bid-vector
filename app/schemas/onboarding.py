"""반자동 온보딩 후보 조회 입출력 스키마.

``GET /api/v1/operator/onboarding-suggestions`` 의 Pydantic 경계. 내부 공고에서
역추천한 회사 프로필/감시 전략 필드 후보를 반환한다. 후보는 **추정**이며 확정이
아니므로(설계 §2, 정직 명세 §2) 모든 후보가 ``needs_confirmation=True`` 이고,
이 응답만으로 ``CompanyProfile``/``OperatorStrategy`` 를 바꾸지 않는다.

필드값은 대상 컬럼 종류에 따라 문자열(business_type), 문자열 리스트(license_codes/
region_codes/focus_categories/focus_regions), 실수(min/max_budget_estimate)로
달라져 ``value`` 를 Union 으로 둔다.
"""

from __future__ import annotations

import enum
from typing import List, Union

from pydantic import BaseModel, ConfigDict, Field

from app.services.onboarding.suggestions import (
    FIELD_BUSINESS_TYPE,
    FIELD_FOCUS_CATEGORIES,
    FIELD_FOCUS_REGIONS,
    FIELD_LICENSE_CODES,
    FIELD_MAX_BUDGET,
    FIELD_MIN_BUDGET,
    FIELD_REGION_CODES,
)


class OnboardingFieldSuggestion(BaseModel):
    """단일 필드 후보 + provenance(설계 §2: source/confidence/needs_confirmation/reason)."""

    model_config = ConfigDict(from_attributes=True)

    field: str = Field(description="후보가 채우는 대상 필드명(CompanyProfile/OperatorStrategy 컬럼)")
    value: Union[str, float, List[str]] = Field(description="추천 후보값(필드 종류별 형태)")
    source: str = Field(description="후보 출처(현재 슬라이스는 internal_notices 단일)")
    confidence: float = Field(ge=0.0, le=1.0, description="후보 신뢰도(0~1, 확정 아님)")
    needs_confirmation: bool = Field(description="사용자 확정 필요 여부 — 항상 True")
    reason: str = Field(description="도출 근거(몇 건 공고에서 나왔는지 등, 한국어)")
    matched_notice_count: int = Field(ge=0, description="이 후보를 지지한 공고 수")


class OnboardingSuggestionsResponse(BaseModel):
    """프로필/전략 후보 묶음 + 매칭 요약(후보 없음 원인 진단 포함)."""

    keywords: List[str] = Field(default_factory=list, description="정규화된 조회 키워드")
    matched_notice_count: int = Field(ge=0, description="seed 에 매칭된 내부 공고 수")
    diagnostics: str = Field(description="후보 유무/원인 요약(설계 §4)")
    profile: List[OnboardingFieldSuggestion] = Field(
        default_factory=list, description="CompanyProfile 후보 묶음"
    )
    strategy: List[OnboardingFieldSuggestion] = Field(
        default_factory=list, description="OperatorStrategy 후보 묶음"
    )
    current_operator_id: int = Field(description="응답이 스코프된 운영자 id(라우터 컨벤션)")
    current_operator_username: str = Field(description="응답이 스코프된 운영자 username")


class OnboardingApplyField(str, enum.Enum):
    """apply 가 받는 확정 필드 집합. 값은 GET 후보(``FIELD_*``)와 동일 단일 출처를
    재사용해(§4.6) 두 엔드포인트가 정확히 같은 필드명을 쓰도록 고정한다. Pydantic 이
    미지원 필드명을 422 로 거른다(설계 §3 확정 필드).
    """

    BUSINESS_TYPE = FIELD_BUSINESS_TYPE
    LICENSE_CODES = FIELD_LICENSE_CODES
    REGION_CODES = FIELD_REGION_CODES
    FOCUS_CATEGORIES = FIELD_FOCUS_CATEGORIES
    FOCUS_REGIONS = FIELD_FOCUS_REGIONS
    MIN_BUDGET_ESTIMATE = FIELD_MIN_BUDGET
    MAX_BUDGET_ESTIMATE = FIELD_MAX_BUDGET


class OnboardingApplyDecision(BaseModel):
    """사용자가 검토·확정한 단일 필드 결정. 서버는 이 값을 **그대로 신뢰**하되
    타입/화이트리스트만 검증한다(설계 §3, 정직 명세 §2 — 서버가 후보를 재계산하지 않음).
    """

    field: OnboardingApplyField = Field(description="반영할 확정 필드명(GET 후보와 동일 집합)")
    value: Union[str, float, List[str]] = Field(
        description="확정값(필드 종류별 형태: 문자열/숫자/문자열 리스트)"
    )


class OnboardingApplyRequest(BaseModel):
    """확정된 필드 결정 리스트. 명시적으로 넘어온(=accepted) 필드만 반영한다."""

    decisions: List[OnboardingApplyDecision] = Field(
        default_factory=list, description="반영할 확정 필드 결정 목록(빈 목록은 no-op)"
    )


class OnboardingAppliedField(BaseModel):
    """반영된 확정 필드 요약(어떤 필드가 어떤 값으로 갱신됐는지, 설계 §3)."""

    field: str = Field(description="반영된 필드명")
    target: str = Field(description="반영 대상(profile=CompanyProfile / strategy=OperatorStrategy)")
    value: Union[str, float, List[str]] = Field(description="저장된 정규화 값")


class OnboardingIgnoredField(BaseModel):
    """반영되지 않은(무시된) 필드 + 사유(예: 중복 필드)."""

    field: str = Field(description="무시된 필드명")
    reason: str = Field(description="무시 사유(한국어)")


class OnboardingApplyResponse(BaseModel):
    """apply 결과 — 반영/무시 요약 + operator envelope(기존 operator 응답 컨벤션)."""

    applied: List[OnboardingAppliedField] = Field(
        default_factory=list, description="반영된 확정 필드 목록"
    )
    ignored: List[OnboardingIgnoredField] = Field(
        default_factory=list, description="무시된 필드 + 사유"
    )
    current_operator_id: int = Field(description="반영이 스코프된 운영자 id")
    current_operator_username: str = Field(description="반영이 스코프된 운영자 username")
