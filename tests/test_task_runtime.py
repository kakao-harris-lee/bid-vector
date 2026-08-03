"""Tests for broker-backed Celery runtime configuration."""

from app.core.config import Settings, settings
from app.services.strategy_scheduler import OperatorStrategyScheduler
from app.tasks.celery_app import (
    Celery,
    INFERENCE_OUTBOX_PROCESS_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME,
    apply_task_result_repr_maxsize,
    build_celery_runtime_config,
    celery_app,
)


def test_settings_auto_promote_database_result_backend_for_external_broker():
    """Switching away from the in-memory broker should auto-enable a DB result backend."""
    configured = Settings(
        DATABASE_URL="postgresql+psycopg://biduser:bidpassword@db:5432/bid_vector_db",
        CELERY_BROKER_URL="amqp://bidvector:bidvector@rabbitmq:5672/bidvector",
        CELERY_RESULT_BACKEND="cache+memory://",
    )

    assert configured.uses_in_memory_celery is False
    assert configured.CELERY_RESULT_BACKEND == "db+postgresql+psycopg://biduser:bidpassword@db:5432/bid_vector_db"


def test_settings_preserve_explicit_result_backend_for_external_broker():
    """Explicit result backend choices should win over the PostgreSQL fallback."""
    configured = Settings(
        DATABASE_URL="postgresql+psycopg://biduser:bidpassword@db:5432/bid_vector_db",
        CELERY_BROKER_URL="amqp://bidvector:bidvector@rabbitmq:5672/bidvector",
        CELERY_RESULT_BACKEND="rpc://",
    )

    assert configured.CELERY_RESULT_BACKEND == "rpc://"


