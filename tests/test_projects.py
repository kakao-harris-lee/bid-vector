"""Tests for project endpoints"""

from app.core.single_user import DEFAULT_OPERATOR_PASSWORD, ensure_operator_account
from app.models.models import InferenceOutboxEvent, Project
from tests.support.auth import authenticate_client


def _authenticate_privileged_operator(client, test_db) -> None:
    operator = ensure_operator_account(test_db)
    authenticate_client(
        client,
        username=operator.username,
        password=DEFAULT_OPERATOR_PASSWORD,
    )


def test_create_project(client):
    """Test project creation"""
    response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test project description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Project"
    assert data["status"] == "open"


def test_list_projects(client):
    """Test listing projects"""
    # Create a project first
    client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    # List projects
    response = client.get("/api/v1/projects/")

    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert data[0]["title"] == "Test Project"


def test_get_project(client):
    """Test getting single project"""
    # Create a project
    create_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = create_response.json()["id"]

    # Get project
    response = client.get(f"/api/v1/projects/{project_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == project_id
    assert data["title"] == "Test Project"


def test_create_project_accepts_notice_and_agency_metadata(client):
    """Manual project creation should persist crawl-link metadata for later matching."""
    response = client.post(
        "/api/v1/projects/",
        json={
            "title": "메타데이터 수동 등록 프로젝트",
            "description": "수동 등록된 공고 메모",
            "requirements": "내부 검토용 요구사항",
            "budget_estimate": 125000000.0,
            "category": "software",
            "notice_number": "R26BK01510420",
            "source_url": "http://ebid.example.com/detail/R26BK01510420",
            "issuing_agency": "조달청",
            "demand_agency": "서울특별시교육청",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["notice_number"] == "R26BK01510420"
    assert payload["source_url"] == "http://ebid.example.com/detail/R26BK01510420"
    assert payload["issuing_agency"] == "조달청"
    assert payload["demand_agency"] == "서울특별시교육청"

    detail = client.get(f"/api/v1/projects/{payload['id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["notice_number"] == "R26BK01510420"
    assert detail_payload["issuing_agency"] == "조달청"


def test_get_nonexistent_project(client):
    """Test getting non-existent project"""
    response = client.get("/api/v1/projects/9999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_create_project_defers_embedding_to_outbox_task(client, test_db):
    """Creating a project should NOT compute the embedding inline.

    The heavy SBERT ``model.encode`` is moved off the synchronous request path:
    ``POST /projects`` returns after committing a durable semantic-input event.
    On the in-memory eager broker (tests) with
    ``CELERY_ALLOW_INLINE_ML_TASKS=false`` inference is only notified, so the
    row has no embedding metadata until the outbox worker runs.
    """
    response = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 분석 플랫폼 구축",
            "description": "AI 분석과 데이터 시각화 기능을 포함한 플랫폼을 구축합니다.",
            "requirements": "데이터 파이프라인, 대시보드, 운영 지원이 필요합니다.",
            "budget_estimate": 120000000.0,
            "category": "software",
        },
    )

    assert response.status_code == 200
    project_id = response.json()["id"]
    project = test_db.query(Project).filter(Project.id == project_id).first()
    assert project is not None
    # Embedding is deferred to the async outbox task; nothing computed inline.
    # Columns keep their schema defaults ("" / "[]") rather than a real vector.
    assert project.semantic_text == ""
    assert project.embedding_payload == "[]"
    assert project.embedding_model is None
    assert project.embedding_updated_at is None


def test_create_project_inference_outbox_task_populates_metadata(client, test_db):
    """The outbox processor fills in the deferred embedding metadata."""
    from app.tasks.jobs import process_inference_outbox

    response = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 분석 플랫폼 구축",
            "description": "AI 분석과 데이터 시각화 기능을 포함한 플랫폼을 구축합니다.",
            "requirements": "데이터 파이프라인, 대시보드, 운영 지원이 필요합니다.",
            "budget_estimate": 120000000.0,
            "category": "software",
        },
    )
    assert response.status_code == 200
    project_id = response.json()["id"]

    # Simulate the periodic worker draining the durable semantic-input event.
    result = process_inference_outbox.run(limit=10)
    assert result["processed_count"] == 1

    test_db.expire_all()
    project = test_db.query(Project).filter(Project.id == project_id).first()
    assert project is not None
    assert project.semantic_text
    assert project.embedding_payload.startswith("[")
    assert project.embedding_model == "fallback-hash-v1"
    assert project.embedding_updated_at is not None


def test_update_project_invalidates_embedding_and_writes_outbox(client, test_db, monkeypatch):
    """Updating semantic fields must never run embedding inference in the API process."""
    from app.api import projects as projects_api
    from app.core.time import utc_now
    from app.services.project_similarity import ProjectSimilarityService

    created = client.post(
        "/api/v1/projects/",
        json={
            "title": "기존 공고",
            "description": "기존 설명",
            "requirements": "기존 요구사항",
            "budget_estimate": 10_000_000.0,
            "category": "software",
        },
    ).json()
    project = test_db.get(Project, created["id"])
    project.semantic_text = "old semantic text"
    project.embedding_payload = "[1.0]"
    project.embedding_model = "old-model"
    project.embedding_updated_at = utc_now()
    test_db.commit()

    def _fail_inline_embedding(self, semantic_text):
        raise AssertionError(f"inline embedding attempted: {semantic_text}")

    def _fail_direct_refresh(*, project_id: int, force: bool = False):
        raise AssertionError(
            f"direct refresh called: project_id={project_id}, force={force}"
        )

    monkeypatch.setattr(ProjectSimilarityService, "_embed_text", _fail_inline_embedding)
    monkeypatch.setattr(
        projects_api, "enqueue_project_embedding_refresh", _fail_direct_refresh
    )

    response = client.put(
        f"/api/v1/projects/{created['id']}",
        json={
            "title": "수정된 공고",
            "description": "수정된 설명",
            "requirements": "수정된 요구사항",
            "budget_estimate": 20_000_000.0,
            "category": "software",
        },
    )

    assert response.status_code == 200
    test_db.expire_all()
    updated = test_db.get(Project, created["id"])
    assert updated.embedding_payload == "[]"
    assert updated.embedding_model is None
    assert updated.embedding_updated_at is None
    events = (
        test_db.query(InferenceOutboxEvent)
        .filter(InferenceOutboxEvent.aggregate_id == created["id"])
        .all()
    )
    assert len(events) == 2
    assert {event.event_type for event in events} == {"semantic_input.changed"}


def test_update_project_preserves_embedding_when_only_source_url_changes(
    client, test_db, monkeypatch
):
    """Non-semantic metadata updates must not enqueue unnecessary ML work."""
    from app.api import projects as projects_api
    from app.core.time import utc_now
    from app.services.project_similarity import ProjectSimilarityService

    created = client.post(
        "/api/v1/projects/",
        json={
            "title": "원본 공고",
            "description": "원본 설명",
            "requirements": "원본 요구사항",
            "budget_estimate": 10_000_000.0,
            "category": "software",
            "source_url": "https://example.com/old",
        },
    ).json()
    project = test_db.get(Project, created["id"])
    project.semantic_text = ProjectSimilarityService().build_semantic_text(project)
    project.embedding_payload = "[1.0]"
    project.embedding_model = "old-model"
    project.embedding_updated_at = utc_now()
    original_updated_at = project.embedding_updated_at
    test_db.commit()

    queued: list[int] = []
    monkeypatch.setattr(
        projects_api,
        "enqueue_project_embedding_refresh",
        lambda *, project_id, force=False: queued.append(project_id),
    )

    response = client.put(
        f"/api/v1/projects/{created['id']}",
        json={
            "title": "원본 공고",
            "description": "원본 설명",
            "requirements": "원본 요구사항",
            "budget_estimate": 10_000_000.0,
            "category": "software",
            "source_url": "https://example.com/new",
        },
    )

    assert response.status_code == 200
    assert queued == []
    test_db.expire_all()
    updated = test_db.get(Project, created["id"])
    assert updated.embedding_payload == "[1.0]"
    assert updated.embedding_model == "old-model"
    assert updated.embedding_updated_at is not None
    assert (
        updated.embedding_updated_at.replace(tzinfo=original_updated_at.tzinfo)
        == original_updated_at
    )


def test_get_similar_projects_missing_analysis_is_read_only(client, test_db, monkeypatch):
    """Similarity GET should not run analysis or expose its internal readiness state."""
    from app.services.project_similarity import ProjectSimilarityService

    def _boom(self, semantic_text: str):
        raise AssertionError("similarity GET must not embed inline")

    monkeypatch.setattr(ProjectSimilarityService, "_embed_text", _boom)
    target = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 분석 플랫폼 구축",
            "description": "AI 분석과 데이터 시각화 기능을 포함한 플랫폼을 구축합니다.",
            "requirements": "데이터 파이프라인, 대시보드, 운영 지원이 필요합니다.",
            "budget_estimate": 120000000.0,
            "category": "software",
        },
    ).json()
    project = test_db.query(Project).filter(Project.id == target["id"]).first()
    assert project is not None
    assert project.embedding_payload == "[]"

    response = client.get(f"/api/v1/projects/{target['id']}/similar")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload).isdisjoint(
        {
            "target_embedding_model",
            "target_embedding_status",
            "target_embedding_updated_at",
            "target_embedding_refresh_required",
            "search_mode",
        }
    )
    assert payload["result_count"] == 0
    assert payload["results"] == []
    test_db.expire_all()
    unchanged = test_db.query(Project).filter(Project.id == target["id"]).first()
    assert unchanged is not None
    assert unchanged.semantic_text == ""
    assert unchanged.embedding_payload == "[]"
    assert unchanged.embedding_updated_at is None


