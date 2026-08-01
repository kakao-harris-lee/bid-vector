"""Session-lifecycle seam for the Celery/background path.

``app.core.database.task_session`` replaces the repeated
``db = SessionLocal(); try: ... finally: db.close()`` boilerplate in
``app/tasks/``. Its contract is deliberately narrow, because task bodies were
migrated onto it without changing their outcomes:

* the session is always closed (success *and* exception paths),
* nothing is committed or rolled back implicitly — bodies that need either keep
  doing it explicitly,
* the factory is injectable (``session_factory=``), and when omitted the
  module-level ``SessionLocal`` is resolved at call time so tests driving a whole
  task have exactly one patch surface.

The FastAPI request dependency ``get_db`` now delegates to the same helper (it was
a byte-for-byte copy of that boilerplate), so the request path and the background
path cannot drift apart. ``TestRequestDependencyLifecycle`` below pins that the
delegation is lifecycle-equivalent on both the success and the exception path.
"""

from __future__ import annotations

import pytest

from app.core import database as database_mod
from app.core.database import task_session


class _FakeSession:
    """Session double recording lifecycle calls."""

    def __init__(self) -> None:
        self.closed = False
        self.committed = 0
        self.rolled_back = 0

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed = True


def test_task_session_uses_injected_factory_and_closes_on_success():
    session = _FakeSession()
    calls: list[int] = []

    def factory():
        calls.append(1)
        return session

    with task_session(factory) as db:
        assert db is session
        assert session.closed is False

    assert calls == [1]
    assert session.closed is True


def test_task_session_closes_on_exception_without_implicit_rollback():
    """Exception path closes the session but must not add a hidden rollback."""
    session = _FakeSession()

    with pytest.raises(RuntimeError, match="boom"):
        with task_session(lambda: session):
            raise RuntimeError("boom")

    assert session.closed is True
    assert session.rolled_back == 0
    assert session.committed == 0


def test_task_session_does_not_commit_implicitly():
    session = _FakeSession()

    with task_session(lambda: session):
        pass

    assert session.committed == 0
    assert session.rolled_back == 0


def test_task_session_default_factory_is_resolved_at_call_time(monkeypatch):
    """Omitting the factory falls back to the patchable module-level SessionLocal."""
    session = _FakeSession()
    monkeypatch.setattr(database_mod, "SessionLocal", lambda: session)

    with task_session() as db:
        assert db is session

    assert session.closed is True


class TestRequestDependencyLifecycle:
    """``get_db`` 위임이 종전 인라인 try/finally 와 수명 동치인지 고정한다.

    이 의존성은 모든 요청 경로에 걸리므로 회귀 비용이 크다. 세션을 **한 번** 열고, 요청
    본문이 끝나거나 예외로 끝나도 **정확히 한 번** 닫고, 암묵 commit/rollback 을 넣지
    않는다는 세 가지를 고정한다(§4.7-3 의 주입 seam 을 그대로 재사용).
    """

    def test_opens_one_session_and_closes_it_after_the_request(self, monkeypatch):
        session = _FakeSession()
        opened: list[int] = []
        monkeypatch.setattr(
            database_mod, "SessionLocal", lambda: (opened.append(1), session)[1]
        )

        generator = database_mod.get_db()
        yielded = next(generator)

        assert yielded is session
        assert opened == [1]
        assert session.closed is False

        with pytest.raises(StopIteration):
            next(generator)

        assert session.closed is True
        assert session.committed == 0
        assert session.rolled_back == 0

    def test_closes_the_session_when_the_endpoint_raises(self, monkeypatch):
        """FastAPI 는 엔드포인트 예외를 yield 지점으로 되던진다 — 그때도 닫아야 한다."""
        session = _FakeSession()
        monkeypatch.setattr(database_mod, "SessionLocal", lambda: session)

        generator = database_mod.get_db()
        next(generator)

        with pytest.raises(RuntimeError, match="endpoint failed"):
            generator.throw(RuntimeError("endpoint failed"))

        assert session.closed is True
        # 암묵 rollback 을 넣지 않는다(종전 동작 유지 — 필요한 곳은 명시적으로 한다).
        assert session.rolled_back == 0
        assert session.committed == 0

    def test_closes_the_session_when_the_generator_is_closed_early(self, monkeypatch):
        """클라이언트 중단 등으로 의존성 제너레이터가 close() 되는 경로."""
        session = _FakeSession()
        monkeypatch.setattr(database_mod, "SessionLocal", lambda: session)

        generator = database_mod.get_db()
        next(generator)
        generator.close()

        assert session.closed is True

    def test_request_path_reaches_the_patched_factory_end_to_end(self, monkeypatch):
        """실제 요청이 이 의존성을 타는지(배선 확인) — 패치한 팩토리가 쓰인다."""
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        session = _FakeSession()
        monkeypatch.setattr(database_mod, "SessionLocal", lambda: session)

        app = FastAPI()

        @app.get("/probe")
        def probe(db=Depends(database_mod.get_db)):  # noqa: ANN001 - test route
            return {"same_session": db is session, "closed_during": session.closed}

        with TestClient(app) as client:
            payload = client.get("/probe").json()

        assert payload == {"same_session": True, "closed_during": False}
        assert session.closed is True


