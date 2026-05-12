"""Tests for operational dashboard reporting."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import CrawlJob, OperatorStrategyRun


def _bootstrap_operator(client):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "ops-dashboard-operator",
            "email": "ops-dashboard@example.com",
            "full_name": "Ops Dashboard Operator",
            "company": "Ops Dashboard Corp",
            "password": "password123",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_operations_dashboard_summarizes_crawl_and_strategy_health(client, test_db):
    """Operations dashboard should return card-ready crawl and strategy run metrics."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    now = datetime.now(UTC)

    test_db.add_all([
        CrawlJob(
            source="koneps",
            target_date="2026-05-12",
            status="completed",
            result_count=5,
            created_at=now - timedelta(days=2),
            completed_at=now - timedelta(days=2, minutes=-5),
        ),
        CrawlJob(
            source="koneps",
            target_date="2026-05-11",
            status="fallback_mock",
            result_count=2,
            error_message="browser unavailable",
            created_at=now - timedelta(days=1),
            completed_at=now - timedelta(days=1, minutes=-5),
        ),
        CrawlJob(
            source="koneps",
            target_date="2026-05-10",
            status="failed",
            result_count=0,
            error_message="timeout",
            created_at=now,
            completed_at=now,
        ),
        OperatorStrategyRun(
            operator_id=operator_id,
            trigger_source="manual_sync",
            status="completed",
            evaluated_project_count=10,
            selected_candidate_count=3,
            persisted_candidate_count=2,
            notification_count=1,
            created_at=now - timedelta(days=2),
            completed_at=now - timedelta(days=2, minutes=-2),
        ),
        OperatorStrategyRun(
            operator_id=operator_id,
            trigger_source="scheduled",
            status="failed",
            error_message="analysis failed",
            evaluated_project_count=4,
            selected_candidate_count=1,
            persisted_candidate_count=0,
            notification_count=0,
            created_at=now - timedelta(days=1),
            completed_at=now - timedelta(days=1),
        ),
        OperatorStrategyRun(
            operator_id=operator_id,
            trigger_source="scheduled",
            status="running",
            evaluated_project_count=0,
            selected_candidate_count=0,
            persisted_candidate_count=0,
            notification_count=0,
            created_at=now,
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 3})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["crawl"]["job_count"] == 3
    assert payload["crawl"]["completed_count"] == 1
    assert payload["crawl"]["fallback_count"] == 1
    assert payload["crawl"]["failed_count"] == 1
    assert payload["crawl"]["success_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert payload["crawl"]["failure_reason_breakdown"]["timeout"] == 1
    assert payload["crawl"]["total_result_count"] == 7

    assert payload["strategy"]["run_count"] == 3
    assert payload["strategy"]["completed_count"] == 1
    assert payload["strategy"]["failed_count"] == 1
    assert payload["strategy"]["running_count"] == 1
    assert payload["strategy"]["completion_rate"] == pytest.approx(0.3333, abs=0.0001)
    assert payload["strategy"]["selection_rate"] == pytest.approx(0.2857, abs=0.0001)
    assert payload["strategy"]["persistence_rate"] == pytest.approx(0.5, abs=0.0001)
    assert payload["strategy"]["notification_rate"] == pytest.approx(0.5, abs=0.0001)
    assert payload["strategy"]["failure_reason_breakdown"]["analysis failed"] == 1

    cards = {card["key"]: card for card in payload["cards"]}
    assert cards["crawl_success_rate"]["status"] == "watch"
    assert cards["strategy_completion_rate"]["status"] == "critical"
    assert cards["strategy_selection_rate"]["value"] == pytest.approx(0.2857, abs=0.0001)
