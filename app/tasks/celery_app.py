"""Celery app skeleton."""

from uuid import uuid4

try:
    from celery import Celery
    from celery.schedules import crontab
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    class _FallbackAsyncResult:
        """In-memory async result object for environments without Celery."""

        def __init__(self, task_id: str, state: str = "PENDING", result=None):
            self.id = task_id
            self.state = state
            self.status = state
            self.result = result

        def ready(self) -> bool:
            return self.state in {"SUCCESS", "FAILURE", "REVOKED"}

        def successful(self) -> bool:
            return self.state == "SUCCESS"


    class _FallbackTask:
        """Minimal callable task wrapper used when Celery is unavailable."""

        def __init__(self, app, func, name: str | None = None):
            self.app = app
            self.run = func
            self.__name__ = getattr(func, "__name__", "task")
            self.name = name or self.__name__

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

        def delay(self, *args, **kwargs):
            return self.apply_async(args=args, kwargs=kwargs)

        def apply_async(self, args=None, kwargs=None, **options):
            del options
            task_id = str(uuid4())
            args = tuple(args or ())
            kwargs = dict(kwargs or {})
            try:
                result = self.run(*args, **kwargs)
                async_result = _FallbackAsyncResult(task_id, state="SUCCESS", result=result)
            except Exception as exc:  # pragma: no cover - exercised only on task failure
                async_result = _FallbackAsyncResult(task_id, state="FAILURE", result=exc)
            self.app._results[task_id] = async_result
            return async_result


    class Celery:  # type: ignore[override]
        """Very small Celery-compatible shim for tests without the dependency installed."""

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.conf = self
            self._results: dict[str, _FallbackAsyncResult] = {}

        def update(self, **kwargs):
            del kwargs

        def task(self, name: str | None = None, **options):
            del options

            def decorator(func):
                return _FallbackTask(self, func, name=name)

            return decorator

        def AsyncResult(self, task_id: str):
            return self._results.get(task_id, _FallbackAsyncResult(task_id))

    class crontab:  # type: ignore[no-redef]
        """Minimal crontab shim for environments without Celery installed."""

        def __init__(self, hour: int = 0, minute: int = 0, **kwargs):
            del kwargs
            self.hour = {int(hour)}
            self.minute = {int(minute)}

from app.core.config import settings

OPERATOR_STRATEGY_MONITOR_TASK_NAME = "jobs.monitor_operator_strategy"
PAPER_BIDDING_FORWARD_TASK_NAME = "jobs.run_forward_paper_bidding"
HISTORICAL_BACKTEST_TASK_NAME = "jobs.run_historical_backtest"
COLLECT_KONEPS_NOTICES_TASK_NAME = "jobs.collect_koneps_notices"
PROJECT_EMBEDDING_REBUILD_TASK_NAME = "jobs.rebuild_project_embeddings"
PRICE_PREDICTOR_TRAINING_TASK_NAME = "ml.train_price_predictor"
DECISION_EXPERIMENT_REEVALUATION_TASK_NAME = "ml.reevaluate_decision_experiment"
SYNTHETIC_BACKTEST_RUN_TASK_NAME = "jobs.run_synthetic_operator_backtest"
ENRICH_BUSINESS_TYPE_TASK_NAME = "jobs.enrich_pending_business_types"
RECLASSIFY_CATEGORIES_TASK_NAME = "jobs.reclassify_pending_categories"
SMOKE_TEST_TASK_NAME = "jobs.run_koneps_telegram_smoke_test"


def build_task_routes() -> dict[str, dict[str, str]]:
    """Route long-running domains onto dedicated queues."""
    return {
        COLLECT_KONEPS_NOTICES_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        "jobs.send_telegram_notification": {"queue": settings.CELERY_OPS_QUEUE},
        "jobs.poll_telegram_updates": {"queue": settings.CELERY_OPS_QUEUE},
        OPERATOR_STRATEGY_MONITOR_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        PAPER_BIDDING_FORWARD_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        HISTORICAL_BACKTEST_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        SYNTHETIC_BACKTEST_RUN_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        ENRICH_BUSINESS_TYPE_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        SMOKE_TEST_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        PROJECT_EMBEDDING_REBUILD_TASK_NAME: {"queue": settings.CELERY_ML_BACKFILL_QUEUE},
        PRICE_PREDICTOR_TRAINING_TASK_NAME: {"queue": settings.CELERY_ML_TRAINING_QUEUE},
        DECISION_EXPERIMENT_REEVALUATION_TASK_NAME: {"queue": settings.CELERY_ML_REEVALUATION_QUEUE},
    }


