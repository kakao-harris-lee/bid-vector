"""분포형 predictor 의 시나리오 후보 선언 + 조립기 한 벌 (§4.5-1 · §4.5-8).

분포를 내는 predictor 는 점추정 하나가 아니라 보수/기준/공격 세 후보를 낸다. 그 세
후보를 만드는 규칙(경계 위치 z · 후보별 가중치 · payload 모양)은 엔진마다 달라야 할
이유가 없는데, Phase 1 분포 엔진과 Phase 2 낙찰률 GBM 이 각자 선언하면 한쪽만 조정될 때
두 엔진의 후보 폭이 조용히 갈린다. 그래서 선언과 조립을 여기 한 벌로 둔다.

경계는 예측 분포의 정규근사 ±1.28σ(z = Φ⁻¹(0.9))다.

⚠ 이 z 는 **명목** 경계이며 실측 커버리지가 명목값과 같다는 주장이 아니다. 사정률
잔차는 초과 첨도가 크고(뾰족한 중심 + 두꺼운 꼬리) 그 결과 실측 커버리지는 명목 80%
보다 넓게 나온다 — 실측값의 단일 출처는 캘리브레이션 리포트
(``scripts/backtest_yega_distribution.py``)이고, 여기에 수치를 적어 두면 데이터가 늘 때
조용히 낡는다. 두꺼운 꼬리 분포 도입은 별도 트랙이다.

소비자 전수(grep ``from app.ai.predictors.scenario_spec``, 테스트 제외):

- ``app/ai/predictors/distribution.py`` — 예정가 분포 엔진(Phase 1). 분포 중심·예측
  표준편차를 투찰율 축으로 환산하는 ``scale`` 인자를 쓴다.
- ``app/ai/predictors/award_rate_gbm.py`` — 낙찰률 GBM(Phase 2). 이미 투찰율 축이라
  ``scale`` 을 쓰지 않는다.
"""

from __future__ import annotations

from typing import Final

from app.ai.predictors.historical import clamp_bid_rate

# 후보 payload 한 건의 값 타입. 라벨(str)과 세 수치(float)뿐이라 ``Any`` 로 넓히지
# 않는다 — ``PredictionResult.bid_rate_candidates`` 쪽 계약이 아직 ``dict[str, Any]``
# 여도, **생산자**가 무엇을 싣는지는 여기서 좁게 말할 수 있다.
ScenarioCandidate = dict[str, float | str]

# 시나리오 라벨 → (구간 경계 부호, 후보 가중치). 가중치 배분은 historical predictor 와
# 같은 비율이라 운영자가 세 엔진의 후보를 나란히 읽을 수 있다.
CANDIDATE_SCENARIOS: Final[tuple[tuple[str, float, float], ...]] = (
    ("conservative", -1.0, 0.24),
    ("base", 0.0, 0.52),
    ("aggressive", 1.0, 0.24),
)

# 정규근사 구간 경계 z = Φ⁻¹(0.9).
SCENARIO_INTERVAL_Z: Final[float] = 1.2816

# payload 반올림 자리수 — 투찰율 4자리, 금액 2자리. 두 엔진이 같은 자리수를 써야
# 후보 payload 가 서로 비교 가능하다.
_BID_RATE_DIGITS: Final[int] = 4
_PRICE_DIGITS: Final[int] = 2


def scenario_bid_rates(*, center: float, std: float, scale: float = 1.0) -> tuple[float, ...]:
    """세 시나리오의 투찰율 — 중심과 정규근사 ±z·σ 경계를 clamp 해서 낸다.

    Args:
        center: 예측 분포의 중심.
        std: 예측 분포의 표준편차.
        scale: 중심·경계를 투찰율 축으로 옮기는 배율. 분포 엔진은 사정률 축의 분포를
            낙찰율/실현 사정률 비로 환산하므로 이 인자를 쓰고, 이미 투찰율 축인
            엔진은 기본값 ``1.0`` 을 그대로 둔다. 배율을 괄호 **밖**에 두는 형태
            (``scale * (center + ...)``)는 분포 엔진의 기존 산술과 부동소수점까지
            동일하다 — 분배법칙으로 펴면 마지막 비트가 달라져 반올림된 payload 가
            바뀔 수 있다.
    """
    return tuple(
        clamp_bid_rate(scale * (center + (sign * SCENARIO_INTERVAL_Z * std)))
        for _label, sign, _weight in CANDIDATE_SCENARIOS
    )


def build_scenario_candidates(
    *, center: float, std: float, budget: float, scale: float = 1.0
) -> list[ScenarioCandidate]:
    """``bid_rate_candidates`` payload 세 건(보수·기준·공격) 한 벌.

    기준 후보는 항상 가운데(인덱스 1)다 — 호출부가 ``candidates[1]`` 로 집는 계약이
    :data:`CANDIDATE_SCENARIOS` 의 순서에 걸려 있으므로, 그 순서는 선언이지 우연이 아니다.
    """
    return [
        {
            "label": label,
            "bid_rate": round(rate, _BID_RATE_DIGITS),
            "predicted_price": round(budget * rate, _PRICE_DIGITS),
            "confidence_weight": weight,
        }
        for (label, _sign, weight), rate in zip(
            CANDIDATE_SCENARIOS,
            scenario_bid_rates(center=center, std=std, scale=scale),
        )
    ]
