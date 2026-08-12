"""``assessment_rate_from_opening`` 특성화 값표 — 라이브 표시 경로의 회귀 가드.

이 함수가 고른 사정률 표본이 **투찰서에 뜨는 하한 미달 빈도**의 분모가 된다
(``bid_summary`` → ``notice_floor_shortfall`` → ``build_floor_shortfall_estimate`` →
``bid_form_draft``). PR #363 이 그 안의 금액비 계산을 ``app.domain.award_rate_label`` 커널로
위임했으므로, **위임 이전 구현의 귀결**을 여기 얼린다. 실 코퍼스 pre/post 대조는 일회성이라
사라지지만 이 값표는 남아, 이 함수가 나중에 바뀌면 잡힌다.

값표는 DB 없이 돈다(§4.7.4) — 이 함수는 ORM 행이 아니라 스칼라만 받는 순수 함수다.

이 파일이 ``tests/test_prediction_label_basis.py`` 에서 분리된 이유는 둘이다: (a) 주제가
다르다 — 그쪽은 **학습 라벨**의 basis 계약, 이쪽은 **운영자 대면 빈도**의 표본 선택이다.
(b) 합쳐 두면 파일이 683줄로 §4.5-4 한도(~500줄)를 넘긴다.

이 함수는 **clean 분모만** 받는다. 커널이 값을 내주는 ``reserve-estimate``·
``base-fallback`` 도 여기서는 버려지는데, 그 거부는 위임 이전과 동일하다(구 구현도
``reliable.source is not CLEAN_BASE`` 에서 None 을 냈다). 학습 라벨과 갈리는 지점이지만
#363 이 만든 갈림이 아니다 — 근거는 ``app/services/floor_shortfall`` 모듈 docstring 1번
(추정 오차가 분포 **꼬리**를 오염시키는데, 이 소비자는 꼬리가 결론이다).
"""

from __future__ import annotations

import json

import pytest

from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_RATIO,
)
from app.services.floor_shortfall import assessment_rate_from_opening

# 표가 덮는 경계:
#   · 분자   None / 0 / 음수 / NaN / +inf / 숫자 문자열 / 비수치 문자열
#   · 분모   clean / 미태깅(None) / non-clean+복구추정치 / non-clean+복구실패 / 0 / None
#            / NaN / +inf / -inf
#   · 보고율 없음 / fraction / percent 스케일 / 창 밖
#   · 독립성 복수예비가격 0개 / 4개(경계 아래) / 5개(경계) / 15개
_RESERVES_15 = json.dumps([100_000_000.0 + index for index in range(15)])
# 독립 예정가 증거로 인정하는 최소 개수는 5(``MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE``).
_RESERVES_5 = json.dumps([100_000_000.0 + index for index in range(5)])
_RESERVES_4 = json.dumps([100_000_000.0 + index for index in range(4)])

# 분모 쪽을 고정한 기본 행 — 분자·보고율만 흔드는 케이스가 이것을 펼쳐 쓴다.
_CLEAN = dict(
    base_amount=100_000_000.0,
    base_amount_basis=BASIS_CLEAN,
    base_amount_estimated=None,
    reserve_prices=None,
)

# (설명, kwargs, 기대값)
_ASSESSMENT_RATE_TABLE = [
    (
        "clean base + 독립 보고율 → 사정률",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        0.875 / 0.87,
    ),
    (
        "percent 스케일 보고율도 같은 결과",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=87.0,
        ),
        0.875 / 0.87,
    ),
    (
        "보고율=금액비인데 복수예비가격 증거 있음 → 사정률 1",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_15,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        1.0,
    ),
    (
        "보고율=금액비인데 증거 없음 → 파생 의심으로 버림",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        None,
    ),
    (
        "복구 추정치는 이 용도에서 거부(꼬리 오염)",
        dict(
            base_amount=113_636_363.6,
            base_amount_basis=BASIS_DERIVED_YEGA,
            base_amount_estimated=100_000_000.0,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "오염 태그 없는 base 폴백도 거부",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=None,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "낙찰 금액 없음",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=0.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "보고 낙찰률 없음",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=None,
        ),
        None,
    ),
    (
        "금액비가 유효 창 밖",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=200_000_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    # ── 분자 경계 ────────────────────────────────────────────────────────────
    (
        "낙찰 금액 None",
        dict(**_CLEAN, winning_amount=None, winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 음수",
        dict(**_CLEAN, winning_amount=-1.0, winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 NaN — <=0 은 통과하지만 유효 창에서 탈락",
        dict(**_CLEAN, winning_amount=float("nan"), winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 +inf — 비가 inf 라 유효 창 밖",
        dict(**_CLEAN, winning_amount=float("inf"), winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 숫자 문자열 — optional_float 이 받는다",
        dict(**_CLEAN, winning_amount="87500000", winning_rate=0.87),
        0.875 / 0.87,
    ),
    (
        "낙찰 금액 비수치 문자열",
        dict(**_CLEAN, winning_amount="abc", winning_rate=0.87),
        None,
    ),
    # ── 분모 경계 ────────────────────────────────────────────────────────────
    (
        "base 0 — 양수 분모 없음",
        dict(
            base_amount=0.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base None",
        dict(
            base_amount=None,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base NaN — _positive_or_none 이 막는다",
        dict(
            base_amount=float("nan"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        # 리뷰어 관찰: ``_positive_or_none`` 은 NaN 만 막고 ``+inf`` 는 통과시킨다
        # (``inf > 0`` 이 참). 그 값이 분모가 되면 비가 0.0 이라 유효 창에서 탈락하므로
        # 라벨로 새지 않는다 — 고치는 대신 그 거동을 여기서 고정한다.
        "base +inf — 분모로 통과하지만 비가 0.0 이라 창 밖",
        dict(
            base_amount=float("inf"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base -inf — 양수가 아니라 분모 없음",
        dict(
            base_amount=float("-inf"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "non-clean + 복구 추정치 없음 → base 폴백도 거부",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_SUSPECT_RATIO,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    # ── 보고율·독립성 경계 ───────────────────────────────────────────────────
    (
        "보고율이 창 밖(200%) → 정규화 후에도 2.0",
        dict(**_CLEAN, winning_amount=87_500_000.0, winning_rate=200.0),
        None,
    ),
    (
        "보고율=금액비 + 복수예비가격 4개(경계 아래) → 독립 증거 부족",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_4,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        None,
    ),
    (
        "보고율=금액비 + 복수예비가격 5개(경계) → 독립 인정",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_5,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        1.0,
    ),
]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [pytest.param(*case[1:], id=case[0]) for case in _ASSESSMENT_RATE_TABLE],
)
def test_assessment_rate_from_opening_is_frozen(kwargs, expected):
    """red line: 사정률 표본 도출이 커널 위임 전후로 같다."""
    assert assessment_rate_from_opening(**kwargs) == expected
