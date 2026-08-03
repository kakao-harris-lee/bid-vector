"""Regression tests for source-neutral collection inference delivery.

Background (fix/scsbid-persist-embedding-redelivery-loop)
--------------------------------------------------------
Collection persists canonical facts and a semantic-input outbox row atomically.
Embedding inference is owned by the declared inference task path for every
source; no synchronous or source-specific collection branch embeds inline.

This module asserts the fixes:

1. Both scsbid and KONEPS persistence skip inference and stage durable events.
2. The inference processor builds the embedding and then emits the existing
   ``embedding.ready`` event for projection.
3. ``collect_koneps_notices`` reuses its crawl-job row when redelivered with the
   same Celery task id (idempotency), instead of multiplying orphans.

No real KONEPS HTTP is made: ``collect_notices`` is monkeypatched and
``ENVIRONMENT=test`` (conftest) skips external calls.
"""

from __future__ import annotations

import pytest

from app.models.models import CrawlJob, HistoricalData, InferenceOutboxEvent, Project
from app.schemas.schemas import CrawlRequest
from app.services.inference_outbox import InferenceOutboxService
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


def test_scsbid_persist_stages_semantic_event_without_inline_embedding(
    test_db, monkeypatch
):
    """Scsbid facts and their source-neutral inference event commit together."""
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

    # No synchronous embedding refresh happened when deferral is requested.
    assert refresh_calls == []

    # The new project's outbox event is surfaced for fast inference dispatch.
    project = test_db.query(Project).one()
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.event_type == "semantic_input.changed"
    assert event.aggregate_id == project.id
    assert event.status == "pending"
    assert response["metadata"]["semantic_input_outbox_event_ids"] == [event.id]
    assert crawl_job.status == "completed"


def test_pending_semantic_event_is_deduplicated_on_identical_recollection(test_db):
    """An existing pending event is sufficient; identical input adds no row."""
    service = KonepsCollectorService()
    request = CrawlRequest(source="scsbid-openapi", category="construction", execution_mode="live")
    crawl_job = CrawlJob(source="scsbid-openapi", status="running")
    test_db.add(crawl_job)
    test_db.commit()
    test_db.refresh(crawl_job)

    first_response = _award_response("R-SCSBID-SYNC", source="scsbid-openapi")
    service.persist_crawl_results(test_db, crawl_job, request, first_response)
    first_event = test_db.query(InferenceOutboxEvent).one()
    second_job = CrawlJob(source="scsbid-openapi", status="running")
    test_db.add(second_job)
    test_db.commit()
    second_response = _award_response("R-SCSBID-SYNC", source="scsbid-openapi")
    service.persist_crawl_results(
        test_db,
        second_job,
        request,
        second_response,
    )

    assert test_db.query(InferenceOutboxEvent).count() == 1
    assert test_db.query(InferenceOutboxEvent).one().id == first_event.id
    assert test_db.query(InferenceOutboxEvent).one().status == "pending"
    assert "semantic_input_outbox_event_ids" not in second_response["metadata"]


def test_legacy_vectorless_project_recollection_stages_semantic_event(test_db):
    """Identical canonical input repairs a legacy project with no vector/event."""
    service = KonepsCollectorService()
    request = CrawlRequest(source="koneps", category="software", execution_mode="live")
    first_job = CrawlJob(source="koneps", status="running")
    test_db.add(first_job)
    test_db.commit()
    service.persist_crawl_results(
        test_db,
        first_job,
        request,
        _award_response("R-LEGACY-VECTORLESS", source="koneps"),
    )
    project = test_db.query(Project).one()
    assert project.embedding_model is None
    test_db.query(InferenceOutboxEvent).delete()
    test_db.commit()

    second_job = CrawlJob(source="koneps", status="running")
    test_db.add(second_job)
    test_db.commit()
    second_response = _award_response("R-LEGACY-VECTORLESS", source="koneps")
    service.persist_crawl_results(
        test_db, second_job, request, second_response
    )

    repaired = test_db.query(InferenceOutboxEvent).one()
    assert repaired.event_type == "semantic_input.changed"
    assert repaired.aggregate_id == project.id
    assert repaired.status == "pending"
    assert second_response["metadata"]["semantic_input_outbox_event_ids"] == [
        repaired.id
    ]


def test_failed_identical_semantic_event_is_reactivated(test_db):
    """An exhausted current event resets to a retryable state on recollection."""
    service = KonepsCollectorService()
    request = CrawlRequest(source="koneps", category="software", execution_mode="live")
    first_job = CrawlJob(source="koneps", status="running")
    test_db.add(first_job)
    test_db.commit()
    service.persist_crawl_results(
        test_db,
        first_job,
        request,
        _award_response("R-FAILED-RETRY", source="koneps"),
    )
    failed = test_db.query(InferenceOutboxEvent).one()
    failed.status = "failed"
    failed.attempts = 99
    failed.last_error = "exhausted"
    failed_id = failed.id
    test_db.commit()

    second_job = CrawlJob(source="koneps", status="running")
    test_db.add(second_job)
    test_db.commit()
    second_response = _award_response("R-FAILED-RETRY", source="koneps")
    service.persist_crawl_results(test_db, second_job, request, second_response)

    reactivated = test_db.query(InferenceOutboxEvent).one()
    assert reactivated.id == failed_id
    assert reactivated.status == "pending"
    assert reactivated.attempts == 0
    assert reactivated.last_error is None
    assert second_response["metadata"]["semantic_input_outbox_event_ids"] == [
        failed_id
    ]


