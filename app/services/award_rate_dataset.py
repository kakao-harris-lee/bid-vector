"""낙찰률 GBM 학습·평가용 행 로더 — ORM → 값 행 (읽기 전용 경계).

판정은 전부 순수 커널이 한다: 라벨은 :func:`~app.domain.award_rate_label.build_award_rate_label`,
피처 조립은 :class:`~app.domain.award_rate_features.AwardRateFeatureSpace`. 이 모듈은
``HistoricalData`` / ``TenderResult`` 에서 스칼라를 꺼내 값 행으로 펴는 얇은 경계다
(§4.7 순수 코어 / 얇은 경계).

네 가지 필터가 **학습 표본의 정의**다.

1. ``status == "ok"`` — 분모에 근거가 있는 라벨만 받는다. ``ok-unverified-base`` 는 값이
   나지만 분모가 기초금액이라는 증거가 없고(저장값을 그대로 쓴 경로), 그 오염이
   카테고리와 교락한다. 이 조건 하나가 라벨 모듈이 진단한 결함을 학습에서 차단한다.
2. ``opened_at`` 존재 + **미래 개찰 배제** — 시간 분할과 cutoff 가 성립하려면 개찰 시각이
   있어야 하고, 시각이 미래인 행은 개찰이 일어나지 않았는데 라벨이 있다는 뜻이라
   적재 사고다. 값을 고치지 않고 관측에서만 뺀다(published_floor_rate 와 같은 스탠스).
3. ``cutoff_at`` — 백테스트에서 "그 시점에 존재했을 행"만 싣는다. 경계는 형제 로더
   ``app/services/backtest_cutoff._query_history_scope`` 와 같은 규칙(``opened_at <
   cutoff``)이다.
4. **피드 출처**(``Project.issuing_agency IS NOT NULL``) — 아래 절. 네 개 중 유일하게 끌 수
   있는 필터다(``PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY``).

피드 출처 필터는 메커니즘 필터가 아니라 **서빙 분포 정합**이다
--------------------------------------------------------------
``Project.issuing_agency`` 는 "공고 피드에서 본 공고"의 축이다. 값이 없는 행은 계약방법이
없는 공고가 아니라 **개찰결과 피드로만 본 공고**다 — ``app/services/koneps/scsbid`` 가
issuing_agency · source_url · contract_method 없이 item 을 만들기 때문이고, 그래서 이 축은
계약방법 텍스트 축과 34,859/34,859 로 완전히 일치한다(읽기 전용 실측).

문제는 두 모집단의 구성비다. 서빙이 마주하는 열린 공고는 사실상 전부 피드 출처인데(실측
99.93%) 학습 코퍼스는 31% 만 그렇다. 즉 학습의 69% 는 **서빙이 결코 마주치지 않는 모집단**
이고, 그 층에는 하한이 게시되지 않는 계열이 섞여 낙찰률 분포 자체가 다르다. 그 위에서 잰
홀드아웃 수치는 라이브에서 나올 수치가 아니다.

필터를 켜면 표본이 줄고 유효 이력이 짧아진다. 그 대가는 실재하지만, 서빙하지 않을 모집단을
섞어 표본을 불리는 것은 측정이 아니다. 필터를 끌 수 있게 둔 것은 완화 장치가 아니라 **전후
비교 장치**이며(끄면 이전 표본 정의가 그대로 복원된다), 수치는 PR 본문에 남긴다.

``Project`` 는 **left outer join** 이다. inner join 이면 필터를 꺼도 Project 행이 없는
이력이 조용히 빠져 "필터 off = 이전 정의"가 성립하지 않는다(라이브 코퍼스의 조인 커버리지는
100% 지만, 그 사실에 표본 정의를 기대게 두지 않는다).

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

from app.core.config import settings
from app.core.time import ensure_utc, utc_now
from app.domain.award_rate_label import AwardRateLabelStatus, build_award_rate_label
from app.domain.published_floor_rate import plausible_published_floor_rate
from app.domain.rate_normalization import to_bid_rate_fraction
from app.models.models import HistoricalData, Project
from app.models.pipeline import TenderResult
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow

__all__ = ["load_award_rate_rows"]


def _candidate_query(
    db: Session,
    *,
    cutoff_at: datetime | None,
    reference_now: datetime,
    feed_origin_only: bool,
) -> Query[Any]:
    """라벨 후보 행을 고르는 읽기 쿼리 — 표본 필터 네 개가 여기 한 곳에 있다.

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
            Project.award_floor_rate,
        )
        .join(
            TenderResult,
            (TenderResult.project_id == HistoricalData.project_id)
            & (TenderResult.is_current.is_(True)),
        )
        .outerjoin(Project, Project.id == HistoricalData.project_id)
        .filter(
            HistoricalData.project_id.isnot(None),
            HistoricalData.opened_at.isnot(None),
            HistoricalData.opened_at <= reference_now,
        )
    )
    if feed_origin_only:
        query = query.filter(Project.issuing_agency.isnot(None))
    if cutoff_at is not None:
        query = query.filter(HistoricalData.opened_at < cutoff_at)
    return query.order_by(HistoricalData.opened_at.asc(), HistoricalData.id.asc())


