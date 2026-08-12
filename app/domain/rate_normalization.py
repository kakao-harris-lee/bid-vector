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

**#256 의 판단을 부분적으로 뒤집은 것이다.** 그때 이 모듈은 유효범위 게이트를 "콜사이트마다
정당하게 다른 것"으로 분류해 병합 제외 대상으로 **명시 선언**했고, **그 분류는 대체로
옳았다.** 위임 콜사이트 전수(grep ``to_bid_rate_fraction``)를 다시 세면 게이트가 실제로
갈린다:

- **닫힌 창 ``[0.5, 1.5]`` — 2곳**: ``prediction_dataset._normalize_bid_rate_value``(파이썬),
  ``bid_target_signals``(SQL WHERE 절, 상수만 참조).
- **양수 게이트(``<= 0 → None``)만 — 5곳**: ``koneps/parsing.normalize_bid_rate_value`` ·
  ``ai/price_prediction/guardrails_context`` · ``award_verification._rate_to_fraction`` ·
  ``base_amount_basis.normalize_winning_rate`` · ``koneps/field_contract._as_fraction``.
- **게이트 없음 — 1곳**: ``paper_bidding_backtest/scoring``.
- **다른 밴드 — 1곳**: ``schemas/koneps_items``(게시 낙찰하한율 밴드).

즉 닫힌 창을 쓰는 곳은 소수다. 그럼에도 이 창을 상수+술어로 올린 근거는 "여러 곳이 같아서"가
아니라 **이 창이 이미 모듈 경계를 넘어 공유되고 있었다**는 사실이다: 값이
``PredictionDatasetService.VALID_BID_RATE_MIN/MAX`` 라는 **service 클래스 속성**으로 살면서
``bid_target_signals`` · ``floor_shortfall`` · ``scripts/measure_stored_bidrate_basis`` 가 그
속성을 참조했다. 새 커널(``award_rate_label``)이 같은 창을 쓰려면 service 를 import 해야
하는데 그러면 순환이 된다. 그래서 **이미 교차 모듈이던 상수의 거처를 중립 지점으로 옮기고**,
닫힌 창을 쓰는 쪽에 비교식 한 벌을 준다. 입력 coercion 은 여전히 콜사이트에 남긴다(#256 의
원래 판단 유지).

⚠ **위 5곳의 양수 게이트는 수렴 대상이 아니다.** 특히
``base_amount_basis.normalize_winning_rate`` 는 게이트의 **부재가 load-bearing** 이라고 자기
docstring 에 적혀 있다("No validity-range gate is applied, so a genuinely low award rate
stays usable for the derived-yega match"). 거기에 이 창을 씌우면 낙찰률 < 0.5 인 행이
``None`` 이 되어 derived-yega 매칭이 스킵되고 ``suspect-fractional`` 로 오분류된다 — #199
provenance 판정이 조용히 틀어진다. 아래 "콜사이트 수렴" 항목이 세는 넷은 **닫힌 창을 쓰는
곳만**이다.

창의 값 ``[0.5, 1.5]`` 는 새로 정한 것이 아니라 ``PredictionDatasetService`` 의
``VALID_BID_RATE_MIN/MAX`` 에 있던 것을 옮긴 것이다(값 동일). 그 클래스 상수는 참조 이름을
보존하기 위해 남아 이 두 상수를 재노출한다. ``bid_target_signals`` 는 파이썬 술어를 못
부르므로 종전대로 상수만 보는데, **창 상수가 쓰이는 자리는 ``case(...)`` 가 아니라
``.filter(...)`` WHERE 절**이다(``case`` 가 참조하는 것은 ``PERCENT_SCALE_THRESHOLD`` 뿐).

⚠ **콜사이트 수렴은 아직 끝나지 않았다.** 아래 넷은 같은 닫힌 구간을 여전히 인라인 비교로
쓴다 — ``prediction_dataset._resolve_bid_rate`` 의 tier 게이트 셋 ·
``prediction_dataset._normalize_bid_rate_value`` · ``bid_target_signals`` 의 SQL WHERE ·
``scripts/measure_stored_bidrate_basis.py::_gate``. 앞 둘은 이 PR 이 학습 라벨 해석 경로를
의도적으로 동결했기 때문이고, SQL 은 술어를 부를 수 없어 구조적으로 남는다. 창을 바꾸려면
이 상수만 고치면 값은 전파되지만(재노출), 비교식이 여러 모양으로 존재한다는 사실은 그대로다.

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
# 상단 1.5 가 ``PERCENT_SCALE_THRESHOLD`` 와 같은 것은 **우연이 아니라 유도 결과**다.
# 스케일 판별이 ``numeric > threshold → /100`` 이므로, 이 창이 성립하려면 threshold 가 두
# 조건을 동시에 만족해야 한다:
#
#   * 정당한 fraction ``[MIN, MAX]`` 가 나눠지지 않고 살아남을 것 → ``threshold >= MAX``
#   * 정당한 percent ``[MIN×100, MAX×100]`` 이 전부 나눠질 것    → ``threshold < MIN×100``
#
# 즉 ``threshold ∈ [1.5, 50)`` 이고, 현재 값 1.5 는 그 구간의 **하단 끝 = 창 상한 자체**다.
# 경계 처리도 맞물린다: 판별은 ``> 1.5`` 라 1.5 를 나누지 않고 창은 ``<= 1.5`` 라 1.5 를
# 받는다 — ``f = 1.5`` 가 살아남는 것은 이 조합일 때뿐이다.
#
# 그래서 독립 조정이 안 된다. **MAX 를 올리면 threshold 도 같이 올려야** 한다(안 그러면 새
# 상한 부근의 fraction 이 /100 되어 창 아래로 떨어진다). 반대로 threshold 를 창 상한 아래로
# 내리면 상한 부근 fraction 이 통째로 탈락한다 — threshold 를 1.4 로 낮추면 ``f = 1.5`` 가
# 0.015 로 오해석돼 창 밖이 된다(실측 확인).
#
# 제약 방향은 한쪽이다: 창이 threshold 의 정의역을 정하고, threshold 는 창을 정하지 않는다.
# 그래서 두 상수를 하나로 합치지도 않는다 — threshold 는 ``[1.5, 50)`` 안 어디든 될 수 있고
# 지금 값이 같은 것은 그 구간의 끝을 택했기 때문이다.
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