def test_completed_current_semantic_event_is_noop_on_recollection(test_db):
    """A healthy embedding plus completed current event needs no new work."""
    service = KonepsCollectorService()
    request = CrawlRequest(source="koneps", category="software", execution_mode="live")
    first_job = CrawlJob(source="koneps", status="running")
    test_db.add(first_job)
    test_db.commit()
    service.persist_crawl_results(
        test_db,
        first_job,
        request,
        _award_response("R-CURRENT-NOOP", source="koneps"),
    )
    processed = ProjectSimilarityService().process_inference_outbox_events(
        test_db, limit=10
    )
    assert processed.processed_count == 1
    semantic_query = test_db.query(InferenceOutboxEvent).filter_by(
        event_type="semantic_input.changed"
    )
    event = semantic_query.one()
    event_id = event.id

    second_job = CrawlJob(source="koneps", status="running")
    test_db.add(second_job)
    test_db.commit()
    second_response = _award_response("R-CURRENT-NOOP", source="koneps")
    service.persist_crawl_results(test_db, second_job, request, second_response)

    current = semantic_query.one()
    assert current.id == event_id
    assert current.status == "completed"
    assert semantic_query.count() == 1
    assert "semantic_input_outbox_event_ids" not in second_response["metadata"]


def test_stale_embedding_reactivates_completed_current_event(test_db):
    """A nonempty but stale stored semantic text cannot suppress recovery."""
    service = KonepsCollectorService()
    request = CrawlRequest(source="koneps", category="software", execution_mode="live")
    first_job = CrawlJob(source="koneps", status="running")
    test_db.add(first_job)
    test_db.commit()
    service.persist_crawl_results(
        test_db,
        first_job,
        request,
        _award_response("R-STALE-RETRY", source="koneps"),
    )
    ProjectSimilarityService().process_inference_outbox_events(test_db, limit=10)
    test_db.expire_all()
    project = test_db.query(Project).one()
    semantic_event = (
        test_db.query(InferenceOutboxEvent)
        .filter_by(event_type="semantic_input.changed")
        .one()
    )
    semantic_event_id = semantic_event.id
    project.semantic_text = "stale but nonempty semantic text"
    test_db.commit()

    second_job = CrawlJob(source="koneps", status="running")
    test_db.add(second_job)
    test_db.commit()
    second_response = _award_response("R-STALE-RETRY", source="koneps")
    service.persist_crawl_results(test_db, second_job, request, second_response)

    reactivated = test_db.get(InferenceOutboxEvent, semantic_event_id)
    assert reactivated is not None
    assert reactivated.status == "pending"
    assert second_response["metadata"]["semantic_input_outbox_event_ids"] == [
        semantic_event_id
    ]


def test_semantic_event_reactivates_when_input_returns_to_prior_version(test_db):
    """A -> B -> A must rebuild A again without violating the unique key."""
    project = Project(
        title="상태 A",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="software",
    )
    test_db.add(project)
    test_db.flush()
    outbox = InferenceOutboxService()

    first_a = outbox.ensure_semantic_input_changed_event(
        test_db, project, semantic_input_changed=True
    )
    assert first_a is not None
    first_a.status = "completed"
    first_a.processed_at = first_a.updated_at
    project.semantic_text = "semantic A"
    project.embedding_payload = "[0.1]"
    project.embedding_model = "test-embedding"
    project.embedding_updated_at = first_a.updated_at
    project.title = "상태 B"
    project.embedding_payload = "[]"
    project.embedding_model = None
    project.embedding_updated_at = None
    event_b = outbox.ensure_semantic_input_changed_event(
        test_db, project, semantic_input_changed=True
    )
    assert event_b is not None
    event_b.status = "completed"
    event_b.processed_at = event_b.updated_at
    project.semantic_text = "semantic B"
    project.embedding_payload = "[0.2]"
    project.embedding_model = "test-embedding"
    project.embedding_updated_at = event_b.updated_at

    project.title = "상태 A"
    project.embedding_payload = "[]"
    project.embedding_model = None
    project.embedding_updated_at = None
    second_a = outbox.ensure_semantic_input_changed_event(
        test_db, project, semantic_input_changed=True
    )
    assert second_a is not None
    test_db.flush()

    assert second_a.id == first_a.id
    assert second_a.status == "pending"
    assert second_a.processed_at is None
    assert test_db.query(InferenceOutboxEvent).count() == 2


