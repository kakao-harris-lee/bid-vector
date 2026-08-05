"""사정률(예정가/기초금액) 표본 수집 + 하한 미달 빈도 추정 — DB 경계.

순수 판정 커널은 :mod:`app.domain.floor_shortfall` 가 소유하고, 이 모듈은 그 커널이
먹을 **표본을 정직하게 고르는 일**만 한다(§4.7 순수 코어 / 얇은 경계).

표본을 어떻게 고르는가 (오염 제거가 이 모듈의 존재 이유)
--------------------------------------------------------
사정률은 저장된 컬럼이 아니라 두 관측의 비로 도출한다::

    사정률 = 예정가/기초금액 = (낙찰가/기초금액) ÷ (낙찰가/예정가)
                              = amount_derived_rate ÷ reported_award_rate

그래서 두 분모가 **서로 독립 관측**일 때만 표본이 성립하고, 그렇지 않으면 사정률이
1 로 붕괴해 "위험 없음"이라는 거짓 신호가 된다. 두 겹으로 막는다:

1. **기초금액 오염(#199)** — ``base_amount`` 의 66% 는 실제 기초금액이 아니라
   ``낙찰가 ÷ 낙찰률`` 역산(=예정가-basis)이거나 VAT 파생이다. 그런 행은 분모가
   예정가라 사정률이 정확히 1 로 나온다. :func:`~app.domain.reliable_base.get_reliable_base`
   가 ``clean`` 으로 판정한 행만 받는다. 복구 추정치(reserve-estimate)는 그 자체가
   추정 오차를 실어 분포의 **꼬리**를 직접 오염시키므로 이 용도에서는 제외한다
   (밴드 캘리브레이션과 달리 여기서는 중심이 아니라 꼬리가 결론이다).
2. **낙찰률 파생(basis 미검증)** — 보고된 ``winning_rate`` 자체가 우리가 가진
   금액비를 되돌려준 값이면 역시 사정률이 1 로 붕괴한다. 그 판별은 홀드아웃 품질
   판정과 **같은 규칙**(:func:`~app.ai.holdout_quality.is_rate_basis_independent`)을
   재사용한다 — 규칙이 두 벌이 되면 한쪽만 바뀌어 갈린다(§4.5.8).

알려진 편향(2번 필터의 대가) — 방향은 조건부다
---------------------------------------------
2번 필터는 보고율과 금액비의 상대오차가 ``RATE_BASIS_INDEPENDENCE_TOLERANCE``(1e-3)
미만인 행을 (복수예비가격 증거가 없으면) 버린다. 그 조건은 사정률로 옮기면
``|1-a|/a < 1e-3``, 즉 **사정률이 약 0.999~1.001 안에 있는 표본**이 분모에서 빠진다는
뜻이다(:data:`EXCLUDED_ASSESSMENT_BAND` — 이 밴드는 스코프 문구에도 드러난다).

빠진 표본이 임계 사정률 a\\* 의 어느 쪽인지에 따라 편향 **방향이 갈린다**:

* ``a\\* > 1.001`` — 빠진 표본이 전부 a\\* 아래(=미달 아님)라 분모만 줄어 빈도가
  **과대**평가된다. "보수적"이 성립하는 것은 이 구간뿐이다.
* ``a\\* ≈ 1``(0.999~1.001 — 하한에 밀착한 추천) — 빠진 표본이 a\\* 를 사이에 두고
  갈려 방향이 **보장되지 않는다**. 하필 실투찰이 가장 위험한 구간이 여기다.
* ``a\\* < 0.999``(추천율이 하한율보다 낮은 경우 — 이 커널은 그것을 막지 않는다) —
  빠진 표본이 전부 a\\* 위(=미달)라 빈도가 **과소**평가된다. 이 구간에서는 편향이
  조용히 안심시키는 방향이다.

따라서 이 값은 표본 수(``sample_count``)·임계 사정률(``critical_assessment_rate``)과
함께 상대 비교로만 쓰고, a\\* 가 1 부근이거나 1 아래면 방향 보장이 없다는 것을 전제로
읽는다.

시간 누수 차단
--------------
``as_of`` 를 주면 그 시점 **이전에 개찰된** 행만 본다. 개찰일이 없는 행은 과거임을
증명할 수 없으므로 항상 제외한다(백테스트에서 미래 표본이 새는 경로를 원천 차단).

성능
----
표본 분포는 공고마다 달라지지 않으므로(추첨 메커니즘은 공고 불변), 요청 경로에서
공고마다 다시 스캔할 필요가 없다. :func:`load_assessment_rate_samples` 를 따로
공개해 호출부가 한 번 적재해 여러 공고에 재사용(캐시)할 수 있게 두고,
:func:`estimate_floor_shortfall` 는 그 결과를 주입받을 수 있다(§4.7.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Sequence

from sqlalchemy.orm import Query, Session

from app.ai.holdout_quality import (
    RATE_BASIS_INDEPENDENCE_TOLERANCE,
    is_rate_basis_independent,
)
from app.domain.aggregates import error_rate
from app.domain.floor_shortfall import (
    MIN_ASSESSMENT_SAMPLES,
    floor_shortfall_frequency,
    is_plausible_assessment_rate,
    resolve_critical_assessment_rate,
)
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, TenderResult
from app.schemas.prediction import FloorShortfallEstimate
from app.services.base_amount_basis import BASIS_CLEAN
from app.services.prediction_dataset import PredictionDatasetService
from app.services.query_predicates import settled_with_amount
from app.utils.numeric import optional_float
from app.utils.sequence_coercion import coerce_numeric_list

# 한 번에 훑는 최대 (HistoricalData × TenderResult) 행 수. 개찰일 최신순이라 이
# 상한은 "최근 N 개찰"로 읽힌다. 표본은 수천 건 규모면 꼬리 추정에 충분하고, 요청
# 경로에서 전수 스캔(수만 행)을 도는 것은 허용하지 않는다(§4.5.1 매직값 선언).
ASSESSMENT_SAMPLE_SCAN_LIMIT: Final[int] = 4000

# 2번 필터(낙찰률 basis 독립성)가 분모에서 빼는 사정률 밴드의 반폭 — 1 ± 이 값이 통째로
# 빠진다. 편향 **방향**이 이 밴드와 임계 사정률의 위치 관계로 갈리므로(모듈 docstring),
# 값을 읽는 쪽이 밴드를 볼 수 있어야 한다. 필터 자체가 쓰는 허용오차와 같은 출처라
# 한쪽만 바뀌어 문구와 실제가 갈릴 수 없다(§4.5.8).
EXCLUDED_ASSESSMENT_BAND: Final[float] = RATE_BASIS_INDEPENDENCE_TOLERANCE

# 낙찰률 정규화(percent↔fraction + 유효범위 게이트)는 학습 데이터셋과 **같은**
# 프리미티브를 써야 표본이 갈리지 않는다. 계측 스크립트
# (scripts/measure_stored_bidrate_basis.py)가 쓰는 것과 동일한 진입점이다.
_DATASET: Final[PredictionDatasetService] = PredictionDatasetService()

# 판정 불가 사유(선언 — 호출부가 문자열을 조립하지 않게 한다).
_REASON_INVALID_RATES: Final[str] = (
    "추천 투찰율 또는 낙찰하한율이 유효하지 않아 임계 사정률을 정의할 수 없습니다."
)
_REASON_INSUFFICIENT_SAMPLES: Final[str] = (
    "사정률 표본이 최소 {minimum}건에 미달합니다(현재 {count}건). 빈도를 발표하지 "
    "않습니다 — 위험이 없다는 뜻이 아닙니다."
)


@dataclass(frozen=True)
class AssessmentRateSamples:
    """수집된 사정률 표본과 그 표본을 고른 기준(감사용).

    ``values`` 는 개연 범위(:func:`is_plausible_assessment_rate`) 안의 값만 담는다.
    ``scope`` 는 어떤 오염 필터·카테고리·기준일로 골랐는지의 사람이 읽는 요약이며
    그대로 응답에 실려 나간다.
    """

    values: tuple[float, ...]
    scope: str


def _describe_scope(category: str | None, as_of: datetime | None) -> str:
    """표본 선택 기준을 사람이 읽을 수 있게 요약한다.

    제외 밴드를 여기에 적는 이유는 그것이 곧 편향 방향의 전제이기 때문이다 — 임계 사정률이
    이 밴드 안이나 아래에 있으면 빈도가 보수적이라는 보장이 사라진다(모듈 docstring).
    """
    parts = [
        "clean-basis 기초금액",
        "독립 관측 낙찰률",
        f"사정률 1±{EXCLUDED_ASSESSMENT_BAND:g} 표본 제외",
        f"최근 개찰 {ASSESSMENT_SAMPLE_SCAN_LIMIT}행 이내",
    ]
    parts.append(f"카테고리={category}" if category else "전 카테고리")
    if as_of is not None:
        parts.append(f"{as_of:%Y-%m-%d} 이전 개찰")
    return " · ".join(parts)


def assessment_rate_from_opening(
    *,
    base_amount: float | None,
    base_amount_basis: str | None,
    base_amount_estimated: float | None,
    reserve_prices: str | None,
    winning_amount: float | None,
    winning_rate: float | None,
) -> float | None:
    """한 개찰의 사정률(예정가/기초금액)을 도출한다. 오염이면 ``None``(순수).

    두 관측이 독립이 아니면(기초금액이 예정가 역산이거나 낙찰률이 금액비 파생이면)
    비가 1 로 붕괴해 "위험 없음"이라는 거짓 표본이 되므로 버린다(모듈 docstring).
    ORM 행이 아니라 스칼라만 받으므로 DB 없이 값 테이블로 검증할 수 있다(§4.7.4).
    """
    reliable = get_reliable_base(
        base_amount=base_amount,
        basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
    )
    if reliable.source is not ReliableBaseSource.CLEAN_BASE or reliable.value is None:
        return None
    base = float(reliable.value)

    # 낙찰가/예정가 — KONEPS 가 보고한 성공사정률(예정가-basis 독립 관측).
    reported_rate = _DATASET._normalize_bid_rate_value(winning_rate)
    if reported_rate is None:
        return None

    amount = optional_float(winning_amount) or 0.0
    if amount <= 0:
        return None
    # 낙찰가/기초금액 — 우리가 가진 금액비. 유효범위 게이트는 학습 데이터셋과 동일.
    amount_derived_rate = amount / base
    if not (
        _DATASET.VALID_BID_RATE_MIN
        <= amount_derived_rate
        <= _DATASET.VALID_BID_RATE_MAX
    ):
        return None

    if not is_rate_basis_independent(
        reserve_price_count=len(coerce_numeric_list(reserve_prices)),
        rate_mismatch_ratio=error_rate(reported_rate, amount_derived_rate),
    ):
        return None

    return amount_derived_rate / reported_rate


def _sample_query(
    db: Session, category: str | None, as_of: datetime | None
) -> Query[Any]:
    """표본 후보 행을 고르는 읽기 쿼리(오염·누수 필터는 여기 한 곳에).

    ``Query[Any]`` 는 회피가 아니라 실제 계약이다 — old-style ``Column(...)`` 모델의
    컬럼 튜플 선택은 행 shape 을 정적으로 표현할 수 없다. 값 계약은 이 쿼리를 소비하는
    :func:`assessment_rate_from_opening` 가 스칼라 시그니처로 못 박는다.
    """
    query = (
        db.query(
            HistoricalData.project_id,
            HistoricalData.base_amount,
            HistoricalData.base_amount_basis,
            HistoricalData.base_amount_estimated,
            HistoricalData.reserve_prices,
            TenderResult.winning_amount,
            TenderResult.winning_rate,
        )
        .join(TenderResult, TenderResult.project_id == HistoricalData.project_id)
        .filter(HistoricalData.project_id.isnot(None))
        .filter(HistoricalData.base_amount_basis == BASIS_CLEAN)
        .filter(HistoricalData.base_amount > 0)
        # 개찰일이 없으면 과거임을 증명할 수 없다 → 항상 제외(누수 차단을 total 하게).
        .filter(HistoricalData.opened_at.isnot(None))
        .filter(settled_with_amount())
        .filter(TenderResult.winning_rate > 0)
    )
    if category:
        query = query.filter(HistoricalData.category == category)
    if as_of is not None:
        query = query.filter(HistoricalData.opened_at <= as_of)
    return query.order_by(
        HistoricalData.opened_at.desc(), HistoricalData.id.desc()
    ).limit(ASSESSMENT_SAMPLE_SCAN_LIMIT)


def load_assessment_rate_samples(
    db: Session,
    *,
    category: str | None = None,
    as_of: datetime | None = None,
) -> AssessmentRateSamples:
    """개찰 이력에서 사정률 표본을 모은다(오염 행 제외).

    Args:
        db: 읽기 전용으로만 쓰는 세션(이 함수는 write/commit 하지 않는다).
        category: 주면 ``HistoricalData.category`` 정확 일치로 좁힌다. 저장된
            카테고리 토큰(예: ``"construction"``)을 그대로 넘겨야 한다 — 별칭
            정규화는 하지 않으므로, 맞지 않는 값은 표본 부족(판정 불가)으로 귀결된다.
        as_of: 주면 이 시점 이전에 개찰된 행만 본다(시간 누수 차단).

    한 공고(project)당 한 표본만 센다 — 같은 개찰이 여러 행으로 적재돼 있어도 분포에
    중복 가중되지 않게 개찰일 최신 행 하나만 취한다.
    """
    seen_projects: set[int] = set()
    values: list[float] = []
    for row in _sample_query(db, category, as_of).all():
        project_id = int(row.project_id)
        if project_id in seen_projects:
            continue
        seen_projects.add(project_id)
        sample = assessment_rate_from_opening(
            base_amount=row.base_amount,
            base_amount_basis=row.base_amount_basis,
            base_amount_estimated=row.base_amount_estimated,
            reserve_prices=row.reserve_prices,
            winning_amount=row.winning_amount,
            winning_rate=row.winning_rate,
        )
        if is_plausible_assessment_rate(sample):
            values.append(float(sample))

    return AssessmentRateSamples(
        values=tuple(values), scope=_describe_scope(category, as_of)
    )


def estimate_floor_shortfall(
    db: Session,
    recommended_rate: float,
    floor_rate: float,
    *,
    category: str | None = None,
    as_of: datetime | None = None,
    samples: AssessmentRateSamples | None = None,
    min_samples: int = MIN_ASSESSMENT_SAMPLES,
) -> FloorShortfallEstimate:
    """추천 투찰율이 낙찰하한 미달이 됐을 **과거 빈도**를 추정한다.

    Args:
        db: 표본 적재용 세션. ``samples`` 를 주입하면 쓰이지 않는다.
        recommended_rate: 추천 투찰율(기초금액 기준 분수).
        floor_rate: 낙찰하한율(예정가격 기준 분수).
        category: 표본 스코프(정확 일치). :func:`load_assessment_rate_samples` 참조.
        as_of: 시간 누수 차단 기준 시점.
        samples: 미리 적재한 표본(캐시 재사용용 seam). 주면 DB 를 다시 읽지 않는다.
        min_samples: 빈도를 발표하기 위한 최소 표본 수.

    Returns:
        :class:`~app.schemas.prediction.FloorShortfallEstimate`. 빈도를 낼 수 없으면
        ``shortfall_frequency`` 가 ``None`` 이고 ``unmeasurable_reason`` 이 채워진다 —
        **"위험 없음"이 아니라 "판정 불가"** 다.
    """
    resolved_samples = (
        samples
        if samples is not None
        else load_assessment_rate_samples(db, category=category, as_of=as_of)
    )
    return build_floor_shortfall_estimate(
        recommended_rate,
        floor_rate,
        resolved_samples,
        min_samples=min_samples,
    )


def build_floor_shortfall_estimate(
    recommended_rate: float,
    floor_rate: float,
    samples: AssessmentRateSamples,
    *,
    min_samples: int = MIN_ASSESSMENT_SAMPLES,
) -> FloorShortfallEstimate:
    """적재된 표본으로 응답 DTO 를 만든다(I/O 없음 — DB 경계에서 분리된 조립부).

    판정 불가 사유를 **명시적으로** 채워, 호출부가 ``None`` 을 0(위험 없음)으로
    읽을 수 없게 한다(정직 명세 §2).
    """
    values: Sequence[float] = samples.values
    frequency = floor_shortfall_frequency(
        recommended_rate, floor_rate, values, min_samples=min_samples
    )
    if frequency is not None:
        return FloorShortfallEstimate(
            shortfall_frequency=frequency.shortfall_frequency,
            shortfall_sample_count=frequency.shortfall_sample_count,
            sample_count=frequency.sample_count,
            minimum_sample_count=min_samples,
            critical_assessment_rate=frequency.critical_assessment_rate,
            scope=samples.scope,
        )

    critical_rate = resolve_critical_assessment_rate(recommended_rate, floor_rate)
    reason = (
        _REASON_INVALID_RATES
        if critical_rate is None
        else _REASON_INSUFFICIENT_SAMPLES.format(
            minimum=min_samples, count=len(values)
        )
    )
    return FloorShortfallEstimate(
        shortfall_frequency=None,
        shortfall_sample_count=0,
        sample_count=len(values),
        minimum_sample_count=min_samples,
        critical_assessment_rate=critical_rate,
        scope=samples.scope,
        unmeasurable_reason=reason,
    )
