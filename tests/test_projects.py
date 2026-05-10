"""Tests for project endpoints"""
import pytest
from fastapi.testclient import TestClient

from app.models.models import Project


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


def test_create_project_persists_embedding_metadata(client, test_db):
    """Creating a project should also persist semantic embedding metadata."""
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
    project = test_db.query(Project).filter(Project.id == response.json()["id"]).first()
    assert project is not None
    assert project.semantic_text
    assert project.embedding_payload.startswith("[")
    assert project.embedding_model == "fallback-hash-v1"
    assert project.embedding_updated_at is not None


def test_get_similar_projects_returns_ranked_matches(client):
    """Similar project endpoint should rank the closest notices first."""
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

    response = client.get(f"/api/v1/projects/{target['id']}/similar")

    assert response.status_code == 200
    data = response.json()
    assert data["target_project_id"] == target["id"]
    assert data["search_mode"] == "python_fallback"
    assert data["result_count"] == 2
    assert data["results"][0]["project_id"] == close_match["id"]
    assert data["results"][1]["project_id"] == farther_match["id"]
    assert all(item["category"] == "software" for item in data["results"])
    assert all(item["project_id"] != other_category["id"] for item in data["results"])
    assert data["results"][0]["similarity_score"] >= data["results"][1]["similarity_score"]


def test_get_similar_projects_respects_similarity_threshold(client):
    """Similarity threshold should filter out weak matches."""
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

    client.post(
        "/api/v1/projects/",
        json={
            "title": "스마트 민원 데이터 분석",
            "description": "민원 데이터 분석과 대시보드 구축",
            "requirements": "리포트 자동화, 시각화",
            "budget_estimate": 85000000.0,
            "category": "software",
        },
    )

    client.post(
        "/api/v1/projects/",
        json={
            "title": "건물 외벽 보수 공사",
            "description": "외벽 균열 보수와 도장 공사",
            "requirements": "현장 시공, 안전관리",
            "budget_estimate": 85000000.0,
            "category": "construction",
        },
    )

    response = client.get(f"/api/v1/projects/{target['id']}/similar?min_similarity=0.95&same_category_only=false")

    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 0
    assert data["results"] == []


def test_refresh_single_project_embedding_endpoint(client, test_db):
    """Single-project refresh endpoint should rebuild embedding metadata on demand."""
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

    response = client.post(f"/api/v1/projects/{created['id']}/embedding/refresh?force=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == created["id"]
    assert payload["embedding_model"] == "fallback-hash-v1"
    assert payload["semantic_text_length"] > 0
    assert payload["embedding_dimensions"] == 384
    assert payload["vector_storage_enabled"] is False
    assert payload["vector_persisted"] is False

    refreshed = test_db.query(Project).filter(Project.id == created["id"]).first()
    assert refreshed is not None
    assert refreshed.embedding_payload.startswith("[")
    assert refreshed.embedding_updated_at is not None


def test_rebuild_project_embeddings_endpoint_filters_projects(client):
    """Batch refresh endpoint should rebuild only the requested project slice."""
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

    construction_id = client.post(
        "/api/v1/projects/",
        json={
            "title": "건설 임베딩 재빌드 제외",
            "description": "건설 카테고리 프로젝트",
            "requirements": "현장 시공 관리",
            "budget_estimate": 99000000.0,
            "category": "construction",
        },
    ).json()["id"]

    response = client.post(
        "/api/v1/projects/embeddings/rebuild?category=software&force=true&limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed_count"] == 2
    assert payload["category"] == "software"
    assert payload["project_status"] is None
    assert payload["force"] is True
    assert payload["vector_storage_enabled"] is False
    assert sorted(payload["project_ids"]) == sorted(software_ids)
    assert all(item["category"] == "software" for item in payload["results"])
    assert construction_id not in payload["project_ids"]


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


def test_rebuild_project_embeddings_async_endpoint_returns_pollable_task(client):
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

    response = client.post(
        "/api/v1/projects/embeddings/rebuild/async?category=software&force=true&limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_name"] == "jobs.rebuild_project_embeddings"
    assert payload["status"] == "completed"
    assert payload["task_id"]
    assert payload["poll_url"].endswith(payload["task_id"])


def test_rebuild_project_embeddings_task_status_endpoint_returns_result(client):
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

    task_response = client.post(
        "/api/v1/projects/embeddings/rebuild/async?category=software&force=true&limit=10"
    )
    task_id = task_response.json()["task_id"]

    response = client.get(f"/api/v1/projects/embeddings/rebuild/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["task_name"] == "jobs.rebuild_project_embeddings"
    assert payload["status"] == "completed"
    assert payload["raw_status"] == "SUCCESS"
    assert payload["ready"] is True
    assert payload["successful"] is True
    assert payload["error"] is None
    assert payload["result"]["processed_count"] == 2
    assert payload["result"]["force"] is True
    assert payload["result"]["category"] == "software"
    assert sorted(payload["result"]["project_ids"]) == sorted(software_ids)
