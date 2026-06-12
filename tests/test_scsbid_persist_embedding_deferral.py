"""Regression tests for scsbid open-bid persistence: deferred embeddings + redelivery idempotency.

Background (fix/scsbid-persist-embedding-redelivery-loop)
--------------------------------------------------------
scsbid (open-bid result) collection processes thousands of award rows per run.
The previous code embedded every item synchronously inside
``persist_crawl_results`` -> ``_resolve_project_for_item``, which blew past the
Celery hard time limit. With ``task_acks_late=True`` the SIGKILLed task was
redelivered with the same task id but no ``crawl_job_id``, so each redelivery
created a fresh orphan ``running`` ``CrawlJob``.

This module asserts the two fixes:

1. scsbid persist defers per-item embeddings (no synchronous
   ``refresh_project_embedding``) and surfaces the touched project ids so the
   task layer can enqueue a single async backfill.
2. KONEPS notice persist keeps inline embeddings (no regression).
3. ``collect_koneps_notices`` reuses its crawl-job row when redelivered with the
   same Celery task id (idempotency), instead of multiplying orphans.

No real KONEPS HTTP is made: ``collect_notices`` is monkeypatched and
``ENVIRONMENT=test`` (conftest) skips external calls.
"""

from __future__ import annotations

from app.models.models import CrawlJob, Project
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService
from app.services.project_similarity import ProjectSimilarityService


def _award_response(notice_number: str, *, source: str) -> dict:
    """Build a minimal open-bid/notice response with one tender-result item."""
    return {
        "job_status": "completed",
        "source": source,
        "collected_count": 1,
        "items": [
            {
                "notice_number": notice_number,
                "title": f"개찰결과 {notice_number}",
                "base_amount": 100_000_000.0,
                "estimated_amount": 96_000_000.0,
                "source_url": f"http://ebid.example.com/detail/{notice_number}",
                "metadata": {
                    "opening_status": "개찰완료",
                    "opening_demand_agency": "서울특별시교육청",
                    "opening_announced_at": "2026-05-10T18:05:00",
                    "winning_company": "주식회사 테스트",
                    "winning_amount": 95_000_000.0,
                    "winning_rate": 95.0,
                },
            }
        ],
        "metadata": {"resolved_mode": "live"},
    }


def test_scsbid_persist_defers_embeddings_and_surfaces_project_ids(test_db, monkeypatch):
    """scsbid persist must NOT embed per item; touched ids go to metadata for async backfill."""
    refresh_calls: list[bool] = []

    def _spy_refresh(self, db, project, force=False):
        refresh_calls.append(bool(force))
        # Mimic the real return contract without invoking the embedding model.
        return [], "spy-model"

    monkeypatch.setattr(
        ProjectSimilarityService, "refresh_project_embedding", _spy_refresh
    )

    service = KonepsCollectorService()
    request = CrawlRequest(source="scsbid-openapi", category="construction", execution_mode="live")
    crawl_job = CrawlJob(source="scsbid-openapi", status="running")
    test_db.add(crawl_job)
    test_db.commit()
    test_db.refresh(crawl_job)

    response = _award_response("R-SCSBID-1", source="scsbid-openapi")
    crawl_job = service.persist_crawl_results(test_db, crawl_job, request, response)

    # No synchronous embedding refresh happened for the scsbid source.
    assert refresh_calls == []

    # The new project's id is surfaced for the async backfill.
    project = test_db.query(Project).one()
    deferred = response["metadata"]["deferred_embedding_project_ids"]
    assert deferred == [int(project.id)]
    assert crawl_job.status == "completed"


def test_koneps_notice_persist_keeps_inline_embedding(test_db, monkeypatch):
    """KONEPS notice persist must still embed inline (regression guard)."""
    refresh_calls: list[bool] = []

    def _spy_refresh(self, db, project, force=False):
        refresh_calls.append(bool(force))
        return [], "spy-model"

    monkeypatch.setattr(
        ProjectSimilarityService, "refresh_project_embedding", _spy_refresh
    )

    service = KonepsCollectorService()
    request = CrawlRequest(source="koneps", category="software", execution_mode="live")
    crawl_job = CrawlJob(source="koneps", status="running")
    test_db.add(crawl_job)
    test_db.commit()
    test_db.refresh(crawl_job)

    response = _award_response("R-KONEPS-1", source="koneps")
    service.persist_crawl_results(test_db, crawl_job, request, response)

    # Inline embedding still happens once (forced for the freshly created project).
    assert refresh_calls == [True]
    # No deferred backfill metadata for the standard notice path.
    assert "deferred_embedding_project_ids" not in response.get("metadata", {})


def test_collect_task_reuses_crawl_job_on_redelivery(test_db, monkeypatch):
    """Same Celery task id (redelivery) must reuse the crawl-job row, not multiply orphans."""
    from app.tasks import jobs

    response = _award_response("R-REDELIVER", source="scsbid-openapi")
    monkeypatch.setattr(
        KonepsCollectorService, "collect_notices", lambda self, request: dict(response)
    )
    # Keep the embedding model out of the task path entirely.
    monkeypatch.setattr(
        ProjectSimilarityService,
        "refresh_project_embedding",
        lambda self, db, project, force=False: ([], "spy-model"),
    )
    # Capture (and neutralise) the async backfill enqueue.
    enqueued: list[list[int]] = []
    monkeypatch.setattr(
        jobs,
        "_enqueue_deferred_embedding_backfill",
        lambda project_ids: enqueued.append(list(project_ids)),
    )

    payload = {"source": "scsbid-openapi", "category": "construction", "execution_mode": "live"}
    task_id = "fixed-redelivery-task-id"

    first = jobs.collect_koneps_notices.apply(
        kwargs={"request_payload": payload}, task_id=task_id
    )
    second = jobs.collect_koneps_notices.apply(
        kwargs={"request_payload": payload}, task_id=task_id
    )

    assert first.successful()
    assert second.successful()

    jobs_rows = (
        test_db.query(CrawlJob)
        .filter(CrawlJob.celery_task_id == task_id)
        .all()
    )
    # Exactly one crawl-job row survives both deliveries.
    assert len(jobs_rows) == 1
    assert jobs_rows[0].celery_task_id == task_id
    assert jobs_rows[0].status == "completed"

    # The backfill enqueue fired on each successful run with the touched id.
    assert enqueued and all(ids for ids in enqueued)
