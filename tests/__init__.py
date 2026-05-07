"""Tests for authentication endpoints"""
import pytest
from fastapi.testclient import TestClient


def test_register_user(client):
    """Test user registration"""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "full_name": "Test User",
            "company": "Test Company",
            "password": "password123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert data["email"] == "test@example.com"


def test_register_duplicate_user(client):
    """Test duplicate user registration"""
    user_data = {
        "username": "testuser",
        "email": "test@example.com",
        "full_name": "Test User",
        "company": "Test Company",
        "password": "password123",
    }

    # First registration
    response1 = client.post("/api/v1/auth/register", json=user_data)
    assert response1.status_code == 200

    # Duplicate registration
    response2 = client.post("/api/v1/auth/register", json=user_data)
    assert response2.status_code == 400
    assert "already exists" in response2.json()["detail"]


@pytest.mark.skip(reason="Requires authentication implementation")
def test_login_user(client):
    """Test user login"""
    # Register first
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "full_name": "Test User",
            "password": "password123",
        }
    )

    # Login
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "password123"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
