"""Celery task-status polling helpers.

Read-only projection of a Celery ``AsyncResult`` into the stable poll response
shape returned by the API status endpoints. Extracted verbatim from
``app.tasks.jobs`` (§4.5 size decomposition); the public ``get_*`` helpers are
re-exported from ``app.tasks.jobs`` so existing ``from app.tasks.jobs import
get_...`` call sites keep working unchanged.
"""

from typing import Any

from app.core.config import settings
from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    DECISION_EXPERIMENT_REEVALUATION_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    PRICE_PREDICTOR_TRAINING_TASK_NAME,
    PROJECT_EMBEDDING_REBUILD_TASK_NAME,
    SYNTHETIC_BACKTEST_RUN_TASK_NAME,
    celery_app,
)


def get_synthetic_backtest_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of a queued synthetic backtest task."""
    return _build_generic_task_status(
        task_id,
        task_name=SYNTHETIC_BACKTEST_RUN_TASK_NAME,
        queue=settings.CELERY_OPS_QUEUE,
        pending_detail="Synthetic backtest is queued.",
        started_detail="Synthetic backtest is running per operator.",
        success_detail="Synthetic backtest completed.",
        failure_detail="Synthetic backtest failed.",
    )


def get_koneps_notice_collection_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of a KONEPS crawl task."""
    async_result = celery_app.AsyncResult(task_id)
    raw_status = str(getattr(async_result, "state", getattr(async_result, "status", "PENDING")) or "PENDING").upper()
    ready = bool(async_result.ready()) if hasattr(async_result, "ready") else raw_status in {"SUCCESS", "FAILURE", "REVOKED"}
    successful = bool(async_result.successful()) if hasattr(async_result, "successful") else raw_status == "SUCCESS"
    result = getattr(async_result, "result", None)
    normalized_status = _normalize_celery_status(raw_status)

    detail = {
        "PENDING": "Task is queued or unknown to the current result backend.",
        "STARTED": "Task is currently collecting KONEPS notices.",
        "SUCCESS": "Task completed successfully.",
        "FAILURE": "Task failed while collecting KONEPS notices.",
        "RETRY": "Task is retrying after a temporary failure.",
        "REVOKED": "Task was cancelled before completion.",
    }.get(raw_status, "Task status is available.")

    crawl_job_id: int | None = None
    if isinstance(result, dict):
        metadata = result.get("metadata", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("crawl_job_id"), int):
            crawl_job_id = metadata["crawl_job_id"]

    payload: dict[str, Any] = {
        "task_id": task_id,
        "task_name": COLLECT_KONEPS_NOTICES_TASK_NAME,
        "status": normalized_status,
        "raw_status": raw_status,
        "ready": ready,
        "successful": successful,
        "detail": detail,
        "crawl_job_id": crawl_job_id,
        "error": None,
        "result": result if successful and isinstance(result, dict) else None,
    }

    if raw_status == "FAILURE" and result is not None:
        payload["error"] = str(result)

    return payload


def get_operator_strategy_monitor_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of an operator strategy monitoring task."""
    async_result = celery_app.AsyncResult(task_id)
    raw_status = str(getattr(async_result, "state", getattr(async_result, "status", "PENDING")) or "PENDING").upper()
    ready = bool(async_result.ready()) if hasattr(async_result, "ready") else raw_status in {"SUCCESS", "FAILURE", "REVOKED"}
    successful = bool(async_result.successful()) if hasattr(async_result, "successful") else raw_status == "SUCCESS"
    result = getattr(async_result, "result", None)
    normalized_status = _normalize_celery_status(raw_status)

    detail = {
        "PENDING": "Task is queued or unknown to the current result backend.",
        "STARTED": "Task is currently executing the operator strategy monitor.",
        "SUCCESS": "Task completed successfully.",
        "FAILURE": "Task failed while executing the operator strategy monitor.",
        "RETRY": "Task is retrying after a temporary failure.",
        "REVOKED": "Task was cancelled before completion.",
    }.get(raw_status, "Task status is available.")

    monitor_run_id: int | None = None
    operator_id: int | None = None
    if isinstance(result, dict) and isinstance(result.get("monitor_run_id"), int):
        monitor_run_id = int(result["monitor_run_id"])
        result.setdefault("task_id", task_id)
    if isinstance(result, dict) and isinstance(result.get("operator_id"), int):
        operator_id = int(result["operator_id"])

    payload: dict[str, Any] = {
        "task_id": task_id,
        "monitor_run_id": monitor_run_id,
        "operator_id": operator_id,
        "task_name": OPERATOR_STRATEGY_MONITOR_TASK_NAME,
        "status": normalized_status,
        "raw_status": raw_status,
        "ready": ready,
        "successful": successful,
        "detail": detail,
        "error": None,
        "result": result if successful and isinstance(result, dict) else None,
    }

    if raw_status == "FAILURE" and result is not None:
        payload["error"] = str(result)

    return payload


def get_project_embedding_rebuild_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of an embedding rebuild task."""
    async_result = celery_app.AsyncResult(task_id)
    raw_status = str(getattr(async_result, "state", getattr(async_result, "status", "PENDING")) or "PENDING").upper()
    ready = bool(async_result.ready()) if hasattr(async_result, "ready") else raw_status in {"SUCCESS", "FAILURE", "REVOKED"}
    successful = bool(async_result.successful()) if hasattr(async_result, "successful") else raw_status == "SUCCESS"
    result = getattr(async_result, "result", None)
    normalized_status = _normalize_celery_status(raw_status)

    detail = {
        "PENDING": "Task is queued or unknown to the current result backend.",
        "STARTED": "Task is currently rebuilding project embeddings.",
        "SUCCESS": "Task completed successfully.",
        "FAILURE": "Task failed while rebuilding project embeddings.",
        "RETRY": "Task is retrying after a temporary failure.",
        "REVOKED": "Task was cancelled before completion.",
    }.get(raw_status, "Task status is available.")

    payload: dict[str, Any] = {
        "task_id": task_id,
        "task_name": PROJECT_EMBEDDING_REBUILD_TASK_NAME,
        "status": normalized_status,
        "raw_status": raw_status,
        "ready": ready,
        "successful": successful,
        "detail": detail,
        "error": None,
        "result": result if successful and isinstance(result, dict) else None,
    }

    if raw_status == "FAILURE" and result is not None:
        payload["error"] = str(result)

    return payload


