"""Background jobs.

Celery task registry. Each ``@celery_app.task`` entry is defined here so its
registration name is unchanged; heavier task bodies and pure helpers live in
sibling ``app/tasks/`` modules (§4.5 size decomposition) and are delegated to
from thin shells. The deferred-backfill enqueue helpers and ``_enqueue_ml_task``
stay in THIS module because tests patch them here
(``jobs._enqueue_ml_task`` / ``jobs._enqueue_deferred_embedding_backfill``) and
rely on them resolving each other within this module namespace.
"""

import logging
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.database import task_session
from app.models.models import User
from app.schemas.schemas import CrawlRequest, OperatorStrategyMonitorRequest
from app.schemas.task_payloads import CrawlTaskRequest, ForwardPaperBiddingTaskRequest, HistoricalBacktestTaskRequest, PricePredictionTrainingTaskRequest, ScsbidReserveDetailBackfillRequest, SyntheticOperatorBacktestTaskRequest, TelegramNotificationTaskRequest
from app.services.decision_experiments import DecisionExperimentService
from app.services.ml_training import PricePredictionTrainingService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.update_processor import TelegramSyncService
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.preview_snapshot import PreviewSnapshotService
from app.services.project_similarity import ProjectSimilarityService
from app.tasks.backtest_jobs import (
    run_historical_backtest_job,
    run_synthetic_operator_backtest_job,
)
from app.tasks.celery_app import (
    BACKFILL_SCSBID_RESERVE_DETAIL_TASK_NAME,
    COLLECT_G2_EVIDENCE_TASK_NAME,
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    DECISION_EXPERIMENT_REEVALUATION_TASK_NAME,
    ENRICH_BUSINESS_TYPE_TASK_NAME,
    FORWARD_SETTLEMENT_TASK_NAME,
    G2_CANDIDATE_RECHECK_TASK_NAME,
    HISTORICAL_BACKTEST_TASK_NAME,
    NOTIFY_AWARD_RESULTS_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    PAPER_BIDDING_FORWARD_TASK_NAME,
    PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME,
    PRICE_PREDICTOR_TRAINING_TASK_NAME,
    PROJECT_EMBEDDING_REBUILD_TASK_NAME,
    RECLASSIFY_CATEGORIES_TASK_NAME,
    RECONCILE_STALE_TASK_RUNS_TASK_NAME,
    SMOKE_TEST_TASK_NAME,
    SYNTHETIC_BACKTEST_RUN_TASK_NAME,
    celery_app,
)
from app.tasks.collection_jobs import run_koneps_collection_job
from app.tasks.evidence_jobs import (
    _write_g2_daily_evidence_draft,
    run_collect_g2_evidence_job,
    run_g2_candidate_recheck_job,
)
from app.tasks.reserve_detail_backfill import (
    _normalize_deferred_reserve_notices,
    run_scsbid_reserve_detail_backfill_job,
)
from app.tasks.task_status import (
    get_decision_experiment_reevaluation_task_status,
    get_koneps_notice_collection_task_status,
    get_operator_strategy_monitor_task_status,
    get_price_predictor_training_task_status,
    get_project_embedding_rebuild_task_status,
    get_synthetic_backtest_task_status,
)

logger = logging.getLogger(__name__)

