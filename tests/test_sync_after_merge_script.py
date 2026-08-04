from pathlib import Path


SCRIPT = Path("scripts/sync-after-merge.sh")


def test_sync_after_merge_starts_beat_only_after_worker_registry_gate():
    content = SCRIPT.read_text(encoding="utf-8")

    stop_index = content.index("stop beat")
    registry_index = content.index("verify_worker_registry ||")
    beat_start_index = content.index("up -d --force-recreate beat")

    assert stop_index < registry_index < beat_start_index
    assert "inspect registered --timeout=10" in content
    assert "inspect active_queues" in content
    assert "--timeout=10 --json" in content
    assert "from app.core.config import settings" in content
    assert "grep -Fxq" in content
    assert "jobs.process_inference_outbox" in content
    assert "jobs.stage_active_similarity_projection_backfill" in content
    assert "settings.CELERY_ML_INFERENCE_QUEUE" in content
