"""Side-effect-free Celery probe used by the runtime measurement CLI."""

from __future__ import annotations

import os
import socket
import time
from typing import TypedDict

from app.tasks.celery_app import (
    RUNTIME_PERFORMANCE_PROBE_TASK_NAME,
    celery_app,
)


class RuntimePerformanceProbePayload(TypedDict):
    queue: str
    queue_wait_ms: float
    started_at_epoch: float
    worker_hostname: str
    worker_pid: int


@celery_app.task(bind=True, name=RUNTIME_PERFORMANCE_PROBE_TASK_NAME)
def runtime_performance_probe(
    self, enqueued_at_epoch: float
) -> RuntimePerformanceProbePayload:
    """Return broker-to-worker start delay without DB writes or external I/O."""
    started_at_epoch = time.time()
    delivery_info = getattr(getattr(self, "request", None), "delivery_info", {}) or {}
    return {
        "queue": str(delivery_info.get("routing_key") or "unknown"),
        "queue_wait_ms": max(
            0.0,
            round((started_at_epoch - float(enqueued_at_epoch)) * 1000.0, 3),
        ),
        "started_at_epoch": started_at_epoch,
        "worker_hostname": socket.gethostname(),
        "worker_pid": os.getpid(),
    }
