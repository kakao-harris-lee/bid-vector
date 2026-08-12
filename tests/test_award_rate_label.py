"""basis 명시 낙찰률 라벨 커널의 값 표 (DB·I/O 없음).

순수 함수라 입력→출력만 고정하면 검증이 끝난다(§4.7.4). 표가 못 박는 것은 셋이다:

1. 분모를 어느 경로로 골랐는지가 **값과 함께** 나온다(``denominator_source``).
2. 라벨이 성립하지 않을 때 ``None`` 하나로 뭉개지 않고 **사유**가 남는다.
3. 유효 창의 경계(0.5 · 1.5)가 **포함**이다 — 기존 tier 게이트와 같은 닫힌 구간이라야
   두 라벨의 표본이 경계에서 갈리지 않는다.
"""

from __future__ import annotations

import math

import pytest

from app.domain.award_rate_label import (
    AwardRateLabelStatus,
    build_award_rate_label,
)
from app.domain.money import Basis
from app.domain.reliable_base import ReliableBaseSource
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_RATIO,
)

CLEAN_BASE = 100_000_000.0


def test_clean_base_yields_base_relative_label():
    """clean 기초금액이면 저장된 base 를 그대로 분모로 쓴다."""
    label = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.status is AwardRateLabelStatus.OK
    assert label.value == 0.875
    assert label.denominator_value == CLEAN_BASE
    assert label.denominator_source is ReliableBaseSource.CLEAN_BASE
    assert label.base_amount_basis == BASIS_CLEAN
    assert label.numerator_basis is Basis.WINNING_AMOUNT
    assert label.denominator_basis is Basis.BASE_AMOUNT


def test_polluted_base_falls_back_to_reserve_recovered_estimate():
    """derived-yega(예정가 오염) base 는 거부하고 복수예비가격 복구 추정치를 분모로 쓴다.

    오염된 base 를 그대로 쓰면 분모가 예정가라 라벨이 다른 축의 수치가 된다 — 이 라벨의
    존재 이유가 그 갈림을 막는 것이다.
    """
    label = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=113_636_363.6,  # 낙찰가 ÷ 사정률 역산 = 예정가-basis 오염
        base_amount_basis=BASIS_DERIVED_YEGA,
        base_amount_estimated=CLEAN_BASE,
    )

    assert label.status is AwardRateLabelStatus.OK
    assert label.value == 0.875
    assert label.denominator_value == CLEAN_BASE
    assert label.denominator_source is ReliableBaseSource.RESERVE_ESTIMATE
    assert label.base_amount_basis == BASIS_DERIVED_YEGA


def test_unclassified_row_is_not_plain_ok_and_declares_no_basis():
    """오염 태그가 없는 행은 값이 나도 ``ok`` 가 아니다 — 분모에 근거가 없기 때문이다.

    값이 나온다는 것과 그 값이 진짜 기초금액이라는 것은 다른 주장이다. 태그가 ``None`` 인
    것은 clean 이라는 뜻이 아니라 판정된 적이 없다는 뜻이므로, 상태를 갈라 싣고 축(basis)은
    말하지 않는다.
    """
    label = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=None,
        base_amount_estimated=None,
    )

    assert label.value == 0.875  # 값 자체는 난다
    assert label.status is AwardRateLabelStatus.OK_UNVERIFIED_BASE
    assert label.denominator_basis is None  # 축을 주장할 근거가 없다
    assert label.denominator_source is ReliableBaseSource.BASE_FALLBACK
    assert label.base_amount_basis is None


def test_polluted_base_without_recovery_is_not_plain_ok():
    """non-clean 인데 복구 추정치가 없으면 저장 base 폴백 — 여기도 ``ok`` 가 아니다.

    태그가 없어 모르는 것(``None``)과 오염이라고 판정됐는데 복구를 못한 것은 사정이 다르지만
    **분모를 믿을 근거가 없다**는 결론은 같다. 두 경우를 같은 상태로 접고, 구분은
    ``base_amount_basis`` 원문이 진다.
    """
    label = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_SUSPECT_RATIO,
        base_amount_estimated=None,
    )

    assert label.value == 0.875
    assert label.status is AwardRateLabelStatus.OK_UNVERIFIED_BASE
    assert label.denominator_basis is None
    assert label.denominator_source is ReliableBaseSource.BASE_FALLBACK
    assert label.base_amount_basis == BASIS_SUSPECT_RATIO


def test_status_ok_alone_selects_only_evidenced_denominators():
    """**payload 만** 보고 ``status == "ok"`` 로 고르면 근거 없는 분모가 자동으로 빠진다.

    R1 의 핵심 계약이다. Phase 2 학습기가 가장 자연스러운 필터를 썼을 때 오염 행이 타깃에
    섞이면, 이 모듈이 기존 ``bid_rate`` 의 결함으로 진단한 혼재를 새 라벨이 그대로
    재생산한다. 네 분모 출처를 한 표로 세워 그 필터의 결과를 고정한다.
    """
    cases = {
        # 이름 → (base_amount, basis, estimated)
        "clean-base": (CLEAN_BASE, BASIS_CLEAN, None),
        "reserve-estimate": (113_636_363.6, BASIS_DERIVED_YEGA, CLEAN_BASE),
        "base-fallback (미태깅)": (CLEAN_BASE, None, None),
        "base-fallback (오염·복구실패)": (CLEAN_BASE, BASIS_SUSPECT_RATIO, None),
    }
    selected = {}
    for name, (base_amount, basis, estimated) in cases.items():
        payload = build_award_rate_label(
            winning_amount=87_500_000.0,
            base_amount=base_amount,
            base_amount_basis=basis,
            base_amount_estimated=estimated,
        ).as_payload()
        # 소비자가 가진 정보는 payload 뿐이다 — 객체 속성을 보지 않는다.
        selected[name] = (
            payload["status"] == "ok",
            payload["denominator_basis"],
            payload["value"],
        )

    assert selected == {
        # 값은 넷 다 0.875 로 같다. 갈리는 것은 그 값을 믿을 근거뿐이다.
        "clean-base": (True, "base_amount", 0.875),
        "reserve-estimate": (True, "base_amount", 0.875),
        "base-fallback (미태깅)": (False, None, 0.875),
        "base-fallback (오염·복구실패)": (False, None, 0.875),
    }


