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

from typing import List, Union

from pydantic import BaseModel, ConfigDict, Field


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
