from app.api.predictions import get_prediction_workflow


class StubPredictionWorkflow:
    def predict_project_price(self, db, request):
        return {
            "predicted_price": 9000.0,
            "price_range_min": 8800.0,
            "price_range_max": 9200.0,
            "confidence_score": 0.7,
            "model_version": "stub",
            "predictor_name": "stub_predictor",
            "predictor_family": "stub",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "predicted_bid_rate": 0.9,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "stub",
        }

    def analyze_project_document(self, db, request):
        return {
            "key_requirements": ["stub requirement"],
            "complexity_score": 0.2,
            "estimated_effort": 1.0,
            "risks": [],
        }


def test_prediction_routes_use_workflow_dependency_for_price_prediction(client):
    client.app.dependency_overrides[get_prediction_workflow] = lambda: StubPredictionWorkflow()
    try:
        project = client.post(
            "/api/v1/projects/",
            json={
                "title": "Price Dependency Project",
                "description": "desc",
                "requirements": "req",
                "budget_estimate": 10_000.0,
                "category": "software",
            },
        ).json()

        response = client.post(
            "/api/v1/predictions/price",
            json={
                "project_id": project["id"],
                "budget_estimate": 10_000.0,
                "category": "software",
                "description": "desc",
            },
        )

        assert response.status_code == 200
        assert response.json()["predictor_name"] == "stub_predictor"
    finally:
        client.app.dependency_overrides.clear()


def test_prediction_routes_use_workflow_dependency_for_document_analysis(client):
    client.app.dependency_overrides[get_prediction_workflow] = lambda: StubPredictionWorkflow()
    try:
        project = client.post(
            "/api/v1/projects/",
            json={
                "title": "Document Project",
                "description": "desc",
                "requirements": "req",
                "budget_estimate": 10_000.0,
                "category": "software",
            },
        ).json()

        response = client.post(
            "/api/v1/predictions/analyze-document",
            json={
                "project_id": project["id"],
                "document_content": "content",
                "document_type": "specification",
            },
        )

        assert response.status_code == 200
        assert response.json()["key_requirements"] == ["stub requirement"]
    finally:
        client.app.dependency_overrides.clear()
