"""Tests for the safe runtime performance probe and measurement helpers."""

from types import SimpleNamespace

from app.core.config import settings
from app.tasks.celery_app import RUNTIME_PERFORMANCE_PROBE_TASK_NAME
from app.tasks.performance_probe import runtime_performance_probe
from scripts.measure_runtime_performance import (
    _enqueue_preview_load,
    build_parser,
    parse_cgroup_anon_bytes,
    parse_memory_bytes,
    percentile,
    summarize,
)


def test_runtime_performance_probe_is_registered_and_side_effect_free():
    payload = runtime_performance_probe.run(enqueued_at_epoch=0.0)

    assert runtime_performance_probe.name == RUNTIME_PERFORMANCE_PROBE_TASK_NAME
    assert payload["queue_wait_ms"] > 0
    assert payload["worker_pid"] > 0
    assert payload["worker_hostname"]


def test_percentile_uses_nearest_rank_for_small_operational_samples():
    values = [1.0, 2.0, 3.0, 4.0, 100.0]

    assert percentile(values, 50) == 3.0
    assert percentile(values, 95) == 100.0
    assert summarize(values).p95 == 100.0


def test_container_memory_parsers_report_anon_rss_and_docker_usage():
    memory_stat = "anon 104857600\nfile 20971520\nkernel 1024\n"

    assert parse_cgroup_anon_bytes(memory_stat) == 104857600
    assert parse_memory_bytes("125.5MiB / 8GiB") == 125.5 * 1024**2


def test_preview_load_is_explicit_and_scoped_to_one_operator():
    parser = build_parser()

    default_args = parser.parse_args([])
    load_args = parser.parse_args(["--preview-load-operator-id", "42"])

    assert default_args.preview_load_operator_id is None
    assert load_args.preview_load_operator_id == 42


def test_preview_load_uses_inference_queue(monkeypatch):
    from app.tasks import jobs

    calls: list[tuple[int, bool]] = []

    def _fake_enqueue(*, operator_id: int, high_priority_only: bool):
        calls.append((operator_id, high_priority_only))
        return SimpleNamespace(id=f"preview-load-{len(calls)}")

    monkeypatch.setattr(jobs, "enqueue_preview_snapshot_recompute", _fake_enqueue)

    payload = _enqueue_preview_load(42)

    assert payload is not None
    assert payload.queue == settings.CELERY_ML_INFERENCE_QUEUE
    assert payload.task_ids == ["preview-load-1", "preview-load-2"]
    assert calls == [(42, False), (42, True)]
