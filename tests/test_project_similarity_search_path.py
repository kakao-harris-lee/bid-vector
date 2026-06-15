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

from app.models.models import Project
from app.services.project_similarity import ProjectSimilarityService


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
    _make_project(test_db, title="후보 A")
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
        return [{"project_id": -1, "title": "from-pg", "similarity_score": 0.9}]

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
    assert response["results"] == [
        {"project_id": -1, "title": "from-pg", "similarity_score": 0.9}
    ]
    assert len(postgres_calls) == 1
    assert postgres_calls[0]["project_id"] == target.id


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

    def _fake_python(candidates, *, query_embedding, limit, min_similarity):
        searched_candidates.append([c.id for c in candidates])
        return [
            {"project_id": cand_a.id, "title": cand_a.title, "similarity_score": 0.5}
        ]

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
