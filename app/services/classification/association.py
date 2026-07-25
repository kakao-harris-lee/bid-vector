"""협회 가입(cohort) 자격 축 — 그룹 인지(``assess_tech_field`` 미러 구조).

공고가 참가자격에 협회 가입을 명시하면(예: 한국엔지니어링협회) 미가입 operator 를
1차 게이트에서 거른다. 첫 실수요 고객(해양엔지니어링협회)에 직접 유효한 축이다.

요건이 명시되지 않은 대다수 공고는 **완전 중립**(score 0.0, passed=True, penalty 0)
이라 축을 추가해도 기존 baseline(score/matched/blocking_axes)이 흔들리지 않는다
(license-axis 커버리지 비대칭 교훈 — unknown/요건없음은 과차단하지 않는다).

**그룹 의미론(면허 게이트·기술부문 축과 정렬)**: 면허요건은 ``lmtGrpNo`` 로 그룹이
나뉘고 **그룹 간 = 대안(OR)**·**그룹 내 = 모두 필요(AND)** 로 해석된다
(:func:`app.services.license_eligibility.assess_license_eligibility` 와 동일한 단일
출처 :func:`parse_license_limit_groups` 로 그룹핑). 이 축은 그 그룹 경계를 존중해,
어느 **한 그룹**의 요구 협회를 전부 보유하면 통과한다(그룹 간 OR). 과거에는 모든
행을 lmtGrpNo 무시하고 평면 합산 후 전체 AND 로 대조해, 협회 요건이 여러 그룹으로
나뉜 공고에서 면허 게이트가 eligible 로 통과시킨 operator 를 협회 축이 과차단하는
두 축 모순이 잠재했다(#254 가 tech_field 에서 겪은 평면-AND 과차단과 **동형**).
지금은 협회 요건이 단일 용어라 드러나지 않았을 뿐이라, 두 축이 같은 OR/AND 로
정렬되도록 선제 이관한다. 이제 세 소비자(면허 게이트·기술부문·협회)가 같은
그룹-OR 커널을 공유한다.

**요건 소스도 면허 게이트와 정렬**된다: 그룹핑·요건 판정을 게이트와 동일하게
``lmtGrpNo`` 그룹의 ``lcnsLmtNm`` 만 본다. 과거 평면 경로(``required_association_
memberships`` → ``extract_eligibility_labels``)는 ``permsnIndstrytyList``(허용 업종
목록)까지 요건 소스로 봤으나, 면허 게이트는 그 필드를 자격 판정에 쓰지 않고
(``parse_license_limit_groups`` 가 ``lcnsLmtNm`` 만 읽음), 협회명은 허용 **업종**
목록이 아니라 참가자격 면허제한 텍스트에 나타나므로 이를 제외해 세 축의 요건 근거를
일치시킨다. 라이브 실측(2026-07-25, license_limits 보유 3,249 공고)에서
``ASSOCIATION_MEMBERSHIP_CANONICALS``(={엔지니어링협회})가 ``permsnIndstrytyList``
에 등장한 행은 **0건**(``lcnsLmtNm`` 에서도 0건)이라, lcnsLmtNm-only 로 정렬해도
실 데이터에서 놓치는 협회 요건이 없다. 두 변화(그룹 OR·소스 정렬) 모두 **완화
방향**(과차단 해소)이라 새로 차단되는 공고는 없다.

요건 추출·매칭은 순수 룰 해석기를 재사용한다(복붙 금지, §4.6). 요건은
``license_limits`` 소스만 보고 title 매칭은 기관명/과업명 오탐 축이라 제외한다(#207
교훈). 요건 측과 프로필 측이 모두 ``match_association_terms`` 의 **동일한 canonical
어휘**로 비교되므로 표면 변형("한국엔지니어링협회"·"엔지니어링 협회")에 의한 과차단이
없다.
"""

from __future__ import annotations

from app.core.single_user import split_multi_value_text
from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.assessment import RuleAssessment
from app.services.classification.group_or import (
    GroupOrVerdict,
    UnconstrainedGroupPolicy,
    evaluate_group_or,
)
from app.services.eligibility_labeling import match_association_terms
from app.services.license_eligibility import parse_license_limit_groups