@pytest.mark.parametrize("winning_amount", [None, 0.0, -1.0, "", "abc"])
def test_missing_numerator_reports_no_winning_amount(winning_amount):
    """분자가 없으면 라벨의 주어가 없다 — 분모는 신고하되 값은 내지 않는다."""
    label = build_award_rate_label(
        winning_amount=winning_amount,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.value is None
    assert label.status is AwardRateLabelStatus.NO_WINNING_AMOUNT
    # 분자가 없어도 분모 관측은 남는다 — 커버리지 손실의 원인을 사후에 가를 수 있어야 한다.
    assert label.denominator_value == CLEAN_BASE
    assert label.denominator_source is ReliableBaseSource.CLEAN_BASE


@pytest.mark.parametrize("base_amount", [None, 0.0, -1.0])
def test_missing_denominator_reports_no_reliable_base(base_amount):
    """양수 기초금액을 못 고르면 ``no-reliable-base`` 로 신고한다."""
    label = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=base_amount,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.value is None
    assert label.status is AwardRateLabelStatus.NO_RELIABLE_BASE
    assert label.denominator_value is None
    assert label.denominator_source is ReliableBaseSource.UNAVAILABLE


@pytest.mark.parametrize(
    ("winning_amount", "expected_rate"),
    [
        (50_000_000.0, 0.5),  # 하단 경계 — 포함
        (150_000_000.0, 1.5),  # 상단 경계 — 포함
    ],
)
def test_valid_window_boundaries_are_inclusive(winning_amount, expected_rate):
    """0.5 · 1.5 는 통과한다(닫힌 구간). 창이 열리면 두 라벨의 표본이 경계에서 갈린다."""
    label = build_award_rate_label(
        winning_amount=winning_amount,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.status is AwardRateLabelStatus.OK
    assert label.value == expected_rate


@pytest.mark.parametrize(
    "winning_amount",
    [
        49_999_999.0,  # 하단 창 밖
        150_000_001.0,  # 상단 창 밖
        float("nan"),  # 수치이지만 비교가 전부 False → 창 밖으로 떨어진다
    ],
)
def test_out_of_window_ratio_is_reported_not_silently_dropped(winning_amount):
    """비는 났지만 창 밖이면 적재 사고다 — 사유를 남기고 값은 내지 않는다."""
    label = build_award_rate_label(
        winning_amount=winning_amount,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.value is None
    assert label.status is AwardRateLabelStatus.OUT_OF_RANGE
    assert label.denominator_value == CLEAN_BASE


def test_label_value_and_denominator_reconstruct_the_award_amount():
    """``value × denominator_value`` 로 낙찰가가 재현된다 — 감사 가능성의 최소 조건."""
    label = build_award_rate_label(
        winning_amount=88_035_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    )

    assert label.value is not None and label.denominator_value is not None
    assert math.isclose(
        label.value * label.denominator_value, 88_035_000.0, rel_tol=1e-12
    )


def test_payload_carries_both_basis_axes_as_plain_strings():
    """직렬화 블록은 값과 basis 를 한 덩어리로 싣는다 — 값만 떼어 곱할 수 없게."""
    payload = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    ).as_payload()

    assert payload == {
        "value": 0.875,
        "status": "ok",
        "numerator_basis": "winning_amount",
        "denominator_basis": "base_amount",
        "denominator_value": CLEAN_BASE,
        "denominator_source": "clean-base",
        "base_amount_basis": BASIS_CLEAN,
    }


def test_base_fallback_payload_is_frozen():
    """근거 없는 분모의 payload 전문 고정 — 이 경로가 프로세스 밖으로 나가는 모양.

    운영 코퍼스에서 값이 나는 라벨의 절반 가까이가 이 경로라, 여기가 조용히 ``ok`` /
    ``"base_amount"`` 로 바뀌면 오염 행이 학습 타깃에 통째로 들어간다. 세 필드가 서로
    모순되지 않는다는 것까지 한 덩어리로 얼린다: ``status`` 가 ``ok`` 가 아니고
    ``denominator_basis`` 가 ``None`` 이며 ``denominator_source`` 가 ``base-fallback`` 이다.
    """
    payload = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=CLEAN_BASE,
        base_amount_basis=None,
        base_amount_estimated=None,
    ).as_payload()

    assert payload == {
        "value": 0.875,
        "status": "ok-unverified-base",
        "numerator_basis": "winning_amount",
        "denominator_basis": None,
        "denominator_value": CLEAN_BASE,
        "denominator_source": "base-fallback",
        "base_amount_basis": None,
    }


def test_failed_label_payload_declares_no_basis_either():
    """분모를 못 고른 라벨도 축을 주장하지 않는다(``denominator_basis`` = ``None``)."""
    payload = build_award_rate_label(
        winning_amount=87_500_000.0,
        base_amount=None,
        base_amount_basis=BASIS_CLEAN,
        base_amount_estimated=None,
    ).as_payload()

    assert payload == {
        "value": None,
        "status": "no-reliable-base",
        "numerator_basis": "winning_amount",
        "denominator_basis": None,
        "denominator_value": None,
        "denominator_source": "unavailable",
        "base_amount_basis": BASIS_CLEAN,
    }
