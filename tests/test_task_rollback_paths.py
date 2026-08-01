"""commit 하는 task body 의 실패 경로 계약 — rollback 1회 + 예외 전파 + close.

``app/tasks/jobs.py`` 에서 세션에 **쓰는** task 는 실패 시 세 가지를 동시에 지켜야 한다:

1. ``db.rollback()`` 을 **정확히 한 번** 호출한다 — 부분 flush 를 남긴 세션이 그대로
   닫히면 SQLAlchemy 구현/백엔드에 따라 무엇이 남는지 예측할 수 없다.
2. 예외를 **삼키지 않고 전파**한다 — Celery 결과가 FAILURE 로 남아야 재시도/알림/증적이
   동작한다(조용한 성공은 고아 상태를 감춘다).
3. 세션을 닫는다(``task_session`` seam 의 ``finally``).

두 task 는 각각 임베딩 재생성(쓰기)과 고아 task-run 정리(상태 전이 쓰기)라 이 계약이
깨지면 각각 임베딩 반쪽 갱신과 재조정 자체의 고아를 남긴다. 여기서는 서비스가 raise 하도록
몰아 그 계약을 단정한다(task 코드 변경 없이 테스트만 추가 —
``tests/test_operator_strategy_monitor_finalize.py`` 의 task 래퍼 실패 테스트와 같은 방식).
"""

from __future__ import annotations

import pytest

from app.core import database as database_mod
from app.tasks import jobs


class _RecordingSession:
    """세션 수명 호출만 기록하는 더블(쿼리는 하지 않는다 — 서비스가 먼저 raise 한다)."""

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1


@pytest.fixture()
def recording_session(monkeypatch) -> _RecordingSession:
    """``task_session`` 의 기본 팩토리(모듈 전역)를 더블로 바꾼다."""
    session = _RecordingSession()
    monkeypatch.setattr(database_mod, "SessionLocal", lambda: session)
    return session


def test_rebuild_project_embeddings_rolls_back_once_and_propagates(
    recording_session, monkeypatch
):
    from app.services.project_similarity import ProjectSimilarityService

    def boom(self, db, **kwargs):
        raise RuntimeError("embedding rebuild failed")

    monkeypatch.setattr(
        ProjectSimilarityService, "rebuild_project_embeddings", boom
    )

    with pytest.raises(RuntimeError, match="embedding rebuild failed"):
        jobs.rebuild_project_embeddings(limit=1)

    assert recording_session.rolled_back == 1
    assert recording_session.committed == 0
    assert recording_session.closed == 1


def test_rebuild_project_embeddings_commits_once_on_success(
    recording_session, monkeypatch
):
    """대조군: 성공 경로는 commit 1회 · rollback 0회 — 가드가 성공을 삼키지 않는다."""
    from app.services.project_similarity import ProjectSimilarityService

    monkeypatch.setattr(
        ProjectSimilarityService,
        "rebuild_project_embeddings",
        lambda self, db, **kwargs: {"updated": 0},
    )

    assert jobs.rebuild_project_embeddings(limit=1) == {"updated": 0}
    assert recording_session.committed == 1
    assert recording_session.rolled_back == 0
    assert recording_session.closed == 1


def test_reconcile_stale_task_runs_rolls_back_once_and_propagates(
    recording_session, monkeypatch
):
    from app.services.stale_task_reconciler import StaleTaskReconcilerService

    def boom(self, db):
        raise RuntimeError("reconcile failed")

    monkeypatch.setattr(StaleTaskReconcilerService, "reconcile", boom)

    with pytest.raises(RuntimeError, match="reconcile failed"):
        jobs.reconcile_stale_task_runs()

    assert recording_session.rolled_back == 1
    # task 는 이 경로에서 commit 하지 않는다(서비스가 자체 commit — 대칭 단정).
    assert recording_session.committed == 0
    assert recording_session.closed == 1


def test_reconcile_stale_task_runs_returns_the_service_result_on_success(
    recording_session, monkeypatch
):
    """대조군: 성공 경로는 서비스 결과를 그대로 돌려주고 rollback 하지 않는다.

    이 body 는 서비스가 자체적으로 commit 하므로 task 는 commit 하지 않는다(종전 동작).
    """
    from app.services.stale_task_reconciler import StaleTaskReconcilerService

    monkeypatch.setattr(
        StaleTaskReconcilerService,
        "reconcile",
        lambda self, db: {"total_finalized": 0},
    )

    assert jobs.reconcile_stale_task_runs() == {"total_finalized": 0}
    assert recording_session.rolled_back == 0
    assert recording_session.closed == 1
