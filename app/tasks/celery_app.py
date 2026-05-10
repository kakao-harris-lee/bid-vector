"""Celery app skeleton."""

from uuid import uuid4

try:
    from celery import Celery
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
            task_id = str(uuid4())
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

from app.core.config import settings

OPERATOR_STRATEGY_MONITOR_TASK_NAME = "jobs.monitor_operator_strategy"


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


celery_app = Celery(
    "bid_vector",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=False,
    task_always_eager=settings.CELERY_BROKER_URL.startswith("memory://"),
    task_store_eager_result=True,
    task_ignore_result=False,
    beat_schedule=build_operator_strategy_monitor_beat_schedule(),
)
