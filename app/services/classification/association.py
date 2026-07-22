"""협회 가입(cohort) 자격 축 — ``assess_license`` 미러 구조.

공고가 참가자격에 협회 가입을 명시하면(예: 한국엔지니어링협회) 미가입 operator 를
1차 게이트에서 거른다. 첫 실수요 고객(해양엔지니어링협회)에 직접 유효한 축이다.

요건이 명시되지 않은 대다수 공고는 **완전 중립**(score 0.0, passed=True, penalty 0)
이라 축을 추가해도 기존 baseline(score/matched/blocking_axes)이 흔들리지 않는다
(license-axis 커버리지 비대칭 교훈 — unknown/요건없음은 과차단하지 않는다).

요건 추출·매칭은 ``eligibility_labeling`` 의 순수 룰 해석기를 재사용한다(복붙 금지,
§4.6). 요건은 ``license_limits`` 소스만 보고 title 매칭은 기관명/과업명 오탐 축이라
제외한다(#207 교훈).
"""

from __future__ import annotations

from app.core.single_user import split_multi_value_text
from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.assessment import RuleAssessment
from app.services.eligibility_labeling import (
    match_association_terms,
    required_association_memberships,
)


def _held_association_memberships(profile: CompanyProfile) -> set[str]:
    """프로필의 협회 가입 다중값 텍스트를 canonical 협회 집합으로 정규화한다(순수).

    ``split_multi_value_text`` 로 토큰화한 뒤 각 토큰을 ``match_association_terms``
    (eligibility_labeling 매칭 규칙 재사용)로 canonical 용어에 매핑한다. 표면 변형
    ("한국엔지니어링협회"·"엔지니어링 협회")이 요건 canonical("엔지니어링협회")과 같은
    어휘로 비교되도록 보장한다.
    """
    held: set[str] = set()
    for token in split_multi_value_text(profile.association_memberships):
        held |= match_association_terms(token)
    return held


def assess_association(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """공고가 명시한 협회 가입 요건을 프로필 가입 현황과 대조한다(``assess_license`` 미러).

    - 요건 없음(대다수 공고) → 중립 PASS, score ``ASSOCIATION_NEUTRAL_SCORE`` (=0.0).
      baseline 불변을 위해 penalty·blocking 을 만들지 않는다.
    - 요건 있음 + 미가입 → ``passed=False`` + ``ASSOCIATION_MISMATCH_PENALTY``.
    - 요건 있음 + 가입 확인 → ``ASSOCIATION_MATCH_SCORE`` + PASS.
    """
    required = required_association_memberships(project.eligibility_raw)

    if not required:
        return RuleAssessment(
            score=config.ASSOCIATION_NEUTRAL_SCORE,
            passed=True,
            reasons=["공고에 협회 가입 요건이 명시되지 않아 협회 조건은 중립 처리했습니다."],
        )

    held = _held_association_memberships(profile)
    missing = required - held
    if not missing:
        return RuleAssessment(
            score=config.ASSOCIATION_MATCH_SCORE,
            passed=True,
            reasons=[
                f"공고가 요구하는 협회 가입을 확인했습니다: {', '.join(sorted(required))}."
            ],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.ASSOCIATION_MISMATCH_PENALTY,
        reasons=[
            f"공고가 요구하는 협회 가입({', '.join(sorted(missing))})이 프로필에 없습니다."
        ],
    )
