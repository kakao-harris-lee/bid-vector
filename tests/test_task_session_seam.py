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


def test_reserve_detail_backfill_job_honours_injected_session_factory():
    """A delegated task body opens/closes its session through the injected seam."""
    from app.tasks.reserve_detail_backfill import (
        run_scsbid_reserve_detail_backfill_job,
    )

    session = _FakeSession()
    chained: list[list[dict]] = []

    # No service key configured in the test environment -> the body returns the
    # missing-key result without HTTP, but it must still have taken its session
    # from the injected factory and closed it.
    result = run_scsbid_reserve_detail_backfill_job(
        [{"notice_number": "N-1", "category": "construction"}],
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
        {"limit": 1}, session_factory=lambda: session
    )

    assert result == {"ok": "synthetic"}
    assert captured["db"] is session
    assert session.closed is True


def test_historical_backtest_job_honours_injected_session_factory(monkeypatch):
    """The historical-backtest body hands the injected session to the service."""
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
        {"limit": 1}, session_factory=lambda: session
    )

    assert result == {"ok": "historical"}
    assert captured["db"] is session
    assert session.closed is True


def test_koneps_collection_job_honours_injected_session_factory(monkeypatch):
    """The collection body queries the injected session and closes it on failure."""
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
            request_payload={},
            crawl_job_id=1,
            enqueue_deferred_embedding_backfill=lambda ids: 0,
            enqueue_deferred_reserve_detail_backfill=lambda notices: 0,
            session_factory=factory,
        )

    assert factory_calls == [1]
    assert session.closed is True