def _published_floor_rate(raw: float | None) -> float | None:
    """저장된 공시 낙찰하한율 → fraction (개연 밴드 밖은 ``None``). **진단 전용이다.**

    이 값은 학습 행에 실리지만 피처가 되지 않는다 — 커버리지가 도메인이 아니라 백필 진행
    상태라서 학습의 "미공시"와 서빙의 "미공시"가 다른 것을 뜻하기 때문이다. 사유와 재도입
    조건은 :class:`~app.services.ml_training.award_rate_gbm.AwardRateTrainingRow` 의 그
    필드 docstring 한 곳에 있다(여기서 되풀이하지 않는다).

    스케일 정규화(percent 88 ↔ fraction 0.88)와 개연 밴드는 **라이브 가격 경로가 쓰는
    것과 같은 두 벌**이다(``app/services/bid_base.resolve_notice_legal_floor_bid_rate``
    → ``to_bid_rate_fraction`` + ``plausible_published_floor_rate``). 진단값이라도 규칙을
    재선언하지 않는 이유는, 두 경로가 다른 밴드를 쓰면 재도입 조건을 확인할 때 비교하는
    두 수가 애초에 같은 정의가 아니게 되기 때문이다.
    """
    if raw is None:
        return None
    try:
        numeric = float(raw)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return plausible_published_floor_rate(to_bid_rate_fraction(numeric))


def _training_row(
    *,
    winning_amount: float | None,
    base_amount: float | None,
    base_amount_basis: str | None,
    base_amount_estimated: float | None,
    category: str | None,
    agency_name: str | None,
    opened_at: datetime,
    award_floor_rate: float | None,
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
        published_floor_rate=_published_floor_rate(award_floor_rate),
    )


def load_award_rate_rows(
    db: Session,
    *,
    cutoff_at: datetime | None = None,
    now: datetime | None = None,
    feed_origin_only: bool | None = None,
) -> list[AwardRateTrainingRow]:
    """근거 있는 낙찰률 라벨이 성립하는 학습 행을 개찰 시각 오름차순으로 싣는다.

    Args:
        db: 읽기 전용 세션(이 함수는 write/commit 하지 않는다).
        cutoff_at: 이 시각 **미만**의 개찰만 싣는다(백테스트 as-of). ``None`` 이면
            전체 구간.
        now: "미래 개찰" 판정의 기준 시각(주입 seam — 테스트가 시계를 고정한다).
        feed_origin_only: 공고 피드 출처 행만 실을지(모듈 docstring 참조). ``None``
            이면 ``PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY`` 설정을 따른다 —
            인자는 전역 설정을 monkeypatch 하지 않고 두 표본 정의를 나란히 재는 seam이다.

    Returns:
        :class:`~app.services.ml_training.award_rate_gbm.AwardRateTrainingRow` 목록.
        정렬은 ``(opened_at, id)`` 라 같은 시각의 행 순서도 결정적이다 — 홀드아웃
        경계가 실행마다 흔들리면 비교 수치가 재현되지 않는다.
    """
    records = _candidate_query(
        db,
        cutoff_at=cutoff_at,
        reference_now=now or utc_now(),
        feed_origin_only=(
            bool(settings.PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY)
            if feed_origin_only is None
            else feed_origin_only
        ),
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
            award_floor_rate=record.award_floor_rate,
        )
        for record in records
    )
    return [row for row in rows if row is not None]
