"""Typed contracts for runtime performance evidence."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MetricSummary(BaseModel):
    samples: int
    min: float | None = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    max: float | None = None


class HttpMeasurement(BaseModel):
    latency_ms: MetricSummary
    concurrency: int
    warmup_requests: int


class QueueMeasurement(BaseModel):
    queue_wait_ms: MetricSummary


class PreviewLoadEvidence(BaseModel):
    operator_id: int
    task_ids: list[str]
    queue: str


class QueueProbeReport(BaseModel):
    probes: dict[str, QueueMeasurement]
    preview_load: PreviewLoadEvidence | None = None


class ContainerMemoryMeasurement(BaseModel):
    rss_anon_mib: MetricSummary
    docker_memory_usage_mib: MetricSummary


class DockerStatsPayload(BaseModel):
    mem_usage: str = Field(alias="MemUsage")


class RuntimePerformanceProbeResult(BaseModel):
    queue: str
    queue_wait_ms: float
    started_at_epoch: float
    worker_hostname: str
    worker_pid: int


class RuntimePerformanceReport(BaseModel):
    schema_version: int = 1
    measured_at: str
    environment: str
    hostname: str
    git_sha: str | None = None
    limitations: list[str]
    http: dict[str, HttpMeasurement] | None = None
    queues: QueueProbeReport | None = None
    containers: dict[str, ContainerMemoryMeasurement] | None = None
