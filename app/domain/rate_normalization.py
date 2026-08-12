"""투찰율 스케일 정규화(percent 0~100 ↔ fraction 0~1) 단일 출처.

투찰율은 저장 경로에 따라 fraction(0.875)으로도, percent(87.5)로도 들어온다
(scsbid는 fraction, HTML 파싱·일부 OpenAPI 필드는 percent). 이를 통일하는
"값이 임계치보다 크면 백분율로 보고 /100" 규칙과 그 임계치가 **7곳에 독립 구현**돼
있었고, 정산 백테스트(`paper_bidding_backtest`)만 임계치가 ``2.0``으로 어긋나
(1.5, 2.0] 구간의 율을 다르게 해석하는 회귀 엔진이었다.

이 모듈은 그 규칙과 임계치를 **단일 출처**로 소유한다. 각 콜사이트의 입력 coercion
(문자열/콤마/`%`/bool/NaN 처리, 반올림)은 입력 shape마다 정당하게 다르므로 그대로 두고
— "중복처럼 보이는 상이"를 억지로 병합하지 않는다 — 오직 divergent했던 **스케일 판별
규칙**만 :func:`to_bid_rate_fraction` 로 위임한다.
SQL-level CASE(`bid_target_signals`)는 파이썬 함수를 못 부르므로 임계치 상수만 참조한다.

율 라벨의 **유효 창**도 여기에 있다(:data:`BID_RATE_PLAUSIBLE_MIN` /
:data:`BID_RATE_PLAUSIBLE_MAX` · :func:`is_plausible_rate_label`). 스케일 판별과 유효성은
다른 규칙이지만(전자는 값을 바꾸고 후자는 값을 버린다) 둘 다 "정규화된 율을 어떻게 읽는가"
라 같은 모듈에 둔다 — ``PERCENT_SCALE_THRESHOLD`` 가 이미 그렇게 살고 있다.

창의 값 ``[0.5, 1.5]`` 는 새로 정한 것이 아니라 ``PredictionDatasetService`` 의
``VALID_BID_RATE_MIN/MAX`` 에 있던 것을 옮긴 것이다(값 동일). 그 클래스 상수는 참조 이름을
보존하기 위해 남아 이 두 상수를 재노출하고, SQL ``CASE``(``bid_target_signals``)는 파이썬
술어를 못 부르므로 종전대로 상수만 본다.

⚠ **콜사이트 수렴은 아직 끝나지 않았다.** ``prediction_dataset._resolve_bid_rate`` 의 tier
게이트 셋 · ``_normalize_bid_rate_value`` 는 같은 닫힌 구간을 여전히 인라인 비교로 쓴다.
이 PR 이 그 경로를 의도적으로 손대지 않았기 때문이고(학습 라벨 해석 경로 동결), 후속에서
이 술어로 수렴시킨다. 그때까지 창을 바꾸려면 이 상수만 고치면 되지만(값은 재노출로 전파),
비교식이 두 모양으로 존재한다는 사실은 그대로다.

순수 함수(I/O 0). ``app.domain.money`` 등과 같은 strict 타이핑 아일랜드다.
"""

from __future__ import annotations

from typing import Final

# percent-scale(0~100) 판별 경계. 이 값을 **초과**하면 백분율로 보고 /100 한다.
# scsbid는 fraction(0.875), HTML 파싱은 percent(87.5)로 저장하므로 둘을 가른다.
# 정확히 1.5인 값은 fraction으로 취급(경계 미포함) — 기존 6개 구현과 동일.
PERCENT_SCALE_THRESHOLD: Final[float] = 1.5

# 율 라벨(낙찰가 ÷ 어떤 금액)의 유효 창 — **닫힌 구간**. 스케일 정규화를 마친 율이 이 창을
# 벗어나면 추첨 결과가 아니라 적재 사고다(스케일 이중 적용, 다른 금액 필드 혼입, 0/누락).
#
# 상단 1.5 가 ``PERCENT_SCALE_THRESHOLD`` 와 값이 같은 것은 별개의 규칙이 같은 숫자를 쓰는
# 것이다(그쪽=percent/fraction 판별, 이쪽=정규화 후 유효성). 한쪽을 다른 쪽으로 대체하지
# 않는다 — 우연한 일치에 기대면 한쪽만 바뀔 때 조용히 갈린다.
#
# ⚠ 사정률 밴드들(``app/core/constants.ASSESSMENT_RATE_PLAUSIBLE_*`` 0.8~1.2 ·
# ``app/domain/floor_shortfall.ASSESSMENT_RATE_MIN/MAX`` 0.90~1.10)과 **다른 축**이다:
# 저것들은 예정가÷기초금액의 밴드, 이것은 낙찰가÷금액의 밴드다. 통합 금지.
BID_RATE_PLAUSIBLE_MIN: Final[float] = 0.5
BID_RATE_PLAUSIBLE_MAX: Final[float] = 1.5


def to_bid_rate_fraction(
    numeric: float, *, threshold: float = PERCENT_SCALE_THRESHOLD
) -> float:
    """percent-scale(> threshold) 값을 fraction으로(÷100). fraction은 그대로 반환.

    스케일 판별만 수행하는 순수 규칙이다. 입력 coercion(None/문자열/음수)·반올림은
    호출부 책임으로 남긴다 — 콜사이트마다 정당하게 다르기 때문.
    """
    return numeric / 100.0 if numeric > threshold else numeric


def is_plausible_rate_label(value: float) -> bool:
    """스케일 정규화를 마친 율 라벨이 유효 창 안인가 (순수 술어).

    경계는 **포함**이다(0.5·1.5 는 통과) — 기존 인라인 게이트가 전부 ``MIN <= x <= MAX``
    였고, 창이 열리거나 닫히면 표본이 경계에서 갈리므로 구간을 그대로 옮겼다.

    입력은 이미 수치로 강제된 값이다(``None`` 을 받지 않는다 — 결측 처리 방식은 콜사이트가
    정한다). NaN 은 비교가 양쪽 다 False 로 떨어져 탈락하는데, 이는 인라인 비교와 같은
    결과다.
    """
    return BID_RATE_PLAUSIBLE_MIN <= value <= BID_RATE_PLAUSIBLE_MAX