def test_build_celery_runtime_config_registers_tasks_and_worker_defaults(monkeypatch):
    """Worker-backed Celery config should register tasks and expose production-friendly defaults."""
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "amqp://bidvector:bidvector@rabbitmq:5672/bidvector")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "db+postgresql+psycopg://biduser:bidpassword@db:5432/bid_vector_db")
    monkeypatch.setattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "bid_vector_ops")
    monkeypatch.setattr(settings, "CELERY_WORKER_CONCURRENCY", 4)
    monkeypatch.setattr(settings, "CELERY_WORKER_PREFETCH_MULTIPLIER", 1)
    monkeypatch.setattr(settings, "CELERY_WORKER_MAX_TASKS_PER_CHILD", 64)
    monkeypatch.setattr(settings, "CELERY_TASK_TIME_LIMIT_SECONDS", 600)
    monkeypatch.setattr(settings, "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 590)
    monkeypatch.setattr(settings, "CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", 1048576)

    config = build_celery_runtime_config()

    assert config["imports"] == ("app.tasks.jobs", "app.tasks.performance_probe")
    assert config["task_default_queue"] == "bid_vector_ops"
    assert config["task_routes"]["jobs.collect_koneps_notices"]["queue"] == settings.CELERY_OPS_QUEUE
    assert (
        config["task_routes"][OPERATOR_STRATEGY_MONITOR_TASK_NAME]["queue"]
        == settings.CELERY_ML_INFERENCE_QUEUE
    )
    assert (
        config["task_routes"][PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME]["queue"]
        == settings.CELERY_ML_INFERENCE_QUEUE
    )
    assert (
        config["task_routes"][INFERENCE_OUTBOX_PROCESS_TASK_NAME]["queue"]
        == settings.CELERY_ML_INFERENCE_QUEUE
    )
    assert config["task_routes"]["jobs.rebuild_project_embeddings"]["queue"] == settings.CELERY_ML_BACKFILL_QUEUE
    assert config["task_routes"]["ml.train_price_predictor"]["queue"] == settings.CELERY_ML_TRAINING_QUEUE
    assert config["task_routes"]["ml.reevaluate_decision_experiment"]["queue"] == settings.CELERY_ML_REEVALUATION_QUEUE
    assert config["task_always_eager"] is False
    assert config["task_acks_late"] is True
    assert config["worker_concurrency"] == 4
    assert config["worker_prefetch_multiplier"] == 1
    assert config["worker_max_tasks_per_child"] == 64
    assert config["task_time_limit"] == 600
    assert config["task_soft_time_limit"] == 590
    assert config["worker_max_memory_per_child"] == 1048576


def test_embedding_rebuild_enqueues_outbox_processor_for_entire_event_batch(monkeypatch):
    """A rebuild that creates more than 50 outbox rows must enqueue a large enough sweep."""
    from contextlib import contextmanager

    from app.tasks import jobs

    class _FakeDb:
        def commit(self):
            pass

        def rollback(self):
            pass

    class _FakeSimilarityService:
        def rebuild_project_embeddings(self, db, **kwargs):
            del db, kwargs
            return {
                "processed_count": 75,
                "outbox_event_ids": list(range(1, 76)),
                "results": [],
            }

    class _FakeAsyncResult:
        id = "outbox-processor-75"

    @contextmanager
    def _fake_task_session():
        yield _FakeDb()

    queued_limits: list[int] = []

    def _fake_enqueue_inference_outbox_processing(*, limit: int = 50):
        queued_limits.append(limit)
        return _FakeAsyncResult()

    monkeypatch.setattr(jobs, "task_session", _fake_task_session)
    monkeypatch.setattr(jobs, "ProjectSimilarityService", _FakeSimilarityService)
    monkeypatch.setattr(
        jobs,
        "enqueue_inference_outbox_processing",
        _fake_enqueue_inference_outbox_processing,
    )

    result = jobs.rebuild_project_embeddings.run(limit=75, force=True)

    assert result["outbox_processor_task_id"] == "outbox-processor-75"
    assert queued_limits == [75]


def test_worker_max_memory_per_child_zero_disables_the_limit(monkeypatch):
    """0 이하는 celery 기본(무제한) — conf 키 자체를 넣지 않는다."""
    monkeypatch.setattr(settings, "CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", 0)

    config = build_celery_runtime_config()

    assert "worker_max_memory_per_child" not in config


def test_result_repr_maxsize_reaches_task_base(monkeypatch):
    """설정한 result repr 상한이 celery Task base 에 반영돼야 한다.

    celery.app.trace 는 성공 task 마다 saferepr(R, resultrepr_maxsize) 를 INFO 로
    에코하므로, 이 상한을 낮추면 성공 로그의 반환값 볼륨이 줄어든다.
    """
    monkeypatch.setattr(settings, "CELERY_TASK_RESULT_REPR_MAXSIZE", 160)

    apply_task_result_repr_maxsize(celery_app)

    assert celery_app.Task.resultrepr_maxsize == 160


def test_non_positive_result_repr_maxsize_keeps_celery_default(monkeypatch):
    """0 이하는 celery 기본(1024) 을 그대로 둔다 — Task base 를 건드리지 않는다."""
    probe = Celery("result-repr-probe")
    celery_default = probe.Task.resultrepr_maxsize
    monkeypatch.setattr(settings, "CELERY_TASK_RESULT_REPR_MAXSIZE", 0)

    apply_task_result_repr_maxsize(probe)

    assert probe.Task.resultrepr_maxsize == celery_default


def test_strategy_scheduler_only_runs_inprocess_for_memory_broker(monkeypatch):
    """The in-process scheduler should stand down when a real broker is configured."""
    scheduler = OperatorStrategyScheduler()

    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    assert scheduler.should_run_inprocess() is True

    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "amqp://bidvector:bidvector@rabbitmq:5672/bidvector")
    assert scheduler.should_run_inprocess() is False


def test_ml_training_endpoint_queues_without_inline_execution(client):
    """ML training requests should return a queued task handle in the API process."""
    response = client.post(
        "/api/v1/ml/training/price-predictor",
        json={
            "release_tag": "test-training-queued",
            "category": "software",
            "limit": 20,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_name"] == "ml.train_price_predictor"
    assert payload["queue"] == settings.CELERY_ML_TRAINING_QUEUE
    assert payload["status"] == "queued"