def test_get_similar_projects_stale_analysis_is_read_only(client, test_db, monkeypatch):
    """Similarity GET should not rebuild or expose stale internal analysis state."""
    from app.models.models import Project
    from app.services.project_similarity import ProjectSimilarityService
    from app.tasks.jobs import rebuild_project_embeddings as rebuild_project_embeddings_task

    target = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 분석 플랫폼 구축",
            "description": "데이터 분석과 시각화 기능을 포함한 플랫폼 구축",
            "requirements": "대시보드, 데이터 파이프라인, 운영 자동화",
            "budget_estimate": 120000000.0,
            "category": "software",
        },
    ).json()
    candidate = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 대시보드 구축",
            "description": "분석 리포트와 시각화 대시보드 개발",
            "requirements": "데이터 파이프라인, 운영 리포트",
            "budget_estimate": 110000000.0,
            "category": "software",
        },
    ).json()
    rebuild_project_embeddings_task.run(project_ids=[target["id"], candidate["id"]])

    project = test_db.query(Project).filter(Project.id == target["id"]).first()
    assert project is not None
    original_semantic_text = project.semantic_text
    original_payload = project.embedding_payload
    original_updated_at = project.embedding_updated_at
    project.description = "저장 임베딩과 다른 새 설명으로 변경"
    test_db.commit()

    def _boom(self, semantic_text: str):
        raise AssertionError("stale similarity GET must not embed inline")

    monkeypatch.setattr(ProjectSimilarityService, "_embed_text", _boom)

    response = client.get(
        f"/api/v1/projects/{target['id']}/similar?min_similarity=0.0"
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload).isdisjoint(
        {
            "target_embedding_model",
            "target_embedding_status",
            "target_embedding_updated_at",
            "target_embedding_refresh_required",
            "search_mode",
        }
    )
    assert payload["result_count"] == 0
    assert payload["results"] == []
    test_db.expire_all()
    unchanged = test_db.query(Project).filter(Project.id == target["id"]).first()
    assert unchanged is not None
    assert unchanged.semantic_text == original_semantic_text
    assert unchanged.embedding_payload == original_payload
    assert unchanged.embedding_updated_at == original_updated_at


