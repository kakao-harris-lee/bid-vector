"""Regression tests for find_similar_projects candidate-load placement.

Background (perf/strategy-monitor-bounded-scan)
-----------------------------------------------
``find_similar_projects`` used to load *every* same-category candidate
(~12k rows in production) and run a per-candidate embedding cache-check/refresh
*before* deciding between the pgvector and Python search paths. The pgvector
path (``_search_with_postgres``) queries against stored embeddings via the HNSW
index and never touches that in-memory candidate list, so on production
(pgvector enabled) the bulk load + refresh was pure waste -- ~20s per candidate
inside the strategy monitor.

The fix moves the candidate query + ``refresh_project_embeddings`` (plural)
into the Python-fallback ``else`` branch only. These tests assert:

1. pgvector path: ``refresh_project_embeddings`` (plural) is NOT called and the
   bulk candidate query is not issued; results come straight from the pgvector
   search.
2. Python fallback path: candidates are still loaded + refreshed + searched
   in-memory (unchanged behavior).
"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_now
from app.models.models import (
    InferenceOutboxEvent,
    Project,
    ProjectSimilarityEdge,
    ProjectSimilaritySnapshot,
)
from app.services.project_similarity import ProjectSimilarityService
from app.services.similarity_read_model import invalidate_project_embedding


def _make_project(db, *, title: str, category: str = "construction") -> Project:
    """Create and flush a minimal Project row."""
    project = Project(
        title=title,
        description=f"{title} 설명",
        requirements="",
        budget_estimate=0.0,
        category=category,
    )
    db.add(project)
    db.flush()
    return project


def test_pgvector_path_skips_bulk_candidate_refresh(test_db, monkeypatch):
    """When pgvector is queryable, the 12k candidate load/refresh must be skipped."""
    target = _make_project(test_db, title="타겟 공고")
    # Other same-category rows that the OLD code would have bulk-loaded/refreshed.
    candidate = _make_project(test_db, title="후보 A")
    _make_project(test_db, title="후보 B")
    test_db.flush()

    service = ProjectSimilarityService()

    # Force the pgvector branch even though the test DB is SQLite.
    monkeypatch.setattr(service, "_can_query_pgvector", lambda db: True)

    plural_refresh_calls: list[int] = []

    def _spy_plural_refresh(db, projects):
        plural_refresh_calls.append(len(list(projects)))

    monkeypatch.setattr(service, "refresh_project_embeddings", _spy_plural_refresh)

    postgres_calls: list[dict] = []

    def _fake_postgres(
        db, *, project, query_embedding, limit, min_similarity, same_category_only
    ):
        postgres_calls.append({"project_id": project.id, "limit": limit})
        return [service._serialize_result(candidate, 0.9)]

    monkeypatch.setattr(service, "_search_with_postgres", _fake_postgres)

    # Guard: the Python fallback must not be reached on the pgvector path.
    def _fail_python(*args, **kwargs):
        raise AssertionError("python fallback must not run on pgvector path")

    monkeypatch.setattr(service, "_search_with_python", _fail_python)

    response = service.find_similar_projects(test_db, target, limit=5)

    # The bulk per-candidate refresh must never fire on the pgvector path.
    assert plural_refresh_calls == []
    # Results come straight from the pgvector search mock.
    assert response["search_mode"] == "postgres_vector"
    assert response["results"][0]["project_id"] == candidate.id
    assert response["results"][0]["similarity_score"] == 0.9
    assert len(postgres_calls) == 1
    assert postgres_calls[0]["project_id"] == target.id


def test_postgres_search_orders_only_by_vector_distance_for_hnsw():
    """HNSW requires ORDER BY distance LIMIT without a secondary sort key."""
    target = Project(id=101, title="타겟 공고", category="service")
    candidate = Project(
        id=202,
        title="후보 공고",
        category="service",
        status="open",
        budget_estimate=1000.0,
    )

    class _FakeQuery:
        def __init__(self) -> None:
            self.order_by_args = None
            self.limit_value = None

        def filter(self, *args):
            return self

        def order_by(self, *args):
            self.order_by_args = args
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def all(self):
            return [(candidate, 0.91)]

    class _FakeDb:
        def __init__(self) -> None:
            self.query_obj = _FakeQuery()

        def query(self, *args):
            return self.query_obj

    db = _FakeDb()
    service = ProjectSimilarityService()

    result = service._search_with_postgres(
        db,
        project=target,
        query_embedding=[0.0] * 384,
        limit=5,
        min_similarity=0.15,
        same_category_only=True,
    )

    assert db.query_obj.limit_value == 5
    assert db.query_obj.order_by_args is not None
    assert len(db.query_obj.order_by_args) == 1
    assert result[0]["project_id"] == candidate.id


def test_python_fallback_still_loads_and_refreshes_candidates(test_db, monkeypatch):
    """Without pgvector, candidates are loaded + refreshed + searched in-memory."""
    target = _make_project(test_db, title="타겟 공고")
    cand_a = _make_project(test_db, title="후보 A")
    cand_b = _make_project(test_db, title="후보 B")
    # A different-category row must be excluded by same_category_only.
    _make_project(test_db, title="다른 카테고리", category="services")
    test_db.flush()

    service = ProjectSimilarityService()

    monkeypatch.setattr(service, "_can_query_pgvector", lambda db: False)

    refreshed_batches: list[list[int]] = []

    def _spy_plural_refresh(db, projects):
        refreshed_batches.append([p.id for p in projects])

    monkeypatch.setattr(service, "refresh_project_embeddings", _spy_plural_refresh)

    searched_candidates: list[list[int]] = []

    def _fake_python(
        candidates, *, query_embedding, limit, min_similarity, embedding_resolver=None
    ):
        # 기본(비-read_only) 경로: 저장본을 읽는 _load_embedding resolver 가 주입된다.
        assert embedding_resolver == service._load_embedding
        searched_candidates.append([c.id for c in candidates])
        return [service._serialize_result(cand_a, 0.5)]

    monkeypatch.setattr(service, "_search_with_python", _fake_python)

    def _fail_postgres(*args, **kwargs):
        raise AssertionError("pgvector search must not run on the fallback path")

    monkeypatch.setattr(service, "_search_with_postgres", _fail_postgres)

    response = service.find_similar_projects(
        test_db, target, limit=5, same_category_only=True
    )

    assert response["search_mode"] == "python_fallback"
    # Candidates were loaded (same category only) and refreshed once.
    assert len(refreshed_batches) == 1
    assert set(refreshed_batches[0]) == {cand_a.id, cand_b.id}
    # The same filtered candidate set reached the in-memory searcher.
    assert len(searched_candidates) == 1
    assert set(searched_candidates[0]) == {cand_a.id, cand_b.id}
    assert response["results"][0]["project_id"] == cand_a.id


def test_python_fallback_real_search_returns_ranked_results(test_db):
    """End-to-end fallback (no mocks on search) still returns ranked similar rows."""
    target = _make_project(test_db, title="도로 포장 공사 서울")
    near = _make_project(test_db, title="도로 포장 공사 부산")
    _make_project(test_db, title="전산 시스템 유지보수")
    test_db.flush()

    service = ProjectSimilarityService()

    response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.0, same_category_only=True
    )

    assert response["search_mode"] == "python_fallback"
    assert response["target_project_id"] == target.id
    result_ids = [row["project_id"] for row in response["results"]]
    # The semantically closest "도로 포장 공사" row should be present and ranked.
    assert near.id in result_ids


def test_stored_similarity_reads_fresh_read_model_without_searching(
    test_db, monkeypatch
):
    """Stored UX reads should use fresh read-model edges before search fallback."""
    target = _make_project(test_db, title="클라우드 보안 관제", category="software")
    candidate = _make_project(test_db, title="클라우드 보안 운영", category="software")
    _make_project(test_db, title="건물 보수 공사", category="construction")

    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, target, force=True)
    service.refresh_project_embedding_details(test_db, candidate, force=True)
    test_db.flush()

    recompute = service.recompute_similarity_read_model(
        test_db,
        project_id=target.id,
        limit=5,
        min_similarity=0.15,
    )
    test_db.commit()
    test_db.refresh(target)

    assert recompute.status == "completed"
    assert recompute.edge_count == 1

    def _fail_search(*args, **kwargs):
        raise AssertionError("fresh read-model hit must not run similarity search")

    monkeypatch.setattr(service, "_search_with_postgres", _fail_search)
    monkeypatch.setattr(service, "_search_with_python", _fail_search)

    response = service.find_similar_projects(
        test_db,
        target,
        limit=5,
        min_similarity=0.15,
        same_category_only=True,
        stored_only=True,
    )

    assert response["search_mode"] == "read_model"
    assert response["projection_status"] == "ready"
    assert response["target_embedding_status"] == "ready"
    assert response["result_count"] == 1
    assert response["results"][0]["project_id"] == candidate.id


def test_zero_result_snapshot_is_a_valid_read_model_hit(test_db, monkeypatch):
    """A projection with no edges must not force every UX GET back onto search."""
    target = _make_project(test_db, title="단독 공고", category="software")
    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, target, force=True)
    result = service.recompute_similarity_read_model(
        test_db, project_id=target.id, limit=5, min_similarity=0.99
    )
    test_db.commit()

    assert result.status == "completed"
    assert result.edge_count == 0
    monkeypatch.setattr(
        service,
        "_search_with_python",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("zero-edge snapshot must be served")
        ),
    )

    response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.99, stored_only=True
    )

    assert response["search_mode"] == "read_model"
    assert response["result_count"] == 0


def test_candidate_category_change_closes_stored_read_on_stale_snapshot(
    test_db, monkeypatch
):
    """Candidate-side semantic/category changes cannot leave target edges fresh."""
    target = _make_project(test_db, title="AI 분석", category="software")
    candidate = _make_project(test_db, title="AI 데이터 분석", category="software")
    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, target, force=True)
    service.refresh_project_embedding_details(test_db, candidate, force=True)
    service.recompute_similarity_read_model(test_db, project_id=target.id, limit=5)
    test_db.commit()

    candidate.category = "construction"
    invalidate_project_embedding(candidate)
    test_db.commit()
    def _fail_search(*args, **kwargs):
        raise AssertionError("stale stored projection must not run inline search")

    monkeypatch.setattr(service, "_search_with_postgres", _fail_search)
    monkeypatch.setattr(service, "_search_with_python", _fail_search)
    response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.15, stored_only=True
    )

    assert response["search_mode"] == "stored_missing"
    assert response["projection_status"] == "stale"
    assert response["result_count"] == 0


def test_unrelated_category_embedding_change_keeps_scoped_snapshot_fresh(
    test_db, monkeypatch
):
    target = _make_project(test_db, title="AI 분석", category="software")
    candidate = _make_project(test_db, title="AI 데이터 분석", category="software")
    unrelated = _make_project(test_db, title="교량 보수", category="construction")
    service = ProjectSimilarityService()
    for project in (target, candidate, unrelated):
        service.refresh_project_embedding_details(test_db, project, force=True)
    service.recompute_similarity_read_model(test_db, project_id=target.id, limit=5)
    test_db.commit()

    unrelated.title = "교량 내진 보강"
    service.refresh_project_embedding_details(test_db, unrelated, force=True)
    test_db.commit()

    monkeypatch.setattr(
        service,
        "_search_with_python",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unrelated category must not invalidate the snapshot")
        ),
    )
    response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.15, stored_only=True
    )

    assert response["search_mode"] == "read_model"
    assert response["projection_status"] == "ready"
    assert response["results"][0]["project_id"] == candidate.id


def test_similarity_projection_excludes_other_embedding_models(test_db):
    target = _make_project(test_db, title="AI 분석", category="software")
    same_model = _make_project(test_db, title="AI 데이터 분석", category="software")
    other_model = _make_project(test_db, title="AI 분석 플랫폼", category="software")
    service = ProjectSimilarityService()
    for project in (target, same_model, other_model):
        service.refresh_project_embedding_details(test_db, project, force=True)
    other_model.embedding_model = "incompatible-model-v2"
    test_db.flush()

    result = service.recompute_similarity_read_model(
        test_db, project_id=target.id, limit=5, min_similarity=0.0
    )
    test_db.commit()
    other_model.title = "AI 분석 플랫폼 개편"
    service.refresh_project_embedding_details(test_db, other_model, force=True)
    other_model.embedding_model = "incompatible-model-v2"
    test_db.commit()
    response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.0, stored_only=True
    )

    assert result.edge_count == 1
    assert [item["project_id"] for item in response["results"]] == [same_model.id]


def test_projection_backfill_ignores_unrelated_corpus_changes(test_db):
    target = _make_project(test_db, title="AI 분석", category="software")
    candidate = _make_project(test_db, title="AI 데이터 분석", category="software")
    unrelated = _make_project(test_db, title="교량 보수", category="construction")
    service = ProjectSimilarityService()
    for project in (target, candidate, unrelated):
        project.status = "open"
        service.refresh_project_embedding_details(test_db, project, force=True)
    service.recompute_similarity_read_model(test_db, project_id=target.id, limit=5)
    test_db.commit()

    unrelated.title = "교량 내진 보강"
    service.refresh_project_embedding_details(test_db, unrelated, force=True)
    test_db.commit()

    result = service.stage_active_similarity_projection_backfill(test_db, limit=10)

    assert target.id not in result.project_ids


def test_embedding_ready_outbox_is_deduplicated_per_version(test_db):
    project = _make_project(test_db, title="중복 이벤트 방지")
    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, project, force=True)
    duplicate = service._outbox.append_embedding_ready_event(test_db, project)
    test_db.flush()

    assert duplicate is not None
    assert test_db.query(InferenceOutboxEvent).count() == 1


def test_active_projection_backfill_reactivates_completed_missing_work(test_db):
    project = _make_project(test_db, title="projection backfill")
    project.status = "open"
    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, project, force=True)
    event = test_db.query(InferenceOutboxEvent).one()
    event.status = "completed"
    event.processed_at = utc_now()
    test_db.commit()

    result = service.stage_active_similarity_projection_backfill(test_db, limit=10)
    test_db.commit()

    assert result.selected_count == 1
    assert result.staged_count == 1
    assert result.project_ids == [project.id]
    staged = test_db.get(InferenceOutboxEvent, result.event_ids[0])
    assert staged is not None
    assert staged.status == "pending"
    assert staged.processed_at is None


def test_stale_running_outbox_claim_is_recovered_and_processed(test_db, monkeypatch):
    project = _make_project(test_db, title="중단 복구 공고")
    service = ProjectSimilarityService()
    service.refresh_project_embedding_details(test_db, project, force=True)
    test_db.flush()
    event = test_db.query(InferenceOutboxEvent).one()
    event.status = "running"
    event.attempts = 1
    event.locked_at = utc_now() - timedelta(hours=1)
    event_id = int(event.id)
    test_db.commit()
    monkeypatch.setattr(
        "app.services.inference_outbox.settings.INFERENCE_OUTBOX_LOCK_TIMEOUT_SECONDS",
        60,
    )

    processed = service.process_inference_outbox_events(test_db, limit=10)

    assert processed.recovered_count == 1
    assert processed.processed_count == 1
    test_db.expire_all()
    assert test_db.get(InferenceOutboxEvent, event_id).status == "completed"


def test_publish_failure_leaves_outbox_for_periodic_delivery(test_db, monkeypatch):
    """Fast-dispatch failure after commit must not lose the projection event."""
    from app.tasks import inference_jobs, jobs

    project = _make_project(test_db, title="발행 실패 복구")
    test_db.commit()

    def _fail_publish(*, limit: int = 50):
        raise RuntimeError(f"broker unavailable for {limit}")

    monkeypatch.setattr(
        inference_jobs, "enqueue_inference_outbox_processing", _fail_publish
    )
    rebuilt = jobs.rebuild_project_embeddings.run(
        project_ids=[project.id], force=True
    )

    assert "outbox_processor_task_id" not in rebuilt
    test_db.expire_all()
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.status == "pending"

    swept = jobs.process_inference_outbox.run(limit=10)
    assert swept["processed_count"] == 1


def test_inference_outbox_processor_creates_similarity_read_model_edges(
    test_db, monkeypatch
):
    """Embedding rebuild writes outbox events; the processor materializes edges."""
    from app.tasks import inference_jobs, jobs

    queued_processor_limits: list[int] = []

    class _FakeAsyncResult:
        id = "outbox-processor-test-task"

    def _fake_enqueue_inference_outbox_processing(*, limit: int = 50):
        queued_processor_limits.append(limit)
        return _FakeAsyncResult()

    monkeypatch.setattr(
        inference_jobs,
        "enqueue_inference_outbox_processing",
        _fake_enqueue_inference_outbox_processing,
    )

    target = _make_project(test_db, title="AI 민원 분석")
    near = _make_project(test_db, title="AI 상담 데이터 분석")
    far = _make_project(test_db, title="네트워크 장비 유지보수")
    test_db.commit()

    rebuild = jobs.rebuild_project_embeddings.run(
        project_ids=[target.id, near.id, far.id],
        force=True,
    )

    assert rebuild["processed_count"] == 3
    assert len(rebuild["outbox_event_ids"]) == 3
    assert rebuild["outbox_processor_task_id"] == "outbox-processor-test-task"
    assert queued_processor_limits == [50]

    test_db.expire_all()
    pending_events = (
        test_db.query(InferenceOutboxEvent)
        .order_by(InferenceOutboxEvent.id.asc())
        .all()
    )
    assert [event.status for event in pending_events] == [
        "pending",
        "pending",
        "pending",
    ]

    monkeypatch.setattr(
        "app.services.inference_outbox.settings.INFERENCE_OUTBOX_RESULT_SAMPLE_LIMIT",
        2,
    )
    processed = jobs.process_inference_outbox.run(limit=10)

    assert processed["processed_count"] == 3
    assert processed["failed_count"] == 0
    assert processed["processed_event_id_first"] == pending_events[0].id
    assert processed["processed_event_id_last"] == pending_events[-1].id
    assert processed["result_sample_count"] == 2
    assert processed["result_sample_truncated"] is True
    assert processed["event_ids"] == [event.id for event in pending_events[:2]]
    assert len(processed["results"]) == 2

    test_db.expire_all()
    completed_events = (
        test_db.query(InferenceOutboxEvent)
        .order_by(InferenceOutboxEvent.id.asc())
        .all()
    )
    assert [event.status for event in completed_events] == [
        "completed",
        "completed",
        "completed",
    ]

    target_edges = (
        test_db.query(ProjectSimilarityEdge)
        .join(
            ProjectSimilaritySnapshot,
            ProjectSimilaritySnapshot.id == ProjectSimilarityEdge.snapshot_id,
        )
        .filter(ProjectSimilaritySnapshot.target_project_id == target.id)
        .order_by(ProjectSimilarityEdge.rank.asc())
        .all()
    )
    assert [edge.rank for edge in target_edges] == [1, 2]

    service = ProjectSimilarityService()
    target = test_db.query(Project).filter(Project.id == target.id).one()
    response = service.find_similar_projects(
        test_db,
        target,
        limit=5,
        min_similarity=0.15,
        same_category_only=True,
        stored_only=True,
    )

    assert response["search_mode"] == "read_model"
    assert response["result_count"] == 2
    assert {row["project_id"] for row in response["results"]} == {near.id, far.id}
