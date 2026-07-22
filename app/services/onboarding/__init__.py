"""반자동 온보딩 지원 서비스 패키지.

첫 슬라이스는 내부 공고(``Project``) 데이터에서 회사 프로필/감시 전략 필드를
역추천하는 **읽기 전용** 후보 생성기다(설계
``docs/superpowers/specs/2026-07-03-business-number-guided-onboarding-design.md``
§2 "KONEPS/내부 공고 후보"). persistence·외부 호출·국세청 조회는 후속 PR로 분리한다.
"""

from app.services.onboarding.suggestions import (
    FieldSuggestion,
    NoticeFeatures,
    OnboardingSeed,
    SuggestionBundle,
    aggregate_suggestions,
    load_notice_features,
    suggest_onboarding_fields,
)

__all__ = [
    "FieldSuggestion",
    "NoticeFeatures",
    "OnboardingSeed",
    "SuggestionBundle",
    "aggregate_suggestions",
    "load_notice_features",
    "suggest_onboarding_fields",
]
