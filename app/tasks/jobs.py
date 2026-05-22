"""Background jobs."""

from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import CrawlJob
from app.schemas.schemas import CrawlRequest, OperatorStrategyMonitorRequest
from app.services.koneps.collector import KonepsCollectorService
from app.services.ml_training import PricePredictionTrainingService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.update_processor import TelegramSyncService
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.project_similarity import ProjectSimilarityService
from app.services.decision_experiments import DecisionExperimentService
from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    DECISION_EXPERIMENT_REEVALUATION_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    PAPER_BIDDING_FORWARD_TASK_NAME,
    PRICE_PREDICTOR_TRAINING_TASK_NAME,
    PROJECT_EMBEDDING_REBUILD_TASK_NAME,
    celery_app,
)


class _QueuedOnlyTaskHandle:
    """Task handle used when ML work must not execute inside the API process."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


def _enqueue_ml_task(task, *, kwargs: dict[str, Any], queue: str):
    """Queue an ML task, refusing eager in-process execution unless explicitly allowed."""
    if settings.uses_in_memory_celery and not settings.CELERY_ALLOW_INLINE_ML_TASKS:
        return _QueuedOnlyTaskHandle(str(uuid4()))
    return task.apply_async(kwargs=kwargs, queue=queue)


@celery_app.task(name=COLLECT_KONEPS_NOTICES_TASK_NAME)
def collect_koneps_notices(
    request_payload: dict[str, Any] | None = None,
    crawl_job_id: int | None = None,
) -> dict:
    """Collect KONEPS notices and persist crawl history inside a background task."""
    request = CrawlRequest(**(request_payload or {}))
    service = KonepsCollectorService()
    db = SessionLocal()
    crawl_job: CrawlJob | None = None

    try:
        if crawl_job_id is not None:
            crawl_job = db.query(CrawlJob).filter(CrawlJob.id == crawl_job_id).first()

        if crawl_job is None:
            crawl_job = service.create_crawl_job(db, request)
        else:
            crawl_job.source = request.source
            crawl_job.target_date = request.target_date
            crawl_job.status = "running"
            crawl_job.result_count = 0
            crawl_job.error_message = None
            crawl_job.completed_at = None
            db.add(crawl_job)
            db.commit()
            db.refresh(crawl_job)

        result = service.collect_notices(request)
        crawl_job = service.persist_crawl_results(db, crawl_job, request, result)
        result.setdefault("metadata", {})["crawl_job_id"] = crawl_job.id
        return result
    except Exception as exc:
        if crawl_job is not None:
            service.mark_crawl_job_failed(db, crawl_job, str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="jobs.send_telegram_notification")
def send_telegram_notification(
    title: str | None = None,
    message: str | None = None,
    url: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
) -> dict:
    """Send a Telegram notification through the Bot API."""
    service = TelegramNotificationService()
    if title is not None and message is not None:
        payload = service.build_message(title, message, url)
    else:
        payload = message or ""
    return service.send_message(payload, reply_markup=reply_markup, chat_id=chat_id)


@celery_app.task(name="jobs.poll_telegram_updates")
def poll_telegram_updates(limit: int | None = None, timeout_seconds: int | None = None) -> dict:
    """Poll Telegram updates and process them using the shared sync service."""
    db = SessionLocal()
    try:
        return TelegramSyncService().sync_updates(db, limit=limit, timeout_seconds=timeout_seconds)
    finally:
        db.close()


@celery_app.task(name=PROJECT_EMBEDDING_REBUILD_TASK_NAME)
def rebuild_project_embeddings(
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    project_status: str | None = None,
    force: bool = False,
) -> dict:
    """Refresh stored project embeddings in a batch-friendly task."""
    db = SessionLocal()
    try:
        result = ProjectSimilarityService().rebuild_project_embeddings(
            db,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name=PRICE_PREDICTOR_TRAINING_TASK_NAME)
def train_price_predictor(request_payload: dict[str, Any] | None = None) -> dict:
    """Run price-predictor training in the dedicated ML training queue."""
    db = SessionLocal()
    try:
        return PricePredictionTrainingService().train_price_predictor(db, request_payload=request_payload)
    finally:
        db.close()


@celery_app.task(name=DECISION_EXPERIMENT_REEVALUATION_TASK_NAME)
def reevaluate_decision_experiment(experiment_run_id: int) -> dict:
    """Re-evaluate a decision experiment outside the API request path."""
    db = SessionLocal()
    try:
        return DecisionExperimentService().evaluate_run(db, run_id=int(experiment_run_id))
    finally:
        db.close()


@celery_app.task(name=OPERATOR_STRATEGY_MONITOR_TASK_NAME)
def monitor_operator_strategy(
    request_payload: dict[str, Any] | None = None,
    monitor_run_id: int | None = None,
    trigger_source: str = StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
) -> dict:
    """Execute the stored operator strategy and persist bid decisions in a background task."""
    request = OperatorStrategyMonitorRequest(**(request_payload or {}))
    db = SessionLocal()
    try:
        return StrategyMonitoringService().execute_monitoring(
            db,
            request=request,
            trigger_source=trigger_source,
            existing_run_id=monitor_run_id,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name=PAPER_BIDDING_FORWARD_TASK_NAME)
def run_forward_paper_bidding(request_payload: dict[str, Any] | None = None) -> dict:
    """Generate forward paper bids for currently open/re-notice projects."""
    payload = dict(request_payload or {})
    db = SessionLocal()
    try:
        return PaperBiddingBacktestService().run_forward_paper_bidding(
            db,
            category=payload.get("category"),
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            strategy_version=str(payload.get("strategy_version") or "scheduled-forward-paper"),
            model_version=str(payload.get("model_version") or "current"),
            history_limit=int(payload.get("history_limit") or 80),
            persist=bool(payload.get("persist", True)),
        )
    finally:
        db.close()


def enqueue_project_embedding_rebuild(
    *,
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    project_status: str | None = None,
    force: bool = False,
):
    """Queue a project embedding rebuild task and return the async task handle."""
    return _enqueue_ml_task(
        rebuild_project_embeddings,
        kwargs={
            "limit": limit,
            "offset": offset,
            "category": category,
            "project_status": project_status,
            "force": force,
        },
        queue=settings.CELERY_ML_BACKFILL_QUEUE,
    )


def enqueue_price_predictor_training(*, request_payload: dict[str, Any]):
    """Queue a price predictor training task and return the async task handle."""
    return _enqueue_ml_task(
        train_price_predictor,
        kwargs={"request_payload": request_payload},
        queue=settings.CELERY_ML_TRAINING_QUEUE,
    )


def enqueue_decision_experiment_reevaluation(*, experiment_run_id: int):
    """Queue a decision experiment re-evaluation task and return the async task handle."""
    return _enqueue_ml_task(
        reevaluate_decision_experiment,
        kwargs={"experiment_run_id": int(experiment_run_id)},
        queue=settings.CELERY_ML_REEVALUATION_QUEUE,
    )


def enqueue_operator_strategy_monitor(
    *,
    request: OperatorStrategyMonitorRequest,
    monitor_run_id: int | None = None,
    trigger_source: str = StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
):
    """Queue an operator strategy monitoring task and return the async task handle."""
    return monitor_operator_strategy.apply_async(
        kwargs={
            "request_payload": request.model_dump(mode="json"),
            "monitor_run_id": monitor_run_id,
            "trigger_source": trigger_source,
        },
        queue=settings.CELERY_OPS_QUEUE,
    )


def enqueue_koneps_notice_collection(
    *,
    request: CrawlRequest,
    crawl_job_id: int | None = None,
):
    """Queue a KONEPS crawl task and return the async task handle."""
    return collect_koneps_notices.apply_async(
        kwargs={
            "request_payload": request.model_dump(mode="json"),
            "crawl_job_id": crawl_job_id,
        },
        queue=settings.CELERY_OPS_QUEUE,
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
    if isinstance(result, dict) and isinstance(result.get("monitor_run_id"), int):
        monitor_run_id = int(result["monitor_run_id"])
        result.setdefault("task_id", task_id)

    payload: dict[str, Any] = {
        "task_id": task_id,
        "monitor_run_id": monitor_run_id,
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
