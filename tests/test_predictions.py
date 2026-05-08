"""Tests for AI prediction endpoints"""
import pytest
from fastapi.testclient import TestClient

from app.models.models import PricePrediction, User


def test_price_prediction(client):
    """Test price prediction endpoint"""
    # Create a project first
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Get price prediction
    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 10000.0,
            "category": "software",
            "description": "Test project description",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "predicted_price" in data
    assert "confidence_score" in data
    assert 0 <= data["confidence_score"] <= 1


def test_bid_recommendation(client):
    """Test bid recommendation endpoint"""
    # Create a project
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Get bid recommendation
    response = client.post(
        "/api/v1/predictions/bid-recommendation",
        json={
            "project_id": project_id,
            "user_historical_data": {"average_bid": 8000.0, "win_rate": 0.5},
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "recommended_bid" in data
    assert "reasoning" in data


def test_document_analysis(client):
    """Test document analysis endpoint"""
    # Create a project
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Analyze document
    response = client.post(
        "/api/v1/predictions/analyze-document",
        json={
            "project_id": project_id,
            "document_content": "1. Must have user authentication\n2. Should support 10000 users\n3. Need API documentation",
            "document_type": "specification",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "key_requirements" in data
    assert "complexity_score" in data
    assert "estimated_effort" in data
    assert isinstance(data["key_requirements"], list)


def test_price_prediction_bootstraps_single_operator_account(client, test_db):
    """Prediction persistence should auto-bind to the singleton operator account."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Prediction Bootstrap Project",
            "description": "Operator persistence check",
            "requirements": "Need prediction persistence",
            "budget_estimate": 20000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 20000.0,
            "category": "software",
            "description": "Prediction bootstrap project",
        }
    )

    assert response.status_code == 200

    users = test_db.query(User).all()
    predictions = test_db.query(PricePrediction).all()

    assert len(users) == 1
    assert len(predictions) == 1
    assert predictions[0].user_id == users[0].id