# Names imported above for re-export only (the task-status poll helpers) — kept
# importable from ``app.tasks.jobs`` so existing ``from app.tasks.jobs import
# get_...`` call sites keep working after the §4.5 decomposition. Listing them in
# ``__all__`` marks them as used for linters. ``_write_g2_daily_evidence_draft``
# and the job/helper functions are used internally by the shells below.
__all__ = [
    "get_decision_experiment_reevaluation_task_status",
    "get_koneps_notice_collection_task_status",
    "get_operator_strategy_monitor_task_status",
    "get_price_predictor_training_task_status",
    "get_project_embedding_rebuild_task_status",
    "get_synthetic_backtest_task_status",
]


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

    Thin shell: the payload is promoted to ``CrawlTaskRequest`` (the sender dumps the
    same field set), the body lives in ``app.tasks.collection_jobs`` and the deferred
    backfill enqueue helpers (patched here in tests) are injected.
    """
    return run_koneps_collection_job(
        self,
        request=CrawlTaskRequest.model_validate(request_payload or {}),
        crawl_job_id=crawl_job_id,
        enqueue_deferred_embedding_backfill=_enqueue_deferred_embedding_backfill,
        enqueue_deferred_reserve_detail_backfill=_enqueue_deferred_reserve_detail_backfill,
    )


@celery_app.task(name="jobs.send_telegram_notification")
def send_telegram_notification(
    title: str | None = None,
    message: str | None = None,
    url: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
) -> dict:
    """Send a Telegram notification through the Bot API (payload validated first)."""
    request = TelegramNotificationTaskRequest.model_validate(
        {"title": title, "message": message, "url": url, "chat_id": chat_id, "reply_markup": reply_markup}
    )
    service = TelegramNotificationService()
    return service.send_message(
        request.build_text(service.build_message),
        reply_markup=request.bot_api_reply_markup(),
        chat_id=request.chat_id,
    )


@celery_app.task(name="jobs.poll_telegram_updates")
def poll_telegram_updates(limit: int | None = None, timeout_seconds: int | None = None) -> dict:
    """Poll Telegram updates and process them using the shared sync service."""
    with task_session() as db:
        return TelegramSyncService().sync_updates(db, limit=limit, timeout_seconds=timeout_seconds)


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
    with task_session() as db:
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


def enqueue_project_embedding_backfill(project_ids: list[int]) -> int:
    """Queue async embedding rebuild(s) for a specific set of project ids.

    Thin public wrapper over :func:`_enqueue_deferred_embedding_backfill` for
    request-path callers (e.g. ``POST /projects``) that need to move the heavy
    SBERT ``model.encode`` off the synchronous request: the freshly created row
    is embedded later by the ``rebuild_project_embeddings`` worker task instead
    of inline. Shares the same semantics as the deferred-crawl backfill:

    - ``force=False`` so a brand-new project (no cached vector) is embedded while
      an unchanged existing project is a no-op.
    - bounded chunking via ``EMBEDDING_BACKFILL_CHUNK_SIZE``.
    - honours the ``CELERY_ALLOW_INLINE_ML_TASKS`` guard, so on the in-memory
      eager broker (tests) the ML task is only queued, never run inline.

    Returns the number of tasks enqueued (0 on empty input or failure).
    """
    return _enqueue_deferred_embedding_backfill(project_ids)


def _enqueue_deferred_reserve_detail_backfill(notices: list[dict[str, Any]]) -> int:
    """Queue ONE serial-chain root task for the deferred reserve-detail notices.

    ScsbidInfoService imposes a *rate* limit (HTTP 429 "API token quota exceeded"),
    not a daily quota: it recovers within ~2 minutes of a 429. The previous version
    split the deferred set into N bounded chunks and enqueued them all at once, so
    several ops workers burst the reserve-detail API *concurrently* and tripped the
    rate limit (small bursts passed with 0 errors, large bursts saw mass 429s).
    Concurrency, not total volume, was the cause.

    The fix enqueues a *single* root ``backfill_scsbid_reserve_detail`` task carrying
    the entire cleaned notice list. That task processes only one
    ``KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE``-sized chunk and self-chains a
    continuation for the remainder, so the API is hit strictly serially.

    Unlike the embedding backfill this is NOT an ML task, so it bypasses the
    ``_enqueue_ml_task`` inline-ML guard and enqueues directly onto the ops queue
    (same pattern as ``collect_koneps_notices.apply_async``).

    Isolated with a try/except so a failed enqueue never breaks a successful
    crawl. Returns the number of root tasks enqueued (1, or 0 on empty/failure).
    """
    cleaned = _normalize_deferred_reserve_notices(notices)
    if not cleaned:
        return 0

    try:
        backfill_scsbid_reserve_detail.apply_async(
            kwargs={"notices": cleaned},
            queue=settings.CELERY_OPS_QUEUE,
        )
        return 1
    except Exception:  # pragma: no cover - defensive: enqueue must not break crawl
        logger.exception(
            "deferred reserve-detail backfill enqueue failed for %d notice(s)",
            len(cleaned),
        )
        return 0


@celery_app.task(bind=True, name=BACKFILL_SCSBID_RESERVE_DETAIL_TASK_NAME)
def backfill_scsbid_reserve_detail(self, notices: list[dict[str, Any]]) -> dict:
    """Fetch deferred scsbid reserve-detail rows and persist them per notice.

    Thin shell: the notices are promoted to a validated DTO here, the body lives in
    ``app.tasks.reserve_detail_backfill``, and the serial self-chain continuation
    (which references this task) is injected.
    """
    request = ScsbidReserveDetailBackfillRequest.model_validate({"notices": notices or []})
    return run_scsbid_reserve_detail_backfill_job(
        request, enqueue_continuation=_enqueue_reserve_detail_continuation
    )


def _enqueue_reserve_detail_continuation(rest: ScsbidReserveDetailBackfillRequest) -> bool:
    if not rest.notices:
        return False
    try:
        # The DTO field name IS the task kwarg, so send/receive stay symmetric.
        backfill_scsbid_reserve_detail.apply_async(
            kwargs=rest.model_dump(mode="json"),
            queue=settings.CELERY_OPS_QUEUE,
        )
        return True
    except Exception:  # pragma: no cover - chain failure must not fail chunk
        logger.exception(
            "backfill_scsbid_reserve_detail continuation enqueue failed "
            "for %d remaining notice(s)",
            len(rest.notices),
        )
        return False


@celery_app.task(name=PRICE_PREDICTOR_TRAINING_TASK_NAME)
def train_price_predictor(request_payload: dict[str, Any] | None = None) -> dict:
    """Run price-predictor training in the dedicated ML training queue."""
    # Validated with the model the API sender dumps. The ML service keeps its dict
    # contract (ml-builder owned), so it gets back only the keys the sender set — its
    # own option defaults stay authoritative.
    request = PricePredictionTrainingTaskRequest.model_validate(request_payload or {})
    with task_session() as db:
        return PricePredictionTrainingService().train_price_predictor(db, request_payload=request.model_dump(mode="json", exclude_unset=True))


@celery_app.task(name=DECISION_EXPERIMENT_REEVALUATION_TASK_NAME)
def reevaluate_decision_experiment(experiment_run_id: int, operator_id: int | None = None) -> dict:
    """Re-evaluate a decision experiment outside the API request path."""
    with task_session() as db:
        operator = None
        if operator_id is not None:
            operator = db.query(User).filter(User.id == int(operator_id)).first()
            if operator is None:
                raise ValueError(f"Operator {int(operator_id)} not found")
        return DecisionExperimentService().evaluate_run(
            db,
            run_id=int(experiment_run_id),
            operator=operator,
        )


@celery_app.task(name=OPERATOR_STRATEGY_MONITOR_TASK_NAME)
def monitor_operator_strategy(
    request_payload: dict[str, Any] | None = None,
    monitor_run_id: int | None = None,
    trigger_source: str = StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
    operator_id: int | None = None,
) -> dict:
    """Execute the stored operator strategy and persist bid decisions in a background task."""
    request = OperatorStrategyMonitorRequest(**(request_payload or {}))
    with task_session() as db:
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


@celery_app.task(name=SYNTHETIC_BACKTEST_RUN_TASK_NAME)
def run_synthetic_operator_backtest(payload: dict[str, Any] | None = None) -> dict:
    """Run the per-synthetic-operator backtest in a background worker.

    Thin shell: payload validated here, body in ``app.tasks.backtest_jobs``.
    """
    return run_synthetic_operator_backtest_job(
        SyntheticOperatorBacktestTaskRequest.model_validate(payload or {})
    )


@celery_app.task(name=PAPER_BIDDING_FORWARD_TASK_NAME)
def run_forward_paper_bidding(request_payload: dict[str, Any] | None = None) -> dict:
    """Generate forward paper bids for currently open/re-notice projects."""
    # DTO fields are the service kwargs 1:1 (same spread as paper_bidding_scheduler).
    request = ForwardPaperBiddingTaskRequest.model_validate(request_payload or {})
    with task_session() as db:
        return PaperBiddingBacktestService().run_forward_paper_bidding(
            db, **request.model_dump()
        )


@celery_app.task(name=FORWARD_SETTLEMENT_TASK_NAME)
def settle_forward_paper_bids(
    operator_id: int | None = None,
    limit: int = 200,
    persist: bool = True,
) -> dict:
    """Settle forward paper bids whose deadline has passed and result is available."""
    with task_session() as db:
        return PaperBiddingBacktestService().run_forward_settlement(
            db,
            operator_id=int(operator_id) if operator_id is not None else None,
            limit=int(limit or 200),
            persist=bool(persist),
        )


@celery_app.task(name=NOTIFY_AWARD_RESULTS_TASK_NAME)
def notify_award_results(limit: int = 50) -> dict:
    """Send the operator a one-shot 낙찰결과 Telegram for newly-awarded real bids.

    Idempotent: each tracked bid is notified at most once (award_notified_at).
    Telegram delivery is skipped in ENVIRONMENT=test by the notification service.
    """
    from app.services.award_notifications import AwardResultNotificationService
    from app.services.opening_result_collection import OpeningResultCollectionService

    with task_session() as db:
        # 개찰 1위(잠정) 수집을 먼저 시도한다. 외부 호출이 실패해도 낙찰결과 알림
        # 흐름을 막지 않도록 예외를 격리한다(시크릿은 로그에 남기지 않음).
        try:
            OpeningResultCollectionService().collect(db)
        except Exception:  # noqa: BLE001 - opening collection must not block the alarm
            db.rollback()
        return AwardResultNotificationService().collect_and_notify(
            db, limit=int(limit or 50)
        )


@celery_app.task(name=HISTORICAL_BACKTEST_TASK_NAME)
def run_historical_backtest(request_payload: dict[str, Any] | None = None) -> dict:
    """Replay awarded TenderResults as paper_bid + settlement comparison.

    Thin shell: payload validated here, body in ``app.tasks.backtest_jobs``.
    """
    return run_historical_backtest_job(
        HistoricalBacktestTaskRequest.model_validate(request_payload or {})
    )


@celery_app.task(name=ENRICH_BUSINESS_TYPE_TASK_NAME)
def enrich_pending_business_types(limit: int | None = None) -> dict:
    """Persist business_type_code/label for recently-collected projects."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    effective_limit = int(limit if limit is not None else settings.BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT)
    effective_limit = max(1, effective_limit)

    with task_session() as db:
        return BusinessTypeEnrichmentService().enrich_pending(db, limit=effective_limit)


