"""집계 원시 연산(절대 오차율·산술평균) 단일 출처.

두 수식이 기능별로 독립 재구현돼 있었다:

* **절대 오차율** ``abs(value - reference) / reference`` — dashboard/feedback/
  reporting/paper_bidding/holdouts 등 6곳 이상에 복붙(그중 dashboard·feedback 는
  바이트 동일).
* **산술평균** ``round(sum(values) / len(values), digits)`` — 8개 ``_average``
  메서드 + synthetic_experiment 인라인식.

이 모듈은 그 두 수식을 **단일 출처**로 소유한다. 콜사이트마다 정당하게 다른 부분
(반올림 자리수, 0-분모 폴백을 None 으로 둘지 0.0 으로 둘지, 입력 coercion)은
"중복처럼 보이는 상이"이므로 억지로 병합하지 않고 파라미터·호출부에 남긴다 —
오직 divergent 하지 않은 **핵심 수식**만 여기로 위임한다.

* 반올림 자리수는 4·6 으로 갈리므로 :func:`average` 는 ``digits`` 를 **필수
  키워드**로 받아 각 콜사이트가 자릿수를 명시하게 한다(암묵 drift 차단).
* 0-분모 처리는 :func:`error_rate` 가 ``None`` 을 반환한다. 0.0 폴백이 필요한
  콜사이트(paper_bidding)는 ``error_rate(...) or 0.0`` 으로 흡수한다.

순수 함수(I/O 0). ``app.domain.money`` 등과 같은 strict 타이핑 아일랜드다.
"""

from __future__ import annotations

from typing import Iterable


def error_rate(value: float | None, reference: float) -> float | None:
    """절대 오차율 ``abs(value - reference) / reference``.

    ``value`` 가 None 이거나 ``reference`` 가 0 이하이면 0-분모 가드로 ``None`` 을
    반환한다(dashboard/feedback 의 ``candidate is None or winning <= 0`` 규칙과 동일).
    """
    if value is None or reference <= 0:
        return None
    return abs(value - reference) / reference


def average(values: Iterable[float], *, digits: int) -> float | None:
    """산술평균을 ``digits`` 자리로 반올림해 반환. 빈 입력이면 ``None``.

    빌트인 ``sum`` 을 그대로 써 기존 8개 ``_average`` 구현과 비트 동일하다.
    ``digits`` 는 필수(자릿수가 콜사이트마다 4·6 으로 갈려 암묵 기본값을 두지 않음).
    """
    values_list = list(values)
    if not values_list:
        return None
    return round(sum(values_list) / len(values_list), digits)
