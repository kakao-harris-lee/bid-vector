#!/usr/bin/env python3
"""Measure HTTP latency, Celery queue wait, and container RSS safely.

The default ``/health`` run is read-only apart from publishing short-lived
no-op Celery probe tasks. Caller-supplied HTTP paths execute the current API
contract and can therefore have existing side effects; verify them first.

Run from the Docker host after deploying the same revision to API and workers::

    python scripts/measure_runtime_performance.py \
      --base-url http://127.0.0.1:3000 \
      --http-path /health \
      --queue bid_vector_ops \
      --container bid_vector_api \
      --container bid_vector_worker \
      --output reports/performance/runtime-baseline.json

Set ``BID_VECTOR_PERF_TOKEN`` when a measured path requires a bearer token.
The token is read only from the environment and is never serialized.
"""
# ruff: noqa: E402 - application imports follow the repo-root bootstrap.
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import time
from urllib import request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.schemas.runtime_performance import (
    ContainerMemoryMeasurement,
    DockerStatsPayload,
    HttpMeasurement,
    MetricSummary,
    PreviewLoadEvidence,
    QueueMeasurement,
    QueueProbeReport,
    RuntimePerformanceProbeResult,
    RuntimePerformanceReport,
)
from scripts._common import positive_int

DEFAULT_HTTP_PATHS = ("/health",)
DEFAULT_QUEUES = ("bid_vector_ops", "bid_vector_ml_inference")
DEFAULT_CONTAINERS = (
    "bid_vector_api",
    "bid_vector_worker",
    "bid_vector_inference_worker",
)
TOKEN_ENV_NAME = "BID_VECTOR_PERF_TOKEN"
_MEMORY_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([KMGTP]?i?B)$", re.IGNORECASE)
_UNIT_BYTES = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
    "pb": 1000**5,
    "pib": 1024**5,
}
LIMITATIONS = [
    "HTTP samples are synthetic requests, not production traffic histograms.",
    "Caller-supplied HTTP paths retain their current API side effects.",
    "rss_anon_mib is cgroup anonymous memory and includes all processes in the container.",
    "Queue probes measure no-op task scheduling delay at the load present during the run.",
]


def percentile(values: list[float], point: float) -> float:
    """Return a deterministic nearest-rank percentile."""
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((point / 100.0) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def summarize(values: list[float], *, digits: int = 3) -> MetricSummary:
    """Build the common latency/memory summary used by every probe."""
    if not values:
        return MetricSummary(samples=0)
    return MetricSummary(
        samples=len(values),
        min=round(min(values), digits),
        mean=round(statistics.fmean(values), digits),
        p50=round(percentile(values, 50), digits),
        p95=round(percentile(values, 95), digits),
        p99=round(percentile(values, 99), digits),
        max=round(max(values), digits),
    )


def _request_once(url: str, *, timeout_seconds: float, token: str | None) -> float:
    headers = {"Accept": "application/json", "User-Agent": "bid-vector-performance-probe/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.perf_counter()
    with request.urlopen(request.Request(url, headers=headers), timeout=timeout_seconds) as response:
        response.read()
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"HTTP {response.status} from {url}")
    return (time.perf_counter() - started) * 1000.0


def measure_http(
    *,
    base_url: str,
    paths: list[str],
    samples: int,
    warmup: int,
    concurrency: int,
    timeout_seconds: float,
    token: str | None,
) -> dict[str, HttpMeasurement]:
    results: dict[str, HttpMeasurement] = {}
    base = base_url.rstrip("/")
    for path in paths:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{base}{normalized_path}"
        for _ in range(warmup):
            _request_once(url, timeout_seconds=timeout_seconds, token=token)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _request_once,
                    url,
                    timeout_seconds=timeout_seconds,
                    token=token,
                )
                for _ in range(samples)
            ]
            durations = [future.result() for future in futures]
        results[normalized_path] = HttpMeasurement(
            latency_ms=summarize(durations),
            concurrency=concurrency,
            warmup_requests=warmup,
        )
    return results


def _enqueue_preview_load(operator_id: int | None) -> PreviewLoadEvidence | None:
    if operator_id is None:
        return None
    from app.core.config import settings
    from app.tasks.jobs import enqueue_preview_snapshot_recompute

    load_tasks = [
        enqueue_preview_snapshot_recompute(
            operator_id=operator_id,
            high_priority_only=flag,
        )
        for flag in (False, True)
    ]
    return PreviewLoadEvidence(
        operator_id=operator_id,
        task_ids=[str(task.id) for task in load_tasks],
        queue=settings.CELERY_ML_INFERENCE_QUEUE,
    )


def _inside_queue_probe(
    *,
    queues: list[str],
    samples: int,
    timeout_seconds: float,
    preview_load_operator_id: int | None,
) -> QueueProbeReport:
    from app.tasks.performance_probe import runtime_performance_probe

    preview_load = _enqueue_preview_load(preview_load_operator_id)
    probes: dict[str, QueueMeasurement] = {}
    for queue in queues:
        pending = [
            runtime_performance_probe.apply_async(
                kwargs={"enqueued_at_epoch": time.time()},
                queue=queue,
                expires=max(10, int(timeout_seconds * 2)),
            )
            for _ in range(samples)
        ]
        results = [
            RuntimePerformanceProbeResult.model_validate(
                async_result.get(timeout=timeout_seconds)
            )
            for async_result in pending
        ]
        probes[queue] = QueueMeasurement(
            queue_wait_ms=summarize([result.queue_wait_ms for result in results])
        )
    return QueueProbeReport(probes=probes, preview_load=preview_load)