def _normalize_task_time_limits(*, soft_seconds: int, hard_seconds: int) -> tuple[int | None, int | None]:
    """Convert configured task limits into Celery-friendly soft/hard values."""
    hard_limit = hard_seconds if hard_seconds > 0 else None
    soft_limit = soft_seconds if soft_seconds > 0 else None

    if hard_limit is not None and soft_limit is not None and soft_limit >= hard_limit:
        soft_limit = max(1, hard_limit - 1)

    return soft_limit, hard_limit


def build_operator_strategy_monitor_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for operator strategy monitoring."""
    if not settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED:
        return {}

    return {
        "operator_strategy_monitor_periodic": {
            "task": OPERATOR_STRATEGY_MONITOR_TASK_NAME,
            "schedule": float(max(1, settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "limit": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT,
                    "high_priority_only": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY,
                    "max_active_bids": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS,
                    "current_workload_score": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE,
                    "same_category_only": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY,
                    "similar_limit": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT,
                    "min_similarity": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY,
                },
                "trigger_source": "scheduled",
            },
        }
    }


def build_paper_bidding_forward_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for forward paper-bidding."""
    if not settings.PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED:
        return {}

    category = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY or "").strip() or None
    scenario = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO or "base").strip() or "base"
    if scenario not in {"conservative", "base", "aggressive"}:
        scenario = "base"

    return {
        "paper_bidding_forward_periodic": {
            "task": PAPER_BIDDING_FORWARD_TASK_NAME,
            "schedule": float(max(1, settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "category": category,
                    "limit": max(1, settings.PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT),
                    "scenario": scenario,
                    "strategy_version": "scheduled-forward-paper",
                    "model_version": "current",
                    "history_limit": max(1, settings.PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT),
                    "persist": settings.PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST,
                },
            },
        }
    }


def build_historical_backtest_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for historical (settled) paper-bidding."""
    if not settings.HISTORICAL_BACKTEST_SCHEDULE_ENABLED:
        return {}

    category = str(settings.HISTORICAL_BACKTEST_SCHEDULE_CATEGORY or "").strip() or None
    scenario = str(settings.HISTORICAL_BACKTEST_SCHEDULE_SCENARIO or "base").strip() or "base"
    if scenario not in {"conservative", "base", "aggressive"}:
        scenario = "base"

    return {
        "historical_backtest_periodic": {
            "task": HISTORICAL_BACKTEST_TASK_NAME,
            "schedule": float(max(1, settings.HISTORICAL_BACKTEST_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "category": category,
                    "limit": max(1, int(settings.HISTORICAL_BACKTEST_SCHEDULE_LIMIT)),
                    "scenario": scenario,
                    "lookback_days": max(1, int(settings.HISTORICAL_BACKTEST_LOOKBACK_DAYS)),
                    "history_limit": max(1, int(settings.HISTORICAL_BACKTEST_SCHEDULE_HISTORY_LIMIT)),
                    "cutoff_hours_before_deadline": max(0, int(settings.HISTORICAL_BACKTEST_SCHEDULE_CUTOFF_HOURS)),
                    "settle_actions": str(settings.HISTORICAL_BACKTEST_SCHEDULE_SETTLE_ACTIONS or "bid_now,review"),
                    "strategy_version": "scheduled-historical-backtest",
                    "model_version": "current",
                    "persist": bool(settings.HISTORICAL_BACKTEST_SCHEDULE_PERSIST),
                },
            },
        }
    }


def build_koneps_collection_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for KONEPS notice collection."""
    if not settings.KONEPS_COLLECTION_SCHEDULE_ENABLED:
        return {}

    category = str(settings.KONEPS_COLLECTION_CATEGORY or "").strip() or None
    execution_mode = str(settings.KONEPS_COLLECTION_EXECUTION_MODE or "auto").strip() or "auto"
    if execution_mode not in {"mock", "live", "auto"}:
        execution_mode = "auto"

    return {
        "koneps_collection_periodic": {
            "task": COLLECT_KONEPS_NOTICES_TASK_NAME,
            "schedule": float(max(1, settings.KONEPS_COLLECTION_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "source": str(settings.KONEPS_COLLECTION_SOURCE or "koneps-openapi").strip() or "koneps-openapi",
                    "category": category,
                    "execution_mode": execution_mode,
                    "max_items": min(100, max(1, int(settings.KONEPS_COLLECTION_MAX_ITEMS))),
                },
            },
        }
    }


