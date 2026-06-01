"""Tests for the synthetic Experiment Lab API + run lifecycle (Phase 1)."""

from __future__ import annotations

import pytest

from app.models.models import (
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.synthetic_backtest import SyntheticBacktestService


def _fake_run_for_all_result(**kwargs):
    return {
        "operator_count": 2,
        "category": kwargs.get("category"),
        "start_at": None,
        "end_at": None,
        "limit": kwargs.get("limit", 100),
        "scenario": kwargs.get("scenario", "base"),
        "results": [
            {
                "slug": "aggressive",
                "settled_count": 5,
                "would_have_won_count": 2,
                "win_rate_on_settled": 0.4,
                "settlement_items": [{"project_id": 1, "would_have_won": True}],
            },
            {
                "slug": "conservative",
                "settled_count": 4,
                "would_have_won_count": 1,
                "win_rate_on_settled": 0.25,
                "settlement_items": [],
            },
        ],
    }


@pytest.fixture
def patched_engine(monkeypatch):
    """Stub the heavy backtest engine so the lifecycle is exercised in isolation."""

    def fake_run_for_all(self, db, **kwargs):
        return _fake_run_for_all_result(**kwargs)

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", fake_run_for_all)


def _create_experiment(client, name="Q1 balanced sweep"):
    return client.post(
        "/api/v1/synthetic/experiments",
        json={
            "name": name,
            "description": "first phase experiment",
            "params": {"limit": 10, "scenario": "base"},
            "operator_slugs": ["aggressive"],
        },
    )


# --- create -------------------------------------------------------------------


def test_create_experiment_ok(client):
    response = _create_experiment(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] > 0
    assert body["name"] == "Q1 balanced sweep"
    assert body["params"]["scenario"] == "base"
    assert body["operator_slugs"] == ["aggressive"]
    assert body["runs"] == []


def test_create_experiment_validation_error(client):
    # Empty name violates min_length=1 -> 422.
    response = client.post(
        "/api/v1/synthetic/experiments",
        json={"name": "", "params": {"limit": 10}},
    )
    assert response.status_code == 422


def test_create_experiment_missing_params_is_422(client):
    response = client.post(
        "/api/v1/synthetic/experiments",
        json={"name": "no-params"},
    )
    assert response.status_code == 422


# --- list / detail ------------------------------------------------------------


def test_list_experiments(client):
    _create_experiment(client, name="exp-a")
    _create_experiment(client, name="exp-b")
    response = client.get("/api/v1/synthetic/experiments")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert {"exp-a", "exp-b"} <= names


def test_get_experiment_detail_ok(client):
    created = _create_experiment(client).json()
    response = client.get(f"/api/v1/synthetic/experiments/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_experiment_not_found(client):
    response = client.get("/api/v1/synthetic/experiments/999999")
    assert response.status_code == 404


# --- run trigger --------------------------------------------------------------


def test_trigger_run_ok(client, patched_engine):
    created = _create_experiment(client).json()
    response = client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["experiment_id"] == created["id"]
    assert body["task_id"]
    # Eager (memory://) execution finishes synchronously before the response.
    assert body["status"] in ("queued", "running", "completed")


def test_trigger_run_experiment_not_found(client, patched_engine):
    response = client.post("/api/v1/synthetic/experiments/999999/runs")
    assert response.status_code == 404


# --- run polling --------------------------------------------------------------


def test_poll_run_ok(client, patched_engine):
    created = _create_experiment(client).json()
    run = client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs").json()
    response = client.get(
        f"/api/v1/synthetic/experiments/{created['id']}/runs/{run['id']}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run["id"]
    assert body["status"] == "completed"
    slugs = {item["operator_slug"] for item in body["results"]}
    assert {"aggressive", "conservative"} == slugs
    # win_rate is a price-only estimate, passed through unchanged.
    for item in body["results"]:
        assert "win_rate_on_settled" in item["metrics"]


def test_poll_run_not_found(client, patched_engine):
    created = _create_experiment(client).json()
    response = client.get(f"/api/v1/synthetic/experiments/{created['id']}/runs/999999")
    assert response.status_code == 404


# --- eager persistence (run -> results land in the DB) ------------------------


def test_eager_run_persists_results(client, patched_engine, test_db):
    created = _create_experiment(client).json()
    run = client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs").json()

    db_run = (
        test_db.query(SyntheticExperimentRun)
        .filter(SyntheticExperimentRun.id == run["id"])
        .first()
    )
    assert db_run is not None
    assert db_run.status == "completed"
    assert db_run.started_at is not None
    assert db_run.finished_at is not None
    assert db_run.summary_json is not None

    results = (
        test_db.query(SyntheticExperimentResult)
        .filter(SyntheticExperimentResult.run_id == run["id"])
        .all()
    )
    assert len(results) == 2
    assert {r.operator_slug for r in results} == {"aggressive", "conservative"}


def test_eager_run_failure_marks_failed(client, monkeypatch, test_db):
    def boom(self, db, **kwargs):
        raise ValueError("no synthetic operators seeded")

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", boom)
    created = _create_experiment(client).json()
    # Eager task re-raises; the trigger may surface a 500, but the lifecycle hook
    # must still have marked the run failed.
    try:
        client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs")
    except Exception:
        pass

    db_run = (
        test_db.query(SyntheticExperimentRun)
        .filter(SyntheticExperimentRun.experiment_id == created["id"])
        .first()
    )
    assert db_run is not None
    assert db_run.status == "failed"
    assert db_run.error
