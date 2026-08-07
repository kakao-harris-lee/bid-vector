"""공고 게시 낙찰하한율의 신뢰(개연) 밴드 — 순수 술어 + 게이트 계약.

밴드 자체는 원래 ``app/ai/floor_applicability`` 에 있었고 홀드아웃 품질 판정만
소비했다. #356(V3 budget_cap 게이트)이 published 하한을 **라이브 가격 경로의 게이트
입력**으로 승격시키면서 소비자가 ``app/services/koneps``(수집) · ``app/services/bid_base``
(라이브) 로 넓어졌는데, 그 두 층은 ``app/ai`` 를 import 하지 않는다(수집 → ML 은 역방향
의존). 그래서 밴드는 ``app/domain`` 으로 내려와 단일 출처가 됐다.

전부 순수 함수라 DB·네트워크 없이 돈다(§4.7).
"""

from __future__ import annotations

import pytest

from app.domain.published_floor_rate import (
    PUBLISHED_FLOOR_MAX_PLAUSIBLE,
    PUBLISHED_FLOOR_MIN_PLAUSIBLE,
    is_published_floor_plausible,
    plausible_published_floor_rate,
)


def test_band_constants_are_unchanged():
    """밴드 값은 이동만 했고 재발명하지 않았다(원래 선언과 동일)."""
    assert PUBLISHED_FLOOR_MIN_PLAUSIBLE == 0.30
    assert PUBLISHED_FLOOR_MAX_PLAUSIBLE == 0.995


@pytest.mark.parametrize(
    "rate, expected",
    [
        # ── 실데이터에서 관측된 진짜 게시값 ──
        (0.89995, True),   # 라이브 최대 실값
        (0.89745, True),   # 국가계약 공사 신율
        (0.87745, True),   # 구율 / 산림사업
        (0.9, True),
        (0.47995, True),   # 라이브 3건 — 하한 0.30 을 0.5 로 올리지 않는 이유
        # ── 경계(포함) ──
        (PUBLISHED_FLOOR_MIN_PLAUSIBLE, True),
        (PUBLISHED_FLOOR_MAX_PLAUSIBLE, True),
        # ── 밴드 밖 ──
        (1.0, False),      # "예정가 전액 이상 투찰" — 낙찰하한이 성립하지 않는다
        (1.1, False),
        (PUBLISHED_FLOOR_MAX_PLAUSIBLE + 0.0001, False),
        (PUBLISHED_FLOOR_MIN_PLAUSIBLE - 0.0001, False),
        (0.0088, False),   # 스케일 오적재(0.88 대신)
        (0.0, False),
        (None, False),
    ],
)
def test_is_published_floor_plausible_boundaries(rate, expected):
    assert is_published_floor_plausible(rate) is expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0.89745, 0.89745),  # 개연값은 그대로 통과
        (0.89995, 0.89995),
        (1.0, None),         # 성립 불가 → "하한 미보고"와 동일 취급
        (0.29, None),
        (None, None),
    ],
)
def test_plausible_published_floor_rate_keeps_or_drops(rate, expected):
    """게이트는 값을 고치지 않는다 — 그대로 통과시키거나 ``None`` 으로 떨어뜨린다."""
    resolved = plausible_published_floor_rate(rate)
    if expected is None:
        assert resolved is None
    else:
        assert resolved == pytest.approx(expected)
