"""Unit tests for the self-healing celery beat scheduler.

These guard the recurring ``_dbm.error: cannot add item to database`` beat
crash-loop: the scheduler must purge a corrupt shelve/dbm schedule db and
rebuild it from the code-defined ``beat_schedule`` instead of propagating the
error and halting the scheduled pipeline.

Note: in the test/dev environment the production ``beat_schedule`` builders are
gated off, so ``celery_app.conf.beat_schedule`` is empty. The ``code_schedule``
fixture injects a minimal entry so the "reload from ``app.conf.beat_schedule``"
behaviour is actually observable.
"""
from __future__ import annotations

import dbm.ndbm
from datetime import timedelta

import pytest
from celery.beat import PersistentScheduler

from app.tasks.beat_scheduler import CORRUPTION_ERRORS, SelfHealingScheduler
from app.tasks.celery_app import celery_app

_SAMPLE_SCHEDULE = {
    "heal-test-ping": {
        "task": "celery.backend_cleanup",
        "schedule": timedelta(seconds=60),
    }
}


@pytest.fixture
def code_schedule(monkeypatch):
    """Give the code-defined schedule a stable, non-empty entry."""
    monkeypatch.setattr(celery_app.conf, "beat_schedule", dict(_SAMPLE_SCHEDULE))
    return _SAMPLE_SCHEDULE


def _make_scheduler(schedule_file) -> SelfHealingScheduler:
    return SelfHealingScheduler(
        app=celery_app,
        schedule_filename=str(schedule_file),
        lazy=False,
    )


def _db_files_exist(schedule_dir, stem: str = "celerybeat-schedule") -> bool:
    return any(
        (schedule_dir / f"{stem}{suffix}").exists()
        for suffix in PersistentScheduler.known_suffixes
    )


def test_recovers_from_corrupt_sync(tmp_path, monkeypatch, code_schedule):
    schedule_file = tmp_path / "celerybeat-schedule"
    scheduler = _make_scheduler(schedule_file)

    # Loaded from the code-defined beat_schedule during (non-lazy) init.
    assert "heal-test-ping" in scheduler.schedule

    orig_sync = PersistentScheduler.sync
    calls = {"n": 0}

    def flaky_sync(self):
        calls["n"] += 1
        if calls["n"] == 1:
            # The real production error: _dbm.error == dbm.ndbm.error.
            raise dbm.ndbm.error("cannot add item to database")
        return orig_sync(self)

    monkeypatch.setattr(PersistentScheduler, "sync", flaky_sync)

    # Corrupt-on-write should be absorbed: purge + rebuild, no raise.
    scheduler.sync()

    assert calls["n"] >= 2  # first raised, rebuild's sync delegated to real sync
    assert "heal-test-ping" in scheduler.schedule  # still populated after rebuild
    assert _db_files_exist(tmp_path)  # db file rebuilt on disk


def test_reentrancy_guard_does_not_infinite_loop(tmp_path, monkeypatch, code_schedule):
    schedule_file = tmp_path / "celerybeat-schedule"
    scheduler = _make_scheduler(schedule_file)

    calls = {"n": 0}

    def always_corrupt_sync(self):
        calls["n"] += 1
        raise dbm.ndbm.error("cannot add item to database")

    monkeypatch.setattr(PersistentScheduler, "sync", always_corrupt_sync)

    # Corruption that persists through recovery must fail fast, not recurse.
    with pytest.raises(dbm.ndbm.error):
        scheduler.sync()

    # Exactly one recovery attempt: the initial sync (1) plus the single sync
    # inside the rebuild's setup_schedule (2); the ``_healing`` guard then
    # re-raises instead of looping.
    assert calls["n"] == 2


def test_corruption_error_types_includes_ndbm():
    # Guards the subtlety that _dbm.error is NOT a subclass of the generic
    # dbm.error, so it must be collected explicitly to be caught.
    assert dbm.ndbm.error in CORRUPTION_ERRORS
