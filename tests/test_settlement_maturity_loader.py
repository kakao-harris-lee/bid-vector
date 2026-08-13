"""정산 성숙도 관측 로더 — "미정산은 0.0 이지 NULL 이 아니다" 회귀 가드.

이 파일이 지키는 성질 하나가 트랙 전체를 좌우한다: ``winning_amount`` 를 ``IS NOT NULL``
로 판정하면 성숙도가 **항상 100%** 로 나오고, 그러면 embargo 는 아무것도 거르지 않으면서
"성숙한 구간만 썼다"고 보고한다. 실제로 그 함정에 먼저 빠진 사람이 있었다.
"""

from datetime import UTC, datetime, timedelta

from app.domain.settlement_maturity import build_weekly_maturity
from app.models.models import HistoricalData, Project
from app.models.pipeline import TenderResult
from app.services.settlement_maturity import load_settlement_observations

_NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _insert(
    db,
    *,
    project_id: int,
    opened_at: datetime,
    winning_amount: float | None = 90_000_000.0,
    issuing_agency: str | None = "시청",
    deadline: datetime | None = None,
    with_history: bool = True,
    with_result: bool = True,
) -> None:
    db.add(
        Project(
            id=project_id,
            title=f"SM-{project_id}",
            category="construction",
            issuing_agency=issuing_agency,
            deadline=deadline or opened_at,
        )
    )
    if with_history:
        db.add(
            HistoricalData(
                project_id=project_id,
                notice_number=f"SM-{project_id}",
                category="construction",
                agency_name="시청",
                base_amount=100_000_000.0,
                opened_at=opened_at,
            )
        )
    if with_result:
        db.add(
            TenderResult(
                project_id=project_id,
                is_current=True,
                winning_amount=winning_amount,
            )
        )
    db.commit()


def test_zero_winning_amount_counts_as_unsettled(test_db):
    """열린 공고의 ``winning_amount`` 는 NULL 이 아니라 0.0 이다(실측)."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=3))
    _insert(
        test_db, project_id=2, opened_at=_NOW - timedelta(days=3), winning_amount=0.0
    )

    observations = load_settlement_observations(test_db, now=_NOW)

    assert len(observations) == 2
    assert sorted(item.settled for item in observations) == [False, True]
    (window,) = build_weekly_maturity(observations)
    assert (window.settled_count, window.opened_count) == (1, 2)


def test_missing_result_row_counts_as_unsettled(test_db):
    """결과 행이 없는 공고는 "결과를 모른다"이지 "모집단에 없다"가 아니다."""
    _insert(
        test_db,
        project_id=1,
        opened_at=_NOW - timedelta(days=3),
        with_result=False,
    )

    (observation,) = load_settlement_observations(test_db, now=_NOW)
    assert observation.settled is False


def test_non_feed_notices_are_outside_the_population(test_db):
    """개찰결과 피드로만 본 공고는 **정산된 순간에만** 관측되므로 분모가 없다."""
    _insert(
        test_db, project_id=1, opened_at=_NOW - timedelta(days=3), issuing_agency=None
    )
    _insert(test_db, project_id=2, opened_at=_NOW - timedelta(days=3))

    observations = load_settlement_observations(test_db, now=_NOW)
    assert len(observations) == 1


def test_future_openings_are_not_part_of_the_denominator(test_db):
    """아직 개찰되지 않은 공고를 분모에 넣으면 최근 구간이 영원히 미성숙으로 보인다."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=3))
    _insert(test_db, project_id=2, opened_at=_NOW + timedelta(days=3))

    observations = load_settlement_observations(test_db, now=_NOW)
    assert len(observations) == 1


def test_notices_without_an_opening_time_fall_back_to_the_deadline(test_db):
    """대체가 없으면 개찰 시각을 못 받은 공고가 분모에서 조용히 빠져 성숙도를 부풀린다."""
    deadline = _NOW - timedelta(days=4)
    _insert(
        test_db,
        project_id=1,
        opened_at=deadline,
        deadline=deadline,
        winning_amount=0.0,
        with_history=False,
    )

    (observation,) = load_settlement_observations(test_db, now=_NOW)
    assert observation.opened_at == deadline.astimezone(UTC)
    assert observation.settled is False
