"""낙찰률 GBM 학습·평가용 행 로더 — ORM → 값 행 (읽기 전용 경계).

판정은 전부 순수 커널이 한다: 라벨은 :func:`~app.domain.award_rate_label.build_award_rate_label`,
피처 조립은 :class:`~app.domain.award_rate_features.AwardRateFeatureSpace`. 이 모듈은
``HistoricalData`` / ``TenderResult`` 에서 스칼라를 꺼내 값 행으로 펴는 얇은 경계다
(§4.7 순수 코어 / 얇은 경계).

세 가지 필터가 **학습 표본의 정의**다.

1. ``status == "ok"`` — 분모에 근거가 있는 라벨만 받는다. ``ok-unverified-base`` 는 값이
   나지만 분모가 기초금액이라는 증거가 없고(저장값을 그대로 쓴 경로), 그 오염이
   카테고리와 교락한다. 이 조건 하나가 라벨 모듈이 진단한 결함을 학습에서 차단한다.
2. ``opened_at`` 존재 + **미래 개찰 배제** — 시간 분할과 cutoff 가 성립하려면 개찰 시각이
   있어야 하고, 시각이 미래인 행은 개찰이 일어나지 않았는데 라벨이 있다는 뜻이라
   적재 사고다. 값을 고치지 않고 관측에서만 뺀다(published_floor_rate 와 같은 스탠스).
3. ``cutoff_at`` — 백테스트에서 "그 시점에 존재했을 행"만 싣는다. 경계는 형제 로더
   ``app/services/backtest_cutoff._query_history_scope`` 와 같은 규칙(``opened_at <
   cutoff``)이다.

⚠ cutoff 규칙의 알려진 낙관 (공시)
-----------------------------------
``opened_at < cutoff`` 는 **개찰이 곧 라벨 가용**이라고 가정한다. 실제로는 낙찰 결과와
reserve detail 이 개찰 며칠 뒤에 수집되므로, 그 시점에 아직 못 봤을 행이 학습에 들어갈
수 있다. 이 낙관은 이 저장소의 기존 백테스트 경로와 **같은** 것이고 비교 대상 베이스라인도
같은 규칙 위에서 측정되므로 비교의 공정성은 유지되지만, 절대 오차의 낙관 편향은 남는다.
수집 지연을 반영하려면 ``TenderResult`` 의 확정 시각을 함께 봐야 하고 그것은 별도 트랙이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Query, Session

from app.core.time import ensure_utc, utc_now
from app.domain.award_rate_label import AwardRateLabelStatus, build_award_rate_label
from app.models.models import HistoricalData
from app.models.pipeline import TenderResult
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow

__all__ = ["load_award_rate_rows"]


def _candidate_query(
    db: Session, *, cutoff_at: datetime | None, reference_now: datetime
) -> Query[Any]:
    """라벨 후보 행을 고르는 읽기 쿼리 — 시간 필터 세 개가 여기 한 곳에 있다.

    ``Query[Any]`` 는 형제 로더(``app/services/floor_shortfall._sample_query``)와 같은
    계약이다: old-style ``Column(...)`` 모델의 컬럼 튜플 선택은 행 shape 을 정적으로
    표현할 수 없고, 값 계약은 이 쿼리를 소비하는 :func:`_training_row` 가 못 박는다.
    """
    query = (
        db.query(
            HistoricalData.id,
            HistoricalData.category,
            HistoricalData.agency_name,
            HistoricalData.base_amount,
            HistoricalData.base_amount_basis,
            HistoricalData.base_amount_estimated,
            HistoricalData.opened_at,
            TenderResult.winning_amount,
        )
        .join(
            TenderResult,
            (TenderResult.project_id == HistoricalData.project_id)
            & (TenderResult.is_current.is_(True)),
        )
        .filter(
            HistoricalData.project_id.isnot(None),
            HistoricalData.opened_at.isnot(None),
            HistoricalData.opened_at <= reference_now,
        )
    )
    if cutoff_at is not None:
        query = query.filter(HistoricalData.opened_at < cutoff_at)
    return query.order_by(HistoricalData.opened_at.asc(), HistoricalData.id.asc())


def _training_row(
    *,
    winning_amount: float | None,
    base_amount: float | None,
    base_amount_basis: str | None,
    base_amount_estimated: float | None,
    category: str | None,
    agency_name: str | None,
    opened_at: datetime,
) -> AwardRateTrainingRow | None:
    """스칼라 한 벌을 학습 행으로 편다. 근거 없는 라벨(``ok`` 아님)은 ``None`` 이다.

    쿼리 행 객체가 아니라 스칼라를 받는 것이 이 경계의 **값 계약**이다(형제 로더
    ``app/services/floor_shortfall`` 와 같은 형태): 행 shape 은 정적으로 표현되지 않으므로,
    실제로 무엇을 읽는지는 이 시그니처가 못 박는다.
    """
    label = build_award_rate_label(
        winning_amount=winning_amount,
        base_amount=base_amount,
        base_amount_basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
    )
    if label.status is not AwardRateLabelStatus.OK or label.value is None:
        return None
    return AwardRateTrainingRow(
        value=float(label.value),
        # 피처의 금액은 라벨의 **분모**와 같은 수여야 한다 — 서빙에서 그 자리를
        # 채우는 것이 기초금액(context.budget)이므로 축이 일치한다.
        amount=float(label.denominator_value or 0.0),
        category=str(category or ""),
        agency=str(agency_name or ""),
        denominator_source=label.denominator_source.value,
        # SQLite 는 naive, Postgres 는 aware 를 낸다. 홀드아웃 분할이 두 종류를
        # 섞어 비교하면 TypeError 로 죽으므로 dialect 차이를 여기서 흡수한다.
        opened_at=ensure_utc(opened_at),
    )


def load_award_rate_rows(
    db: Session,
    *,
    cutoff_at: datetime | None = None,
    now: datetime | None = None,
) -> list[AwardRateTrainingRow]:
    """근거 있는 낙찰률 라벨이 성립하는 학습 행을 개찰 시각 오름차순으로 싣는다.

    Args:
        db: 읽기 전용 세션(이 함수는 write/commit 하지 않는다).
        cutoff_at: 이 시각 **미만**의 개찰만 싣는다(백테스트 as-of). ``None`` 이면
            전체 구간.
        now: "미래 개찰" 판정의 기준 시각(주입 seam — 테스트가 시계를 고정한다).

    Returns:
        :class:`~app.services.ml_training.award_rate_gbm.AwardRateTrainingRow` 목록.
        정렬은 ``(opened_at, id)`` 라 같은 시각의 행 순서도 결정적이다 — 홀드아웃
        경계가 실행마다 흔들리면 비교 수치가 재현되지 않는다.
    """
    records = _candidate_query(
        db, cutoff_at=cutoff_at, reference_now=now or utc_now()
    ).all()
    rows = (
        _training_row(
            winning_amount=record.winning_amount,
            base_amount=record.base_amount,
            base_amount_basis=record.base_amount_basis,
            base_amount_estimated=record.base_amount_estimated,
            category=record.category,
            agency_name=record.agency_name,
            opened_at=record.opened_at,
        )
        for record in records
    )
    return [row for row in rows if row is not None]
