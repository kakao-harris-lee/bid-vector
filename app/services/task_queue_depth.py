"""Broker-side depth of a single-consumer Celery queue.

Why this exists
---------------
On 2026-08-13 the similarity projection backfill pinned the one inference worker
for four hours and ``bid_vector_ml_inference`` accumulated 21,321 messages. The
backlog had been building for days and **nothing reported it**: every existing
check looks at rows the pipeline produced, and a pipeline whose consumer is
blocked produces no rows to look at. The absence of output is exactly what the
queue depth makes visible.

The reading is deliberately best-effort. A transport that cannot answer a passive
declare (``memory://`` under tests, for instance) yields ``depth=None`` rather
than an exception, because "we could not measure" and "the queue is deep" are
different facts and must not collapse into one alarm.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

logger = logging.getLogger(__name__)


class QueueDeclareOk(Protocol):
    """The subset of a broker's declare-ok frame this module reads."""

    message_count: int


class BrokerChannel(Protocol):
    def queue_declare(self, queue: str, passive: bool = ...) -> QueueDeclareOk: ...


class BrokerConnection(Protocol):
    """Structural contract for the broker connection — the seam tests replace."""

    default_channel: BrokerChannel

    def __enter__(self) -> "BrokerConnection": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


BrokerConnectionFactory = Callable[[], BrokerConnection]


@dataclass(frozen=True)
class QueueDepthReading:
    """``depth is None`` means unmeasured — never "empty"."""

    queue: str
    depth: int | None = None
    error: str = ""

    @property
    def measured(self) -> bool:
        return self.depth is not None

    def exceeds(self, threshold: int) -> bool:
        """Only a measured depth can exceed anything."""
        return self.depth is not None and self.depth > int(threshold)


@dataclass(frozen=True)
class QueueDepthVerdict:
    """A depth reading judged against its threshold, ready for smoke evidence."""

    healthy: bool
    detail: str
    unmeasured_reason: str
    evidence: dict[str, int | str | None]


def assess_queue_depth(
    queue: str,
    threshold: int,
    *,
    connection_factory: BrokerConnectionFactory | None = None,
) -> QueueDepthVerdict:
    """Judge one queue's backlog.

    An unmeasurable queue is *healthy with a reason*, not a failure: "we could not
    read the depth" is not evidence of a deep queue, and reporting it red would
    train the operator to ignore the one signal that catches a blocked consumer.
    """
    reading = read_queue_depth(queue, connection_factory=connection_factory)
    evidence: dict[str, int | str | None] = {
        "queue_name": queue,
        "queue_depth": reading.depth,
        "queue_depth_threshold": int(threshold),
    }
    if not reading.measured:
        return QueueDepthVerdict(
            healthy=True,
            detail=f"{queue} depth unavailable ({reading.error})",
            unmeasured_reason=f"queue depth unavailable — {reading.error}",
            evidence=evidence,
        )
    if reading.exceeds(threshold):
        return QueueDepthVerdict(
            healthy=False,
            detail=(
                f"{queue} queue backlog {reading.depth} exceeds {threshold}: "
                "the single consumer is not keeping up"
            ),
            unmeasured_reason="",
            evidence=evidence,
        )
    return QueueDepthVerdict(
        healthy=True,
        detail=f"{queue} depth {reading.depth} (threshold {threshold})",
        unmeasured_reason="",
        evidence=evidence,
    )


def _default_connection_factory() -> BrokerConnection:
    from app.tasks.celery_app import celery_app

    return celery_app.connection_for_read()


def read_queue_depth(
    queue: str,
    *,
    connection_factory: BrokerConnectionFactory | None = None,
) -> QueueDepthReading:
    """Read ``queue``'s pending message count via a passive declare.

    ``connection_factory`` is the injection seam: tests pass a fake broker rather
    than patching the Celery app. Passive declare does not create the queue, so
    reading a queue no worker has declared yet reports it as absent instead of
    silently bringing it into existence.
    """
    factory = connection_factory or _default_connection_factory
    try:
        with factory() as connection:
            declared = connection.default_channel.queue_declare(
                queue=queue, passive=True
            )
        return QueueDepthReading(queue=queue, depth=int(declared.message_count))
    except Exception as exc:  # noqa: BLE001 - an unmeasurable queue is not an outage
        logger.warning("queue depth unavailable for %s: %s", queue, exc)
        return QueueDepthReading(queue=queue, error=f"{type(exc).__name__}: {exc}")