def build_business_type_enrichment_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for business_type enrichment."""
    if not settings.BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED:
        return {}

    return {
        "business_type_enrichment_periodic": {
            "task": ENRICH_BUSINESS_TYPE_TASK_NAME,
            "schedule": float(max(1, settings.BUSINESS_TYPE_ENRICHMENT_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "limit": max(1, int(settings.BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT)),
            },
        }
    }


def build_category_reclassify_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for SBERT prototype category reclassification."""
    if not settings.CATEGORY_RECLASSIFY_SCHEDULE_ENABLED:
        return {}

    return {
        "category_reclassify_periodic": {
            "task": RECLASSIFY_CATEGORIES_TASK_NAME,
            "schedule": float(max(1, settings.CATEGORY_RECLASSIFY_INTERVAL_MINUTES) * 60),
            "kwargs": {"limit": max(1, int(settings.CATEGORY_RECLASSIFY_BATCH_LIMIT))},
        }
    }


def build_smoke_test_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the daily KONEPS+Telegram smoke test."""
    if not settings.SMOKE_TEST_SCHEDULE_ENABLED:
        return {}
    hour = max(0, min(23, int(settings.SMOKE_TEST_HOUR_UTC)))
    minute = max(0, min(59, int(settings.SMOKE_TEST_MINUTE)))
    return {
        "smoke_test_daily": {
            "task": SMOKE_TEST_TASK_NAME,
            "schedule": crontab(hour=hour, minute=minute),
        }
    }


def build_celery_runtime_config() -> dict[str, object]:
    """Build the shared Celery runtime configuration for eager and worker-backed modes."""
    soft_time_limit, hard_time_limit = _normalize_task_time_limits(
        soft_seconds=settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS,
        hard_seconds=settings.CELERY_TASK_TIME_LIMIT_SECONDS,
    )
    eager_mode = settings.uses_in_memory_celery

    config: dict[str, object] = {
        "imports": ("app.tasks.jobs",),
        "task_serializer": "json",
        "accept_content": ["json"],
        "result_serializer": "json",
        "timezone": "Asia/Seoul",
        "enable_utc": False,
        "task_default_queue": settings.CELERY_TASK_DEFAULT_QUEUE,
        "task_routes": build_task_routes(),
        "task_always_eager": eager_mode,
        "task_store_eager_result": True,
        "task_ignore_result": False,
        "task_track_started": settings.CELERY_TASK_TRACK_STARTED,
        "result_expires": settings.CELERY_RESULT_EXPIRES_SECONDS,
        "worker_send_task_events": settings.CELERY_WORKER_SEND_TASK_EVENTS,
        "task_send_sent_event": settings.CELERY_TASK_SEND_SENT_EVENT,
        "worker_concurrency": settings.CELERY_WORKER_CONCURRENCY,
        "worker_prefetch_multiplier": settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
        "worker_max_tasks_per_child": settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
        "broker_connection_retry_on_startup": settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP,
        "broker_connection_max_retries": settings.CELERY_BROKER_CONNECTION_MAX_RETRIES,
        "broker_transport_options": {
            "max_retries": settings.CELERY_BROKER_PUBLISH_MAX_RETRIES,
        },
        "beat_schedule": {
            **build_operator_strategy_monitor_beat_schedule(),
            **build_paper_bidding_forward_beat_schedule(),
            **build_historical_backtest_beat_schedule(),
            **build_koneps_collection_beat_schedule(),
            **build_business_type_enrichment_beat_schedule(),
            **build_category_reclassify_beat_schedule(),
            **build_smoke_test_beat_schedule(),
        },
    }

    if soft_time_limit is not None:
        config["task_soft_time_limit"] = soft_time_limit
    if hard_time_limit is not None:
        config["task_time_limit"] = hard_time_limit

    if eager_mode:
        config.update(
            task_acks_late=False,
            task_acks_on_failure_or_timeout=True,
        )
    else:
        config.update(
            task_acks_late=True,
            task_acks_on_failure_or_timeout=True,
            worker_cancel_long_running_tasks_on_connection_loss=True,
        )

    return config


celery_app = Celery(
    "bid_vector",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(**build_celery_runtime_config())
