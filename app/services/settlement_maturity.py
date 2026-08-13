"""정산 성숙도 관측 로더 — ORM → 값 (읽기 전용 경계).

판정은 전부 순수 커널이 한다(:mod:`app.domain.settlement_maturity`). 이 모듈은 "공고 하나가
언제 개찰됐고 우리가 그 결과를 아는가"만 꺼내 값으로 편다(§4.7 순수 코어 / 얇은 경계).

⚠ 함정: **미정산은 NULL 이 아니라 0.0 이다**
--------------------------------------------
``TenderResult.winning_amount`` 는 결과를 모르는 공고에서 ``NULL`` 이 아니라 **0.0** 이다
(실측 2026-08-13: 피드 공고 30,393건이 전부 current ``TenderResult`` 를 갖고
``winning_amount IS NULL`` 은 0건인데, ``status='open'`` 15,067건 중 15,057건이
``winning_amount == 0``). 따라서 정산 여부를 ``IS NOT NULL`` 로 판정하면 **성숙도가 항상
100%** 로 나온다. 판정은 반드시 ``> 0`` 이다.

모집단은 항상 **피드 출처**(``Project.issuing_agency IS NOT NULL``)다
--------------------------------------------------------------------
표본 정의를 ``--no-feed-origin-only`` 로 넓혀도 이 분모는 넓히지 않는다. 개찰결과 피드로만
본 공고는 **정산된 순간에만 관측되므로** 미정산 쪽 분모가 애초에 존재하지 않고, 그 모집단의
"성숙도"는 정의상 100% 가 되어 embargo 를 무력화한다. 성숙도는 미정산 공고를 셀 수 있는
모집단에서만 의미가 있다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from app.core.time import ensure_utc, utc_now
from app.domain.settlement_maturity import SettlementObservation
from app.models.models import HistoricalData, Project
from app.models.pipeline import TenderResult

__all__ = ["load_settlement_observations"]


def _observation_query(db: Session, *, reference_now: datetime) -> Query[Any]:
    """피드 공고 × (개찰 시각, 낙찰가) 읽기 쿼리.

    ``Query[Any]`` 는 형제 로더(``app/services/award_rate_dataset._candidate_query``)와
    같은 계약이다: old-style ``Column(...)`` 모델의 컬럼 튜플 선택은 행 shape 을 정적으로
    표현할 수 없고, 값 계약은 이 쿼리를 소비하는 호출부가 못 박는다.
    """
    opened = (
        db.query(
            HistoricalData.project_id.label("project_id"),
            func.min(HistoricalData.opened_at).label("opened_at"),
        )
        .filter(
            HistoricalData.project_id.isnot(None),
            HistoricalData.opened_at.isnot(None),
        )
        .group_by(HistoricalData.project_id)
        .subquery()
    )
    axis = func.coalesce(opened.c.opened_at, Project.deadline)
    return (
        db.query(axis.label("opened_at"), TenderResult.winning_amount)
        .select_from(Project)
        .outerjoin(opened, opened.c.project_id == Project.id)
        .outerjoin(
            TenderResult,
            (TenderResult.project_id == Project.id)
            & (TenderResult.is_current.is_(True)),
        )
        .filter(
            Project.issuing_agency.isnot(None),
            axis.isnot(None),
            axis <= reference_now,
        )
    )


def load_settlement_observations(
    db: Session, *, now: datetime | None = None
) -> list[SettlementObservation]:
    """피드 공고별 (개찰 시각, 결과를 아는가)를 싣는다(SELECT 전용 — write/commit 없음).

    시간 축은 **개찰 시각**이다(``HistoricalData.opened_at`` 의 공고별 최솟값). 홀드아웃
    평가 창이 같은 축으로 행을 고르므로, 성숙도의 분모와 평가 대상이 같은 구간 정의를
    공유한다. 개찰 시각이 없으면 마감 시각으로 대체한다 — 실측상 두 축은 p50/p90 모두
    1시간 차이이고 97.8% 가 같은 주에 들어가며(2026-08-13), 대체가 없으면 개찰 시각을 못
    받은 공고가 **분모에서 조용히 빠져 성숙도를 부풀린다**. 둘 다 없는 공고는 어느 구간에도
    귀속시킬 수 없어 빠진다(현재 실측 0건).

    ``TenderResult`` 는 left outer join 이다. current 결과 행이 없는 공고는 "결과를
    모른다"이지 "모집단에 없다"가 아니다(현재 실측 0건이지만, 그 사실에 분모를 기대게 두지
    않는다).

    Args:
        db: 읽기 전용 세션.
        now: "미래 개찰" 판정의 기준 시각(주입 seam — 테스트가 시계를 고정한다).
            아직 개찰되지 않은 공고는 성숙도의 분모가 아니다.

    Returns:
        :class:`~app.domain.settlement_maturity.SettlementObservation` 목록.
    """
    records = _observation_query(db, reference_now=now or utc_now()).all()
    return [
        SettlementObservation(
            # SQLite 는 naive, Postgres 는 aware 를 낸다 — 구간 비교가 두 종류를 섞어
            # 죽지 않게 경계에서 흡수한다(형제 로더 award_rate_dataset 와 같은 스탠스).
            opened_at=ensure_utc(record.opened_at),
            settled=(record.winning_amount or 0.0) > 0.0,
        )
        for record in records
    ]
