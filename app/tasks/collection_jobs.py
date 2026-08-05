"""KONEPS collection job body.

Extracted from ``app.tasks.jobs`` (§4.5 size decomposition). The ``@task`` entry
``collect_koneps_notices`` stays in ``app.tasks.jobs`` (registration name
unchanged) as a thin shell that injects the post-commit inference notification
and reserve-detail enqueue seams.
"""

import logging
import math
from typing import Any, Callable

from celery.exceptions import SoftTimeLimitExceeded

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine, task_session
from app.core.time import utc_now
from app.models.models import CrawlJob
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collection import serialize_collect_payload
from app.services.koneps.collector import KonepsCollectorService
from app.services.task_singleton import AdvisorySingletonLease

logger = logging.getLogger(__name__)

# Every collection source (construction/service/scsbid) shares this one lease on
# purpose: KONEPS must be called serially with throttling, so category-scoped
# locks are not an option. Contention is resolved by waiting (see
# ``_handle_lease_busy``), never by collecting concurrently.
KONEPS_COLLECTION_LEASE_KEY = "koneps_collection"

_LEASE_BUSY_MESSAGE = "singleton lock busy; duplicate collection suppressed"


def _default_collection_lease() -> AdvisorySingletonLease:
    return AdvisorySingletonLease(engine, KONEPS_COLLECTION_LEASE_KEY)


def _record_duplicate_collection(self, request, *, retries=0, session_factory=None):
    # ``error_message`` 는 analytics 의 failure_reason_breakdown 에서 원문이 곧 키라
    # 상수로 유지한다. 재시도 횟수를 접미사로 붙이면 같은 원인이 횟수별 버킷으로
    # 쪼개지므로 횟수는 metadata 에만 남긴다.
    with task_session(session_factory) as db:
        row = CrawlJob(
            source=request.source,
            target_date=request.target_date,
            category=request.category,
            execution_mode=request.execution_mode,
            max_items=request.max_items,
            release_sha=str(settings.APP_RELEASE_SHA or "").strip() or None,
            release_tag=str(settings.APP_RELEASE_TAG or "").strip() or None,
            status="duplicate_suppressed",
            result_count=0,
            error_message=_LEASE_BUSY_MESSAGE,
            celery_task_id=str(getattr(self.request, "id", "") or "") or None,
            completed_at=utc_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "job_status": "duplicate_suppressed",
            "source": request.source,
            "collected_count": 0,
            "items": [],
            "metadata": {
                "crawl_job_id": int(row.id),
                "reason": "singleton_lock_busy",
                "lease_busy_retries": int(retries),
            },
        }


def lease_busy_retry_budget() -> tuple[int, int]:
    """``(max_retries, countdown)`` — 공유 리스 대기에 쓸 재시도 창.

    창의 크기는 튜닝 대상이 아니라 유도값이다. 리스는 길어야 hard time limit 만큼
    잡혀 있고(그 시점에 워커가 SIGKILL 되어 세션 락이 풀린다) 그보다 짧게 기다리면
    기아가 남는다. 반대로 수집 주기를 넘겨 기다리면 다음 tick 의 dispatch 와 겹쳐
    대기 메시지가 쌓인다. 그래서 둘 중 작은 쪽을 창으로 삼고 지연으로 나눈다.

    지연을 0 이하로 두면 재시도 없이 기존 즉시 ``duplicate_suppressed`` 로 돌아간다.
    """
    delay = int(settings.KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS)
    if delay <= 0:
        return 0, 0
    window = min(
        max(0, int(settings.CELERY_TASK_TIME_LIMIT_SECONDS)),
        max(0, int(settings.KONEPS_COLLECTION_INTERVAL_MINUTES)) * 60,
    )
    return math.ceil(window / delay), delay


def _can_retry_lease_busy(task_request, crawl_job_id) -> bool:
    """재시도는 beat dispatch 경로에만 적용한다.

    Eager mode re-runs a retry inline (``memory://`` broker, ``task_always_eager``)
    and a direct call has no broker at all, so both keep the immediate
    ``duplicate_suppressed`` record instead of looping in-process.

    ``crawl_job_id`` 가 있으면 ``POST /operations/crawl/async`` 가 이미 ``queued`` 행을
    만들어 두고 사람이 응답을 기다리는 요청이다. 여기서 최대 재시도 창(1800s)만큼 더
    기다리면 그 행의 나이가 stale reconciler 임계(hard limit + grace = 2100s,
    ``stale_threshold_seconds()``)를 넘어 **살아있는 작업이** ``failed [reconciled]`` 로
    오판 마감된다. 이 경로는 즉시 suppressed 로 답하는 편이 정직하고, 이 수정의 목적인
    beat 기아 해소는 그대로 보존된다(beat 는 ``crawl_job_id`` 를 넘기지 않는다).
    """
    if crawl_job_id is not None:
        return False
    if bool(getattr(task_request, "is_eager", False)):
        return False
    return not bool(getattr(task_request, "called_directly", True))


