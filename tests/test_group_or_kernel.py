"""그룹-OR / 그룹내-AND 평가 커널 값 테이블(§4.5.3).

``evaluate_group_or`` 는 면허 게이트와 tech_field 축이 공유하는 순수 fold 다.
집합만으로 판정하는 커널의 계약(판정 우선순위·무제약 정책·그룹별 비트)을 소비자
없이 고정한다 — 두 소비자의 콜사이트 교체가 판정을 바꾸지 않음을 이 표가 증명한다.
"""
from __future__ import annotations

import pytest

from app.services.classification.group_or import (
    GroupOrOutcome,
    GroupOrVerdict,
    UnconstrainedGroupPolicy,
    evaluate_group_or,
)


def _fs(*items: str) -> frozenset[str]:
    return frozenset(items)


# --- DEFER 정책 (tech_field 축) 값 테이블 -------------------------------------


@pytest.mark.parametrize(
    ("projections", "held", "expected_verdict", "expected_satisfied"),
    [
        # 그룹 없음 → 제약 없음.
        ([], _fs("a"), GroupOrVerdict.NO_CONSTRAINT, ()),
        # 무제약 그룹만(빈 projection) → 제약 없음(무제약 경로여도 NO_CONSTRAINT 우선).
        ([_fs()], _fs("a"), GroupOrVerdict.NO_CONSTRAINT, (False,)),
        ([_fs(), _fs()], _fs("a"), GroupOrVerdict.NO_CONSTRAINT, (False, False)),
        # 단일 제약 그룹 충족 → SATISFIED.
        ([_fs("a")], _fs("a", "b"), GroupOrVerdict.SATISFIED, (True,)),
        # 단일 제약 그룹 그룹내 AND 미충족(하나만 보유) → UNSATISFIED.
        ([_fs("a", "b")], _fs("a"), GroupOrVerdict.UNSATISFIED, (False,)),
        # 그룹내 AND 전부 보유 → SATISFIED.
        ([_fs("a", "b")], _fs("a", "b", "c"), GroupOrVerdict.SATISFIED, (True,)),
        # 다중 그룹 OR — 한 그룹만 충족해도 통과.
        ([_fs("a"), _fs("b")], _fs("a"), GroupOrVerdict.SATISFIED, (True, False)),
        # 다중 제약 그룹 모두 미충족 → UNSATISFIED.
        ([_fs("a"), _fs("b")], _fs("c"), GroupOrVerdict.UNSATISFIED, (False, False)),
        # 제약 + 무제약 혼합, 제약 충족 → SATISFIED(무제약은 satisfied=False).
        ([_fs("a"), _fs()], _fs("a"), GroupOrVerdict.SATISFIED, (True, False)),
        # 제약 + 무제약 혼합, 제약 미충족 → DEFER(무제약 경로가 보류시킴).
        ([_fs("a"), _fs()], _fs("b"), GroupOrVerdict.DEFER, (False, False)),
        # 빈 held 로도 제약 미충족 + 무제약 존재 → DEFER.
        ([_fs("a"), _fs()], _fs(), GroupOrVerdict.DEFER, (False, False)),
    ],
)
def test_defer_policy_value_table(
    projections, held, expected_verdict, expected_satisfied
):
    outcome = evaluate_group_or(
        projections, held, unconstrained_policy=UnconstrainedGroupPolicy.DEFER
    )

    assert outcome.verdict is expected_verdict
    assert outcome.satisfied == expected_satisfied
    assert outcome.constrained == tuple(bool(p) for p in projections)


def test_defer_is_the_default_policy():
    """정책 미지정 시 DEFER 가 기본(tech_field 축 안전 기본값)."""
    default = evaluate_group_or([_fs("a"), _fs()], _fs("b"))
    explicit = evaluate_group_or(
        [_fs("a"), _fs()], _fs("b"), unconstrained_policy=UnconstrainedGroupPolicy.DEFER
    )

    assert default == explicit
    assert default.verdict is GroupOrVerdict.DEFER


# --- SATISFY 정책 (면허 게이트) 값 테이블 -------------------------------------


@pytest.mark.parametrize(
    ("projections", "held", "expected_verdict", "expected_satisfied"),
    [
        # 면허 게이트 실제 경로: 모든 그룹이 제약(요건 키 non-empty). SATISFY 는
        # 무제약 경로가 없어 실행되지 않으므로 DEFER 와 동일 판정을 낸다.
        ([_fs("a")], _fs("a"), GroupOrVerdict.SATISFIED, (True,)),
        ([_fs("a"), _fs("b")], _fs("b"), GroupOrVerdict.SATISFIED, (False, True)),
        ([_fs("a", "b")], _fs("a"), GroupOrVerdict.UNSATISFIED, (False,)),
        ([_fs("a"), _fs("b")], _fs("c"), GroupOrVerdict.UNSATISFIED, (False, False)),
        # 무제약 그룹은(불가 케이스지만) SATISFY 정책 하에서 vacuous 충족 → SATISFIED.
        ([_fs()], _fs(), GroupOrVerdict.SATISFIED, (True,)),
        ([_fs("a"), _fs()], _fs("z"), GroupOrVerdict.SATISFIED, (False, True)),
    ],
)
def test_satisfy_policy_value_table(
    projections, held, expected_verdict, expected_satisfied
):
    outcome = evaluate_group_or(
        projections, held, unconstrained_policy=UnconstrainedGroupPolicy.SATISFY
    )

    assert outcome.verdict is expected_verdict
    assert outcome.satisfied == expected_satisfied


def test_satisfy_and_defer_agree_when_all_groups_constrained():
    """모든 그룹이 제약이면 두 정책이 완전히 같은 판정을 낸다(면허 게이트 불변식).

    면허 게이트는 무제약 그룹이 생기지 않으므로, SATISFY 를 넘겨도 판정이 DEFER 와
    다르지 않다 — 게이트 콜사이트 교체가 어떤 판정 변화도 만들지 않는다는 보증.
    """
    cases = [
        ([_fs("a")], _fs("a")),
        ([_fs("a"), _fs("b")], _fs("b")),
        ([_fs("a", "b")], _fs("a")),
        ([_fs("a"), _fs("b"), _fs("c")], _fs("c")),
        ([_fs("a")], _fs("z")),
    ]
    for projections, held in cases:
        satisfy = evaluate_group_or(
            projections, held, unconstrained_policy=UnconstrainedGroupPolicy.SATISFY
        )
        defer = evaluate_group_or(
            projections, held, unconstrained_policy=UnconstrainedGroupPolicy.DEFER
        )
        assert satisfy == defer


# --- 커널 계약 -----------------------------------------------------------------


def test_outcome_is_frozen_and_index_aligned():
    outcome = evaluate_group_or([_fs("a"), _fs(), _fs("b", "c")], _fs("a"))

    assert isinstance(outcome, GroupOrOutcome)
    assert outcome.constrained == (True, False, True)
    assert outcome.satisfied == (True, False, False)
    with pytest.raises(Exception):
        outcome.verdict = GroupOrVerdict.SATISFIED  # type: ignore[misc]


def test_accepts_plain_set_for_held():
    """held 는 frozenset 뿐 아니라 일반 set 도 받는다(tech_field 는 set 을 넘긴다)."""
    outcome = evaluate_group_or([_fs("a")], {"a", "b"})

    assert outcome.verdict is GroupOrVerdict.SATISFIED
