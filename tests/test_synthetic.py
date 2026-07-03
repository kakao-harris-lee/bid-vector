"""Tests for synthetic-operator seed + backtest API."""

from __future__ import annotations

from app.services.synthetic_backtest import SyntheticBacktestService


class _FakePaperBacktestService:
    def __init__(self):
        self.calls = []

    def run_historical_backtest(self, db, **kwargs):
        self.calls.append(kwargs)
        return {
            "summary": {
                "candidate_count": 42,
                "paper_bid_count": 35,
                "settled_count": 35,
                "would_have_won_price_only_count": 12,
            },
            "settlements": [],
        }


def test_synthetic_backtest_reads_counts_from_backtest_summary():
    fake_backtest = _FakePaperBacktestService()
    service = SyntheticBacktestService(backtest_service=fake_backtest)
    service.list_operators = lambda db: [
        {
            "user_id": 101,
            "username": "synthetic-sample",
            "slug": "sample",
            "is_custom": False,
            "display_name": "Sample",
            "company": "Sample Co",
            "business_type": "service",
            "focus_categories": ["service"],
            "annual_revenue": 1.0,
            "capacity_score": 0.5,
            "bid_now_threshold": 0.75,
            "review_threshold": 0.55,
        }
    ]

    payload = service.run_for_all(
        object(),
        limit=1000,
        settle_actions=["bid_now", "review"],
        cutoff_hours_before_deadline=4,
        history_limit=120,
    )

    result = payload["results"][0]
    assert result["candidate_count"] == 42
    assert result["paper_bid_count"] == 35
    assert result["settled_count"] == 35
    assert fake_backtest.calls[0]["settle_actions"] == ["bid_now", "review"]
    assert fake_backtest.calls[0]["cutoff_hours_before_deadline"] == 4
    assert fake_backtest.calls[0]["history_limit"] == 120


def test_seed_synthetic_operators_is_idempotent(client):
    first = client.post("/api/v1/synthetic/operators/seed", json={})
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["seeded_count"] == len(first_payload["operators"])
    assert first_payload["seeded_count"] > 0
    first_user_ids = {item["user_id"] for item in first_payload["operators"]}

    # Second seed without purge → idempotent (no new rows)
    second = client.post("/api/v1/synthetic/operators/seed", json={})
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["purged_count"] == 0
    assert second_payload["seeded_count"] == first_payload["seeded_count"]
    second_user_ids = {item["user_id"] for item in second_payload["operators"]}
    assert second_user_ids == first_user_ids

    listed = client.get("/api/v1/synthetic/operators")
    assert listed.status_code == 200
    assert listed.json()["operator_count"] == first_payload["seeded_count"]


def test_list_synthetic_operators_empty_until_seeded(client):
    response = client.get("/api/v1/synthetic/operators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_count"] == 0
    assert payload["operators"] == []


def test_backtest_run_returns_per_operator_results_with_safe_win_rate(client):
    """Run on empty data: each operator must return a zero/None win rate without raising."""
    seed = client.post("/api/v1/synthetic/operators/seed", json={})
    assert seed.status_code == 200

    response = client.post(
        "/api/v1/synthetic/backtests/run",
        json={"limit": 10, "scenario": "base"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["operator_count"] == seed.json()["seeded_count"]
    assert len(payload["results"]) == payload["operator_count"]
    for result in payload["results"]:
        # win_rate_on_settled must be None (no settled rows) — never a numeric default
        assert result["win_rate_on_settled"] is None
        assert result["settled_count"] == 0
        assert result["would_have_won_count"] == 0


def test_backtest_run_requires_seeded_operators(client):
    response = client.post(
        "/api/v1/synthetic/backtests/run",
        json={"limit": 5, "scenario": "base"},
    )
    assert response.status_code == 404


def test_backtest_run_response_includes_settlement_sample_for_drilldown(client):
    """Per-operator result must carry the drilldown payload, even if empty."""
    seed = client.post("/api/v1/synthetic/operators/seed", json={})
    assert seed.status_code == 200

    response = client.post(
        "/api/v1/synthetic/backtests/run",
        json={"limit": 5, "scenario": "base"},
    )
    assert response.status_code == 200
    payload = response.json()
    for result in payload["results"]:
        # Drilldown contract: items list always present + count consistent
        assert isinstance(result["settlement_items"], list)
        assert result["settlement_sample_count"] == len(result["settlement_items"])


def test_backtest_run_async_endpoint_returns_pollable_task(client):
    """Async path queues a Celery task; status endpoint reflects it."""
    seed = client.post("/api/v1/synthetic/operators/seed", json={})
    assert seed.status_code == 200

    queued = client.post(
        "/api/v1/synthetic/backtests/run-async",
        json={"limit": 5, "scenario": "base"},
    )
    assert queued.status_code == 202, queued.text
    payload = queued.json()
    task_id = payload["task_id"]
    assert payload["poll_url"].endswith(task_id)
    assert payload["task_name"] == "jobs.run_synthetic_operator_backtest"

    status_response = client.get(f"/api/v1/synthetic/backtests/tasks/{task_id}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["task_id"] == task_id
    assert status_payload["task_name"] == "jobs.run_synthetic_operator_backtest"


def test_backtest_run_async_requires_seeded_operators(client):
    response = client.post(
        "/api/v1/synthetic/backtests/run-async",
        json={"limit": 5, "scenario": "base"},
    )
    assert response.status_code == 404
