"""추정가격 출처 판정(순수 규칙)의 값표.

수집 생산자가 "어느 축에서 값을 얻었는가"를 라벨로 접는 규칙이다. I/O 도 ORM 도 없으므로
입력→출력 표로 전수 검증한다(§4.7-4). 이 라벨을 write 가 어떻게 해석하는지는
``tests/test_koneps_budget_fields.py``, 생산자가 실제로 어느 키에서 얻는지는
``tests/test_koneps_collected_item_contract.py`` 가 각각 고정한다.
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    ESTIMATE_SOURCE_BASE_FALLBACK,
    ESTIMATE_SOURCE_BUDGET_FALLBACK,
    ESTIMATE_SOURCE_NOTICE,
)
from app.domain.estimate_provenance import estimate_source


@pytest.mark.parametrize(
    ("notice_estimate", "resolved_estimate", "expected"),
    [
        # 추정가격 키에서 값을 얻었으면 권위값이다(같은 값이 아래 축에도 있든 없든).
        (113_636_364.0, 113_636_364.0, ESTIMATE_SOURCE_NOTICE),
        # 추정가격 키는 비었고 예산 키가 값을 채웠다 — 게시값이지만 개념이 다르다.
        (None, 125_000_000.0, ESTIMATE_SOURCE_BUDGET_FALLBACK),
        (0.0, 125_000_000.0, ESTIMATE_SOURCE_BUDGET_FALLBACK),
        # 추정가격 축이 통째로 비면 item 은 기초금액 사본을 싣는다.
        (None, None, ESTIMATE_SOURCE_BASE_FALLBACK),
        (None, 0.0, ESTIMATE_SOURCE_BASE_FALLBACK),
        (0.0, 0.0, ESTIMATE_SOURCE_BASE_FALLBACK),
    ],
)
def test_estimate_source_value_table(notice_estimate, resolved_estimate, expected):
    assert estimate_source(notice_estimate, resolved_estimate) == expected


def test_only_the_notice_axis_carries_authority():
    """예산 폴백은 '값은 있으나 권위는 없음' — 이 구분이 리뷰 api-m1 회귀의 차단선이다.

    배정예산액은 추정가격 이상(상한 성격)이라, 권위를 주면 패스마다 해석 키가 바뀔 때 분모가
    위로 떠 오염 판정이 clean 으로 되돌아간다.
    """
    assert estimate_source(1.0, 1.0) == ESTIMATE_SOURCE_NOTICE
    assert estimate_source(None, 1.0) != ESTIMATE_SOURCE_NOTICE
