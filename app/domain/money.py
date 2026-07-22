"""금액 basis(기준) 타입 — 반복 base/basis 대입 버그(#162/#195/#220) 재발 방지.

이 도메인의 '금액'은 basis(무엇을 기준으로 한 금액인가)가 다르면 서로 다른 값이다.
예정가(planned_price)를 기초금액(base_amount) 자리에 넣는 것은 float 레벨에서는
유효한 대입이지만 의미적으로는 틀린 값이고, 그것이 반복 회귀의 근본 원인이었다
(#162 과세 공고 저평가, #195 밴드 base 오정합, #220).

이 모듈은 신규/경계 코드에서 금액의 basis를 **명시**하기 위한 얇은 타입만 제공한다.
런타임 부담 0(NewType은 런타임에 그냥 float), IO 0(순수). 전역 금액을 이 타입으로
감싸지 않는다(과설계 금지) — 예정가/기초금액을 서로 혼동하면 안 되는 경계 시그니처
에서만 basis를 구분하는 용도다.
"""

from __future__ import annotations

from enum import Enum
from typing import NewType


class Basis(str, Enum):
    """금액이 어떤 기준(basis)으로 표현됐는지 — 교차 대입 금지의 단일 어휘.

    값은 저장소에서 이미 쓰는 문자열 리터럴(price_predictions·HistoricalData 등)과
    맞춘다. ``str`` 혼합 Enum이라 직렬화/비교 시 값 문자열을 그대로 쓸 수 있다.
    """

    PLANNED_PRICE = "planned_price"      # 예정가: 발주처가 산정한 예정가격(사정률 적용 후)
    BASE_AMOUNT = "base_amount"          # 기초금액(=사업금액): 투찰율의 곱셈 base (#162)
    WINNING_AMOUNT = "winning_amount"    # 낙찰가: 실제 낙찰된 투찰 금액
    BUDGET_ESTIMATE = "budget_estimate"  # 추정가격: 부가세 포함 추정 총액(법정 하한 구간 기준)


# 예정가/기초금액을 시그니처에서 타입으로 구분한다. mypy가 두 basis의 교차 대입을
# 에러로 잡고, 런타임에는 그냥 float라 성능/직렬화 부담이 없다(NewType). 감싸는 것은
# 명시가 필요한 경계 값에 한정한다(과설계 금지).
BaseAmount = NewType("BaseAmount", float)  # 기초금액 basis 금액(사업금액)
YegaAmount = NewType("YegaAmount", float)  # 예정가 basis 금액(예정가격)