def test_get_similar_projects_returns_ranked_matches(client):
    """Similar project endpoint should rank the closest notices first."""
    from app.tasks.jobs import rebuild_project_embeddings as rebuild_project_embeddings_task

    target = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 분석 플랫폼 구축",
            "description": "AI 분석, 데이터 시각화, 보고서 자동화를 포함한 플랫폼 구축",
            "requirements": "데이터 파이프라인, 분석 대시보드, 운영 지원",
            "budget_estimate": 130000000.0,
            "category": "software",
        },
    ).json()

    close_match = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 데이터 대시보드 구축",
            "description": "데이터 분석과 시각화 중심의 대시보드 시스템 개발",
            "requirements": "분석 보고서, 대시보드, 운영 유지관리",
            "budget_estimate": 118000000.0,
            "category": "software",
        },
    ).json()

    farther_match = client.post(
        "/api/v1/projects/",
        json={
            "title": "정보보안 관제 시스템 유지관리",
            "description": "보안 모니터링과 침해대응 운영 지원",
            "requirements": "24시간 관제, 로그 분석, 보안 이벤트 대응",
            "budget_estimate": 140000000.0,
            "category": "software",
        },
    ).json()

    other_category = client.post(
        "/api/v1/projects/",
        json={
            "title": "청사 냉난방 설비 교체 공사",
            "description": "노후 냉난방 설비 교체 및 배관 정비",
            "requirements": "기계설비 시공, 현장 안전관리",
            "budget_estimate": 160000000.0,
            "category": "construction",
        },
    ).json()

    rebuild_project_embeddings_task.run(
        project_ids=[
            target["id"],
            close_match["id"],
            farther_match["id"],
            other_category["id"],
        ]
    )

    response = client.get(f"/api/v1/projects/{target['id']}/similar")

    assert response.status_code == 200
    data = response.json()
    assert data["target_project_id"] == target["id"]
    assert set(data).isdisjoint(
        {
            "target_embedding_model",
            "target_embedding_status",
            "target_embedding_updated_at",
            "target_embedding_refresh_required",
            "search_mode",
        }
    )
    assert data["result_count"] == 2
    assert data["results"][0]["project_id"] == close_match["id"]
    assert data["results"][1]["project_id"] == farther_match["id"]
    assert all(item["category"] == "software" for item in data["results"])
    assert all(item["project_id"] != other_category["id"] for item in data["results"])
    assert data["results"][0]["similarity_score"] >= data["results"][1]["similarity_score"]