def _handle_lease_busy(self, request, *, crawl_job_id=None, session_factory=None):
    """Wait for the shared lease instead of starving this category.

    The loser of a same-tick collision used to record ``duplicate_suppressed``
    immediately, which made whichever category lost the race skip collection for
    the whole cycle. Retrying on a countdown keeps KONEPS calls serial (the lease
    is still the arbiter) while giving the loser the rest of the cycle to run.
    The suppressed record is preserved for a genuinely exhausted budget.
    """
    task_request = getattr(self, "request", None)
    retries = max(0, int(getattr(task_request, "retries", 0) or 0))
    max_retries, countdown = lease_busy_retry_budget()

    if retries < max_retries and _can_retry_lease_busy(task_request, crawl_job_id):
        logger.info(
            "koneps collection lease busy; retrying in %ds "
            "(source=%s category=%s attempt=%d/%d)",
            countdown,
            request.source,
            request.category,
            retries + 1,
            max_retries,
        )
        raise self.retry(countdown=countdown, max_retries=max_retries)

    logger.warning(
        "koneps collection lease busy; suppressing run "
        "(source=%s category=%s retries=%d)",
        request.source,
        request.category,
        retries,
    )
    return _record_duplicate_collection(
        self, request, retries=retries, session_factory=session_factory
    )


def run_singleton_koneps_collection_job(
    self,
    *,
    request,
    crawl_job_id,
    notify_inference_outbox_committed,
    enqueue_deferred_reserve_detail_backfill,
    run_job,
    lease_factory=None,
    session_factory=None,
):
    """Run one collection under the shared KONEPS lease.

    ``lease_factory``/``session_factory`` are the injection seams (§4.7): the
    defaults bind the app engine/session, tests pass their own.
    """
    lease = (lease_factory or _default_collection_lease)()
    if not lease.acquire():
        return _handle_lease_busy(
            self,
            request,
            crawl_job_id=crawl_job_id,
            session_factory=session_factory,
        )
    try:
        return run_job(
            self,
            request=request,
            crawl_job_id=crawl_job_id,
            notify_inference_outbox_committed=notify_inference_outbox_committed,
            enqueue_deferred_reserve_detail_backfill=(
                enqueue_deferred_reserve_detail_backfill
            ),
        )
    finally:
        lease.release()


def run_koneps_collection_job(
    self,
    *,
    request: CrawlRequest,
    crawl_job_id: int | None,
    notify_inference_outbox_committed: Callable[[list[int]], Any],
    enqueue_deferred_reserve_detail_backfill: Callable[[list[dict[str, Any]]], int],
    session_factory: Callable[[], Session] | None = None,
) -> dict:
    """Collect KONEPS notices and persist crawl history inside a background task.

    The ``@task`` shell promotes the broker payload to ``CrawlTaskRequest`` before
    calling this body, so the crawl parameters arrive validated (never a raw
    ``dict``).

    ``session_factory`` is the session seam (defaults to the app ``SessionLocal``
    via ``task_session``), so callers/tests can inject their own session.

    Idempotency: with ``task_acks_late=True`` a task that is SIGKILLed by the
    hard time limit is redelivered with the *same* Celery task id but no
    ``crawl_job_id`` (beat dispatch). Stamping ``celery_task_id`` on the row and
    reusing it on redelivery prevents an orphan ``running`` crawl-job from being
    created on every redelivery.
    """
    service = KonepsCollectorService()
    request = service._normalize_request(request)
    with task_session(session_factory) as db:
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
                crawl_job.category = request.category
                crawl_job.execution_mode = request.execution_mode
                crawl_job.max_items = request.max_items
                crawl_job.release_sha = str(settings.APP_RELEASE_SHA or "").strip() or None
                crawl_job.release_tag = str(settings.APP_RELEASE_TAG or "").strip() or None
                crawl_job.status = "running"
                crawl_job.result_count = 0
                crawl_job.received_count = 0
                crawl_job.normalized_count = 0
                crawl_job.duplicate_count = 0
                crawl_job.dropped_count = 0
                crawl_job.persisted_count = 0
                crawl_job.source_total_count = None
                crawl_job.pages_fetched = None
                crawl_job.truncated = False
                crawl_job.drop_reasons = {}
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

            # Only the per-notice reserve-detail HTTP fetch remains source-specific.
            # Embeddings never run in collection persistence for any source: the
            # transaction records semantic-input outbox rows consumed by the
            # declared inference task path.
            is_scsbid = service._is_scsbid_openapi_source(request.source)
            defer_reserve_detail = (
                bool(settings.KONEPS_SCSBID_RESERVE_DETAIL_DEFER) and is_scsbid
            )
            result = service.collect_notices(
                request, db=db, defer_reserve_detail=defer_reserve_detail
            )
            crawl_job = service.persist_crawl_results(
                db, crawl_job, request, result
            )
            result.setdefault("metadata", {})["crawl_job_id"] = crawl_job.id

            outbox_event_ids = result.get("metadata", {}).get(
                "semantic_input_outbox_event_ids"
            )
            if outbox_event_ids:
                try:
                    notify_inference_outbox_committed(list(outbox_event_ids))
                except Exception:  # noqa: BLE001 - durable outbox remains retryable
                    logger.exception(
                        "semantic-input outbox fast dispatch failed for %d event(s)",
                        len(outbox_event_ids),
                    )

            deferred_reserve = result.get("metadata", {}).get(
                "deferred_reserve_detail_notices"
            )
            if deferred_reserve:
                enqueue_deferred_reserve_detail_backfill(list(deferred_reserve))

            # celery 경계: 브로커/결과 백엔드로 나가는 payload 는 순수 JSON 값이어야
            # 하므로 수집 DTO 직렬화는 여기 한 번만 수행한다(수집 내부는 모델 유지).
            return serialize_collect_payload(result)
        except SoftTimeLimitExceeded as exc:
            # Stop the redelivery loop: mark this run failed and ack the message so
            # the same payload is not re-run forever past the soft limit.
            db.rollback()
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
            db.rollback()
            if crawl_job is not None:
                service.mark_crawl_job_failed(db, crawl_job, str(exc))
            raise