@celery_app.task(name=RECLASSIFY_CATEGORIES_TASK_NAME)
def reclassify_pending_categories(limit: int | None = None) -> dict:
    """Re-assign Project.category for rows stuck at 'general'/'other' via SBERT prototype cosine sim."""
    from app.core.config import settings
    from app.services.category_classifier import CategoryClassifierService

    effective_limit = int(limit if limit is not None else settings.CATEGORY_RECLASSIFY_BATCH_LIMIT)
    effective_limit = max(1, effective_limit)

    with task_session() as db:
        return CategoryClassifierService().reclassify_pending(db, limit=effective_limit)


@celery_app.task(name=SMOKE_TEST_TASK_NAME)
def run_koneps_telegram_smoke_test() -> dict:
    """Daily KONEPS + Telegram end-to-end smoke test."""
    from dataclasses import asdict
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    with task_session() as db:
        service = KonepsTelegramSmokeTestService()
        report = service.run(db)
        try:
            service.persist_report(db, report)
        except Exception:  # noqa: BLE001 — persistence must not mask the smoke result
            logger.exception("failed to persist smoke test run")
        return asdict(report)


@celery_app.task(name=G2_CANDIDATE_RECHECK_TASK_NAME)
def run_g2_candidate_recheck() -> dict:
    """Daily read-only G-2 candidate re-check across synthetic operators.

    Thin shell: body in ``app.tasks.evidence_jobs``; the session lifecycle stays
    here via the shared ``task_session`` seam. The body returns the typed summary;
    the shell lowers it to a JSON-safe dict for the celery result backend (whose
    json serializer cannot carry a pydantic model).
    """
    with task_session() as db:
        return run_g2_candidate_recheck_job(db).model_dump(mode="json")


