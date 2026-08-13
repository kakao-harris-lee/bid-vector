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

import logging
from datetime import timedelta
from types import SimpleNamespace

from app.core.time import utc_now
from app.models.models import ProjectSimilaritySnapshot
from app.schemas.similarity_runtime import SimilarityProjectionBackfillResult
from app.services.similarity_projection_backfill import (
    _backfill_candidates,
    stage_active_similarity_projection_backfill,
)
from app.tasks import inference_jobs

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


# ── 노후도 우선 정렬이 만든 새 위험: 재계산 불가 대상의 머리 응집 ──────────────
#
# 스테이징되지 못한 대상은 computed_at 이 그대로라 다음 배치에서도 최선두다. id
# 순서에서는 그런 대상이 id 공간에 흩어져 슬롯 1개씩만 잠식했지만, 노후도 우선에서는
# 머리에 뭉쳐 배치를 통째로 채우고 **회전을 멈춘다**. 라이브 실측은 현재 0건이지만
# (활성 전원 embedding_state=ready) 그 상태에 신호가 없다는 것이 위험이었다.


def _backfill_result(*, selected: int, staged: int, blocked: list[int] | None = None):
    return SimilarityProjectionBackfillResult(
        selected_count=selected,
        staged_count=staged,
        limit=100,
        blocked_project_ids=blocked or [],
    )


def test_targets_that_cannot_be_staged_are_named_in_the_result(test_db):
    """무엇이 막혔는지 결과에 남아야 진단이 된다 — 개수만으로는 찾아갈 수 없다."""
    project = _make_project(test_db, title="투영 불가 대상")

    class _NeverReady(_StubReadModel):
        def embedding_state(self, _project):
            return SimpleNamespace(status="pending")

    class _UnusedOutbox:
        def append_embedding_ready_event(self, *args, **kwargs):
            raise AssertionError("준비되지 않은 대상을 스테이징하면 안 된다")

    result = stage_active_similarity_projection_backfill(
        test_db, read_model=_NeverReady(), outbox=_UnusedOutbox(), limit=10
    )

    assert result.selected_count == 1
    assert result.staged_count == 0
    assert result.blocked_project_ids == [int(project.id)]


def test_a_blocked_target_stays_at_the_head_of_the_next_batch(test_db):
    """정렬이 만든 위험 자체를 고정한다: 막힌 대상은 전진하지 않아 계속 최선두다."""
    blocked = _make_project(test_db, title="영원히 최선두")
    other = _make_project(test_db, title="정상 대상")
    _aged_snapshot(test_db, blocked, hours_ago=9)
    _aged_snapshot(test_db, other, hours_ago=5)

    assert _candidate_ids(test_db, limit=1) == [blocked.id]
    # 스테이징에 실패하면 computed_at 이 그대로이므로 다음 배치도 같은 대상이다.
    assert _candidate_ids(test_db, limit=1) == [blocked.id]


def test_a_fully_blocked_batch_is_reported_as_a_stalled_rotation(caplog):
    with caplog.at_level(logging.WARNING, logger=inference_jobs.__name__):
        inference_jobs._warn_if_rotation_stalled(
            _backfill_result(selected=100, staged=0, blocked=[7, 8, 9])
        )

    assert "rotation stalled" in caplog.text
    assert "selected=100" in caplog.text
    assert "[7, 8, 9]" in caplog.text


def test_a_batch_that_staged_anything_is_not_reported(caplog):
    """부분 차단은 회전을 멈추지 않는다 — 매 배치 경고하면 신호가 죽는다."""
    with caplog.at_level(logging.WARNING, logger=inference_jobs.__name__):
        inference_jobs._warn_if_rotation_stalled(
            _backfill_result(selected=100, staged=1, blocked=list(range(99)))
        )

    assert "rotation stalled" not in caplog.text


def test_an_empty_candidate_set_is_not_a_stall(caplog):
    """후보가 없어 0건인 것은 드레인 완료지 정지가 아니다."""
    with caplog.at_level(logging.WARNING, logger=inference_jobs.__name__):
        inference_jobs._warn_if_rotation_stalled(
            _backfill_result(selected=0, staged=0)
        )

    assert "rotation stalled" not in caplog.text
