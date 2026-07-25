"""투찰율 스케일 정규화(percent 0~100 ↔ fraction 0~1) 단일 출처.

투찰율은 저장 경로에 따라 fraction(0.875)으로도, percent(87.5)로도 들어온다
(scsbid는 fraction, HTML 파싱·일부 OpenAPI 필드는 percent). 이를 통일하는
"값이 임계치보다 크면 백분율로 보고 /100" 규칙과 그 임계치가 **7곳에 독립 구현**돼
있었고, 정산 백테스트(`paper_bidding_backtest`)만 임계치가 ``2.0``으로 어긋나
(1.5, 2.0] 구간의 율을 다르게 해석하는 회귀 엔진이었다.

이 모듈은 그 규칙과 임계치를 **단일 출처**로 소유한다. 각 콜사이트의 입력 coercion
(문자열/콤마/`%`/bool/NaN 처리, 유효범위 게이트, 반올림)은 입력 shape마다 정당하게
다르므로 그대로 두고 — "중복처럼 보이는 상이"를 억지로 병합하지 않는다 — 오직
divergent했던 **스케일 판별 규칙**만 :func:`to_bid_rate_fraction` 로 위임한다.
SQL-level CASE(`bid_target_signals`)는 파이썬 함수를 못 부르므로 임계치 상수만 참조한다.

순수 함수(I/O 0). ``app.domain.money`` 등과 같은 strict 타이핑 아일랜드다.
"""

from __future__ import annotations

from typing import Final

# percent-scale(0~100) 판별 경계. 이 값을 **초과**하면 백분율로 보고 /100 한다.
# scsbid는 fraction(0.875), HTML 파싱은 percent(87.5)로 저장하므로 둘을 가른다.
# 정확히 1.5인 값은 fraction으로 취급(경계 미포함) — 기존 6개 구현과 동일.
PERCENT_SCALE_THRESHOLD: Final[float] = 1.5


def to_bid_rate_fraction(
    numeric: float, *, threshold: float = PERCENT_SCALE_THRESHOLD
) -> float:
    """percent-scale(> threshold) 값을 fraction으로(÷100). fraction은 그대로 반환.

    스케일 판별만 수행하는 순수 규칙이다. 입력 coercion(None/문자열/음수/유효범위)·
    반올림은 호출부 책임으로 남긴다 — 콜사이트마다 정당하게 다르기 때문.
    """
    return numeric / 100.0 if numeric > threshold else numeric
