"""Tests for broker-backed Celery runtime configuration."""

from app.core.config import Settings, settings
from app.services.strategy_scheduler import OperatorStrategyScheduler
from app.tasks.celery_app import build_celery_runtime_config


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

    config = build_celery_runtime_config()

    assert config["imports"] == ("app.tasks.jobs",)
    assert config["task_default_queue"] == "bid_vector_ops"
    assert config["task_routes"]["jobs.collect_koneps_notices"]["queue"] == settings.CELERY_OPS_QUEUE
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
