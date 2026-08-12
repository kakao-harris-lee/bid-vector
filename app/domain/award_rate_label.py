"""basis 명시 낙찰률 라벨 — 분자·분모가 무엇인지 값과 함께 실어 나른다.

왜 별도 라벨이 필요한가
-----------------------
기존 학습 라벨 ``HistoricalData.bid_rate`` 는 write 지점이 한 곳
(``app/services/koneps/persistence.py``)이지만 그 자리에 담기는 값의 **분모가 조건부로
갈린다**. 수집 투영(``app/services/koneps/scsbid.py``)이 이렇게 만든다::

    실 기초금액(reserve detail) 확보 → winning_amount ÷ base_amount  (기초금액-relative)
    확보 실패                        → KONEPS ``sucsfbidRate``        (예정가-relative)

두 갈래는 같은 컬럼에 같은 이름으로 앉아 구분이 남지 않는다. 그런데 서빙은 이 율을
**기초금액**에 곱한다(``app/ai/predictors/historical`` 의 ``round(budget * base_rate, 2)``
— ``budget`` 은 ``app/services/bid_base.resolve_notice_bid_base`` 가 낸 기초금액-basis
값). 예정가-relative 성분은 그 곱셈에서 ``1/사정률`` 배만큼 어긋난다.

혼재가 무작위였다면 학습기가 잡음으로 흡수했겠지만, 갈림을 결정하는 것이 **reserve
detail 수집 성공 여부**이고 그 성공률이 카테고리와 상관된다. 즉 오염 성분이 피처와
교락한다(실측 수치는 PR 본문 — 이 파일에 수치를 적어두면 데이터가 늘 때 조용히 낡는다).

이 모듈이 하는 일
-----------------
분모를 :func:`~app.domain.reliable_base.get_reliable_base` **한 벌**로 고른다(#199 오염
태그를 읽어 신뢰 기초금액을 내는 기존 접근자). 어느 경로로 골랐는지
(``denominator_source``)와 원본 행의 오염 태그(``base_amount_basis``)가 값과 함께 나간다
— 소비자가 basis 를 모른 채 곱하는 것이 이 저장소가 반복해서 겪은 회귀라, 값만 떼어 갈
수 없는 모양으로 싣는다.

**분모가 기초금액이라는 주장의 세기는 경로마다 다르다.** ``clean-base`` 와
``reserve-estimate`` 는 근거가 있는 기초금액이지만, ``base-fallback`` 은 오염 태그가 없거나
복구 추정치가 없어 저장된 ``base_amount`` 를 그대로 쓴 것이라 그 값이 기초금액이라는 증거가
없다(운영 코퍼스에서 성립 라벨의 절반 가까이가 이 경로다 — 수치는 PR 본문). 그래서 라벨을
소비하는 쪽은 ``denominator_source`` 로 걸러 쓸 수 있어야 하고, 이 필드를 버리고 값만 쓰면
이 라벨은 기존 라벨과 같은 종류의 혼재를 반복한다.

이 모듈이 하지 않는 일
----------------------
기존 라벨 해석 경로(``PredictionDatasetService._resolve_bid_rate`` 의 tier 우선순위)를
바꾸지 않는다. #195 가 발주처 밴드에 이미 E[사정률]을 곱하므로
(``app/domain/basis_conversion.convert_yega_band_to_base``), 라벨까지 같이 환산하면
사정률이 두 번 반영된다.

현재 소비자는 둘이다: 학습 시계열이 이 라벨을 **싣기만** 하고(``award_rate_label`` 키 —
읽는 쪽은 아직 없다), ``app/services/floor_shortfall.assessment_rate_from_opening`` 이
같은 금액비를 각자 계산하던 것을 이 커널로 위임한다(그쪽은 ``clean-base`` 만 받는다).

순수 함수(값 입력 → 값 출력, I/O 0). mypy strict 아일랜드.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.money import Basis
from app.domain.rate_normalization import is_plausible_rate_label
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.utils.numeric import optional_float


class AwardRateLabelStatus(str, Enum):
    """라벨이 성립했는가, 못 했으면 무엇이 없었는가 (감사 어휘).

    "라벨 없음"을 ``None`` 하나로 뭉개면 커버리지 손실의 원인을 사후에 가릴 수 없다.
    사유를 나눠 실어 소비자가 분모(왜 못 썼는가)를 집계할 수 있게 한다.
    """

    OK = "ok"                                  # 값 성립
    NO_WINNING_AMOUNT = "no-winning-amount"    # 낙찰가 없음/비양수 — 라벨의 분자가 없다
    NO_RELIABLE_BASE = "no-reliable-base"      # 양수 기초금액을 못 고름
    OUT_OF_RANGE = "out-of-range"              # 비는 났지만 유효 창 밖 = 적재 사고


@dataclass(frozen=True)
class AwardRateLabel:
    """낙찰가 ÷ 신뢰 기초금액 과 그 값을 만든 basis·출처.

    ``value`` 는 ``status is OK`` 일 때만 채워진다. 나머지 필드는 성립 여부와 무관하게
    항상 관측된 만큼 채워지므로, 실패한 라벨도 왜 실패했는지 집계할 수 있다.
    """

    value: float | None
    """낙찰가 ÷ 기초금액 (분수). ``status is not OK`` 면 ``None``."""

    status: AwardRateLabelStatus
    """성립 여부와 실패 사유."""

    denominator_value: float | None
    """실제로 분모에 쓰인 기초금액. 값이 있으면 ``value × 이 값 = 낙찰가`` 로 재현된다."""

    denominator_source: ReliableBaseSource
    """분모를 어느 경로로 골랐는가 (clean-base / reserve-estimate / base-fallback /
    unavailable). ``base-fallback`` 은 오염 태그가 없거나 복구 추정치가 없어 저장된
    ``base_amount`` 를 그대로 쓴 경우라, 그 값이 기초금액이라는 **증거는 없다**."""

    base_amount_basis: str | None
    """원본 행의 #199 오염 태그 원문(``clean`` / ``derived-yega`` / ...). 분류 전 행은
    ``None`` 이다 — clean 이라는 뜻이 아니라 판정된 적이 없다는 뜻이다."""

    @property
    def numerator_basis(self) -> Basis:
        """분자의 금액 basis — 항상 낙찰가(``TenderResult.winning_amount``)."""
        return Basis.WINNING_AMOUNT

    @property
    def denominator_basis(self) -> Basis:
        """분모가 **어느 축이어야 하는가** — 기초금액. 관측이 아니라 계약이다.

        ``clean-base`` 와 ``reserve-estimate`` 는 그 계약을 근거와 함께 만족한다(전자는
        정수 원화로 검증된 저장값, 후자는 복수예비가격 midpoint 로 복구한 기초금액).
        ``base-fallback`` 은 근거 없이 저장값을 쓴 것이라 실제로는 예정가-basis 오염일 수
        있다 — 그 구분은 ``denominator_source`` 가 지고, 이 속성은 지지 않는다.
        """
        return Basis.BASE_AMOUNT

    def as_payload(self) -> dict[str, float | str | None]:
        """직렬화용 평문 dict — enum 은 값 문자열로 편다.

        데이터셋 직렬화가 이 블록을 통째로 싣는다. 값과 basis 를 한 블록에 묶어 두면
        소비자가 ``value`` 만 떼어 곱하면서 basis 를 잃는 경로가 구조적으로 눈에 띈다.
        """
        return {
            "value": self.value,
            "status": self.status.value,
            "numerator_basis": self.numerator_basis.value,
            "denominator_basis": self.denominator_basis.value,
            "denominator_value": self.denominator_value,
            "denominator_source": self.denominator_source.value,
            "base_amount_basis": self.base_amount_basis,
        }


def _resolve_value(
    amount: float, denominator: float | None
) -> tuple[float | None, AwardRateLabelStatus]:
    """분자·분모에서 값과 상태를 정한다 — 첫 실패가 이긴다(순수).

    순서는 분자 → 분모 → 유효 창이다. 분자가 없으면 라벨의 주어(어떤 낙찰인가)가 없으므로
    분모를 볼 것도 없다. NaN 분자는 ``<= 0`` 비교를 통과하지만 유효 창에서 탈락한다 —
    기존 소비자(``floor_shortfall.assessment_rate_from_opening``)와 같은 귀결이다.
    """
    if amount <= 0:
        return None, AwardRateLabelStatus.NO_WINNING_AMOUNT
    if denominator is None:
        return None, AwardRateLabelStatus.NO_RELIABLE_BASE
    rate = amount / denominator
    if not is_plausible_rate_label(rate):
        return None, AwardRateLabelStatus.OUT_OF_RANGE
    return rate, AwardRateLabelStatus.OK


def build_award_rate_label(
    *,
    winning_amount: float | None,
    base_amount: float | None,
    base_amount_basis: str | None,
    base_amount_estimated: float | None,
) -> AwardRateLabel:
    """한 개찰의 basis 명시 낙찰률 라벨을 만든다 (순수).

    Args:
        winning_amount: 실제 낙찰 금액(``TenderResult.winning_amount``).
        base_amount: 저장된 기초금액(``HistoricalData.base_amount``) — 오염돼 있을 수 있다.
        base_amount_basis: 그 값의 #199 오염 태그.
        base_amount_estimated: 복수예비가격 midpoint 로 복구한 기초금액 추정치.

    Returns:
        :class:`AwardRateLabel`. 실패해도 예외를 던지지 않고 사유를 실은 라벨을 낸다.

    ``winning_amount`` coercion 은 기존 소비자가 쓰던 프리미티브
    (``optional_float(...) or 0.0``)를 그대로 옮겼다 — 표본이 갈리지 않게 하려는 것이다.
    """
    reliable = get_reliable_base(
        base_amount=base_amount,
        basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
    )
    # ``get_reliable_base`` 는 양수 유한값만 싣는다(그 안의 ``_positive_or_none``). 그래서
    # 양수 검사를 다시 하지 않고, ``None`` 이 곧 "양수 base 없음"이다.
    denominator = None if reliable.value is None else float(reliable.value)
    value, status = _resolve_value(optional_float(winning_amount) or 0.0, denominator)
    return AwardRateLabel(
        value=value,
        status=status,
        denominator_value=denominator,
        denominator_source=reliable.source,
        base_amount_basis=base_amount_basis,
    )
