"""#212 회귀: json 컬럼을 가진 엔티티에 DISTINCT 를 걸면 Postgres 에서 죽는다.

배경
----
``Project.eligibility_raw`` 는 ``JSON`` 컬럼이고, PostgreSQL 의 ``json`` 타입에는
동등 연산자가 없다. 그래서 엔티티 전체를 뽑는 ``SELECT DISTINCT`` 는
``UndefinedFunction`` 으로 실패한다. SQLite 는 같은 쿼리를 통과시키므로 유닛
스위트가 이 갭을 잡지 못했고, 2026-07-19 라이브 실행이 잡았다
(:mod:`app.services.opening_result_collection` 의 ``_candidate_projects`` 주석).

그때의 수정은 "중복 제거를 쿼리가 아니라 파이썬 id 집합으로 한다"였다. 그 교훈은
지금까지 주석과 SQLite 테스트로만 남아 있어서, 누군가 ``.distinct()`` 를 다시
붙여도 CI 는 초록이었다. 여기서는 **실제 Postgres** 에 대고 세 가지를 고정한다.

1. 현재 후보 조회가 json 컬럼이 채워진 실데이터에서 살아남고 중복도 제거한다.
2. 순진한 엔티티 ``SELECT DISTINCT`` 는 여전히 실패한다(가드가 미신이 아님).
3. 안전한 대안(스칼라 id 프로젝션)은 통과한다(수정 패턴의 실행 가능한 문서).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.exc import ProgrammingError

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, Project
from app.services.opening_result_collection import OpeningResultCollectionService

pytestmark = pytest.mark.postgres

# 실공고에서 관측되는 형태의 자격요건 원문(#212 재현에는 json 이 실제로 차 있어야
# 관측 조건이 라이브와 같아진다).
ELIGIBILITY_RAW = {
    "license_limits": ["항만및해안공사업"],
    "region_limits": ["울산광역시"],
    "notes": None,
}


def _seed_real_bid_project(session, *, notice_number: str, bid_count: int) -> Project:
    """실투찰 레코드가 ``bid_count`` 건 달린 마감 지난 공고 1건을 만든다."""
    project = Project(
        title=f"실투찰 {notice_number}",
        description="postgres tier fixture",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=notice_number,
        status="open",
        deadline=utc_now() - timedelta(days=1),
        eligibility_raw=ELIGIBILITY_RAW,
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    operator = ensure_operator_account(session)
    for index in range(bid_count):
        session.add(
            BidDecisionRecord(
                project_id=project.id,
                operator_id=operator.id,
                submitted_bid_amount=77_308_840.0 + index,
                submitted_at=utc_now(),
            )
        )
    session.commit()
    return project


def test_candidate_projects_dedupes_on_postgres_with_json_column(postgres_session):
    """다건 실투찰 join 의 중복 제거가 실제 Postgres 에서 성립한다.

    SQLite 판(``tests/test_opening_result_collection.py``)과 같은 시나리오지만,
    dialect 가 실제로 json 컬럼을 들고 있는 상태에서 실행된다.
    """
    operator = ensure_operator_account(postgres_session)
    project = _seed_real_bid_project(
        postgres_session, notice_number="R26BK00000212", bid_count=3
    )

    candidates = OpeningResultCollectionService()._candidate_projects(
        postgres_session, operator=operator, now=utc_now(), limit=10
    )

    assert [candidate.id for candidate in candidates] == [project.id]
    assert candidates[0].eligibility_raw == ELIGIBILITY_RAW


def test_entity_select_distinct_fails_on_json_column(postgres_session):
    """순진한 엔티티 DISTINCT 는 Postgres 에서 여전히 실패해야 한다.

    이 테스트가 실패하기 시작하면 그건 회귀가 아니라 전제 변화다(컬럼 타입이
    ``jsonb`` 로 바뀌었거나 PostgreSQL 이 ``json`` 등치를 갖게 된 경우). 그때는
    ``_candidate_projects`` 의 파이썬 dedupe 주석도 함께 갱신해야 하므로,
    조용히 지나가지 않고 여기서 걸리는 편이 맞다.
    """
    _seed_real_bid_project(
        postgres_session, notice_number="R26BK00000213", bid_count=2
    )

    with pytest.raises(ProgrammingError) as excinfo:
        postgres_session.query(Project).distinct().all()

    assert "json" in str(excinfo.value).lower()
    postgres_session.rollback()


def test_scalar_id_distinct_is_the_safe_alternative(postgres_session):
    """json 컬럼을 투영에서 빼면 DISTINCT 는 안전하다 (수정 패턴)."""
    project = _seed_real_bid_project(
        postgres_session, notice_number="R26BK00000214", bid_count=2
    )

    rows = (
        postgres_session.query(Project.id)
        .join(BidDecisionRecord, BidDecisionRecord.project_id == Project.id)
        .filter(BidDecisionRecord.submitted_bid_amount.isnot(None))
        .distinct()
        .all()
    )

    assert [row.id for row in rows] == [project.id]
