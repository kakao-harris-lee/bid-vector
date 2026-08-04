from pathlib import Path


SCRIPT = Path("scripts/sync-after-merge.sh")


def test_sync_after_merge_starts_beat_only_after_worker_registry_gate():
    content = SCRIPT.read_text(encoding="utf-8")

    stop_index = content.index("stop beat")
    registry_index = content.index("verify_worker_registry ||")
    beat_start_index = content.index("up -d --force-recreate beat")

    assert stop_index < registry_index < beat_start_index
    assert "inspect registered" in content
    assert "inspect active_queues" in content
    assert content.count("--timeout=10 --json") == 2


def test_gate_delegates_both_inspect_payloads_to_the_topology_checker():
    content = SCRIPT.read_text(encoding="utf-8")

    assert '{"registered": %s, "active_queues": %s}' in content
    # Run as a module: invoking it by path would put scripts/ on sys.path
    # instead of the repo root and break the `app` imports the gate needs.
    assert "python -m scripts._sync_queue_check" in content


def test_gate_keeps_no_hardcoded_task_or_queue_expectations():
    """Expectations live in settings/beat schedule; the shell must not copy them."""
    content = SCRIPT.read_text(encoding="utf-8")

    assert "jobs." not in content
    assert "settings.CELERY" not in content
    assert "bid_vector_ml" not in content


def test_usage_documents_recreation_rather_than_restart():
    header = SCRIPT.read_text(encoding="utf-8").split("set -euo pipefail")[0]

    assert "recreate" in header
    assert "--concurrency" in header
    assert "restart all task services" not in header