def test_koneps_notice_persist_uses_same_outbox_path(test_db, monkeypatch):
    """Standard KONEPS notices use the same non-inline inference boundary."""
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

    assert refresh_calls == []
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.event_type == "semantic_input.changed"
    assert response["metadata"]["semantic_input_outbox_event_ids"] == [event.id]


def test_collect_task_reuses_crawl_job_on_redelivery(test_db, monkeypatch):
    """Same Celery task id (redelivery) must reuse the crawl-job row, not multiply orphans."""
    from app.tasks import jobs

    response = _award_response("R-REDELIVER", source="scsbid-openapi")
    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None, **kwargs: dict(response),
    )
    # Keep the embedding model out of the collection task path entirely.
    monkeypatch.setattr(
        ProjectSimilarityService,
        "refresh_project_embedding",
        lambda self, db, project, force=False: ([], "spy-model"),
    )
    # Capture (and neutralise) the inference-outbox fast dispatch.
    enqueued: list[list[int]] = []
    monkeypatch.setattr(
        jobs,
        "notify_inference_outbox_committed",
        lambda event_ids: enqueued.append(list(event_ids)),
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

    # Only the first delivery changed canonical semantic input; redelivery is a no-op.
    assert len(enqueued) == 1
    assert enqueued[0]
    assert test_db.query(InferenceOutboxEvent).count() == 1


def test_persist_commit_failure_rolls_back_facts_and_semantic_event(
    test_db, monkeypatch
):
    """Canonical facts cannot commit without their inference request (or vice versa)."""
    service = KonepsCollectorService()
    request = CrawlRequest(
        source="koneps", category="software", execution_mode="live"
    )
    crawl_job = CrawlJob(source="koneps", status="running")
    test_db.add(crawl_job)
    test_db.commit()

    def _fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(test_db, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        service.persist_crawl_results(
            test_db,
            crawl_job,
            request,
            _award_response("R-COMMIT-FAIL", source="koneps"),
        )

    test_db.rollback()
    assert test_db.query(Project).count() == 0
    assert test_db.query(HistoricalData).count() == 0
    assert test_db.query(InferenceOutboxEvent).count() == 0


def test_collection_enqueue_failure_leaves_durable_semantic_event(
    test_db, monkeypatch
):
    """Broker failure after commit does not fail collection or lose inference work."""
    from app.tasks import jobs

    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, request, db=None, **kwargs: _award_response(
            "R-ENQUEUE-FAIL", source="koneps"
        ),
    )

    def _fail_enqueue(event_ids):
        raise RuntimeError(f"broker unavailable for {event_ids}")

    monkeypatch.setattr(jobs, "notify_inference_outbox_committed", _fail_enqueue)

    result = jobs.collect_koneps_notices.apply(
        kwargs={
            "request_payload": {
                "source": "koneps",
                "category": "software",
                "execution_mode": "live",
            }
        },
        task_id="semantic-enqueue-failure",
    )

    assert result.successful()
    test_db.expire_all()
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.event_type == "semantic_input.changed"
    assert event.status == "pending"
    assert test_db.query(CrawlJob).filter_by(status="completed").count() == 1


def test_inference_task_turns_semantic_event_into_embedding_ready_projection(
    test_db,
):
    """The declared inference task owns embedding, then preserves ready projection."""
    from app.tasks import jobs

    service = KonepsCollectorService()
    request = CrawlRequest(
        source="koneps", category="software", execution_mode="live"
    )
    crawl_job = CrawlJob(source="koneps", status="running")
    test_db.add(crawl_job)
    test_db.commit()
    service.persist_crawl_results(
        test_db,
        crawl_job,
        request,
        _award_response("R-INFERENCE-TASK", source="koneps"),
    )
    project_id = test_db.query(Project.id).scalar()

    first = jobs.process_inference_outbox.run(limit=10)

    assert first["processed_count"] == 1
    test_db.expire_all()
    project = test_db.get(Project, project_id)
    assert project is not None
    assert project.embedding_model
    events = test_db.query(InferenceOutboxEvent).order_by(InferenceOutboxEvent.id).all()
    assert [(event.event_type, event.status) for event in events] == [
        ("semantic_input.changed", "completed"),
        ("embedding.ready", "pending"),
    ]

    second = jobs.process_inference_outbox.run(limit=10)

    assert second["processed_count"] == 1
    test_db.expire_all()
    assert [
        event.status
        for event in test_db.query(InferenceOutboxEvent)
        .order_by(InferenceOutboxEvent.id)
        .all()
    ] == ["completed", "completed"]


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


def test_sync_crawl_route_records_scsbid_inference_event(client, test_db, monkeypatch):
    """Synchronous collection also commits an event instead of embedding inline."""
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
    assert refresh_calls == []
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.status == "pending"
    assert test_db.query(Project).count() == 1
