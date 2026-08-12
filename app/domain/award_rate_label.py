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

**분모가 기초금액이라는 주장의 세기는 경로마다 다르고, 그 차이가 직렬화된다.**
``clean-base`` 와 ``reserve-estimate`` 는 근거가 있는 기초금액이지만
(:data:`EVIDENCED_DENOMINATOR_SOURCES`), ``base-fallback`` 은 오염 태그가 없거나 복구
추정치가 없어 저장된 ``base_amount`` 를 그대로 쓴 것이라 그 값이 기초금액이라는 증거가
없다(운영 코퍼스에서 값이 나는 라벨의 절반 가까이가 이 경로다 — 수치는 PR 본문).

그래서 근거 없는 분모는 **상태 자체를 다르게** 낸다: ``ok`` 가 아니라
``ok-unverified-base`` 이고, ``denominator_basis`` 는 ``None`` 이다(축을 주장할 근거가
없으므로 축을 말하지 않는다). 소비자가 가장 자연스럽게 쓰는 필터인 ``status == "ok"`` 로
타깃을 고르면 근거 없는 분모가 **자동으로 빠진다** — 이 조건을 코드가 아니라 payload 에
심어야 하는 이유는, 그러지 않으면 이 모듈이 기존 ``bid_rate`` 의 결함으로 진단한 바로 그
혼재(카테고리와 교락한 분모 혼입)를 새 라벨이 그대로 재생산하기 때문이다.

``app.domain.reliable_base.ReliableBase.basis`` 도 입력과 무관하게 상수
``Basis.BASE_AMOUNT`` 를 내지만 그 값은 **직렬화되지 않는다**(그 접근자를 쓰는 쪽은 같은
프로세스 안에서 ``source`` 를 함께 본다). 이 PR 이 그 패턴을 **학습 데이터셋 payload 로
승격**시켰기 때문에, 상수 단언을 그대로 옮기면 프로세스 밖 소비자에게는 hedge 가 사라진
단언만 남는다. 그것이 여기서만 조건부로 낸 이유다.

이 모듈이 하지 않는 일
----------------------
기존 라벨 해석 경로(``PredictionDatasetService._resolve_bid_rate`` 의 tier 우선순위)를
바꾸지 않는다. #195 가 발주처 밴드에 이미 E[사정률]을 곱하므로
(``app/domain/basis_conversion.convert_yega_band_to_base``), 라벨까지 같이 환산하면
사정률이 두 번 반영된다.

현재 importer 전수(grep ``from app.domain.award_rate_label``, 테스트 제외):

- ``app/services/prediction_label_basis.py`` — 학습 시계열 어댑터. ``prediction_dataset``
  이 이 라벨을 ``award_rate_label`` 키로 **싣기만** 한다(읽는 쪽은 아직 없다).
- ``app/services/floor_shortfall.py`` — 같은 금액비를 각자 계산하던 것을 이 커널에 위임
  (그쪽은 ``clean-base`` 만 받는다 — ``reserve-estimate`` 도 거부하므로 이 모듈의
  ``EVIDENCED_DENOMINATOR_SOURCES`` 보다 좁다).
- ``scripts/measure_award_rate_label_coverage.py`` — 읽기 전용 계측(프로덕션 경로 아님).

