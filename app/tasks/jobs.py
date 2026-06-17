"""Background jobs."""

import logging
from typing import Any
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import CrawlJob, User
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
    ENRICH_BUSINESS_TYPE_TASK_NAME,
    FORWARD_SETTLEMENT_TASK_NAME,
    HISTORICAL_BACKTEST_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    PAPER_BIDDING_FORWARD_TASK_NAME,
    PRICE_PREDICTOR_TRAINING_TASK_NAME,
    PROJECT_EMBEDDING_REBUILD_TASK_NAME,
    RECLASSIFY_CATEGORIES_TASK_NAME,
    SMOKE_TEST_TASK_NAME,
    SYNTHETIC_BACKTEST_RUN_TASK_NAME,
    celery_app,
)

logger = logging.getLogger(__name__)


class _QueuedOnlyTaskHandle:
    """Task handle used when ML work must not execute inside the API process."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


def _enqueue_ml_task(task, *, kwargs: dict[str, Any], queue: str):
    """Queue an ML task, refusing eager in-process execution unless explicitly allowed."""
    if settings.uses_in_memory_celery and not settings.CELERY_ALLOW_INLINE_ML_TASKS:
        return _QueuedOnlyTaskHandle(str(uuid4()))
    return task.apply_async(kwargs=kwargs, queue=queue)


@celery_app.task(bind=True, name=COLLECT_KONEPS_NOTICES_TASK_NAME)
def collect_koneps_notices(
    self,
    request_payload: dict[str, Any] | None = None,
    crawl_job_id: int | None = None,
) -> dict:
    """Collect KONEPS notices and persist crawl history inside a background task.

    Idempotency: with ``task_acks_late=True`` a task that is SIGKILLed by the
    hard time limit is redelivered with the *same* Celery task id but no
    ``crawl_job_id`` (beat dispatch). Stamping ``celery_task_id`` on the row and
    reusing it on redelivery prevents an orphan ``running`` crawl-job from being
    created on every redelivery.
    """
    request = CrawlRequest(**(request_payload or {}))
    service = KonepsCollectorService()
    db = SessionLocal()
    crawl_job: CrawlJob | None = None
    task_id = getattr(getattr(self, "request", None), "id", None)

    try:
        if crawl_job_id is not None:
            crawl_job = db.query(CrawlJob).filter(CrawlJob.id == crawl_job_id).first()

        # Redelivery path: no explicit crawl_job_id but a row already exists for
        # this task id -- reuse it instead of spawning an orphan.
        if crawl_job is None and task_id:
            crawl_job = (
                db.query(CrawlJob)
                .filter(CrawlJob.celery_task_id == str(task_id))
                .order_by(CrawlJob.id.desc())
                .first()
            )

        if crawl_job is None:
            # First delivery: stamp the task id in the INSERT so the row is
            # recoverable immediately (no orphan window) and the realtime event
            # is published once, already stamped. create_crawl_job commits.
            crawl_job = service.create_crawl_job(
                db, request, celery_task_id=task_id
            )
        else:
            # Reuse path (explicit crawl_job_id or redelivery match): reset the
            # row to running and ensure the task id is recorded.
            crawl_job.source = request.source
            crawl_job.target_date = request.target_date
            crawl_job.status = "running"
            crawl_job.result_count = 0
            crawl_job.error_message = None
            crawl_job.completed_at = None
            # Only stamp when empty: a manual re-queue / async retry may reuse an
            # existing row under a new task id; overwriting would let a later
            # redelivery-match grab the wrong row.
            if task_id and not crawl_job.celery_task_id:
                crawl_job.celery_task_id = str(task_id)
            db.add(crawl_job)
            db.commit()
            db.refresh(crawl_job)

        result = service.collect_notices(request, db=db)
        # Defer embeddings only on this time-limited Celery path; synchronous
        # callers embed inline (see persist_crawl_results docstring).
        defer_embeddings = service._is_scsbid_openapi_source(request.source)
        crawl_job = service.persist_crawl_results(
            db, crawl_job, request, result, defer_embeddings=defer_embeddings
        )
        result.setdefault("metadata", {})["crawl_job_id"] = crawl_job.id

        deferred_ids = result.get("metadata", {}).get("deferred_embedding_project_ids")
        if deferred_ids:
            _enqueue_deferred_embedding_backfill(list(deferred_ids))

        return result
    except SoftTimeLimitExceeded as exc:
        # Stop the redelivery loop: mark this run failed and ack the message so
        # the same payload is not re-run forever past the soft limit.
        if crawl_job is not None:
            service.mark_crawl_job_failed(
                db, crawl_job, f"soft time limit exceeded: {exc}"
            )
        logger.warning(
            "collect_koneps_notices hit soft time limit (task_id=%s source=%s)",
            task_id,
            request.source,
        )
        return {
            "job_status": "failed",
            "source": request.source,
            "collected_count": 0,
            "items": [],
            "metadata": {
                "crawl_job_id": int(crawl_job.id) if crawl_job is not None else None,
                "error": "soft_time_limit_exceeded",
            },
        }
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
    project_ids: list[int] | None = None,
) -> dict:
    """Refresh stored project embeddings in a batch-friendly task.

    When ``project_ids`` is supplied the rebuild targets exactly those rows
    (used by the deferred-embedding backfill enqueued after scsbid crawl
    persistence); paging is bypassed in that mode.
    """
    db = SessionLocal()
    try:
        result = ProjectSimilarityService().rebuild_project_embeddings(
            db,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
            project_ids=project_ids,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _enqueue_deferred_embedding_backfill(project_ids: list[int]) -> int:
    """Queue async embedding rebuild(s) for projects whose embeddings were deferred.

    The ids are split into bounded chunks (``EMBEDDING_BACKFILL_CHUNK_SIZE``) and
    enqueued as separate ``rebuild_project_embeddings`` tasks so a large catch-up
    sweep cannot run as one unbounded task that re-creates the time-limit
    redelivery loop on the ML backfill queue.

    ``force=False`` mirrors the original inline semantics: a freshly created
    project (no cached vector) is still embedded, while an unchanged existing
    project that matched the crawl is a no-op (skipped) — so award rows that map
    to pre-existing notices do not trigger thousands of needless re-embeds.

    Isolated with a try/except so a failed enqueue never breaks a successful
    crawl. Honours the ``CELERY_ALLOW_INLINE_ML_TASKS`` guard: on the in-memory
    eager broker the ML task is not run inline unless explicitly allowed.

    Returns the number of tasks enqueued (0 on empty input or failure).
    """
    normalized = sorted({int(pid) for pid in project_ids})
    if not normalized:
        return 0

    chunk_size = max(1, int(settings.EMBEDDING_BACKFILL_CHUNK_SIZE))
    enqueued = 0
    try:
        for start in range(0, len(normalized), chunk_size):
            chunk = normalized[start:start + chunk_size]
            _enqueue_ml_task(
                rebuild_project_embeddings,
                kwargs={"project_ids": chunk, "force": False},
                queue=settings.CELERY_ML_BACKFILL_QUEUE,
            )
            enqueued += 1
    except Exception:  # pragma: no cover - defensive: enqueue must not break crawl
        logger.exception(
            "deferred embedding backfill enqueue failed for %d project(s)",
            len(normalized),
        )
    return enqueued


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
    operator_id: int | None = None,
) -> dict:
    """Execute the stored operator strategy and persist bid decisions in a background task."""
    request = OperatorStrategyMonitorRequest(**(request_payload or {}))
    db = SessionLocal()
    try:
        operator = None
        if operator_id is not None:
            operator = db.query(User).filter(User.id == int(operator_id)).first()
            if operator is None:
                raise ValueError(f"Operator {int(operator_id)} not found")
        return StrategyMonitoringService().execute_monitoring(
            db,
            request=request,
            trigger_source=trigger_source,
            existing_run_id=monitor_run_id,
            operator=operator,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name=SYNTHETIC_BACKTEST_RUN_TASK_NAME)
def run_synthetic_operator_backtest(payload: dict[str, Any] | None = None) -> dict:
    """Run the per-synthetic-operator backtest in a background worker.

    Mirrors the synchronous `/api/v1/synthetic/backtests/run` endpoint but
    returns the comparison payload as the task result so the frontend can poll
    `/api/v1/synthetic/backtests/tasks/{task_id}` for completion.

    When the payload carries a ``run_id`` (Experiment Lab execution), the
    run/result lifecycle is persisted via ``run_experiment_backtest``; the legacy
    ad-hoc path (no ``run_id``) keeps its original behaviour unchanged.
    """
    from datetime import datetime
    from app.services.synthetic_backtest import SyntheticBacktestService

    data = dict(payload or {})

    if data.get("run_id") is not None:
        from app.services.synthetic_experiment import run_experiment_backtest

        return run_experiment_backtest(data)
    start_at_raw = data.get("start_at")
    end_at_raw = data.get("end_at")
    start_at = datetime.fromisoformat(start_at_raw) if isinstance(start_at_raw, str) else None
    end_at = datetime.fromisoformat(end_at_raw) if isinstance(end_at_raw, str) else None

    db = SessionLocal()
    try:
        return SyntheticBacktestService().run_for_all(
            db,
            start_at=start_at,
            end_at=end_at,
            category=data.get("category"),
            limit=int(data.get("limit") or 100),
            scenario=str(data.get("scenario") or "base"),
            slugs=data.get("slugs"),
        )
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


@celery_app.task(name=FORWARD_SETTLEMENT_TASK_NAME)
def settle_forward_paper_bids(
    operator_id: int | None = None,
    limit: int = 200,
    persist: bool = True,
) -> dict:
    """Settle forward paper bids whose deadline has passed and result is available."""
    db = SessionLocal()
    try:
        return PaperBiddingBacktestService().run_forward_settlement(
            db,
            operator_id=int(operator_id) if operator_id is not None else None,
            limit=int(limit or 200),
            persist=bool(persist),
        )
    finally:
        db.close()


@celery_app.task(name=HISTORICAL_BACKTEST_TASK_NAME)
def run_historical_backtest(request_payload: dict[str, Any] | None = None) -> dict:
    """Replay awarded TenderResults as paper_bid + settlement comparison."""
    from datetime import datetime, timedelta, timezone
    from app.core.config import settings as runtime_settings
    from app.services.paper_bidding_backtest import PaperBiddingBacktestService

    payload = dict(request_payload or {})
    db = SessionLocal()
    try:
        lookback = max(1, int(payload.pop("lookback_days", runtime_settings.HISTORICAL_BACKTEST_LOOKBACK_DAYS)))
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=lookback)
        settle_actions_raw = payload.pop("settle_actions", None)
        if isinstance(settle_actions_raw, str):
            settle_actions = tuple(s.strip() for s in settle_actions_raw.split(",") if s.strip())
        elif isinstance(settle_actions_raw, (list, tuple)):
            settle_actions = tuple(settle_actions_raw)
        else:
            settle_actions = ("bid_now", "review")
        return PaperBiddingBacktestService().run_historical_backtest(
            db,
            category=payload.get("category") or None,
            start_at=start_at,
            end_at=end_at,
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            strategy_version=str(payload.get("strategy_version") or "scheduled-historical-backtest"),
            model_version=str(payload.get("model_version") or "current"),
            cutoff_hours_before_deadline=int(payload.get("cutoff_hours_before_deadline") or 2),
            history_limit=int(payload.get("history_limit") or 80),
            settle_actions=settle_actions,
            persist=bool(payload.get("persist", True)),
        )
    finally:
        db.close()


@celery_app.task(name=ENRICH_BUSINESS_TYPE_TASK_NAME)
def enrich_pending_business_types(limit: int | None = None) -> dict:
    """Persist business_type_code/label for recently-collected projects."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    effective_limit = int(limit if limit is not None else settings.BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT)
    effective_limit = max(1, effective_limit)

    db = SessionLocal()
    try:
        return BusinessTypeEnrichmentService().enrich_pending(db, limit=effective_limit)
    finally:
        db.close()


