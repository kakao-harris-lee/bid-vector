"""Overlapping ticks of the projection backfill must not run concurrently.

On 2026-08-13 the schedule fired every 60s while one batch took 55-80s, so every
tick landed on top of the previous one: 193 consecutive runs pinned the single
inference worker for four hours and 21,321 events piled up behind them.
Overlapping runs do not share the work — they select the same head of the
candidate set — so the second one is pure waste plus contention.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.services.task_singleton import AdvisorySingletonLease, singleton_lease
from app.tasks import inference_jobs


class _FakePostgresBind:
    """A bind whose advisory locks behave like PostgreSQL session locks.

    ``AdvisorySingletonLease`` short-circuits to "always acquired" on non-
    PostgreSQL dialects, so SQLite cannot exercise the contention path at all.
    """

    class _Dialect:
        name = "postgresql"

    def __init__(self) -> None:
        self.dialect = self._Dialect()
        self.engine = self
        self.held: set[int] = set()
        self.open_connections = 0

    def connect(self) -> "_FakeConnection":
        self.open_connections += 1
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, bind: _FakePostgresBind) -> None:
        self._bind = bind
        self._owned: set[int] = set()

    def execute(self, statement, parameters):
        sql = str(statement)
        lock_id = int(parameters["lock_id"])
        if "pg_try_advisory_lock" in sql:
            if lock_id in self._bind.held:
                return _FakeResult(False)
            self._bind.held.add(lock_id)
            self._owned.add(lock_id)
            return _FakeResult(True)
        if "pg_advisory_unlock" in sql:
            self._bind.held.discard(lock_id)
            self._owned.discard(lock_id)
            return _FakeResult(True)
        raise AssertionError(f"unexpected statement: {sql}")

    def close(self) -> None:
        # A dropped session releases everything it held — this is why the lease
        # needs no expiry: a killed worker cannot wedge the schedule.
        for lock_id in set(self._owned):
            self._bind.held.discard(lock_id)
        self._owned.clear()
        self._bind.open_connections -= 1


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


def test_second_holder_is_refused_while_the_first_holds_the_lease():
    bind = _FakePostgresBind()

    with singleton_lease(bind, "backfill") as first:
        with singleton_lease(bind, "backfill") as second:
            assert first is True
            assert second is False


def test_lease_is_reusable_after_the_holder_finishes():
    bind = _FakePostgresBind()

    with singleton_lease(bind, "backfill") as first:
        assert first is True
    with singleton_lease(bind, "backfill") as second:
        assert second is True

    assert bind.held == set()
    assert bind.open_connections == 0


def test_lease_is_released_even_when_the_body_raises():
    bind = _FakePostgresBind()

    with pytest.raises(RuntimeError, match="boom"):
        with singleton_lease(bind, "backfill") as acquired:
            assert acquired is True
            raise RuntimeError("boom")

    with singleton_lease(bind, "backfill") as after:
        assert after is True


def test_a_dead_holder_does_not_wedge_the_lease():
    """A PostgreSQL *session* lock dies with its connection — no expiry to tune."""
    bind = _FakePostgresBind()
    abandoned = AdvisorySingletonLease(bind, "backfill")
    assert abandoned.acquire() is True

    abandoned._connection.close()  # worker killed; connection dropped

    with singleton_lease(bind, "backfill") as acquired:
        assert acquired is True


def test_different_keys_do_not_block_each_other():
    bind = _FakePostgresBind()

    with singleton_lease(bind, "backfill") as first:
        with singleton_lease(bind, "some_other_periodic_task") as second:
            assert first is True
            assert second is True


@contextmanager
def _busy_lease():
    yield False


@contextmanager
def _free_lease():
    yield True


class _NoCloseSession:
    """Keep the pytest-owned session alive across ``task_session``."""

    def __init__(self, db) -> None:
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self) -> None:
        return None


def test_task_skips_its_body_when_the_previous_tick_still_holds_the_lease(
    test_db, monkeypatch
):
    monkeypatch.setattr(
        "app.core.database.SessionLocal", lambda: _NoCloseSession(test_db)
    )
    calls: list[int] = []

    class _NeverRuns:
        def stage_active_similarity_projection_backfill(self, db, *, limit):
            calls.append(limit)
            raise AssertionError("body ran while the lease was held")

    monkeypatch.setattr(inference_jobs, "ProjectSimilarityService", _NeverRuns)
    monkeypatch.setattr(
        inference_jobs,
        "singleton_lease",
        lambda bind, key: _busy_lease(),
    )

    payload = inference_jobs.stage_active_similarity_projection_backfill(limit=100)

    assert payload["duplicate_suppressed"] is True
    assert payload["selected_count"] == 0
    assert payload["staged_count"] == 0
    assert payload["limit"] == 100
    assert calls == []


def test_task_runs_its_body_when_the_lease_is_free(test_db, monkeypatch):
    monkeypatch.setattr(
        "app.core.database.SessionLocal", lambda: _NoCloseSession(test_db)
    )
    monkeypatch.setattr(
        inference_jobs, "singleton_lease", lambda bind, key: _free_lease()
    )

    payload = inference_jobs.stage_active_similarity_projection_backfill(limit=7)

    assert payload["duplicate_suppressed"] is False
    assert payload["limit"] == 7
