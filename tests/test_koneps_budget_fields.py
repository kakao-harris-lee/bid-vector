"""공고 금액 축 write 정책 — 추정가격 출처 인지 덮어쓰기 가드의 값표 테스트.

규칙 자체(``should_write_budget_estimate``)는 ORM 도, DB 도 보지 않는 순수 함수라
입력→출력 값표로 전수 검증한다(§4.7-4). 그 규칙을 실제 행에 바르는 얇은 applier
(``apply_budget_amounts``)는 별도로 "min/max 축은 손대지 않는다"만 고정한다.
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    ESTIMATE_SOURCE_BASE_FALLBACK,
    ESTIMATE_SOURCE_BUDGET_FALLBACK,
    ESTIMATE_SOURCE_DERIVED,
    ESTIMATE_SOURCE_NOTICE,
    ESTIMATED_AMOUNT_SOURCES,
)
from app.models.models import Project
from app.schemas.koneps_items import KonepsCollectedItem
from app.services.koneps import budget_fields

# 권위는 공고 게시 추정가격 하나뿐이고, 나머지는 전부 빈 자리만 채운다(fill-only).
_UNTRUSTED = (
    ESTIMATE_SOURCE_DERIVED,
    ESTIMATE_SOURCE_BUDGET_FALLBACK,
    ESTIMATE_SOURCE_BASE_FALLBACK,
    None,
)


def test_untrusted_set_is_the_whole_vocabulary_minus_notice():
    """어휘가 늘면 이 테스트가 먼저 깨진다 — 새 출처의 신뢰 판정을 명시적으로 결정하게 한다."""
    expected = set(ESTIMATED_AMOUNT_SOURCES) - {ESTIMATE_SOURCE_NOTICE} | {None}
    assert set(_UNTRUSTED) == expected


# --------------------------------------------------------------------------- #
# 1. 순수 규칙 값표
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source", ESTIMATED_AMOUNT_SOURCES + (None,))
@pytest.mark.parametrize("current", [None, 0.0, 100_000_000.0])
@pytest.mark.parametrize("incoming", [None, 0.0, -1.0])
def test_absent_incoming_never_writes(current, incoming, source):
    """값이 없는(None/0/음수) 유입은 출처와 무관하게 기존 값을 지우지 않는다."""
    assert (
        budget_fields.should_write_budget_estimate(current, incoming, source) is False
    )


@pytest.mark.parametrize("current", [None, 0.0, 100_000_000.0])
def test_notice_source_always_writes(current):
    """공고가 게시한 추정가격은 정정공고/재공고 반영을 위해 기존 값을 덮는다."""
    assert (
        budget_fields.should_write_budget_estimate(
            current, 120_000_000.0, ESTIMATE_SOURCE_NOTICE
        )
        is True
    )


@pytest.mark.parametrize("source", _UNTRUSTED)
@pytest.mark.parametrize("current", [None, 0.0])
def test_untrusted_source_fills_an_empty_slot(current, source):
    """파생/폴백/미신고 값도 **빈 자리**는 채운다(최초 수집에서 값이 사라지지 않게)."""
    assert (
        budget_fields.should_write_budget_estimate(current, 120_000_000.0, source)
        is True
    )


@pytest.mark.parametrize("source", _UNTRUSTED)
def test_untrusted_source_never_clobbers_a_stored_estimate(source):
    """파생(예정가)·기초금액 폴백·미신고는 이미 저장된 양수 추정가격을 덮지 못한다.

    이 한 줄이 재태깅을 되돌리던 두 프로덕션 시퀀스(scsbid 개찰 패스 / 추정가격 미공급
    재수집)를 동시에 막는다 — 분모(``project.budget_estimate``)가 보존되기 때문이다.
    """
    assert (
        budget_fields.should_write_budget_estimate(100_000_000.0, 111_000_000.0, source)
        is False
    )


@pytest.mark.parametrize(
    ("current", "incoming", "source", "expected"),
    [
        (None, 0.0, ESTIMATE_SOURCE_NOTICE, 0.0),
        (None, 120_000_000.0, ESTIMATE_SOURCE_NOTICE, 120_000_000.0),
        (100_000_000.0, 120_000_000.0, ESTIMATE_SOURCE_NOTICE, 120_000_000.0),
        (100_000_000.0, 111_000_000.0, ESTIMATE_SOURCE_DERIVED, 100_000_000.0),
        (100_000_000.0, 0.0, ESTIMATE_SOURCE_NOTICE, 100_000_000.0),
        (None, 0.0, None, 0.0),
    ],
)
def test_stored_value_table(current, incoming, source, expected):
    """저장될 값 — 쓰지 않기로 하면 기존 값을 float 로 정규화해 그대로 둔다(선재 동작)."""
    assert budget_fields.stored_budget_estimate(current, incoming, source) == expected


def test_stored_value_is_coerced_before_it_counts_as_occupied():
    """저장 값은 숫자로 해석한 뒤 '자리가 찼는지'를 본다(ORM 이 float 를 보장하지 않는다).

    유입 값은 DTO 가 float 로 좁혀 오지만 ``current`` 는 컬럼에서 오므로, 숫자로 해석되지
    않는 값은 빈 자리로 취급해 파생 값이라도 채우게 둔다(값 소실 방지).
    """
    assert (
        budget_fields.should_write_budget_estimate(
            "100000000", 111_000_000.0, ESTIMATE_SOURCE_DERIVED
        )
        is False
    )
    assert (
        budget_fields.should_write_budget_estimate(
            "미상", 111_000_000.0, ESTIMATE_SOURCE_DERIVED
        )
        is True
    )


# --------------------------------------------------------------------------- #
# 2. applier — min/max 축은 이 PR 의 스코프가 아니다
# --------------------------------------------------------------------------- #
def _project(**kwargs) -> Project:
    defaults = {
        "title": "금액 축",
        "description": "",
        "requirements": "",
        "category": "construction",
    }
    return Project(**{**defaults, **kwargs})


def test_apply_keeps_budget_min_max_semantics_when_the_estimate_is_guarded():
    """추정가격을 지켜도 min/max 는 유입 금액을 그대로 반영한다(축 분리, 선재 동작)."""
    project = _project(budget_estimate=100_000_000.0, budget_min=0.0, budget_max=0.0)
    item = KonepsCollectedItem(
        notice_number="BUDGET-1",
        title="개찰결과",
        base_amount=0.0,
        estimated_amount=111_000_000.0,
        estimated_amount_source=ESTIMATE_SOURCE_DERIVED,
    )

    budget_fields.apply_budget_amounts(project, item=item)

    assert project.budget_estimate == 100_000_000.0  # 가드로 보존
    assert project.budget_min == 111_000_000.0  # min/max 는 종전대로 유입값 반영
    assert project.budget_max == 111_000_000.0


def test_apply_keeps_prior_min_max_when_the_item_carries_no_amount():
    """금액이 하나도 없는 item 은 min/max 도 건드리지 않는다(선재 동작)."""
    project = _project(
        budget_estimate=100_000_000.0, budget_min=90_000_000.0, budget_max=110_000_000.0
    )
    item = KonepsCollectedItem(
        notice_number="BUDGET-2", title="금액 없음", base_amount=0.0
    )

    budget_fields.apply_budget_amounts(project, item=item)

    assert project.budget_estimate == 100_000_000.0
    assert project.budget_min == 90_000_000.0
    assert project.budget_max == 110_000_000.0
