"""KONEPS collection job body.

Extracted verbatim from ``app.tasks.jobs`` (§4.5 size decomposition). The
``@task`` entry ``collect_koneps_notices`` stays in ``app.tasks.jobs``
(registration name unchanged) as a thin shell that injects the two deferred
backfill enqueue helpers — those helpers reference other Celery tasks and are
patched via the ``jobs`` module in tests (``jobs._enqueue_deferred_embedding_backfill``),
so they must stay defined in ``app.tasks.jobs`` and be passed in here.
"""

import logging
from typing import Any, Callable

from celery.exceptions import SoftTimeLimitExceeded

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import task_session
from app.models.models import CrawlJob
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collection import serialize_collect_payload
from app.services.koneps.collector import KonepsCollectorService

logger = logging.getLogger(__name__)


def run_koneps_collection_job(
    self,
    *,
    request: CrawlRequest,
    crawl_job_id: int | None,
    enqueue_deferred_embedding_backfill: Callable[[list[int]], int],
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

            # Defer embeddings AND the per-notice scsbid reserve-detail HTTP fetch
            # only on this time-limited Celery path; synchronous callers do both
            # inline (see persist_crawl_results / collect_notices docstrings). The
            # inline reserve-detail fetch (one throttled HTTP call per non-settled
            # award, thousands per sweep) is what blew past the hard time limit and
            # spun a 0-row redelivery loop; deferring it to a bounded backfill keeps
            # the collection task short.
            is_scsbid = service._is_scsbid_openapi_source(request.source)
            defer_embeddings = is_scsbid
            defer_reserve_detail = (
                bool(settings.KONEPS_SCSBID_RESERVE_DETAIL_DEFER) and is_scsbid
            )
            result = service.collect_notices(
                request, db=db, defer_reserve_detail=defer_reserve_detail
            )
            crawl_job = service.persist_crawl_results(
                db, crawl_job, request, result, defer_embeddings=defer_embeddings
            )
            result.setdefault("metadata", {})["crawl_job_id"] = crawl_job.id

            deferred_ids = result.get("metadata", {}).get("deferred_embedding_project_ids")
            if deferred_ids:
                enqueue_deferred_embedding_backfill(list(deferred_ids))

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
