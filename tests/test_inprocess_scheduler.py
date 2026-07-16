"""Lifecycle tests for the shared in-process scheduler base class.

These exercise the common ``BaseInProcessScheduler`` lifecycle (start creates a
task, stop cancels and clears it, ``should_run_inprocess`` gating) and assert
that each concrete subclass preserves its original started-log string.
"""

import asyncio
import logging

import pytest

from app.core.config import settings
from app.services.inprocess_scheduler import BaseInProcessScheduler
from app.services.paper_bidding_scheduler import PaperBiddingForwardScheduler
from app.services.strategy_scheduler import OperatorStrategyScheduler


class _RecordingScheduler(BaseInProcessScheduler):
    """Minimal concrete scheduler used to drive the base lifecycle in tests."""

    def __init__(self, enabled=True):
        super().__init__()
        self._enabled = enabled
        self.run_count = 0
        self.payload_sentinel = object()

    def is_enabled(self):
        return self._enabled

    def _task_name(self):
        return "recording_scheduler"

    def _enabled_log_message(self):
        return "Recording scheduling is enabled; use Celery beat/worker for broker %s."

    def _started_log_message(self):
        return "Started in-process recording scheduler (interval=%s minutes)."

    def _started_log_interval(self):
        return 7

    def _interval_seconds(self):
        # Long enough that the sleep loop never fires within the test window.
        return 3600

    def _run_on_startup(self):
        return True

    def build_payload(self):
        return self.payload_sentinel

    def _run_once_sync(self, payload):
        assert payload is self.payload_sentinel
        self.run_count += 1


def _force_inprocess(monkeypatch):
    """Make ``should_run_inprocess`` return True regardless of host config."""
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    assert settings.uses_in_memory_celery is True


def test_should_run_inprocess_gates_on_enabled_and_broker(monkeypatch):
    scheduler = _RecordingScheduler(enabled=True)

    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    assert scheduler.should_run_inprocess() is True

    scheduler._enabled = False
    assert scheduler.should_run_inprocess() is False

    scheduler._enabled = True
    monkeypatch.setattr(
        settings,
        "CELERY_BROKER_URL",
        "amqp://bidvector:bidvector@rabbitmq:5672/bidvector",
    )
    assert scheduler.should_run_inprocess() is False


def test_start_creates_named_task_then_stop_clears_it(monkeypatch):
    _force_inprocess(monkeypatch)
    scheduler = _RecordingScheduler(enabled=True)

    async def exercise():
        await scheduler.start()
        assert scheduler._task is not None
        assert scheduler._task.get_name() == "recording_scheduler"
        # Let the run-on-startup cycle execute once.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await scheduler.stop()
        assert scheduler._task is None

    asyncio.run(exercise())
    assert scheduler.run_count >= 1


def test_start_is_idempotent_while_task_running(monkeypatch):
    _force_inprocess(monkeypatch)
    scheduler = _RecordingScheduler(enabled=True)

    async def exercise():
        await scheduler.start()
        first = scheduler._task
        await scheduler.start()
        assert scheduler._task is first
        await scheduler.stop()

    asyncio.run(exercise())


def test_stop_without_start_is_noop():
    scheduler = _RecordingScheduler(enabled=True)
    asyncio.run(scheduler.stop())
    assert scheduler._task is None


def test_start_skips_loop_but_logs_when_not_inprocess(monkeypatch, caplog):
    scheduler = _RecordingScheduler(enabled=True)
    monkeypatch.setattr(
        settings,
        "CELERY_BROKER_URL",
        "amqp://bidvector:bidvector@rabbitmq:5672/bidvector",
    )

    with caplog.at_level(logging.INFO, logger="app.services.inprocess_scheduler"):
        asyncio.run(scheduler.start())

    assert scheduler._task is None
    assert any(
        "Recording scheduling is enabled; use Celery beat/worker for broker"
        in record.getMessage()
        for record in caplog.records
    )


def test_paper_scheduler_preserves_started_log_string(monkeypatch, caplog):
    _force_inprocess(monkeypatch)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_INTERVAL_MINUTES", 60)
    # Keep the loop from invoking the real backtest service during the test.
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_RUN_ON_STARTUP", False)
    scheduler = PaperBiddingForwardScheduler()

    async def exercise():
        with caplog.at_level(logging.INFO, logger="app.services.inprocess_scheduler"):
            await scheduler.start()
        await scheduler.stop()

    asyncio.run(exercise())

    assert any(
        record.getMessage()
        == "Started in-process forward paper-bidding scheduler (interval=60 minutes)."
        for record in caplog.records
    )


def test_strategy_scheduler_preserves_started_log_string(monkeypatch, caplog):
    _force_inprocess(monkeypatch)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES", 12)
    # Keep the loop from invoking the real monitoring service during the test.
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP", False)
    scheduler = OperatorStrategyScheduler()

    async def exercise():
        with caplog.at_level(logging.INFO, logger="app.services.inprocess_scheduler"):
            await scheduler.start()
        await scheduler.stop()

    asyncio.run(exercise())

    assert any(
        record.getMessage()
        == "Started in-process operator strategy scheduler (interval=12 minutes)."
        for record in caplog.records
    )


def test_base_scheduler_is_abstract():
    with pytest.raises(TypeError):
        BaseInProcessScheduler()


class _FakeDBSession:
    """Minimal session double: records that it was closed."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_paper_scheduler_uses_injected_session_factory(monkeypatch):
    session = _FakeDBSession()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return session

    received = {}

    class _FakeBacktestService:
        def run_forward_paper_bidding(self, db, **payload):
            received["db"] = db
            received["payload"] = payload
            return {
                "run_id": 1,
                "summary": {"candidate_count": 0, "paper_bid_count": 0},
            }

    monkeypatch.setattr(
        "app.services.paper_bidding_scheduler.PaperBiddingBacktestService",
        _FakeBacktestService,
    )

    scheduler = PaperBiddingForwardScheduler(session_factory=factory)
    scheduler._run_once_sync({"category": None, "limit": 1})

    assert factory_calls == [1]
    assert received["db"] is session
    assert received["payload"] == {"category": None, "limit": 1}
    assert session.closed is True


def test_strategy_scheduler_uses_injected_session_factory(monkeypatch):
    session = _FakeDBSession()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return session

    received = {}

    class _FakeMonitoringService:
        SCHEDULED_TRIGGER_SOURCE = "scheduled"

        def execute_monitoring(self, db, *, request, trigger_source):
            received["db"] = db
            received["trigger_source"] = trigger_source
            return {
                "persisted_candidate_count": 0,
                "notification_count": 0,
                "monitor_run_id": None,
            }

    monkeypatch.setattr(
        "app.services.strategy_scheduler.StrategyMonitoringService",
        _FakeMonitoringService,
    )

    scheduler = OperatorStrategyScheduler(session_factory=factory)
    scheduler._run_once_sync(scheduler.build_request())

    assert factory_calls == [1]
    assert received["db"] is session
    assert received["trigger_source"] == "scheduled"
    assert session.closed is True