@celery_app.task(name=COLLECT_G2_EVIDENCE_TASK_NAME)
def collect_g2_evidence(window_days: int = 30, recent_limit: int = 5) -> dict:
    """Daily read-only snapshot of the per-operator G-2 evidence ledger.

    Thin shell: body in ``app.tasks.evidence_jobs``. The db lifecycle stays here
    via the shared ``task_session`` seam, and ``_write_g2_daily_evidence_draft``
    (patched via this module in tests) is injected by name — the injected
    reference is resolved from this module's globals at call time, honouring the
    monkeypatch. The typed summary is lowered to a JSON-safe dict for the result
    backend (same reason as ``run_g2_candidate_recheck``).
    """
    with task_session() as db:
        return run_collect_g2_evidence_job(
            db,
            window_days=window_days,
            recent_limit=recent_limit,
            write_daily_draft=_write_g2_daily_evidence_draft,
        ).model_dump(mode="json")


@celery_app.task(name=RECONCILE_STALE_TASK_RUNS_TASK_NAME)
def reconcile_stale_task_runs() -> dict:
    """Finalize orphaned non-terminal task-run rows left by hard-kill/restart/DB-down.

    Periodic janitor backstop for the in-task finalize-on-failure path: when a
    worker is SIGKILLed by the hard time limit, restarted, or loses the database
    at the moment it tries to mark a run failed, the ``operator_strategy_runs`` /
    ``crawl_jobs`` row is stranded in a non-terminal (``running`` / ``queued``)
    state forever and trips the operations dashboard ``task_stale_queue:
    critical`` KPI. This sweep flips any such row older than the Celery hard time
    limit (plus a grace margin) to ``failed`` with a ``[reconciled]`` marker.
    Status-only transition: it never deletes the row or its partial results.
    """
    from app.services.stale_task_reconciler import StaleTaskReconcilerService

    with task_session() as db:
        try:
            result = StaleTaskReconcilerService().reconcile(db)
            if result.get("total_finalized"):
                logger.info(
                    "reconcile_stale_task_runs finalized strategy_runs=%s crawl_jobs=%s preview_snapshots=%s (threshold=%ss)",
                    result.get("strategy_runs_finalized"),
                    result.get("crawl_jobs_finalized"),
                    result.get("preview_snapshots_finalized"),
                    result.get("threshold_seconds"),
                )
            return result
        except Exception:
            db.rollback()
            raise


