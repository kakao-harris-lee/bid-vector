"""Backpressure signal for the single-consumer ML inference queue.

21,321 messages accumulated over days behind a blocked consumer and no check
reported it, because every other smoke phase inspects rows the pipeline
produced — and a blocked pipeline produces none. These tests pin the two things
that make the signal trustworthy: it fires when the queue is genuinely deep, and
it does not turn "could not measure" into a red phase.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.smoke_evidence import trim_phase_evidence
from app.services.smoke_test import KonepsTelegramSmokeTestService
from app.services.task_queue_depth import QueueDepthReading, read_queue_depth


class _FakeDeclareOk:
    def __init__(self, message_count: int) -> None:
        self.message_count = message_count


class _FakeChannel:
    def __init__(self, depths: dict[str, int]) -> None:
        self._depths = depths
        self.passive_calls: list[str] = []

    def queue_declare(self, queue: str, passive: bool = False):
        assert passive is True, "reading a depth must never create the queue"
        self.passive_calls.append(queue)
        if queue not in self._depths:
            raise RuntimeError(f"NOT_FOUND - no queue '{queue}'")
        return _FakeDeclareOk(self._depths[queue])


class _FakeConnection:
    def __init__(self, depths: dict[str, int]) -> None:
        self.default_channel = _FakeChannel(depths)
        self.closed = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args) -> None:
        self.closed = True


def _factory(depths: dict[str, int]):
    connections: list[_FakeConnection] = []

    def factory():
        connection = _FakeConnection(depths)
        connections.append(connection)
        return connection

    factory.connections = connections  # type: ignore[attr-defined]
    return factory


def test_depth_is_read_with_a_passive_declare():
    factory = _factory({"bid_vector_ml_inference": 42})

    reading = read_queue_depth(
        "bid_vector_ml_inference", connection_factory=factory
    )

    assert reading.depth == 42
    assert reading.measured is True
    assert factory.connections[0].closed is True


def test_an_unreachable_broker_reads_as_unmeasured_not_empty():
    def factory():
        raise ConnectionRefusedError("broker down")

    reading = read_queue_depth("bid_vector_ml_inference", connection_factory=factory)

    assert reading.depth is None
    assert reading.measured is False
    assert "ConnectionRefusedError" in reading.error


def test_a_missing_queue_reads_as_unmeasured():
    reading = read_queue_depth("never_declared", connection_factory=_factory({}))

    assert reading.measured is False


def test_unmeasured_depth_never_counts_as_exceeding():
    assert QueueDepthReading(queue="q").exceeds(0) is False
    assert QueueDepthReading(queue="q", depth=0).exceeds(0) is False
    assert QueueDepthReading(queue="q", depth=1).exceeds(0) is True


def _phase(depths: dict[str, int]):
    service = KonepsTelegramSmokeTestService(
        queue_connection_factory=_factory(depths)
    )
    return service._phase_inference_queue_depth()


def test_phase_passes_on_a_shallow_queue():
    phase = _phase({settings.CELERY_ML_INFERENCE_QUEUE: 3})

    assert phase.passed is True
    assert phase.data["queue_depth"] == 3
    assert phase.data["queue_name"] == settings.CELERY_ML_INFERENCE_QUEUE


def test_phase_fails_once_the_backlog_clears_the_threshold():
    threshold = int(settings.ML_INFERENCE_QUEUE_DEPTH_WARN_THRESHOLD)
    phase = _phase({settings.CELERY_ML_INFERENCE_QUEUE: threshold + 1})

    assert phase.passed is False
    assert phase.failure_category == "task_broker"
    assert str(threshold) in phase.detail
    assert phase.action_required
    assert phase.retry_method


def test_phase_holds_at_exactly_the_threshold():
    threshold = int(settings.ML_INFERENCE_QUEUE_DEPTH_WARN_THRESHOLD)
    phase = _phase({settings.CELERY_ML_INFERENCE_QUEUE: threshold})

    assert phase.passed is True


def test_the_incident_depth_would_have_failed_this_phase():
    """21,321 — the depth the 2026-08-13 runaway reached with nothing reporting it."""
    phase = _phase({settings.CELERY_ML_INFERENCE_QUEUE: 21_321})

    assert phase.passed is False


def test_phase_does_not_go_red_when_the_depth_cannot_be_measured():
    def factory():
        raise RuntimeError("transport does not support passive declare")

    service = KonepsTelegramSmokeTestService(queue_connection_factory=factory)

    phase = service._phase_inference_queue_depth()

    assert phase.passed is True
    assert phase.skip_reason
    assert phase.data["queue_depth"] is None


def test_queue_depth_evidence_survives_persistence_trimming():
    """Evidence keys outside the allowlist are dropped before they reach the run row."""
    threshold = int(settings.ML_INFERENCE_QUEUE_DEPTH_WARN_THRESHOLD)
    phase = _phase({settings.CELERY_ML_INFERENCE_QUEUE: threshold + 10})

    trimmed = trim_phase_evidence({"name": phase.name, "data": phase.data})

    assert trimmed["queue_depth"] == threshold + 10
    assert trimmed["queue_depth_threshold"] == threshold
    assert trimmed["queue_name"] == settings.CELERY_ML_INFERENCE_QUEUE
