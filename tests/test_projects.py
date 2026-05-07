"""Tests for project endpoints"""
import pytest
from fastapi.testclient import TestClient


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


def test_get_nonexistent_project(client):
    """Test getting non-existent project"""
    response = client.get("/api/v1/projects/9999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
