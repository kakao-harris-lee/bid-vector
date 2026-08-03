"""Authorization and compatibility checks for the UX/ML control-plane boundary."""

import runpy
from datetime import timedelta
from pathlib import Path

from alembic import op as alembic_op

from app.core.security import get_password_hash
from app.core.time import utc_now
from app.core.single_user import DEFAULT_OPERATOR_PASSWORD, ensure_operator_account
from app.models.models import Project, SimilarProjectsRefreshOperation, User
from tests.support.auth import bearer_headers


def _create_non_privileged_operator(test_db) -> User:
    operator = User(
        username="project-viewer",
        email="project-viewer@example.com",
        full_name="Project Viewer",
        company="Example",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(operator)
    test_db.commit()
    test_db.refresh(operator)
    return operator


def _create_project(test_db) -> Project:
    project = Project(
        title="유사 공고 경계 테스트",
        description="유사 공고 갱신 계약을 검증합니다.",
        requirements="도메인 응답과 폴링",
        budget_estimate=100_000_000,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _run_owner_privilege_migration(test_db, monkeypatch) -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "a8d1c4e7f2b6_upgrade_legacy_singleton_owner_admin.py"
    )
    upgrade = runpy.run_path(str(migration_path))["upgrade"]
    monkeypatch.setattr(alembic_op, "get_bind", test_db.connection)
    upgrade()
    test_db.expire_all()


def test_similar_projects_refresh_requires_bearer_but_allows_regular_operator(
    client,
    test_db,
):
    project = _create_project(test_db)
    _create_non_privileged_operator(test_db)

    unauthenticated = client.post(
        f"/api/v1/projects/{project.id}/similar/refresh"
    )
    assert unauthenticated.status_code == 401

    response = client.post(
        f"/api/v1/projects/{project.id}/similar/refresh",
        headers=bearer_headers(client, username="project-viewer"),
    )
    assert response.status_code == 202
    assert response.json()["operation"] == "refresh_similar_projects"


def test_similar_projects_refresh_polling_maps_terminal_state_without_raw_details(
    client,
    test_db,
    monkeypatch,
):
    from app.services import similar_projects_refresh as refresh_service

    project = _create_project(test_db)
    _create_non_privileged_operator(test_db)
    class _TaskHandle:
        id = "celery-task-1"

    monkeypatch.setattr(
        refresh_service,
        "enqueue_project_embedding_refresh",
        lambda **_kwargs: _TaskHandle(),
    )
    monkeypatch.setattr(
        refresh_service,
        "get_project_embedding_rebuild_task_status",
        lambda task_id: {
            "task_id": task_id,
            "task_name": "jobs.rebuild_project_embeddings",
            "status": "completed",
            "raw_status": "SUCCESS",
            "ready": True,
            "successful": True,
            "detail": "Task completed successfully.",
            "error": None,
            "result": None,
        },
    )

    headers = bearer_headers(client, username="project-viewer")
    accepted = client.post(
        f"/api/v1/projects/{project.id}/similar/refresh",
        headers=headers,
    )
    assert accepted.status_code == 202
    operation = accepted.json()
    assert operation["operation_id"] != _TaskHandle.id

    response = client.get(operation["poll_url"], headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["is_terminal"] is True
    assert payload["succeeded"] is True
    assert set(payload).isdisjoint({"task_name", "queue", "raw_status"})
    assert "embedding" not in response.text.lower()


def test_similar_projects_refresh_is_bound_to_project_and_operator(
    client,
    test_db,
):
    project = _create_project(test_db)
    other_project = _create_project(test_db)
    operator = _create_non_privileged_operator(test_db)
    other_operator = User(
        username="other-viewer",
        email="other-viewer@example.com",
        full_name="Other Viewer",
        company="Example",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(other_operator)
    test_db.commit()

    owner_headers = bearer_headers(client, username=operator.username)
    accepted = client.post(
        f"/api/v1/projects/{project.id}/similar/refresh",
        headers=owner_headers,
    )
    assert accepted.status_code == 202
    operation_id = accepted.json()["operation_id"]

    wrong_project = client.get(
        f"/api/v1/projects/{other_project.id}/similar/refresh/operations/{operation_id}",
        headers=owner_headers,
    )
    assert wrong_project.status_code == 404

    wrong_operator = client.get(
        accepted.json()["poll_url"],
        headers=bearer_headers(client, username=other_operator.username),
    )
    assert wrong_operator.status_code == 404

    missing = client.get(
        f"/api/v1/projects/{project.id}/similar/refresh/operations/missing-operation",
        headers=owner_headers,
    )
    assert missing.status_code == 404


def test_expired_similar_projects_refresh_becomes_terminal_failed(
    client,
    test_db,
):
    project = _create_project(test_db)
    operator = _create_non_privileged_operator(test_db)
    headers = bearer_headers(client, username=operator.username)
    accepted = client.post(
        f"/api/v1/projects/{project.id}/similar/refresh",
        headers=headers,
    )
    assert accepted.status_code == 202

    operation = test_db.get(
        SimilarProjectsRefreshOperation,
        accepted.json()["operation_id"],
    )
    assert operation is not None
    operation.expires_at = utc_now() - timedelta(seconds=1)
    test_db.commit()

    response = client.get(accepted.json()["poll_url"], headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["is_terminal"] is True
    assert response.json()["succeeded"] is False


def test_ml_control_routes_use_privileged_admin_namespace_and_protected_legacy_alias(
    client,
    test_db,
):
    canonical = ensure_operator_account(test_db)
    _create_non_privileged_operator(test_db)
    request_payload = {
        "release_tag": "architecture-boundary-test",
        "category": "software",
        "limit": 20,
    }

    assert (
        client.post(
            "/api/v1/admin/ml/training/price-predictor",
            json=request_payload,
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/ml/training/price-predictor",
            json=request_payload,
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/admin/ml/training/price-predictor",
            json=request_payload,
            headers=bearer_headers(client, username="project-viewer"),
        ).status_code
        == 403
    )

    privileged_headers = bearer_headers(
        client,
        username=canonical.username,
        password=DEFAULT_OPERATOR_PASSWORD,
    )
    admin_response = client.post(
        "/api/v1/admin/ml/training/price-predictor",
        json=request_payload,
        headers=privileged_headers,
    )
    assert admin_response.status_code == 202
    assert admin_response.json()["poll_url"].startswith("/api/v1/admin/ml/")

    legacy_response = client.post(
        "/api/v1/ml/training/price-predictor",
        json=request_payload,
        headers=privileged_headers,
    )
    assert legacy_response.status_code == 202
    assert legacy_response.json()["poll_url"].startswith("/api/v1/admin/ml/")

    schema = client.get("/openapi.json").json()
    assert (
        schema["paths"]["/api/v1/ml/training/price-predictor"]["post"][
            "deprecated"
        ]
        is True
    )
    assert (
        schema["paths"]["/api/v1/admin/ml/training/price-predictor"]["post"].get(
            "deprecated", False
        )
        is False
    )


def test_custom_bootstrapped_singleton_owner_has_admin_ml_access(client, test_db):
    password = "custom-owner-password"
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "primary-owner",
            "email": "primary-owner@example.com",
            "password": password,
            "full_name": "Primary Owner",
            "company": "Owner Company",
        },
    )
    assert bootstrap.status_code == 200
    owner = ensure_operator_account(test_db)
    assert owner.username == "primary-owner"
    assert owner.is_admin is True

    response = client.post(
        "/api/v1/admin/ml/training/price-predictor",
        json={
            "release_tag": "custom-owner-boundary-test",
            "category": "software",
            "limit": 20,
        },
        headers=bearer_headers(
            client,
            username=owner.username,
            password=password,
        ),
    )
    assert response.status_code == 202


def test_existing_custom_singleton_owner_is_upgraded_for_admin_ml_access(
    client,
    test_db,
):
    password = "legacy-owner-password"
    legacy_owner = User(
        username="legacy-primary-owner",
        email="legacy-primary-owner@example.com",
        full_name="Legacy Primary Owner",
        company="Owner Company",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=False,
    )
    test_db.add(legacy_owner)
    test_db.commit()
    test_db.refresh(legacy_owner)

    owner = ensure_operator_account(test_db)

    assert owner.id == legacy_owner.id
    assert owner.is_admin is True
    response = client.post(
        "/api/v1/admin/ml/training/price-predictor",
        json={
            "release_tag": "legacy-custom-owner-boundary-test",
            "category": "software",
            "limit": 20,
        },
        headers=bearer_headers(
            client,
            username=owner.username,
            password=password,
        ),
    )
    assert response.status_code == 202


def test_owner_privilege_migration_upgrades_existing_custom_singleton(
    test_db,
    monkeypatch,
):
    legacy_owner = User(
        username="migrated-primary-owner",
        email="migrated-primary-owner@example.com",
        full_name="Migrated Primary Owner",
        company="Owner Company",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(legacy_owner)
    test_db.commit()

    _run_owner_privilege_migration(test_db, monkeypatch)

    test_db.refresh(legacy_owner)
    assert legacy_owner.is_admin is True


def test_multi_user_database_does_not_promote_arbitrary_first_account(
    client,
    test_db,
    monkeypatch,
):
    first = _create_non_privileged_operator(test_db)
    second = User(
        username="synthetic-reviewer",
        email="synthetic-reviewer@example.com",
        full_name="Synthetic Reviewer",
        company="Synthetic Company",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(second)
    test_db.commit()

    _run_owner_privilege_migration(test_db, monkeypatch)
    resolved = ensure_operator_account(test_db)

    test_db.refresh(first)
    test_db.refresh(second)
    assert resolved.id == first.id
    assert first.is_admin is False
    assert second.is_admin is False
    response = client.post(
        "/api/v1/admin/ml/training/price-predictor",
        json={
            "release_tag": "multi-user-boundary-test",
            "category": "software",
            "limit": 20,
        },
        headers=bearer_headers(client, username=first.username),
    )
    assert response.status_code == 403


def test_single_synthetic_account_is_not_promoted_to_owner(test_db):
    synthetic = User(
        username="synthetic-only-validation",
        email="synthetic-only-validation@example.com",
        full_name="Synthetic Only Validation",
        company="Synthetic Company",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(synthetic)
    test_db.commit()

    resolved = ensure_operator_account(test_db)

    test_db.refresh(synthetic)
    assert resolved.id == synthetic.id
    assert synthetic.is_admin is False


def test_similar_projects_public_contract_omits_ml_and_storage_details(client, test_db):
    project = _create_project(test_db)
    response = client.get(f"/api/v1/projects/{project.id}/similar")
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
    assert all("embedding_model" not in item for item in payload["results"])

    schema = client.get("/openapi.json").json()
    response_schema = schema["components"]["schemas"]["SimilarProjectsResponse"]
    assert set(response_schema["properties"]).isdisjoint(
        {
            "target_embedding_model",
            "target_embedding_status",
            "target_embedding_updated_at",
            "target_embedding_refresh_required",
            "search_mode",
        }
    )
    item_schema = schema["components"]["schemas"]["SimilarProjectSummary"]
    assert "embedding_model" not in item_schema["properties"]


def test_embedding_compatibility_refresh_is_deprecated_and_privileged(
    client,
    test_db,
):
    canonical = ensure_operator_account(test_db)
    _create_non_privileged_operator(test_db)
    project = _create_project(test_db)
    legacy_path = f"/api/v1/projects/{project.id}/embedding/refresh"

    assert client.post(legacy_path).status_code == 401
    assert (
        client.post(
            legacy_path,
            headers=bearer_headers(client, username="project-viewer"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            legacy_path,
            headers=bearer_headers(
                client,
                username=canonical.username,
                password=DEFAULT_OPERATOR_PASSWORD,
            ),
        ).status_code
        == 202
    )

    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/projects/{project_id}/embedding/refresh"][
        "post"
    ]["deprecated"] is True
