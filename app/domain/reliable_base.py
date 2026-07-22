"""basis-aware 기초금액 선택 — ``base_amount_basis``(#199) 태그의 첫 production 소비자.

#199가 각 ``HistoricalData.base_amount`` 에 provenance(``clean`` / ``derived-yega`` /
``derived-vat`` / ``suspect-fractional``)를 태깅했지만, 이를 실제로 읽는 소비자가 0개라
예측 base 해석이 오염된 base(예: ``win ÷ winning_rate`` 역산 = **예정가-basis**)를 그대로
읽을 수 있는 갭이 있었다. 이 모듈은 그 태그를 보고 **신뢰 가능한 기초금액**을 고른다.

정직한 라이브 영향(P2 진단 실측): OPEN 공고의 ``base_amount`` 오염은 사실상 0건이다
(derived-yega 오염은 전부 개찰 후 settled 스냅샷에서 발생). 따라서 이 접근자의 현재
라이브 영향은 ~0(open 공고 추천 base 불변)이며, 값은 (a) #199 태그를 실제 소비해 갭을
방어적으로 닫고, (b) 향후 오염이 라이브에 닿아도 reserve 복구 추정치로 방어하며,
(c) 재수집으로 settled된 희귀 open 공고 edge에 대비하는 데 있다. 이 모듈은 실투찰 패찰
원인을 고치지 않는다(그건 밴드 예정가-편향 캘리브레이션 이슈로 별개다).

순수 함수(값 입력 → 값 출력, I/O 0)다. DB 경계는 ``app/services/bid_base.py`` 가 담당하고,
이 모듈은 basis 규칙만 해석한다(§4.7 순수 코어/얇은 경계 분리). basis 어휘는
``base_amount_basis`` 단일 출처의 상수를 재사용하고(새 리터럴 금지), 금액 basis 타입은
Phase 1a의 ``app/domain/money`` 를 소비한다. mypy strict 아일랜드.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.money import BaseAmount, Basis
from app.services.base_amount_basis import ALL_BASES, BASIS_CLEAN

# non-clean provenance 버킷(derived-yega / derived-vat / suspect-fractional): 이
# basis의 ``base_amount`` 는 예정가-basis 등으로 오염됐을 수 있어 신뢰 불가다.
# ``base_amount_basis`` 단일 출처의 ``ALL_BASES`` 에서 clean만 제외해 도출한다 —
# 새 문자열 리터럴을 이 모듈에 두지 않는다(§4.5 매직값 금지).
_NON_CLEAN_BASES: frozenset[str] = frozenset(b for b in ALL_BASES if b != BASIS_CLEAN)


class ReliableBaseSource(str, Enum):
    """어느 값을 왜 골랐는가 — 감사(provenance) 어휘.

    호출부는 이 값으로 base가 실제로 대체됐는지(``RESERVE_ESTIMATE``)를 구분해 로깅/감사할
    수 있다. ``CLEAN_BASE`` / ``BASE_FALLBACK`` 은 기존 동작(저장된 base_amount)과 동일하다.
    """

    CLEAN_BASE = "clean-base"              # basis == clean → 저장된 base_amount(신뢰)
    RESERVE_ESTIMATE = "reserve-estimate"  # non-clean basis → 복수예비가격 복구 추정치
    BASE_FALLBACK = "base-fallback"        # basis 미상/non-clean+추정 없음 → base_amount 폴백
    UNAVAILABLE = "unavailable"            # 양수 base 없음 → 호출부가 상위 추정가격 폴백


@dataclass(frozen=True)
class ReliableBase:
    """basis-aware 기초금액 선택 결과 — 값(기초금액-basis)과 근거(source)."""

    value: BaseAmount | None
    source: ReliableBaseSource

    @property
    def basis(self) -> Basis:
        """선택된 값의 금액 basis — 항상 기초금액(BASE_AMOUNT), 예정가 아님(#162).

        핵심: ``RESERVE_ESTIMATE`` 로 고르는 ``base_amount_estimated`` 는 복수예비가격
        midpoint로 복구한 **기초금액**이고, 우리가 거부하는 derived-yega ``base_amount`` 는
        실은 **예정가-basis** 오염이다. 이 접근자는 어느 경로든 기초금액-basis 값만 낸다.
        """
        return Basis.BASE_AMOUNT


def _positive_or_none(value: float | None) -> float | None:
    """양수 유한 실수만 통과, 그 외(None/0/음수/비수치/NaN)는 None."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result if result > 0 else None


def get_reliable_base(
    base_amount: float | None,
    basis: str | None,
    base_amount_estimated: float | None,
) -> ReliableBase:
    """오염(basis)을 보고 신뢰 가능한 기초금액을 고른다(순수, 선언적 우선순위).

    우선순위:
      1. basis == ``clean`` & base_amount>0 → **base_amount**(신뢰, 기존과 동일).
      2. basis가 non-clean(derived-yega/derived-vat/suspect) & base_amount_estimated>0
         → **base_amount_estimated**(복수예비가격 midpoint 복구 기초금액 — 개찰 시점
         값이라 시간 누수 아님).
      3. 그 외 base_amount>0 → **base_amount** 폴백(basis 미상/미분류 open 공고, 또는
         non-clean인데 reserve 복구 실패 — 기존 동작 보존).
      4. 양수 base 없음 → ``UNAVAILABLE``(호출부가 상위 추정가격으로 폴백).

    clean 레코드와 basis 미분류(``basis is None``) 레코드는 항상 base_amount를 그대로
    돌려주므로 기존 예측이 바이트 동일하다(회귀 0). 값이 바뀌는 유일한 경우는 basis가
    **명시적 non-clean**이고 복구 추정치가 양수일 때뿐이다(2번 경로).
    """
    base = _positive_or_none(base_amount)
    estimated = _positive_or_none(base_amount_estimated)

    if basis == BASIS_CLEAN and base is not None:
        return ReliableBase(BaseAmount(base), ReliableBaseSource.CLEAN_BASE)

    if basis in _NON_CLEAN_BASES and estimated is not None:
        return ReliableBase(BaseAmount(estimated), ReliableBaseSource.RESERVE_ESTIMATE)

    if base is not None:
        return ReliableBase(BaseAmount(base), ReliableBaseSource.BASE_FALLBACK)

    return ReliableBase(None, ReliableBaseSource.UNAVAILABLE)
