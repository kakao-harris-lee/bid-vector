"""선택 순서가 기아를 만들지 않는가 — 백필이 무엇을 **먼저** 고르는지.

후보 집합은 한 회전의 처리량보다 크고, 갱신된 대상은 포인터가 끝에 닿기 훨씬 전에
다시 후보가 된다. 그래서 ``Project.id`` 오름차순은 낮은 id 머리를 왕복할 뿐 꼬리에
도달하지 못한다. 그 꼬리는 **가장 최근 공고**다: 임베딩 때 최초 투영 1회를 받고,
최대 수명에서 stale 이 되고, 그 뒤로 어떤 파이프라인도 돌아오지 않는다 — 운영자가
아직 투찰할 수 있는 바로 그 공고들에서 빈 유사공고 패널이 영구히 남는다.

정합 규칙(:mod:`app.domain.projection_freshness`)이 막았다고 선언한 상태가 규칙
불일치가 아니라 **순서 기아**로 재현되는 경로라, 규칙과 별개로 순서를 고정한다.

여기 테스트들은 ``order_by(Project.id.asc())`` 로 되돌리면 실패해야 한다. 그래서
모든 케이스에서 **가장 오래된 스냅샷을 가장 높은 id 에 붙여** id 순서와 노후도
순서가 일부러 어긋나게 만든다.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_now
from app.models.models import ProjectSimilaritySnapshot
from app.services.similarity_projection_backfill import _backfill_candidates

from tests.test_similarity_projection_backfill_scope import (
    _StubReadModel,
    _make_project,
    _make_snapshot,
)

# A snapshot that recorded an empty corpus is always materially drifted (the
# threshold floors at one row), so these targets stay candidates no matter how
# often they are refreshed. That is the real steady state on the live embedding
# model, where the 0.5% threshold is ~18 rows against ~29 new rows an hour.
ALWAYS_STALE_CORPUS_COUNT = 0


def _aged_snapshot(db, project, *, hours_ago: float) -> ProjectSimilaritySnapshot:
    return _make_snapshot(
        db,
        project,
        corpus_embedding_count=ALWAYS_STALE_CORPUS_COUNT,
        corpus_embedding_updated_at=project.embedding_updated_at,
        computed_at=utc_now() - timedelta(hours=hours_ago),
    )


def _candidate_ids(db, limit: int) -> list[int]:
    return [int(p.id) for p in _backfill_candidates(db, _StubReadModel(), limit)]


def test_oldest_projection_is_selected_first(test_db):
    """가장 오래된 투영이 먼저. 오래된 쪽을 높은 id 에 붙여 id 순서와 어긋나게 둔다."""
    newest = _make_project(test_db, title="1시간 전 투영")
    middle = _make_project(test_db, title="3시간 전 투영")
    oldest = _make_project(test_db, title="9시간 전 투영")
    _aged_snapshot(test_db, newest, hours_ago=1)
    _aged_snapshot(test_db, middle, hours_ago=3)
    _aged_snapshot(test_db, oldest, hours_ago=9)

    assert _candidate_ids(test_db, limit=10) == [oldest.id, middle.id, newest.id]


def test_a_target_without_a_projection_outranks_every_stale_one(test_db):
    """투영이 아예 없는 대상이 가장 급하다 (NULLS FIRST)."""
    stale = _make_project(test_db, title="아주 오래된 투영")
    _aged_snapshot(test_db, stale, hours_ago=99)
    never_projected = _make_project(test_db, title="투영 없음")

    assert _candidate_ids(test_db, limit=10) == [never_projected.id, stale.id]


def test_a_bounded_batch_reaches_the_newest_notices(test_db):
    """배치가 후보 집합보다 작아도 최신 공고(높은 id)가 선택된다."""
    targets = [_make_project(test_db, title=f"공고 {index}") for index in range(6)]
    for index, project in enumerate(targets):
        _aged_snapshot(test_db, project, hours_ago=index + 1)

    selected = _candidate_ids(test_db, limit=2)

    assert selected == [targets[-1].id, targets[-2].id]


def test_a_bounded_batch_rotates_instead_of_starving_the_tail(test_db):
    """핵심 회귀: 재무효화가 계속되어도 모든 대상이 한 회전 안에 서빙된다.

    전 대상이 영구히 후보인 상태(라이브 모델의 실제 정상 상태)에서 배치를 반복한다.
    노후도 순서면 정확히 한 바퀴에 전원이 서빙되고, id 순서면 낮은 id 세 건만
    매 라운드 다시 선택되어 꼬리가 영원히 굶는다.
    """
    targets = [_make_project(test_db, title=f"공고 {index}") for index in range(9)]
    snapshots = {}
    for index, project in enumerate(targets):
        # 가장 낮은 id 가 가장 최근 투영 — id 순서와 노후도 순서를 반대로 둔다.
        snapshots[int(project.id)] = _aged_snapshot(
            test_db, project, hours_ago=index + 1
        )

    batch_size = 3
    served: set[int] = set()
    for _round in range(len(targets) // batch_size):
        batch = _backfill_candidates(test_db, _StubReadModel(), batch_size)
        assert batch, "후보가 남아 있는데 배치가 비었다"
        for project in batch:
            served.add(int(project.id))
            snapshots[int(project.id)].computed_at = utc_now()
        test_db.flush()

    assert served == {int(project.id) for project in targets}


def test_refreshing_a_target_moves_it_to_the_back_of_the_rotation(test_db):
    """갱신된 대상은 즉시 다시 뽑히지 않는다 — 왕복(포인터 되돌림)이 없어야 한다."""
    first = _make_project(test_db, title="가장 오래된")
    second = _make_project(test_db, title="두 번째")
    first_snapshot = _aged_snapshot(test_db, first, hours_ago=9)
    _aged_snapshot(test_db, second, hours_ago=5)

    assert _candidate_ids(test_db, limit=1) == [first.id]

    first_snapshot.computed_at = utc_now()
    test_db.flush()

    assert _candidate_ids(test_db, limit=1) == [second.id]
