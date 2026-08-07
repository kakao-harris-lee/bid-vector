"""공고가 게시한 낙찰하한율의 **신뢰(개연) 밴드** 단일 출처.

왜 여기 있는가
--------------
이 밴드는 원래 :mod:`app.ai.floor_applicability` 에 있었고 홀드아웃 품질 판정만
소비했다. #356(V3 budget_cap 게이트)이 published 하한을 **라이브 가격 경로의 게이트
입력**으로 승격시키면서 소비자가 세 층으로 넓어졌다:

* 수집 — ``app/services/koneps/persistence`` · ``scripts/backfill_award_floor_rate``
  (``Project.award_floor_rate`` 로 write 하는 전부)
* 라이브 가격 — ``app/services/bid_base.resolve_notice_legal_floor_bid_rate``
* 분석·표시 — ``app/ai/holdout_quality`` · ``app/services/notice_floor_shortfall``

수집 층은 ``app/ai`` 를 import 하지 않는다(수집 → ML 은 역방향 의존이다). 규칙이 두
벌이 되면 수집이 거른 값과 분석이 거른 값이 갈리므로(§4.5.8), 밴드는 세 층이 모두
의존할 수 있는 ``app/domain`` 으로 내려왔다. 순수 함수(I/O 0)이고
``app.domain.rate_normalization`` 과 같은 무의존 아일랜드다.

무엇을 판정하는가
-----------------
KONEPS ``sucsfbidLwltRate`` 원문은 우리가 만든 값이 아니라 **발주기관이 게시한 값을
그대로 전사**한 것이다. 전사 자체는 옳지만, 원문에 하한으로 성립하지 않는 값이 섞여
들어온다(라이브 실측: 1.00000 이 47건). 이 모듈은 그 값을 고치지 않는다 — 하한으로
**해석할 수 있는지**만 판정하고, 밖이면 "하한 미보고"와 같은 자리(``None``)로 떨어뜨린다.

스케일 정규화(percent 87.5 ↔ fraction 0.875)는 이 모듈의 일이 아니다.
:func:`app.domain.rate_normalization.to_bid_rate_fraction` 이 먼저 끝내 놓은 fraction 을
받는다 — 그 함수 자체에 상한을 심으면 1.0 이 적법한 다른 율(낙찰률 · 투찰률)까지
같이 죽는다.
"""

from __future__ import annotations

from typing import Final

# 하한율 1.00000 은 "예정가 전액 이상 투찰"을 뜻해 낙찰하한의 의미가 성립하지 않는다
# (라이브 실측 47건). 상한 0.995 는 관측된 최대 실값 0.89995 위로 충분한 여유다.
PUBLISHED_FLOOR_MAX_PLAUSIBLE: Final[float] = 0.995
# 하한 0.30 은 백분율/분수 스케일 오적재(예: 0.88 대신 0.0088)를 걸러내는 선이다.
# 0.5 로 올리지 않는 이유: 라이브에 0.47995 가 3건 있고 .995 로 끝나는 표기 형태가
# 다른 실값(0.87745/0.89745)과 같아 진짜 게시값으로 보인다. 이 값을 버리고 era-tier
# (0.89745)로 폴백하면 오히려 새 오탐을 만든다.
PUBLISHED_FLOOR_MIN_PLAUSIBLE: Final[float] = 0.30


def is_published_floor_plausible(rate: float | None) -> bool:
    """공고 게시 낙찰하한율이 하한으로 해석할 수 있는 개연 범위 안인가(경계 포함).

    범위 밖 값은 **고치지 않고** 하한 해석에서만 제외한다. 제외했다는 사실은 호출부가
    남긴다(분석 경로는 ``published_floor_implausible`` 로 리포트에, 수집 경로는 로그·
    카운터로) — 침묵 스킵 금지.
    """
    if rate is None:
        return False
    return PUBLISHED_FLOOR_MIN_PLAUSIBLE <= float(rate) <= PUBLISHED_FLOOR_MAX_PLAUSIBLE


def plausible_published_floor_rate(rate: float | None) -> float | None:
    """개연 범위 안이면 값 그대로, 밖이면 ``None``.

    :func:`is_published_floor_plausible` 위의 얇은 명명 래퍼다. write 게이트(수집)와
    read 게이트(라이브 가격)가 둘 다 원하는 모양이 "버릴지 말지"가 아니라 "이 자리에
    무엇을 둘지"라서, 호출부마다 삼항 연산을 반복하지 않게 한 벌만 둔다.
    """
    return rate if is_published_floor_plausible(rate) else None
