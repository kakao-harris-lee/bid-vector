"""기술부문(cohort) 자격 축 — ``assess_association`` 미러 구조.

공고가 참가자격에 특정 기술부문(항만/해양/수로 = TECH_FIELD_TERMS, license_limits
원문 앵커)을 명시하면 해당 기술부문을 보유하지 않은 operator 를 1차 게이트에서
거른다. #234 가 캡처만 하던 ``CompanyProfile.tech_fields`` 를 classifier 추천 축으로
소비하는 것으로, 첫 실수요 고객(해양엔지니어링)에 직접 유효하다.

요건이 명시되지 않은 대다수 공고는 **완전 중립**(score 0.0, passed=True, penalty 0)
이라 축을 추가해도 기존 baseline(score/matched/blocking_axes)이 흔들리지 않는다
(license-axis 커버리지 비대칭 교훈 — unknown/요건없음은 과차단하지 않는다).

요건 추출·매칭은 ``eligibility_labeling`` 의 순수 룰 해석기를 재사용한다(복붙 금지,
§4.6). 요건은 ``license_limits`` 소스만 보고 title 매칭은 기관명/과업명 오탐 축이라
제외한다(#207 교훈). 요건 측(``required_tech_fields``)과 프로필 측
(``match_tech_field_terms``)이 **동일한 표준명 어휘**로 비교되므로 code↔name 혼선
(#231)에 의한 과차단이 없다.
"""

from __future__ import annotations

from app.core.single_user import split_multi_value_text
from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.assessment import RuleAssessment
from app.services.eligibility_labeling import (
    match_tech_field_terms,
    required_tech_fields,
)


def _held_tech_fields(profile: CompanyProfile) -> set[str]:
    """프로필의 기술부문 다중값 텍스트를 canonical 표준명 집합으로 정규화한다(순수).

    ``split_multi_value_text`` 로 토큰화한 뒤 각 토큰을 ``match_tech_field_terms``
    (eligibility_labeling 매칭 규칙 재사용)로 canonical 표준명에 매핑한다. 온보딩이
    저장한 표준명("항만및해안")과 표면 변형("엔지니어링사업(항만"·"수로측량")이 요건
    canonical 과 같은 어휘로 비교되도록 보장한다.
    """
    held: set[str] = set()
    for token in split_multi_value_text(profile.tech_fields):
        held |= match_tech_field_terms(token)
    return held


def assess_tech_field(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """공고가 명시한 기술부문 요건을 프로필 보유 현황과 대조한다(``assess_association`` 미러).

    - 요건 없음(대다수 공고) → 중립 PASS, score ``TECH_FIELD_NEUTRAL_SCORE`` (=0.0).
      baseline 불변을 위해 penalty·blocking 을 만들지 않는다.
    - 요건 있음 + 미보유 → ``passed=False`` + ``TECH_FIELD_MISMATCH_PENALTY``.
    - 요건 있음 + 보유 확인 → ``TECH_FIELD_MATCH_SCORE`` + PASS.
    """
    required = required_tech_fields(project.eligibility_raw)

    if not required:
        return RuleAssessment(
            score=config.TECH_FIELD_NEUTRAL_SCORE,
            passed=True,
            reasons=[
                "공고에 기술부문 요건이 명시되지 않아 기술부문 조건은 중립 처리했습니다."
            ],
        )

    held = _held_tech_fields(profile)
    missing = required - held
    if not missing:
        return RuleAssessment(
            score=config.TECH_FIELD_MATCH_SCORE,
            passed=True,
            reasons=[
                f"공고가 요구하는 기술부문을 확인했습니다: {', '.join(sorted(required))}."
            ],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.TECH_FIELD_MISMATCH_PENALTY,
        reasons=[
            f"공고가 요구하는 기술부문({', '.join(sorted(missing))})이 프로필에 없습니다."
        ],
    )
