"""lmtGrpNo 그룹-OR / 그룹내-AND 자격 평가 커널 (순수 · 소비자 공유).

KONEPS 면허요건은 ``lmtGrpNo`` 로 그룹이 나뉘고 **그룹 간 = 대안(OR)**·
**그룹 내 = 모두 필요(AND)** 로 해석된다(단일 출처 파서
:func:`app.services.license_eligibility.parse_license_limit_groups`). 이 그룹 경계를
"어느 한 그룹의 요구를 전부 보유하면 통과(OR)" 로 접는 **평가 fold** 는 원래 두 곳에
독립 구현돼 있었다:

* 면허 게이트 :func:`app.services.license_eligibility.assess_license_eligibility`
  — ``for group in groups: group.missing(profile_keys)`` 로 any-group 통과.
* 기술부문 축 :func:`app.services.classification.tech_field.assess_tech_field`
  — ``[g for g in constrained if g <= held]`` 로 any 통과 + "무제약 그룹 → 중립 defer".

두 fold 가 어긋나면 두 축이 같은 공고를 반대로 판정하는 모순이 생긴다(#254 회귀:
tech_field 축이 면허 게이트가 통과시킨 operator 를 과차단). 이 모듈은 그 fold 를
단일 출처로 모아 **판정 결과가 바뀌지 않도록** 두 소비자가 공유하게 한다.

설계(§4.7): 순수 함수 — 집합만 받아 집합 대수로 판정한다. 소비자별로 다른
**projection**(그룹에서 어떤 요건 집합을 뽑아 어떤 보유 집합과 비교하는지)과
**결과 해석**(RuleAssessment · LicenseEligibility)·evidence 는 이 커널 밖(소비자)에
남는다. 그룹 내 AND 는 "그룹의 요구 집합 ⊆ 보유 집합" 부분집합 판정으로 표현되고
(면허 게이트의 요구별 부분집합 판정과 합집합 부분집합이 동치 — 요건 키가 항상
non-empty 이므로), 그룹 간 OR 은 "한 그룹이라도 충족" 으로 표현된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import Enum


class UnconstrainedGroupPolicy(Enum):
    """빈-projection(무제약) 그룹 처리 정책.

    소비자는 각 그룹을 자기 관점으로 projection 한다(면허 게이트는 면허 키,
    tech_field 는 기술부문 표준명). 어떤 그룹이 그 관점에서 **아무 요건도 부과하지
    않으면**(projection 이 빈 집합) 그 그룹을 어떻게 볼지는 소비자마다 다르므로 정책
    파라미터로 명시한다.
    """

    DEFER = "defer"
    """무제약 그룹은 그 자체로 통과가 아니라 판정을 **보류(중립)** 시킨다.

    tech_field 축이 쓰는 정책: 무제약 그룹은 tech_field 가 검증할 수 없는 다른 자격
    경로(비-기술 면허)이므로, 제약 그룹을 충족하지 못했더라도 과차단하지 않고
    :attr:`GroupOrVerdict.DEFER` 로 보류한다(license-axis 커버리지 비대칭 교훈).
    """

    SATISFY = "satisfy"
    """무제약 그룹은 요건 0 = **충족**으로 본다(공집합 ⊆ held 의 vacuous truth).

    면허 게이트가 쓰는(암묵) 정책: 면허 그룹은 항상 요건 키를 가지므로(요구 면허명이
    없으면 그룹 자체가 생기지 않는다) 무제약이 될 수 없어 이 정책은 실제로 실행되지
    않는다. 다만 현행 ``LicenseGroup.missing`` 이 빈 요건을 (vacuous) 충족으로 보는
    동작과 일치하는 라벨이라, 게이트의 암묵 동작을 정확히 보존한다.
    """


class GroupOrVerdict(Enum):
    """그룹-OR fold 판정. 소비자가 자기 결과 타입/문구로 해석한다."""

    SATISFIED = "satisfied"
    """최소 한 그룹을 충족했다(그룹 간 OR 통과)."""

    UNSATISFIED = "unsatisfied"
    """제약 그룹이 있으나 어느 것도 충족하지 못했고, 보류시킬 무제약 경로도 없다."""

    DEFER = "defer"
    """제약 그룹을 충족하지 못했으나 무제약 경로(다른 그룹)가 있어 보류한다(DEFER 정책)."""

    NO_CONSTRAINT = "no_constraint"
    """제약 그룹이 하나도 없다(요건 없음 또는 전부 무제약)."""


@dataclass(frozen=True)
class GroupOrOutcome:
    """그룹-OR fold 결과 — 판정 + projection 순서에 맞춘 그룹별 비트.

    ``satisfied``/``constrained`` 는 입력 ``projections`` 와 index 정렬돼 있어,
    소비자가 원래 그룹 객체와 zip 해 matched/missing·evidence·매칭 용어를 만든다.
    """

    verdict: GroupOrVerdict
    satisfied: tuple[bool, ...]
    constrained: tuple[bool, ...]


def evaluate_group_or(
    projections: Sequence[AbstractSet[str]],
    held: AbstractSet[str],
    *,
    unconstrained_policy: UnconstrainedGroupPolicy = UnconstrainedGroupPolicy.DEFER,
) -> GroupOrOutcome:
    """그룹별 요구 집합을 보유 집합과 대조해 그룹-OR / 그룹내-AND 로 판정한다(순수).

    - 그룹 내 AND: 그룹의 요구 집합이 ``held`` 의 부분집합이면 그 그룹은 충족.
    - 그룹 간 OR: 어느 한 그룹이라도 충족하면 :attr:`GroupOrVerdict.SATISFIED`.
    - 무제약(빈 projection) 그룹: ``unconstrained_policy`` 에 따라 보류(DEFER)하거나
      vacuous 충족(SATISFY)으로 본다.

    판정 우선순위(이 순서가 두 소비자의 기존 분기 구조와 정렬된다):

    1. 충족 그룹이 하나라도 있으면 ``SATISFIED``.
    2. 아니고 제약 그룹이 하나도 없으면 ``NO_CONSTRAINT``.
    3. 아니고 무제약 그룹이 있으면(DEFER 정책 하에서만 여기 도달) ``DEFER``.
    4. 그 외(모두 제약·미충족) ``UNSATISFIED``.

    IO/DB 접근 없음.
    """
    constrained = tuple(bool(projection) for projection in projections)
    satisfy_unconstrained = unconstrained_policy is UnconstrainedGroupPolicy.SATISFY
    satisfied = tuple(
        (projection <= held) if is_constrained else satisfy_unconstrained
        for projection, is_constrained in zip(projections, constrained)
    )

    if any(satisfied):
        verdict = GroupOrVerdict.SATISFIED
    elif not any(constrained):
        verdict = GroupOrVerdict.NO_CONSTRAINT
    elif not all(constrained):
        verdict = GroupOrVerdict.DEFER
    else:
        verdict = GroupOrVerdict.UNSATISFIED

    return GroupOrOutcome(verdict=verdict, satisfied=satisfied, constrained=constrained)
