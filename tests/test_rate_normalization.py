"""app.domain.rate_normalization 단일 출처 + 7개 콜사이트 특성화(characterization).

목적:
- 신설 순수 규칙 :func:`to_bid_rate_fraction` 와 임계치 상수 검증.
- 각 콜사이트를 도메인 규칙 위임으로 교체한 뒤에도 **동작이 불변**임을 고정한다.
  threshold 1.5인 6곳은 리팩터 전/후 diff 0(아래 그리드가 그 golden).
- 유일한 동작 변경 지점(`paper_bidding_backtest._normalize_rate`, 기존 >2.0 →
  통일 >1.5)은 (1.5, 2.0] 밴드의 재해석을 명시적으로 문서화한다.
"""

from __future__ import annotations

import types

import pytest

from app.ai.price_prediction import _normalize_optional_bid_rate
from app.domain.rate_normalization import PERCENT_SCALE_THRESHOLD, to_bid_rate_fraction
from app.services.award_verification import _rate_to_fraction
from app.services.base_amount_basis import normalize_winning_rate
from app.services.koneps import parsing
from app.services.koneps.field_contract import _as_fraction
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.prediction_dataset import PredictionDatasetService


# --- 신설 순수 규칙 --------------------------------------------------------


def test_percent_scale_threshold_is_single_source() -> None:
    assert PERCENT_SCALE_THRESHOLD == 1.5


@pytest.mark.parametrize(
    "numeric, expected",
    [
        (0.4, 0.4),  # fraction stays
        (0.875, 0.875),
        (1.5, 1.5),  # exactly at threshold — NOT divided (boundary excluded)
        (1.500001, 0.01500001),  # just above threshold — divided
        (1.6, 0.016),
        (2.0, 0.02),
        (87.5, 0.875),
        (150.0, 1.5),
        (0.0, 0.0),  # scale rule alone applies no gate
        (-5.0, -5.0),
    ],
)
def test_to_bid_rate_fraction(numeric: float, expected: float) -> None:
    assert to_bid_rate_fraction(numeric) == pytest.approx(expected)


def test_to_bid_rate_fraction_custom_threshold() -> None:
    # 규칙은 threshold 주입을 허용하지만 단일 출처 기본값이 1.5임을 확인.
    assert to_bid_rate_fraction(1.6, threshold=2.0) == pytest.approx(1.6)
    assert to_bid_rate_fraction(2.5, threshold=2.0) == pytest.approx(0.025)


# --- 콜사이트 특성화: threshold 1.5 (리팩터 전/후 diff 0) -------------------

# 공유 입력 그리드 — 각 함수의 현행 출력을 golden 으로 고정한다(2026-07-25 probe).
_GRID = [None, "", 0, 0.0, -5.0, 0.4, 0.5, 0.875, 1.0, 1.5, 1.6, 2.0, 2.5,
         87.5, 88.035, 150.0, 200.0, "87.5%", "0.875", "1,234", "x", True, False]


def _pd_normalize(value: object) -> float | None:
    ns = types.SimpleNamespace(
        VALID_BID_RATE_MIN=PredictionDatasetService.VALID_BID_RATE_MIN,
        VALID_BID_RATE_MAX=PredictionDatasetService.VALID_BID_RATE_MAX,
    )
    return PredictionDatasetService._normalize_bid_rate_value(ns, value)


# golden per 함수 (그리드 순서대로). None 은 파싱거부/비양수/범위밖.
_GOLDEN = {
    "parsing": [None, None, None, None, None, 0.4, 0.5, 0.875, 1.0, 1.5, 0.016,
                0.02, 0.025, 0.875, 0.88035, 1.5, 2.0, 0.875, 0.875, 12.34,
                None, 1.0, None],
    "price_pred": [None, None, None, None, None, 0.4, 0.5, 0.875, 1.0, 1.5,
                   0.016, 0.02, 0.025, 0.875, 0.88035, 1.5, 2.0, None, 0.875,
                   12.34, None, None, None],
    "winning_rate": [None, None, None, None, None, 0.4, 0.5, 0.875, 1.0, 1.5,
                     0.016, 0.02, 0.025, 0.875, 0.88035, 1.5, 2.0, None, 0.875,
                     None, None, 1.0, None],
    "award": [None, None, None, None, None, 0.4, 0.5, 0.875, 1.0, 1.5, 0.016,
              0.02, 0.025, 0.875, 0.88035, 1.5, 2.0, None, 0.875, None, None,
              1.0, None],
    "prediction_dataset": [None, None, None, None, None, None, 0.5, 0.875, 1.0,
                           1.5, None, None, None, 0.875, 0.88035, 1.5, None,
                           None, 0.875, None, None, 1.0, None],
    "field_contract": [None, None, None, None, None, 0.4, 0.5, 0.875, 1.0, 1.5,
                       0.016, 0.02, 0.025, 0.875, 0.88035, 1.5, 2.0, 0.875,
                       0.875, 12.34, None, None, None],
}

_FUNCS = {
    "parsing": parsing.normalize_bid_rate_value,
    "price_pred": _normalize_optional_bid_rate,
    "winning_rate": normalize_winning_rate,
    "award": _rate_to_fraction,
    "prediction_dataset": _pd_normalize,
    "field_contract": _as_fraction,
}


@pytest.mark.parametrize("name", sorted(_FUNCS))
def test_callsite_characterization(name: str) -> None:
    fn = _FUNCS[name]
    expected = _GOLDEN[name]
    assert len(expected) == len(_GRID)
    for value, want in zip(_GRID, expected):
        got = fn(value)
        if want is None:
            assert got is None, f"{name}({value!r}) -> {got!r}, want None"
        else:
            assert got == pytest.approx(want), f"{name}({value!r}) -> {got!r}"


# --- 유일한 동작 변경: paper_bidding_backtest._normalize_rate --------------
# 기존 threshold >2.0 → 통일 >1.5. (1.5, 2.0] 밴드만 해석이 바뀐다.


def _pb_normalize(value: float) -> float:
    return PaperBiddingBacktestService._normalize_rate(None, value)  # self 미사용


@pytest.mark.parametrize(
    "value, expected",
    [
        # 밴드 밖 — 불변
        (0.875, 0.875),
        (1.0, 1.0),
        (1.5, 1.5),  # 경계 미포함
        (2.5, 0.025),  # >2.0 이미 나눠지던 값 — 불변
        (87.5, 0.875),
        (0.0, 0.0),  # <=0 게이트 없음 — 불변(passthrough)
        (-5.0, -5.0),
        # (1.5, 2.0] 밴드 — 동작 변경(통일 후 /100)
        (1.6, 0.016),
        (2.0, 0.02),
    ],
)
def test_paper_bidding_normalize_unified_threshold(
    value: float, expected: float
) -> None:
    assert _pb_normalize(value) == pytest.approx(expected)