def test_reserve_detail_backfill_job_honours_injected_session_factory():
    """A delegated task body opens/closes its session through the injected seam.

    The body's payload argument is the validated DTO the ``@task`` shell promotes
    (``app.schemas.task_payloads``), so the seam is exercised with the same input
    the worker produces.
    """
    from app.schemas.task_payloads import ScsbidReserveDetailBackfillRequest
    from app.tasks.reserve_detail_backfill import (
        run_scsbid_reserve_detail_backfill_job,
    )

    session = _FakeSession()
    chained: list[ScsbidReserveDetailBackfillRequest] = []

    # No service key configured in the test environment -> the body returns the
    # missing-key result without HTTP, but it must still have taken its session
    # from the injected factory and closed it.
    result = run_scsbid_reserve_detail_backfill_job(
        ScsbidReserveDetailBackfillRequest.model_validate(
            {"notices": [{"notice_number": "N-1", "category": "construction"}]}
        ),
        enqueue_continuation=lambda rest: bool(chained.append(rest)),
        session_factory=lambda: session,
    )

    assert result["errors"] >= 1
    assert session.closed is True
    assert chained == []


def _forbid_global_session(monkeypatch) -> None:
    """Fail loudly if a body falls back to the module global instead of injection."""

    def _forbidden():  # pragma: no cover - only runs when injection is broken
        raise AssertionError("module-global SessionLocal must not be used")

    monkeypatch.setattr(database_mod, "SessionLocal", _forbidden)


def test_synthetic_backtest_job_honours_injected_session_factory(monkeypatch):
    """The synthetic-backtest body hands the injected session to the service."""
    from app.schemas.task_payloads import SyntheticOperatorBacktestTaskRequest
    from app.services.synthetic_backtest import SyntheticBacktestService
    from app.tasks.backtest_jobs import run_synthetic_operator_backtest_job

    session = _FakeSession()
    captured: dict = {}
    _forbid_global_session(monkeypatch)

    def _fake_run_for_all(self, db, **kwargs):
        captured["db"] = db
        return {"ok": "synthetic"}

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", _fake_run_for_all)

    result = run_synthetic_operator_backtest_job(
        SyntheticOperatorBacktestTaskRequest(limit=1), session_factory=lambda: session
    )

    assert result == {"ok": "synthetic"}
    assert captured["db"] is session
    assert session.closed is True


def test_historical_backtest_job_honours_injected_session_factory(monkeypatch):
    """The historical-backtest body hands the injected session to the service."""
    from app.schemas.task_payloads import HistoricalBacktestTaskRequest
    from app.services.paper_bidding_backtest import PaperBiddingBacktestService
    from app.tasks.backtest_jobs import run_historical_backtest_job

    session = _FakeSession()
    captured: dict = {}
    _forbid_global_session(monkeypatch)

    def _fake_run_historical(self, db, **kwargs):
        captured["db"] = db
        return {"ok": "historical"}

    monkeypatch.setattr(
        PaperBiddingBacktestService, "run_historical_backtest", _fake_run_historical
    )

    result = run_historical_backtest_job(
        HistoricalBacktestTaskRequest(limit=1), session_factory=lambda: session
    )

    assert result == {"ok": "historical"}
    assert captured["db"] is session
    assert session.closed is True


def test_koneps_collection_job_honours_injected_session_factory(monkeypatch):
    """The collection body queries the injected session and closes it on failure."""
    from app.schemas.task_payloads import CrawlTaskRequest
    from app.tasks.collection_jobs import run_koneps_collection_job

    class _BoomSession(_FakeSession):
        """Injected session whose first query fails, so no HTTP is attempted."""

        def query(self, *args, **kwargs):
            raise RuntimeError("injected session reached")

    session = _BoomSession()
    factory_calls: list[int] = []
    _forbid_global_session(monkeypatch)

    def factory():
        factory_calls.append(1)
        return session

    # crawl_job_id set -> the body's first act is a lookup on the injected
    # session, so the failure proves the injection took effect before any
    # collector I/O; the seam must still close the session on the error path.
    with pytest.raises(RuntimeError, match="injected session reached"):
        run_koneps_collection_job(
            None,
            request=CrawlTaskRequest(),
            crawl_job_id=1,
            enqueue_deferred_embedding_backfill=lambda ids: 0,
            enqueue_deferred_reserve_detail_backfill=lambda notices: 0,
            session_factory=factory,
        )

    assert factory_calls == [1]
    assert session.closed is True