def test_get_similar_projects_respects_similarity_threshold(client):
    """Similarity threshold should filter out weak matches."""
    from app.tasks.jobs import rebuild_project_embeddings as rebuild_project_embeddings_task

    target = client.post(
        "/api/v1/projects/",
        json={
            "title": "AI 콜센터 상담 품질 분석",
            "description": "상담 음성 분석과 품질 평가 자동화 시스템",
            "requirements": "STT, 감성 분석, 통계 리포트",
            "budget_estimate": 90000000.0,
            "category": "software",
        },
    ).json()

    related = client.post(
        "/api/v1/projects/",
        json={
            "title": "스마트 민원 데이터 분석",
            "description": "민원 데이터 분석과 대시보드 구축",
            "requirements": "리포트 자동화, 시각화",
            "budget_estimate": 85000000.0,
            "category": "software",
        },
    )

    unrelated = client.post(
        "/api/v1/projects/",
        json={
            "title": "건물 외벽 보수 공사",
            "description": "외벽 균열 보수와 도장 공사",
            "requirements": "현장 시공, 안전관리",
            "budget_estimate": 85000000.0,
            "category": "construction",
        },
    )

    rebuild_project_embeddings_task.run(
        project_ids=[target["id"], related.json()["id"], unrelated.json()["id"]]
    )

    response = client.get(
        f"/api/v1/projects/{target['id']}/similar?min_similarity=0.95&same_category_only=false"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 0
    assert data["results"] == []


def test_refresh_similar_projects_endpoint_uses_domain_operation_contract(client, test_db):
    """User refresh should queue work without exposing ML infrastructure details."""
    from app.tasks.jobs import rebuild_project_embeddings as rebuild_project_embeddings_task

    created = client.post(
        "/api/v1/projects/",
        json={
            "title": "온디맨드 재임베딩 테스트",
            "description": "프로젝트 임베딩을 수동으로 다시 생성합니다.",
            "requirements": "분석 대시보드와 운영 자동화",
            "budget_estimate": 88000000.0,
            "category": "software",
        },
    ).json()

    project = test_db.query(Project).filter(Project.id == created["id"]).first()
    assert project is not None
    project.semantic_text = ""
    project.embedding_payload = "[]"
    project.embedding_model = None
    project.embedding_updated_at = None
    test_db.commit()

    _authenticate_privileged_operator(client, test_db)
    response = client.post(f"/api/v1/projects/{created['id']}/similar/refresh?force=true")

    assert response.status_code == 202
    payload = response.json()
    assert payload["operation_id"]
    assert payload["operation"] == "refresh_similar_projects"
    assert payload["project_id"] == created["id"]
    assert payload["status"] == "accepted"
    assert set(payload).isdisjoint({"task_name", "queue", "raw_status"})
    assert "embedding" not in payload["poll_url"]

    status_response = client.get(payload["poll_url"])
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["operation_id"] == payload["operation_id"]
    assert status_payload["status"] == "accepted"
    assert status_payload["is_terminal"] is False
    assert status_payload["succeeded"] is False
    assert set(status_payload).isdisjoint({"task_name", "queue", "raw_status"})

    refreshed = test_db.query(Project).filter(Project.id == created["id"]).first()
    assert refreshed is not None
    assert refreshed.embedding_payload == "[]"
    assert refreshed.embedding_updated_at is None

    result = rebuild_project_embeddings_task.run(project_ids=[created["id"]], force=True)
    assert result["processed_count"] == 1
    test_db.expire_all()
    refreshed = test_db.query(Project).filter(Project.id == created["id"]).first()
    assert refreshed is not None
    assert refreshed.embedding_payload.startswith("[")
    assert refreshed.embedding_updated_at is not None


def test_rebuild_project_embeddings_endpoint_queues_backfill(client, test_db):
    """Legacy batch refresh endpoint should only enqueue work from the API process."""
    software_ids = []
    for index in range(2):
        created = client.post(
            "/api/v1/projects/",
            json={
                "title": f"소프트웨어 임베딩 재빌드 {index + 1}",
                "description": "유사 공고 검색용 임베딩 재계산",
                "requirements": "데이터 분석 및 운영 유지관리",
                "budget_estimate": 70000000.0 + index * 5000000.0,
                "category": "software",
            },
        ).json()
        software_ids.append(created["id"])

    client.post(
        "/api/v1/projects/",
        json={
            "title": "건설 임베딩 재빌드 제외",
            "description": "건설 카테고리 프로젝트",
            "requirements": "현장 시공 관리",
            "budget_estimate": 99000000.0,
            "category": "construction",
        },
    )

    _authenticate_privileged_operator(client, test_db)
    response = client.post(
        "/api/v1/projects/embeddings/rebuild?category=software&force=true&limit=10"
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_name"] == "jobs.rebuild_project_embeddings"
    assert payload["status"] == "queued"
    assert payload["task_id"]
    assert payload["poll_url"].endswith(payload["task_id"])

    status_response = client.get(payload["poll_url"])
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["raw_status"] == "PENDING"
    assert status_payload["result"] is None


def test_rebuild_project_embeddings_task_returns_batch_summary(test_db):
    """Batch embedding task should return a structured refresh summary."""
    from app.tasks.jobs import rebuild_project_embeddings as rebuild_project_embeddings_task

    test_db.add_all([
        Project(
            title="배치 재임베딩 1",
            description="프로젝트 임베딩 배치 테스트",
            requirements="벡터 갱신",
            budget_estimate=50000000.0,
            category="software",
        ),
        Project(
            title="배치 재임베딩 2",
            description="프로젝트 임베딩 배치 테스트",
            requirements="벡터 갱신",
            budget_estimate=52000000.0,
            category="software",
        ),
    ])
    test_db.commit()

    result = rebuild_project_embeddings_task.run(limit=10, force=True)

    assert result["processed_count"] == 2
    assert result["force"] is True
    assert result["vector_storage_enabled"] is False
    assert len(result["results"]) == 2
    assert all(item["embedding_dimensions"] == 384 for item in result["results"])


def test_rebuild_project_embeddings_async_endpoint_returns_pollable_task(client, test_db):
    """Async rebuild endpoint should return a task id and poll URL for batch refresh jobs."""
    for index in range(2):
        client.post(
            "/api/v1/projects/",
            json={
                "title": f"비동기 재임베딩 {index + 1}",
                "description": "비동기 배치 재임베딩 테스트",
                "requirements": "상태 조회 API 검증",
                "budget_estimate": 60000000.0 + index * 5000000.0,
                "category": "software",
            },
        )

    _authenticate_privileged_operator(client, test_db)
    response = client.post(
        "/api/v1/projects/embeddings/rebuild/async?category=software&force=true&limit=10"
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_name"] == "jobs.rebuild_project_embeddings"
    assert payload["status"] == "queued"
    assert payload["task_id"]
    assert payload["poll_url"].endswith(payload["task_id"])


def test_rebuild_project_embeddings_task_status_endpoint_returns_result(client, test_db):
    """Task status endpoint should expose the completed batch summary for eager/fallback tasks."""
    software_ids = []
    for index in range(2):
        created = client.post(
            "/api/v1/projects/",
            json={
                "title": f"상태 조회 재임베딩 {index + 1}",
                "description": "완료된 태스크 상태 조회 테스트",
                "requirements": "결과 요약 반환",
                "budget_estimate": 61000000.0 + index * 5000000.0,
                "category": "software",
            },
        ).json()
        software_ids.append(created["id"])

    _authenticate_privileged_operator(client, test_db)
    task_response = client.post(
        "/api/v1/projects/embeddings/rebuild/async?category=software&force=true&limit=10"
    )
    task_id = task_response.json()["task_id"]

    response = client.get(f"/api/v1/projects/embeddings/rebuild/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["task_name"] == "jobs.rebuild_project_embeddings"
    assert payload["status"] == "queued"
    assert payload["raw_status"] == "PENDING"
    assert payload["ready"] is False
    assert payload["successful"] is False
    assert payload["error"] is None
    assert payload["result"] is None


def _create_test_project(client, **overrides):
    payload = {
        "title": "Filter Test Project",
        "description": "filterable project description",
        "requirements": "test",
        "budget_estimate": 100_000_000.0,
        "category": "software",
    }
    payload.update(overrides)
    response = client.post("/api/v1/projects/", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_list_projects_supports_text_search_and_returns_total_count(client):
    _create_test_project(client, title="공항 시스템 구축 사업", notice_number="A-001")
    _create_test_project(client, title="동대문 도로 공사", notice_number="B-002")
    _create_test_project(client, title="공항 청소 용역", notice_number="C-003")

    response = client.get("/api/v1/projects/", params={"q": "공항"})

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert "공항 시스템 구축 사업" in titles
    assert "공항 청소 용역" in titles
    assert "동대문 도로 공사" not in titles
    assert response.headers["X-Total-Count"] == "2"


def test_list_projects_filters_by_budget_and_agency(client):
    _create_test_project(
        client,
        title="대형 입찰",
        budget_estimate=900_000_000.0,
        issuing_agency="조달청",
        demand_agency="서울시",
    )
    _create_test_project(
        client,
        title="중형 입찰",
        budget_estimate=300_000_000.0,
        issuing_agency="조달청",
        demand_agency="부산시",
    )
    _create_test_project(
        client,
        title="소형 입찰",
        budget_estimate=50_000_000.0,
        issuing_agency="국방부",
        demand_agency="육군본부",
    )

    budget_filtered = client.get(
        "/api/v1/projects/",
        params={"budget_min": 200_000_000, "budget_max": 1_000_000_000},
    )
    assert budget_filtered.status_code == 200
    budget_titles = [item["title"] for item in budget_filtered.json()]
    assert set(budget_titles) == {"대형 입찰", "중형 입찰"}
    assert budget_filtered.headers["X-Total-Count"] == "2"

    agency_filtered = client.get(
        "/api/v1/projects/", params={"agency": "부산"}
    )
    assert agency_filtered.status_code == 200
    agency_titles = [item["title"] for item in agency_filtered.json()]
    assert agency_titles == ["중형 입찰"]
    assert agency_filtered.headers["X-Total-Count"] == "1"


def test_list_projects_paginates_with_skip_and_limit(client):
    for index in range(6):
        _create_test_project(
            client,
            title=f"페이지네이션 프로젝트 {index}",
            notice_number=f"P-{index:03d}",
        )

    first_page = client.get(
        "/api/v1/projects/",
        params={"q": "페이지네이션", "skip": 0, "limit": 2},
    )
    assert first_page.status_code == 200
    assert len(first_page.json()) == 2
    assert first_page.headers["X-Total-Count"] == "6"

    second_page = client.get(
        "/api/v1/projects/",
        params={"q": "페이지네이션", "skip": 2, "limit": 2},
    )
    assert second_page.status_code == 200
    assert len(second_page.json()) == 2
    first_ids = {item["id"] for item in first_page.json()}
    second_ids = {item["id"] for item in second_page.json()}
    assert first_ids.isdisjoint(second_ids)