def measure_queues(
    *,
    api_container: str,
    queues: list[str],
    samples: int,
    timeout_seconds: float,
    preview_load_operator_id: int | None,
) -> QueueProbeReport:
    command = [
        "docker",
        "exec",
        api_container,
        "python",
        "scripts/measure_runtime_performance.py",
        "--inside-queue-probe",
        "--queue-samples",
        str(samples),
        "--queue-timeout-seconds",
        str(timeout_seconds),
    ]
    for queue in queues:
        command.extend(("--queue", queue))
    if preview_load_operator_id is not None:
        command.extend(("--preview-load-operator-id", str(preview_load_operator_id)))
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return QueueProbeReport.model_validate_json(completed.stdout)


def parse_cgroup_anon_bytes(payload: str) -> int:
    """Parse cgroup v2 anonymous memory, the aggregate container RSS proxy."""
    for line in payload.splitlines():
        key, _, value = line.partition(" ")
        if key == "anon" and value.strip().isdigit():
            return int(value.strip())
    raise ValueError("cgroup memory.stat does not expose anon bytes")


def parse_memory_bytes(value: str) -> float:
    """Parse the first Docker stats memory value into bytes."""
    usage = value.split("/", 1)[0].strip()
    matched = _MEMORY_VALUE.match(usage)
    if matched is None:
        raise ValueError(f"unsupported Docker memory value: {value!r}")
    amount, unit = matched.groups()
    return float(amount) * _UNIT_BYTES[unit.lower()]


def _container_memory_sample(container: str) -> tuple[float, float]:
    memory_stat = subprocess.run(
        ["docker", "exec", container, "cat", "/sys/fs/cgroup/memory.stat"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    stats_payload = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    stats = DockerStatsPayload.model_validate_json(stats_payload)
    return (
        parse_cgroup_anon_bytes(memory_stat) / (1024.0**2),
        parse_memory_bytes(stats.mem_usage) / (1024.0**2),
    )


def measure_container_memory(
    *, containers: list[str], samples: int, interval_seconds: float
) -> dict[str, ContainerMemoryMeasurement]:
    values: dict[str, tuple[list[float], list[float]]] = {
        container: ([], []) for container in containers
    }
    for sample_index in range(samples):
        for container in containers:
            rss_mib, usage_mib = _container_memory_sample(container)
            values[container][0].append(rss_mib)
            values[container][1].append(usage_mib)
        if sample_index + 1 < samples:
            time.sleep(interval_seconds)
    return {
        container: ContainerMemoryMeasurement(
            rss_anon_mib=summarize(rss_values),
            docker_memory_usage_mib=summarize(usage_values),
        )
        for container, (rss_values, usage_values) in values.items()
    }


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--http-path", action="append", default=[])
    parser.add_argument("--http-samples", type=positive_int, default=40)
    parser.add_argument("--http-warmup", type=int, default=5)
    parser.add_argument("--http-concurrency", type=positive_int, default=1)
    parser.add_argument("--http-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--queue", action="append", default=[])
    parser.add_argument("--queue-samples", type=positive_int, default=20)
    parser.add_argument("--queue-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--preview-load-operator-id", type=positive_int)
    parser.add_argument("--api-container", default="bid_vector_api")
    parser.add_argument("--container", action="append", default=[])
    parser.add_argument("--rss-samples", type=positive_int, default=5)
    parser.add_argument("--rss-interval-seconds", type=float, default=1.0)
    parser.add_argument("--environment-label", default="local-development")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--skip-queue", action="store_true")
    parser.add_argument("--skip-rss", action="store_true")
    parser.add_argument("--inside-queue-probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def _collect_report(args: argparse.Namespace) -> RuntimePerformanceReport:
    report = RuntimePerformanceReport(
        measured_at=datetime.now(UTC).isoformat(),
        environment=args.environment_label,
        hostname=socket.gethostname(),
        git_sha=_git_sha(),
        limitations=LIMITATIONS,
    )
    if not args.skip_http:
        report.http = measure_http(
            base_url=args.base_url,
            paths=args.http_path or list(DEFAULT_HTTP_PATHS),
            samples=args.http_samples,
            warmup=max(0, args.http_warmup),
            concurrency=args.http_concurrency,
            timeout_seconds=args.http_timeout_seconds,
            token=os.getenv(TOKEN_ENV_NAME) or None,
        )
    if not args.skip_queue:
        report.queues = measure_queues(
            api_container=args.api_container,
            queues=args.queue or list(DEFAULT_QUEUES),
            samples=args.queue_samples,
            timeout_seconds=args.queue_timeout_seconds,
            preview_load_operator_id=args.preview_load_operator_id,
        )
    if not args.skip_rss:
        report.containers = measure_container_memory(
            containers=args.container or list(DEFAULT_CONTAINERS),
            samples=args.rss_samples,
            interval_seconds=max(0.0, args.rss_interval_seconds),
        )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.inside_queue_probe:
        payload = _inside_queue_probe(
            queues=args.queue or list(DEFAULT_QUEUES),
            samples=args.queue_samples,
            timeout_seconds=args.queue_timeout_seconds,
            preview_load_operator_id=args.preview_load_operator_id,
        )
        print(payload.model_dump_json(exclude_none=True))
        return 0

    report = _collect_report(args)
    rendered = report.model_dump_json(indent=2, exclude_none=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