목록은 grep 전수로만 쓴다(#362 리뷰 N2 의 교훈 — 기억으로 열거하면 빠진다).

순수 함수(값 입력 → 값 출력, I/O 0). mypy strict 아일랜드.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.money import Basis
from app.domain.rate_normalization import is_plausible_rate_label
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.utils.numeric import optional_float

# 분모가 기초금액이라는 **근거**가 있는 출처. 선언으로 두는 이유는 이 집합 하나가
# ``status`` 의 ok/ok-unverified-base 갈림과 ``denominator_basis`` 의 유무를 **동시에**
# 결정하기 때문이다 — 두 곳이 각자 판정하면 payload 안에서 서로 모순될 수 있다.
#   clean-base       정수 원화로 검증된 저장 base (#199 clean 태그)
#   reserve-estimate 복수예비가격 midpoint 로 복구한 기초금액
# ``base-fallback`` 은 여기 없다: 태그가 없거나(판정된 적 없음) non-clean 인데 복구
# 추정치도 없어 저장값을 그대로 쓴 경로라, 그 값이 예정가-basis 오염일 수 있다.
EVIDENCED_DENOMINATOR_SOURCES: frozenset[ReliableBaseSource] = frozenset(
    {ReliableBaseSource.CLEAN_BASE, ReliableBaseSource.RESERVE_ESTIMATE}
)


class AwardRateLabelStatus(str, Enum):
    """라벨이 성립했는가, 못 했으면(또는 덜 성립했으면) 무엇이 없었는가 (감사 어휘).

    "라벨 없음"을 ``None`` 하나로 뭉개면 커버리지 손실의 원인을 사후에 가릴 수 없다.
    사유를 나눠 실어 소비자가 분모(왜 못 썼는가)를 집계할 수 있게 한다.

    ``OK`` 와 ``OK_UNVERIFIED_BASE`` 는 **둘 다 값이 난다**. 가르는 것은 값의 존재가 아니라
    분모의 근거이며, 학습 타깃 선택은 ``OK`` 만 받는 것이 기본값이어야 한다.
    """

    OK = "ok"                                  # 값 성립 + 분모에 근거 있음
    OK_UNVERIFIED_BASE = "ok-unverified-base"  # 값은 났지만 분모가 base-fallback(근거 없음)
    NO_WINNING_AMOUNT = "no-winning-amount"    # 낙찰가 없음/비양수 — 라벨의 분자가 없다
    NO_RELIABLE_BASE = "no-reliable-base"      # 양수 기초금액을 못 고름
    OUT_OF_RANGE = "out-of-range"              # 비는 났지만 유효 창 밖 = 적재 사고


@dataclass(frozen=True)
class AwardRateLabel:
    """낙찰가 ÷ 신뢰 기초금액 과 그 값을 만든 basis·출처.

    ``value`` 는 ``status`` 가 ``OK`` 또는 ``OK_UNVERIFIED_BASE`` 일 때 채워진다. 나머지
    필드는 성립 여부와 무관하게 관측된 만큼 채워지므로, 실패한 라벨도 왜 실패했는지 집계할
    수 있다.
    """

    value: float | None
    """낙찰가 ÷ 분모 (분수). 값이 났어도 분모에 근거가 없을 수 있다 — ``status`` 를 볼 것."""

    status: AwardRateLabelStatus
    """성립 여부, 분모 근거 유무, 실패 사유."""

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
    def denominator_basis(self) -> Basis | None:
        """분모의 금액 basis — **근거가 있을 때만** 기초금액이라고 말한다.

        ``clean-base``·``reserve-estimate`` 는 근거와 함께 그 축을 만족한다(전자는 정수
        원화로 검증된 저장값, 후자는 복수예비가격 midpoint 로 복구한 기초금액). 나머지는
        ``None`` 이다 — ``base-fallback`` 은 저장값을 근거 없이 쓴 것이라 실제로는
        예정가-basis 오염일 수 있고, ``unavailable`` 은 아예 분모가 없다.

        여기서 상수 ``Basis.BASE_AMOUNT`` 를 내면 payload 안에
        ``denominator_basis="base_amount"`` 와 ``denominator_source="base-fallback"`` 이
        서로 모순되게 앉는다. 축을 모를 때는 축을 말하지 않는 쪽이 정직하다(§2).
        """
        if self.denominator_source in EVIDENCED_DENOMINATOR_SOURCES:
            return Basis.BASE_AMOUNT
        return None

    def as_payload(self) -> dict[str, float | str | None]:
        """직렬화용 평문 dict — enum 은 값 문자열로 편다.

        데이터셋 직렬화가 이 블록을 통째로 싣는다. 값과 basis 를 한 블록에 묶어 두면
        소비자가 ``value`` 만 떼어 곱하면서 basis 를 잃는 경로가 구조적으로 눈에 띈다.

        payload **만** 보고도 근거 없는 분모를 걸러낼 수 있어야 한다는 것이 이 블록의 계약
        이다. 두 축이 그 일을 한다: ``status == "ok"``(근거 없는 분모는 다른 상태)와
        ``denominator_basis is not None``. 둘은 같은 선언
        (:data:`EVIDENCED_DENOMINATOR_SOURCES`)에서 나오므로 서로 어긋날 수 없다.
        """
        basis = self.denominator_basis
        return {
            "value": self.value,
            "status": self.status.value,
            "numerator_basis": self.numerator_basis.value,
            "denominator_basis": None if basis is None else basis.value,
            "denominator_value": self.denominator_value,
            "denominator_source": self.denominator_source.value,
            "base_amount_basis": self.base_amount_basis,
        }


def _resolve_value(
    amount: float, denominator: float | None, source: ReliableBaseSource
) -> tuple[float | None, AwardRateLabelStatus]:
    """분자·분모에서 값과 상태를 정한다 — 첫 실패가 이긴다(순수).

    순서는 분자 → 분모 → 유효 창 → 분모 근거다. 분자가 없으면 라벨의 주어(어떤 낙찰인가)가
    없으므로 분모를 볼 것도 없다. NaN 분자는 ``<= 0`` 비교를 통과하지만 유효 창에서
    탈락한다 — 기존 소비자(``floor_shortfall.assessment_rate_from_opening``)와 같은
    귀결이다.

    마지막 갈림(``OK`` vs ``OK_UNVERIFIED_BASE``)은 값을 바꾸지 않는다. 값은 같고 그 값을
    믿을 근거만 다르며, 그 구분을 소비자가 놓치지 못하게 상태에 실는다.
    """
    if amount <= 0:
        return None, AwardRateLabelStatus.NO_WINNING_AMOUNT
    if denominator is None:
        return None, AwardRateLabelStatus.NO_RELIABLE_BASE
    rate = amount / denominator
    if not is_plausible_rate_label(rate):
        return None, AwardRateLabelStatus.OUT_OF_RANGE
    if source not in EVIDENCED_DENOMINATOR_SOURCES:
        return rate, AwardRateLabelStatus.OK_UNVERIFIED_BASE
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
        값이 나도 분모에 근거가 없으면 상태가 ``ok-unverified-base`` 다.

    ``winning_amount`` coercion 은 기존 소비자가 쓰던 프리미티브
    (``optional_float(...) or 0.0``)를 그대로 옮겼다 — 표본이 갈리지 않게 하려는 것이다.
    """
    reliable = get_reliable_base(
        base_amount=base_amount,
        basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
    )
    # ``get_reliable_base`` 는 양수 값만 싣는다(그 안의 ``_positive_or_none`` — NaN 은 막고
    # ``+inf`` 는 통과시킨다). 그래서 양수 검사를 다시 하지 않고, ``None`` 이 곧 "양수 base
    # 없음"이다. ``+inf`` base 는 비가 0.0 이 되어 유효 창에서 탈락하므로 값으로 새지
    # 않는다(그 거동을 tests/test_prediction_label_basis.py 값표가 고정한다).
    denominator = None if reliable.value is None else float(reliable.value)
    value, status = _resolve_value(
        optional_float(winning_amount) or 0.0, denominator, reliable.source
    )
    return AwardRateLabel(
        value=value,
        status=status,
        denominator_value=denominator,
        denominator_source=reliable.source,
        base_amount_basis=base_amount_basis,
    )