@celery_app.task(name=RECLASSIFY_CATEGORIES_TASK_NAME)
def reclassify_pending_categories(limit: int | None = None) -> dict:
    """Re-assign Project.category for rows stuck at 'general'/'other' via SBERT prototype cosine sim."""
    from app.core.config import settings
    from app.services.category_classifier import CategoryClassifierService

    effective_limit = int(limit if limit is not None else settings.CATEGORY_RECLASSIFY_BATCH_LIMIT)
    effective_limit = max(1, effective_limit)

    db = SessionLocal()
    try:
        return CategoryClassifierService().reclassify_pending(db, limit=effective_limit)
    finally:
        db.close()


@celery_app.task(name=SMOKE_TEST_TASK_NAME)
def run_koneps_telegram_smoke_test() -> dict:
    """Daily KONEPS + Telegram end-to-end smoke test."""
    from dataclasses import asdict
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    db = SessionLocal()
    try:
        service = KonepsTelegramSmokeTestService()
        report = service.run(db)
        try:
            service.persist_report(db, report)
        except Exception:  # noqa: BLE001 — persistence must not mask the smoke result
            logger.exception("failed to persist smoke test run")
        return asdict(report)
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


def enqueue_synthetic_operator_backtest(*, payload: dict[str, Any]):
    """Queue a per-synthetic-operator backtest task and return the async task handle."""
    return run_synthetic_operator_backtest.apply_async(
        kwargs={"payload": payload},
        queue=settings.CELERY_OPS_QUEUE,
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


def enqueue_operator_strategy_monitor(
    *,
    request: OperatorStrategyMonitorRequest,
    monitor_run_id: int | None = None,
    trigger_source: str = StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
    operator_id: int | None = None,
):
    """Queue an operator strategy monitoring task and return the async task handle."""
    return monitor_operator_strategy.apply_async(
        kwargs={
            "request_payload": request.model_dump(mode="json"),
            "monitor_run_id": monitor_run_id,
            "trigger_source": trigger_source,
            "operator_id": operator_id,
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
