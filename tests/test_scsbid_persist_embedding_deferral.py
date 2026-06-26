"""Regression tests for scsbid open-bid persistence: deferred embeddings + redelivery idempotency.

Background (fix/scsbid-persist-embedding-redelivery-loop)
--------------------------------------------------------
scsbid (open-bid result) collection processes thousands of award rows per run.
The previous code embedded every item synchronously inside
``persist_crawl_results`` -> ``_resolve_project_for_item``, which blew past the
Celery hard time limit. With ``task_acks_late=True`` the SIGKILLed task was
redelivered with the same task id but no ``crawl_job_id``, so each redelivery
created a fresh orphan ``running`` ``CrawlJob``.

This module asserts the fixes:

1. ``persist_crawl_results`` defers per-item embeddings only when the *caller*
   passes ``defer_embeddings=True`` (the Celery collection task), surfacing the
   touched project ids for a single async backfill. Synchronous callers leave
   the default ``False`` and embed inline -- otherwise scsbid projects created
   via ``POST /operations/crawl`` or the backfill script would never be embedded.
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


def test_persist_with_defer_embeddings_true_skips_and_surfaces_project_ids(test_db, monkeypatch):
    """defer_embeddings=True must NOT embed per item; touched ids go to metadata for async backfill."""
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
    crawl_job = service.persist_crawl_results(
        test_db, crawl_job, request, response, defer_embeddings=True
    )

    # No synchronous embedding refresh happened when deferral is requested.
    assert refresh_calls == []

    # The new project's id is surfaced for the async backfill.
    project = test_db.query(Project).one()
    deferred = response["metadata"]["deferred_embedding_project_ids"]
    assert deferred == [int(project.id)]
    assert crawl_job.status == "completed"


def test_scsbid_persist_default_embeds_inline_no_deferred_metadata(test_db, monkeypatch):
    """Regression: scsbid persist via a synchronous caller (default defer_embeddings=False)
    must embed inline and surface NO deferred metadata.

    Guards the blocking regression where ``POST /operations/crawl`` and the scsbid
    backfill script silently created un-embedded projects because they never
    consumed ``deferred_embedding_project_ids``.
    """
    refresh_calls: list[bool] = []

    def _spy_refresh(self, db, project, force=False):
        refresh_calls.append(bool(force))
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

    response = _award_response("R-SCSBID-SYNC", source="scsbid-openapi")
    # Default call (no defer_embeddings) mirrors the synchronous /crawl + script paths.
    service.persist_crawl_results(test_db, crawl_job, request, response)

    # Inline embedding happened (forced for the freshly created project)...
    assert refresh_calls == [True]
    # ...and no async backfill metadata leaked out.
    assert "deferred_embedding_project_ids" not in response.get("metadata", {})


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
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None, **kwargs: dict(response),
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


def test_deferred_backfill_chunks_large_id_sets(monkeypatch):
    """A large deferred-id set must split into bounded chunks, not one unbounded task."""
    from app.tasks import jobs
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMBEDDING_BACKFILL_CHUNK_SIZE", 200)

    captured: list[dict] = []

    def _fake_enqueue_ml(task, *, kwargs, queue):
        captured.append({"kwargs": kwargs, "queue": queue})
        return object()

    monkeypatch.setattr(jobs, "_enqueue_ml_task", _fake_enqueue_ml)

    project_ids = list(range(1, 451))  # 450 ids -> ceil(450/200) == 3 chunks
    enqueued_count = jobs._enqueue_deferred_embedding_backfill(project_ids)

    assert enqueued_count == 3
    assert len(captured) == 3

    chunk_sizes = [len(call["kwargs"]["project_ids"]) for call in captured]
    assert chunk_sizes == [200, 200, 50]

    # force=False mirrors inline semantics (new project embeds, unchanged skips).
    assert all(call["kwargs"]["force"] is False for call in captured)
    assert all(call["queue"] == settings.CELERY_ML_BACKFILL_QUEUE for call in captured)

    # Chunks together cover exactly the input ids, with no overlap.
    flattened = [pid for call in captured for pid in call["kwargs"]["project_ids"]]
    assert sorted(flattened) == project_ids


def test_backfill_force_false_embeds_new_skips_unchanged(test_db):
    """force=False must embed a fresh project but no-op an unchanged existing one."""
    embed_calls: list[int] = []

    service = ProjectSimilarityService()
    # Spy on the actual model-inference entry point so we count real embeds only.
    original_embed = service._embed_text

    def _counting_embed(text):
        embed_calls.append(1)
        return original_embed(text)

    service._embed_text = _counting_embed  # type: ignore[method-assign]

    new_project = Project(
        title="신규 공고",
        description="신규 설명",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(new_project)
    test_db.flush()

    # New project (no cached vector) -> embedded even with force=False.
    service.refresh_project_embedding(test_db, new_project, force=False)
    assert len(embed_calls) == 1
    assert new_project.semantic_text

    # Re-running with no semantic change -> no-op (no additional embed).
    service.refresh_project_embedding(test_db, new_project, force=False)
    assert len(embed_calls) == 1


def test_sync_crawl_route_embeds_scsbid_inline(client, test_db, monkeypatch):
    """End-to-end: synchronous POST /operations/crawl for a scsbid source embeds inline.

    The synchronous route does not pass defer_embeddings, so even a scsbid source
    must embed its freshly created project inline (no async backfill exists on
    this path). Guards the blocking regression of un-embedded scsbid projects.
    """
    refresh_calls: list[bool] = []

    def _spy_refresh(self, db, project, force=False):
        refresh_calls.append(bool(force))
        return [], "spy-model"

    monkeypatch.setattr(
        ProjectSimilarityService, "refresh_project_embedding", _spy_refresh
    )
    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None, **kwargs: _award_response(
            "R-SCSBID-ROUTE", source="scsbid-openapi"
        ),
    )

    response = client.post(
        "/api/v1/operations/crawl",
        json={
            "source": "scsbid-openapi",
            "category": "construction",
            "execution_mode": "live",
        },
    )

    assert response.status_code == 200
    # Inline embedding fired for the freshly created scsbid project.
    assert refresh_calls == [True]
    # No deferred metadata on the synchronous route.
    assert "deferred_embedding_project_ids" not in response.json().get("metadata", {})
    assert test_db.query(Project).count() == 1