def _memberships_by_group(eligibility_raw: dict | None) -> list[frozenset[str]]:
    """공고 면허요건을 ``lmtGrpNo`` 그룹별 협회 가입 canonical 집합으로 매핑한다(순수).

    면허 게이트·기술부문 축과 **동일한 그룹핑 단일 출처**(``parse_license_limit_groups``)
    로 행을 lmtGrpNo 그룹으로 묶고(그룹 간 OR·그룹 내 AND), 각 그룹의 요구 면허명
    (``.names()``)을 ``match_association_terms`` 로 협회 가입 canonical 집합에 union
    한다. ``match_association_terms`` 는 협회 **가입**(FLAG_ASSOCIATION) canonical 만
    돌려주므로(엔지니어링사업자/활동주체 = 사업 신고는 제외) 결과는
    ``ASSOCIATION_MEMBERSHIP_CANONICALS`` 부분집합이다. 그룹 등장 순서를 유지하며,
    협회를 요구하지 않는 그룹(면허만)은 빈 frozenset 으로 남긴다(호출부가 "무제약
    자격 경로"로 인지해 과차단하지 않도록). IO/DB 접근 없음.
    """
    groups: list[frozenset[str]] = []
    for group in parse_license_limit_groups(eligibility_raw):
        memberships: set[str] = set()
        for name in group.names():
            memberships |= match_association_terms(name)
        groups.append(frozenset(memberships))
    return groups


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


def _format_groups(groups: list[frozenset[str]]) -> str:
    """협회 요건 그룹 목록을 사람이 읽는 ``[a, b] / [c]`` 형태로 만든다(reason 용)."""
    return " / ".join(f"[{', '.join(sorted(group))}]" for group in groups)


def assess_association(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """공고가 명시한 협회 가입 요건을 프로필 가입 현황과 대조한다(그룹 인지, ``assess_tech_field`` 미러).

    면허 게이트·기술부문 축과 **동일한 그룹-OR 커널**(:func:`app.services.
    classification.group_or.evaluate_group_or`, DEFER 정책)로 lmtGrpNo 그룹 의미론
    (그룹 간 OR·그룹 내 AND)을 평가한다. 판정 fold 를 세 축이 공유해 서로 어긋나지
    않도록 한다(#254 동형 회귀 선제 차단).

    - 협회 요구 그룹 없음(대다수 공고) → 중립 PASS, score
      ``ASSOCIATION_NEUTRAL_SCORE`` (=0.0). penalty·blocking 을 만들지 않는다.
    - 어느 한 그룹의 요구 협회를 전부 보유 → ``ASSOCIATION_MATCH_SCORE`` + PASS.
    - 협회 무제약 그룹(다른 자격 경로) 존재 → 중립 PASS. 협회 축은 그 그룹의 (비-협회)
      면허를 검증할 수 없으므로 defer 한다(과차단 금지, license-axis 교훈).
    - 위 어느 경로도 아니면(모든 요구 그룹 미충족) → ``passed=False`` +
      ``ASSOCIATION_MISMATCH_PENALTY``.

    단일 그룹 공고는 "그룹 내 AND"가 기존 평면 AND 와 같아 동작이 완전 불변이다.
    """
    groups = _memberships_by_group(project.eligibility_raw)
    held = _held_association_memberships(profile)
    outcome = evaluate_group_or(
        groups, held, unconstrained_policy=UnconstrainedGroupPolicy.DEFER
    )

    if outcome.verdict is GroupOrVerdict.SATISFIED:
        matched = sorted(
            {
                term
                for group, is_satisfied in zip(groups, outcome.satisfied)
                if is_satisfied
                for term in group
            }
        )
        return RuleAssessment(
            score=config.ASSOCIATION_MATCH_SCORE,
            passed=True,
            reasons=[
                f"공고가 요구하는 협회 가입을 확인했습니다: {', '.join(matched)}."
            ],
        )

    if outcome.verdict is GroupOrVerdict.UNSATISFIED:
        constrained = [
            group
            for group, is_constrained in zip(groups, outcome.constrained)
            if is_constrained
        ]
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=config.ASSOCIATION_MISMATCH_PENALTY,
            reasons=[
                "공고가 요구하는 협회 가입을 어느 그룹으로도 충족하지 못했습니다"
                f"(그룹별 요구, 하나만 전부 보유하면 됨: {_format_groups(constrained)})."
            ],
        )

    if outcome.verdict is GroupOrVerdict.DEFER:
        # 협회를 요구하지 않는 그룹(무제약 = 다른 자격 경로)이 하나라도 있으면, 그
        # 경로가 성립할 수 있으므로 과차단하지 않고 중립으로 defer 한다.
        return RuleAssessment(
            score=config.ASSOCIATION_NEUTRAL_SCORE,
            passed=True,
            reasons=[
                "협회 가입을 요구하지 않는 자격 경로(다른 그룹)가 있어 과차단하지 않고 "
                "협회 조건은 중립 처리했습니다."
            ],
        )

    # GroupOrVerdict.NO_CONSTRAINT — 제약 그룹이 하나도 없다(협회 가입 요건 없음).
    return RuleAssessment(
        score=config.ASSOCIATION_NEUTRAL_SCORE,
        passed=True,
        reasons=["공고에 협회 가입 요건이 명시되지 않아 협회 조건은 중립 처리했습니다."],
    )
