"""착지 코퍼스 로더 — 게시 하한 × 정산 낙찰 행의 읽기 전용 DB 경계.

판정은 전부 :mod:`app.services.ml_training.award_landing_ladder` 의 순수 사다리가 하고,
이 모듈은 **후보 행을 고르고 회계를 모으는 일**만 한다(§4.7 순수 코어 / 얇은 경계).
그래서 사다리는 DB 없이 값 테이블로 검증되고, 이 모듈은 "무엇을 조인해 어떤 순서로
훑는가"만 책임진다.

DB 는 SELECT 전용이다(이 모듈에 write/commit/flush 가 없다).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Row, and_
from sqlalchemy.orm import Query, Session

from app.models.models import HistoricalData, Project
from app.models.pipeline import TenderResult
from app.services.ml_training.award_landing_ladder import (
    DEFAULT_REGIME_GATE,
    REGIME_GATE_POLICIES,
    LadderStage,
    LandingCandidate,
    LandingRow,
    LandingRowOutcome,
    NegativeMarginAudit,
    build_landing_row,
)

__all__ = ["AwardLandingCorpus", "load_award_landing_corpus"]


@dataclass(frozen=True)
class AwardLandingCorpus:
    """코퍼스 + 사다리 회계 + 게이트 전 레짐 분포 + 음수 마진 감사.

    ``regime_histogram`` 은 **레짐 게이트를 걸기 전** 분포다. 게이트가 코퍼스를 비웠을
    때 그것이 "수의가 많아서"인지 "텍스트가 없어서(=ambiguous)"인지 이 분포 없이는
    말할 수 없다.
    """

    rows: tuple[LandingRow, ...]
    candidate_count: int
    ladder_counts: dict[str, int]
    regime_histogram: dict[str, int]
    negative_margins: tuple[NegativeMarginAudit, ...]
    regime_gate: str
    as_of: datetime | None

    @property
    def accepted_count(self) -> int:
        return len(self.rows)


def _candidate_query(db: Session, *, as_of: datetime | None) -> Query[Any]:
    """게시 하한을 가진 정산 공고의 후보 행(SELECT 전용).

    ``Query[Any]`` 는 형제 로더(``award_rate_dataset._candidate_query``)와 같은 계약이다
    — old-style ``Column(...)`` 모델의 컬럼 튜플 선택은 행 shape 을 정적으로 표현할 수
    없고, 값 계약은 이 쿼리를 소비하는 :func:`build_landing_row` 가 못 박는다.
    """
    query = (
        db.query(
            Project.id.label("project_id"),
            Project.category.label("project_category"),
            Project.title,
            Project.description,
            Project.requirements,
            Project.award_floor_rate,
            HistoricalData.category.label("history_category"),
            HistoricalData.agency_name,
            HistoricalData.base_amount,
            HistoricalData.base_amount_basis,
            HistoricalData.base_amount_estimated,
            HistoricalData.reserve_prices,
            HistoricalData.opened_at,
            TenderResult.winning_amount,
            TenderResult.winning_rate,
        )
        .join(HistoricalData, HistoricalData.project_id == Project.id)
        .join(
            TenderResult,
            and_(
                TenderResult.project_id == Project.id,
                TenderResult.is_current.is_(True),
            ),
        )
        .filter(Project.award_floor_rate.isnot(None))
        # 개찰일이 없으면 era tier 도 as-of 도 성립하지 않는다 → 항상 제외.
        .filter(HistoricalData.opened_at.isnot(None))
    )
    if as_of is not None:
        query = query.filter(HistoricalData.opened_at <= as_of)
    return query.order_by(HistoricalData.opened_at.desc(), HistoricalData.id.desc())


def regime_text(
    title: str | None, description: str | None, requirements: str | None
) -> str:
    """레짐 분류에 넣을 공고 텍스트 — 제목+본문+요구사항(라이브 추천과 같은 조합).

    라이브 경로(``opportunity_analysis/orchestration``)가 predictor 에 넘기는 것과 같은
    조합이다(#239 title 정렬). 백테스트만 다른 텍스트를 보면 여기서 잰 레짐 분포가
    서빙이 마주할 분포가 아니게 된다.
    """
    return " ".join(str(part) for part in (title, description, requirements) if part)


def _candidate_from_record(record: Row[Any], project_id: int) -> LandingCandidate:
    """쿼리 행 → 사다리 입력 값 객체. **여기가 유일한 ORM 접점**이다.

    컬럼 튜플 Row 는 정적 shape 이 없으므로(``_candidate_query`` 참조) 그 약한 경계를
    이 한 함수에 가두고, 사다리는 :class:`LandingCandidate` 로 계약을 받는다.
    """
    return LandingCandidate(
        project_id=project_id,
        opened_at=record.opened_at,
        category=record.history_category or record.project_category,
        agency_name=record.agency_name,
        regime_text=regime_text(record.title, record.description, record.requirements),
        award_floor_rate=record.award_floor_rate,
        winning_amount=record.winning_amount,
        winning_rate=record.winning_rate,
        base_amount=record.base_amount,
        base_amount_basis=record.base_amount_basis,
        base_amount_estimated=record.base_amount_estimated,
        reserve_prices=record.reserve_prices,
    )


@dataclass
class _CorpusAccumulator:
    """훑으면서 모으는 것들 — 통과 행·거부 계수·레짐 분포·음수 마진 감사.

    루프 본문이 아니라 여기에 모으는 이유는 **회계가 코퍼스의 일부**이기 때문이다.
    탈락을 조용히 버리면 "표본이 얕다"와 "표본이 오염됐다"가 리포트에서 구별되지 않는다.
    """

    seen: set[int] = field(default_factory=set)
    rows: list[LandingRow] = field(default_factory=list)
    negatives: list[NegativeMarginAudit] = field(default_factory=list)
    ladder: Counter[str] = field(default_factory=Counter)
    regimes: Counter[str] = field(default_factory=Counter)

    def claim(self, project_id: int) -> bool:
        """처음 보는 공고면 ``True``(중복 개찰 행은 분포에 두 번 실리지 않는다)."""
        if project_id in self.seen:
            return False
        self.seen.add(project_id)
        return True

    def add(self, outcome: LandingRowOutcome) -> None:
        if outcome.regime_label is not None:
            self.regimes[outcome.regime_label] += 1
        if outcome.negative_margin is not None:
            self.negatives.append(outcome.negative_margin)
        if outcome.row is None:
            self.ladder[outcome.stage.value if outcome.stage else "unknown"] += 1
            return
        self.rows.append(outcome.row)

    def build(self, *, regime_gate: str, as_of: datetime | None) -> AwardLandingCorpus:
        return AwardLandingCorpus(
            rows=tuple(
                sorted(self.rows, key=lambda item: (item.opened_at, item.project_id))
            ),
            candidate_count=len(self.seen),
            ladder_counts={
                stage.value: self.ladder.get(stage.value, 0) for stage in LadderStage
            },
            regime_histogram=dict(sorted(self.regimes.items())),
            negative_margins=tuple(
                sorted(self.negatives, key=lambda item: (item.margin, item.project_id))
            ),
            regime_gate=regime_gate,
            as_of=as_of,
        )


def load_award_landing_corpus(
    db: Session,
    *,
    as_of: datetime | None = None,
    regime_gate: str = DEFAULT_REGIME_GATE,
) -> AwardLandingCorpus:
    """게시 하한 × 정산 낙찰 코퍼스를 사다리 회계와 함께 싣는다(write/commit 없음).

    ``as_of`` 는 그 시각 **이하** 개찰만 본다(시간 누수 차단). 한 공고당 한 행만 센다 —
    같은 개찰이 여러 ``HistoricalData`` 로 적재돼도 중복 가중되지 않게 최신 행만 취한다.
    """
    if regime_gate not in REGIME_GATE_POLICIES:
        raise ValueError(
            f"unknown regime gate {regime_gate!r}; "
            f"declared: {sorted(REGIME_GATE_POLICIES)}"
        )
    allowed = REGIME_GATE_POLICIES[regime_gate]
    accumulator = _CorpusAccumulator()
    for record in _candidate_query(db, as_of=as_of).all():
        project_id = int(record.project_id)
        if not accumulator.claim(project_id):
            continue
        accumulator.add(
            build_landing_row(
                _candidate_from_record(record, project_id), regime_labels=allowed
            )
        )
    return accumulator.build(regime_gate=regime_gate, as_of=as_of)