def get_price_predictor_training_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of a queued price-predictor training task."""
    return _build_generic_task_status(
        task_id,
        task_name=PRICE_PREDICTOR_TRAINING_TASK_NAME,
        queue=settings.CELERY_ML_TRAINING_QUEUE,
        pending_detail="Task is queued or unknown to the current result backend.",
        started_detail="Task is currently training price predictor artifacts.",
        success_detail="Task completed successfully.",
        failure_detail="Task failed while training price predictor artifacts.",
    )


def get_decision_experiment_reevaluation_task_status(task_id: str) -> dict[str, Any]:
    """Read and normalize the current state of a queued decision-experiment re-evaluation task."""
    return _build_generic_task_status(
        task_id,
        task_name=DECISION_EXPERIMENT_REEVALUATION_TASK_NAME,
        queue=settings.CELERY_ML_REEVALUATION_QUEUE,
        pending_detail="Task is queued or unknown to the current result backend.",
        started_detail="Task is currently re-evaluating the decision experiment.",
        success_detail="Task completed successfully.",
        failure_detail="Task failed while re-evaluating the decision experiment.",
    )


def _build_generic_task_status(
    task_id: str,
    *,
    task_name: str,
    queue: str,
    pending_detail: str,
    started_detail: str,
    success_detail: str,
    failure_detail: str,
) -> dict[str, Any]:
    """Read a Celery task status into a stable poll response."""
    async_result = celery_app.AsyncResult(task_id)
    raw_status = str(getattr(async_result, "state", getattr(async_result, "status", "PENDING")) or "PENDING").upper()
    ready = bool(async_result.ready()) if hasattr(async_result, "ready") else raw_status in {"SUCCESS", "FAILURE", "REVOKED"}
    successful = bool(async_result.successful()) if hasattr(async_result, "successful") else raw_status == "SUCCESS"
    result = getattr(async_result, "result", None)
    detail = {
        "PENDING": pending_detail,
        "STARTED": started_detail,
        "SUCCESS": success_detail,
        "FAILURE": failure_detail,
        "RETRY": "Task is retrying after a temporary failure.",
        "REVOKED": "Task was cancelled before completion.",
    }.get(raw_status, "Task status is available.")

    payload: dict[str, Any] = {
        "task_id": task_id,
        "task_name": task_name,
        "queue": queue,
        "status": _normalize_celery_status(raw_status),
        "raw_status": raw_status,
        "ready": ready,
        "successful": successful,
        "detail": detail,
        "error": None,
        "result": result if successful and isinstance(result, dict) else None,
    }
    if raw_status == "FAILURE" and result is not None:
        payload["error"] = str(result)
    return payload


def _normalize_celery_status(raw_status: str) -> str:
    """Map raw Celery states to a stable API contract."""
    return {
        "PENDING": "queued",
        "RECEIVED": "queued",
        "STARTED": "running",
        "RETRY": "running",
        "SUCCESS": "completed",
        "FAILURE": "failed",
        "REVOKED": "cancelled",
    }.get(raw_status, "queued")
