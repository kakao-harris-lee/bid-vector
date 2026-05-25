"""Tests for synthetic-operator seed + backtest API."""

from __future__ import annotations


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
