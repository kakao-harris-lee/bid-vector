"""기술부문(cohort) 자격 축 — ``assess_association`` 미러 구조.

공고가 참가자격에 특정 기술부문(항만/해양/수로 = TECH_FIELD_TERMS, license_limits
원문 앵커)을 명시하면 해당 기술부문을 보유하지 않은 operator 를 1차 게이트에서
거른다. #234 가 캡처만 하던 ``CompanyProfile.tech_fields`` 를 classifier 추천 축으로
소비하는 것으로, 첫 실수요 고객(해양엔지니어링)에 직접 유효하다.

요건이 명시되지 않은 대다수 공고는 **완전 중립**(score 0.0, passed=True, penalty 0)
이라 축을 추가해도 기존 baseline(score/matched/blocking_axes)이 흔들리지 않는다
(license-axis 커버리지 비대칭 교훈 — unknown/요건없음은 과차단하지 않는다).

**그룹 의미론(면허 게이트와 정렬)**: 면허요건은 ``lmtGrpNo`` 로 그룹이 나뉘고
**그룹 간 = 대안(OR)**·**그룹 내 = 모두 필요(AND)** 로 해석된다
(:func:`app.services.license_eligibility.assess_license_eligibility` 와 동일한 단일
출처 :func:`parse_license_limit_groups` 로 그룹핑). 이 축은 그 그룹 경계를 존중해,
어느 **한 그룹**의 요구 기술부문을 전부 보유하면 통과한다(그룹 간 OR). 과거에는
모든 행을 lmtGrpNo 무시하고 평면 합산 후 전체 AND 로 대조해, 두 그룹으로 나뉜
공고(예: 그룹1 ``엔지니어링사업(해양)`` / 그룹2 ``기술사사무소(해양)`` — 라이브
R26BK01642740 등)에서 면허 게이트가 eligible 로 통과시킨 operator 를 tech_field 축이
과차단하는 두 축 모순이 있었다. 이제 두 축이 같은 OR/AND 로 정렬된다.

**단일 그룹 공고(코퍼스 73%)는 동작 완전 불변**이다 — 그룹이 하나면 "그룹 내 AND"
가 곧 기존 평면 AND 와 같다. 방향은 완화(과차단 해소)만이며, 어떤 공고도 새로
차단되지 않는다.

요건 추출·매칭은 순수 룰 해석기를 재사용한다(복붙 금지, §4.6). 요건은
``license_limits`` 소스만 보고 title 매칭은 기관명/과업명 오탐 축이라 제외한다(#207
교훈). 요건 측과 프로필 측이 모두 ``match_tech_field_terms`` 의 **동일한 표준명
어휘**로 비교되므로 code↔name 혼선(#231)에 의한 과차단이 없다.
"""

from __future__ import annotations

from app.core.single_user import split_multi_value_text
from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.assessment import RuleAssessment
from app.services.eligibility_labeling import match_tech_field_terms
from app.services.license_eligibility import parse_license_limit_groups


def _tech_fields_by_group(eligibility_raw: dict | None) -> list[frozenset[str]]:
    """공고 면허요건을 ``lmtGrpNo`` 그룹별 기술부문 표준명 집합으로 매핑한다(순수).

    면허 게이트와 **동일한 그룹핑 단일 출처**(``parse_license_limit_groups``)로 행을
    lmtGrpNo 그룹으로 묶고(그룹 간 OR·그룹 내 AND), 각 그룹의 요구 면허명(``.names()``)
    을 ``match_tech_field_terms`` 로 기술부문 표준명 집합에 union 한다. 그룹 등장
    순서를 유지하며, 기술부문을 요구하지 않는 그룹(비-기술 면허만)은 빈 frozenset 으로
    남긴다(호출부가 "무제약 자격 경로"로 인지해 과차단하지 않도록). IO/DB 접근 없음.
    """
    groups: list[frozenset[str]] = []
    for group in parse_license_limit_groups(eligibility_raw):
        fields: set[str] = set()
        for name in group.names():
            fields |= match_tech_field_terms(name)
        groups.append(frozenset(fields))
    return groups


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


def _format_groups(groups: list[frozenset[str]]) -> str:
    """기술부문 그룹 목록을 사람이 읽는 ``[a, b] / [c]`` 형태로 만든다(reason 용)."""
    return " / ".join(f"[{', '.join(sorted(group))}]" for group in groups)


def assess_tech_field(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """공고가 명시한 기술부문 요건을 프로필 보유 현황과 대조한다(그룹 인지, ``assess_association`` 미러).

    면허 게이트와 동일한 lmtGrpNo 그룹 의미론(그룹 간 OR·그룹 내 AND)을 따른다.

    - 기술부문 요구 그룹 없음(대다수 공고) → 중립 PASS, score
      ``TECH_FIELD_NEUTRAL_SCORE`` (=0.0). penalty·blocking 을 만들지 않는다.
    - 어느 한 그룹의 요구 기술부문을 전부 보유 → ``TECH_FIELD_MATCH_SCORE`` + PASS.
    - 기술부문 무제약 그룹(다른 자격 경로) 존재 → 중립 PASS. tech_field 는 그 그룹의
      (비-기술) 면허를 검증할 수 없으므로 defer 한다(과차단 금지, license-axis 교훈).
    - 위 어느 경로도 아니면(모든 요구 그룹 미충족) → ``passed=False`` +
      ``TECH_FIELD_MISMATCH_PENALTY``.

    단일 그룹 공고는 "그룹 내 AND"가 기존 평면 AND 와 같아 동작이 완전 불변이다.
    """
    groups = _tech_fields_by_group(project.eligibility_raw)
    constrained = [group for group in groups if group]

    if not constrained:
        return RuleAssessment(
            score=config.TECH_FIELD_NEUTRAL_SCORE,
            passed=True,
            reasons=[
                "공고에 기술부문 요건이 명시되지 않아 기술부문 조건은 중립 처리했습니다."
            ],
        )

    held = _held_tech_fields(profile)
    satisfied = [group for group in constrained if group <= held]
    if satisfied:
        matched = sorted({term for group in satisfied for term in group})
        return RuleAssessment(
            score=config.TECH_FIELD_MATCH_SCORE,
            passed=True,
            reasons=[
                f"공고가 요구하는 기술부문을 확인했습니다: {', '.join(matched)}."
            ],
        )

    # 기술부문을 요구하지 않는 그룹(무제약 = 다른 자격 경로)이 하나라도 있으면, 그
    # 경로가 성립할 수 있으므로 과차단하지 않고 중립으로 defer 한다.
    if len(constrained) < len(groups):
        return RuleAssessment(
            score=config.TECH_FIELD_NEUTRAL_SCORE,
            passed=True,
            reasons=[
                "기술부문을 요구하지 않는 자격 경로(다른 그룹)가 있어 과차단하지 않고 "
                "기술부문 조건은 중립 처리했습니다."
            ],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.TECH_FIELD_MISMATCH_PENALTY,
        reasons=[
            "공고가 요구하는 기술부문을 어느 그룹으로도 충족하지 못했습니다"
            f"(그룹별 요구, 하나만 전부 보유하면 됨: {_format_groups(constrained)})."
        ],
    )
