"""추천 투찰가가 낙찰하한에 미달했을 **과거 빈도** 추정 — 순수 판정 커널.

왜 필요한가
-----------
추천 투찰가는 **기초금액** 기준 율(r)로 산정되는데(#162), 실격을 가르는 낙찰하한가는
**추첨된 예정가격** 기준(예정가 × 하한율 f)이다. 예정가는 복수예비가격 추첨으로
기초금액 대비 몇 %p 씩 흔들리므로(사정률 a = 예정가/기초금액), 같은 r 이라도 추첨
결과에 따라 하한 위/아래가 갈린다::

    투찰가 = r × 기초금액
    하한가 = f × 예정가 = f × a × 기초금액
    하한 미달 ⟺ r × 기초금액 < f × a × 기초금액 ⟺ a > r / f

즉 **임계 사정률 a\\* = r / f** 하나가 그 공고의 여유를 전부 요약한다. r = 0.8811,
f = 0.88 이면 a\\* = 1.00125 로, 사정률이 +0.125% 만 넘게 뽑혀도 실격이다. 이 여유가
어디에도 표시되지 않아 실투찰이 같은 함정을 반복해서 밟았다.

무엇을 계산하는가 (정직 명세 §2)
--------------------------------
이 모듈은 **확률을 계산하지 않는다.** 과거 개찰에서 관측된 사정률 표본 중 a\\* 를
초과한 표본의 **비율(빈도)** 만 센다. 결과의 뜻은 "이 추천율로 과거 표본 공고들에
투찰했다면 하한 미달로 실격 판정됐을 표본 비율"이며, 이번 공고의 추첨 결과에 대한
확률 주장이 아니다. 그래서 이름·필드 어디에도 ``probability`` 를 쓰지 않고
``frequency`` 로만 표기한다. 표본 수와 임계 사정률을 결과에 동봉해 그 값이 무엇으로
나왔는지 호출부가 그대로 감사할 수 있게 한다.

표본이 최소치에 못 미치면 ``0.0`` 이 아니라 ``None`` 이다 — "위험 없음"과 "측정
불가"를 같은 값으로 내보내면 침묵이 안전으로 읽힌다.

순수 함수(I/O 0)다. 표본 수집과 오염 필터는 DB 경계인
:mod:`app.services.floor_shortfall` 가 담당한다(§4.7 순수 코어 / 얇은 경계).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.domain.aggregates import rate

# ── 선언 상수(§4.5.1 매직값 금지) ─────────────────────────────────────────────
# 빈도를 발표하기 위한 최소 표본 수. 이항 비율 추정의 표준오차는 최악(p=0.5)에서도
# sqrt(0.25/150) ≈ 4.1%p 라, 이 규모면 "37% vs 5%" 같은 의사결정 수준의 구분은
# 견딘다. 이보다 적으면 빈도가 표본 잡음과 구분되지 않으므로 판정을 포기한다.
MIN_ASSESSMENT_SAMPLES: Final[int] = 150

# 사정률(예정가/기초금액)의 물리적 개연 범위. KONEPS 복수예비가격은 기초금액을
# 대략 ±2~3% 로 둘러싸도록 생성되므로 실제 추첨 결과가 이 범위를 벗어날 수 없다.
# 범위 밖 값은 추첨 결과가 아니라 수집/파싱 오류이므로 표본에서 제외한다.
# ⚠ app/core/constants.ASSESSMENT_RATE_PLAUSIBLE_*(0.8~1.2)와 이름이 비슷하지만
# **다른 밴드·다른 목적**(그쪽=관측 필터)이다 — §4.5-8 근거 통합 금지(리뷰 N4):
# 합치면 shortfall 분모가 넓어져 투찰서의 하한 미달 빈도가 낙관적으로 표시된다.
ASSESSMENT_RATE_MIN: Final[float] = 0.90
ASSESSMENT_RATE_MAX: Final[float] = 1.10

# 빈도 반올림 자리수. 0.0001 = 0.01%p 해상도로, 최소 표본 수(150)가 주는 실제
# 분해능(1/150 ≈ 0.0067)보다 충분히 곱다.
FREQUENCY_DIGITS: Final[int] = 4

# 임계 사정률 반올림 자리수. 1.00125 같은 값의 유효 자리를 보존한다.
CRITICAL_RATE_DIGITS: Final[int] = 6


@dataclass(frozen=True)
class FloorShortfallFrequency:
    """하한 미달 빈도 추정 결과 — 값과 그 산출 근거를 함께 들고 다닌다.

    ``shortfall_frequency`` 는 확률이 아니라 **과거 표본 비율**이다(모듈 docstring).
    ``sample_count`` / ``critical_assessment_rate`` 가 동봉되므로 호출부는 이 값이
    몇 개의 표본에서 어떤 임계로 나왔는지 재현·감사할 수 있다.
    """

    critical_assessment_rate: float
    """임계 사정률 a\\* = 추천율 / 하한율. 사정률이 이 값을 넘으면 하한 미달."""

    shortfall_frequency: float
    """표본 중 a\\* 를 초과한 비율(0-1). 실제 실격 확률이 아니다."""

    shortfall_sample_count: int
    """a\\* 를 초과한 표본 수(분자)."""

    sample_count: int
    """빈도 산출에 실제로 쓰인 개연 범위 내 표본 수(분모)."""


def is_plausible_assessment_rate(value: float | None) -> bool:
    """사정률 표본이 물리적 개연 범위 안에 있는가(순수 술어).

    ``None`` 과 NaN 은 비교가 모두 False 로 떨어져 자연히 제외된다. 범위 밖 값은
    추첨 결과가 아니라 데이터 오류이므로 빈도 분모에서 뺀다.
    """
    if value is None:
        return False
    return ASSESSMENT_RATE_MIN <= value <= ASSESSMENT_RATE_MAX


def resolve_critical_assessment_rate(
    recommended_rate: float, floor_rate: float
) -> float | None:
    """임계 사정률 a\\* = 추천 투찰율 ÷ 낙찰하한율.

    Args:
        recommended_rate: 추천 투찰율(기초금액 기준 분수, 예: 0.8811).
        floor_rate: 낙찰하한율(예정가격 기준 분수, 예: 0.88).

    Returns:
        사정률이 이 값을 **초과**하면 추천가가 하한 미달이 되는 경계. 두 입력 중
        하나라도 양수가 아니면 ``None``(경계를 정의할 수 없음).

    두 율의 basis 가 서로 다른 것이 이 계산의 핵심이다 — 분자는 기초금액 기준,
    분모는 예정가 기준이라 그 비가 곧 "허용 가능한 예정가/기초금액 배율"이 된다.
    """
    if recommended_rate <= 0 or floor_rate <= 0:
        return None
    return round(recommended_rate / floor_rate, CRITICAL_RATE_DIGITS)


def floor_shortfall_frequency(
    recommended_rate: float,
    floor_rate: float,
    assessment_samples: Sequence[float],
    *,
    min_samples: int = MIN_ASSESSMENT_SAMPLES,
) -> FloorShortfallFrequency | None:
    """과거 사정률 표본에서 추천가가 하한 미달이 됐을 **빈도**를 센다(순수).

    Args:
        recommended_rate: 추천 투찰율(기초금액 기준 분수).
        floor_rate: 낙찰하한율(예정가격 기준 분수).
        assessment_samples: 과거 개찰에서 관측된 사정률(예정가/기초금액) 표본.
            개연 범위(:func:`is_plausible_assessment_rate`) 밖 값은 분모에서 빠진다.
        min_samples: 빈도를 발표하기 위한 최소 표본 수.

    Returns:
        :class:`FloorShortfallFrequency`, 또는 다음이면 ``None``:
          - 추천율/하한율이 양수가 아님(임계 사정률 정의 불가)
          - 개연 범위 내 표본이 ``min_samples`` 미만(측정 불가)

        ``None`` 은 "위험 없음"이 아니라 **"판정 불가"** 다. 호출부는 이 둘을 절대
        같은 표시로 합치면 안 된다(정직 명세 §2).

    경계 판정은 **초과(>)** 다: 사정률이 정확히 a\\* 면 투찰가 == 하한가라 적격심사
    하한(미만이 실격)을 만족하므로 미달로 세지 않는다.
    """
    critical_rate = resolve_critical_assessment_rate(recommended_rate, floor_rate)
    if critical_rate is None:
        return None

    usable = [
        float(sample)
        for sample in assessment_samples
        if is_plausible_assessment_rate(sample)
    ]
    if len(usable) < min_samples:
        return None

    shortfall_count = sum(1 for sample in usable if sample > critical_rate)
    return FloorShortfallFrequency(
        critical_assessment_rate=critical_rate,
        shortfall_frequency=rate(
            shortfall_count, len(usable), digits=FREQUENCY_DIGITS
        ),
        shortfall_sample_count=shortfall_count,
        sample_count=len(usable),
    )