@celery_app.task(bind=True, name=PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME)
def recompute_preview_snapshot(self, operator_id: int, high_priority_only: bool = False) -> dict:
    """운영자 preview 스냅샷 1키를 재계산·영속화한다 (설계 2026-07-30 §6.3).

    body 는 ``task_session`` 시임 + DB-first 라이프사이클(mark_running/completed/
    failed — synthetic experiment run 패턴). celery_task_id 멱등(crawl_jobs
    패턴): UNIQUE(operator_id, high_priority_only) 행에 task id 를 스탬프하므로
    고아 행(중복 행) 자체가 생길 수 없다. 실행 도중 SIGKILL/재시작(예:
    ``docker compose restart worker``)이 나면 — ``task_reject_on_worker_lost`` 를
    켜지 않았으므로 task 는 재전달되지 않고 — 행은 running 인 채 남는다. 그
    고아 running 은 preview 회수창
    (``OPERATOR_PREVIEW_SNAPSHOT_RUNNING_RECLAIM_SECONDS``, 기본 300s)에서
    다음 GET 자동 디스패치·명시 갱신이 회수·재클레임하고, 그마저 놓친 훨씬
    오래된 행은 stale-task-reconciler 가 backstop 으로 failed 마감한다.
    """
    task_id = getattr(getattr(self, "request", None), "id", None)
    with task_session() as db:
        try:
            return PreviewSnapshotService().run_recompute(
                db,
                operator_id=int(operator_id),
                high_priority_only=bool(high_priority_only),
                task_id=str(task_id) if task_id else None,
            )
        except Exception:
            db.rollback()
            raise


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


def enqueue_decision_experiment_reevaluation(*, experiment_run_id: int, operator_id: int | None = None):
    """Queue a decision experiment re-evaluation task and return the async task handle."""
    kwargs = {"experiment_run_id": int(experiment_run_id)}
    if operator_id is not None:
        kwargs["operator_id"] = int(operator_id)
    return _enqueue_ml_task(
        reevaluate_decision_experiment,
        kwargs=kwargs,
        queue=settings.CELERY_ML_REEVALUATION_QUEUE,
    )


def enqueue_synthetic_operator_backtest(*, payload: dict[str, Any]):
    """Queue a per-synthetic-operator backtest task and return the async task handle."""
    return run_synthetic_operator_backtest.apply_async(
        kwargs={"payload": payload},
        queue=settings.CELERY_OPS_QUEUE,
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


def enqueue_preview_snapshot_recompute(*, operator_id: int, high_priority_only: bool):
    """Queue a preview-snapshot recompute task and return the async task handle."""
    return recompute_preview_snapshot.apply_async(
        kwargs={
            "operator_id": int(operator_id),
            "high_priority_only": bool(high_priority_only),
        },
        queue=settings.CELERY_OPS_QUEUE,
    )
