"""수집 item 추정가격의 **출처 판정** — 어느 축에서 값이 왔는가.

왜 여기 있는가
--------------
어휘는 :mod:`app.core.constants` 가 선언하고, 그 어휘를 **해석**하는 두 지점이 층이 다르다:

* 생산자(``app/services/koneps/openapi.py``) — 해석된 금액을 보고 라벨을 붙인다(여기).
* write 가드(``app/services/koneps/budget_fields.py``) — 라벨을 보고 덮을지 정한다.

두 지점이 각자 판정을 들고 있으면 "무엇을 게시값으로 볼 것인가"가 갈린다(§4.5-8). 그래서
판정은 이 순수 함수 한 벌이고, 수집 층이 ORM 도 dict 경계도 끌어오지 않게 ``app/domain``
아일랜드에 둔다(``published_floor_rate`` 와 같은 모양 — I/O 0, 의존 0).

무엇을 판정하는가
-----------------
KONEPS 공고 행은 추정가격 자리를 **세 축**으로 채울 수 있고, 그 축이 곧 값의 권위다.

1. 공고가 추정가격으로 게시한 키(``presmptPrce``/``presmptAmt``) — 권위값(``notice``).
2. 그게 없을 때 쓰는 예산 키(``asignBdgtAmt``/``bdgtAmt``) — 게시값이지만 **추정가격이
   아니다**. 배정예산은 상한 성격이라 추정가격 이상이고, 이 값에 권위를 주면 패스마다 해석
   키가 달라질 때 분모가 위로 떠 오염 판정(#358 suspect-ratio)이 clean 으로 되돌아간다.
3. 추정가격 축이 통째로 빈 행 — item 은 기초금액 사본을 싣는다(비 1.0 자기충족).

해석 **순서**는 ``koneps.field_contract_spec`` 의 키 그룹이 단일 출처로 갖고, 이 모듈은 그
순서대로 해석된 결과만 받는다.
"""

from __future__ import annotations

from app.core.constants import (
    ESTIMATE_SOURCE_BASE_FALLBACK,
    ESTIMATE_SOURCE_BUDGET_FALLBACK,
    ESTIMATE_SOURCE_NOTICE,
    EstimatedAmountSource,
)


def estimate_source(
    notice_estimate: float | None, resolved_estimate: float | None
) -> EstimatedAmountSource:
    """게시 추정가격 > 예산 폴백 > 기초금액 사본 — 값이 실린 첫 축이 출처다.

    ``notice_estimate`` 는 추정가격 키만으로 해석한 값이고, ``resolved_estimate`` 는 예산
    폴백까지 포함한 축 전체의 해석값이다. 둘 다 0/None 이면 item 의 추정가격 자리에는
    기초금액이 복사된다(생산자의 ``estimated_amount or base_amount``).
    """
    if notice_estimate:
        return ESTIMATE_SOURCE_NOTICE
    if resolved_estimate:
        return ESTIMATE_SOURCE_BUDGET_FALLBACK
    return ESTIMATE_SOURCE_BASE_FALLBACK
