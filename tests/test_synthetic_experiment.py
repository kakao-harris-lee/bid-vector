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
        assert item["sample_status"] == "insufficient_sample"
        assert item["sample_target"] == 30
        assert item["missing_settled_count"] > 0
        assert item["metrics"]["sample_status"] == "insufficient_sample"
    assert body["summary"]["sample_status"] == "insufficient_sample"
    assert body["summary"]["run_total_sample_target"] == 100
    report = body["summary"]["sample_report"]
    assert report["preset_name"] == "Q1 balanced sweep"
    assert report["report_status"] == "insufficient_sample"
    assert report["ready_for_repeatable_reporting"] is False
    assert report["synthetic_only"] is True
    lacking_dimensions = {item["dimension"] for item in report["lacking_groups"]}
    assert {"preset", "business_type"} <= lacking_dimensions


def test_poll_run_not_found(client, patched_engine):
    created = _create_experiment(client).json()
    response = client.get(f"/api/v1/synthetic/experiments/{created['id']}/runs/999999")
    assert response.status_code == 404


def test_poll_run_marks_sufficient_sample(client, monkeypatch):
    def fake_run_for_all(self, db, **kwargs):
        return {
            "operator_count": 3,
            "category": kwargs.get("category"),
            "start_at": None,
            "end_at": None,
            "limit": kwargs.get("limit", 100),
            "scenario": kwargs.get("scenario", "base"),
            "results": [
                {"slug": "a", "settled_count": 35, "settlement_items": []},
                {"slug": "b", "settled_count": 35, "settlement_items": []},
                {"slug": "c", "settled_count": 35, "settlement_items": []},
            ],
        }

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", fake_run_for_all)
    created = _create_experiment(client).json()
    run = client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs").json()

    response = client.get(
        f"/api/v1/synthetic/experiments/{created['id']}/runs/{run['id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["sample_status"] == "sufficient"
    assert body["summary"]["total_settled_count"] == 105
    assert {item["sample_status"] for item in body["results"]} == {"sufficient"}
    assert body["summary"]["sample_report"]["by_preset"][0]["sample_status"] == "sufficient"


def test_experiment_presets_are_listed_and_saved_idempotently(client):
    response = client.get("/api/v1/synthetic/experiments/presets")
    assert response.status_code == 200
    presets = {item["name"]: item for item in response.json()["presets"]}
    assert {
        "g1-construction-base-12m",
        "g1-service-base-12m",
        "g1-goods-base-12m",
        "g1-software-base-12m",
    } <= set(presets)
    assert presets["g1-construction-base-12m"]["experiment_id"] is None
    assert presets["g1-construction-base-12m"]["params"]["limit"] == 200

    first = client.post(
        "/api/v1/synthetic/experiments/presets/g1-construction-base-12m"
    )
    second = client.post(
        "/api/v1/synthetic/experiments/presets/g1-construction-base-12m"
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["name"] == "g1-construction-base-12m"
    assert first.json()["params"]["category"] == "construction"
    assert first.json()["operator_slugs"] == [
        "cn-small-gangwon",
        "cn-mid-gyeonggi",
        "cn-electric-telecom-national",
    ]
    service_preset = presets["g1-service-base-12m"]
    assert service_preset["params"]["category"] is None
    assert service_preset["operator_slugs"] == [
        "eng-supervision-busan",
        "eng-design-daejeon",
        "gs-cleaning-metro",
        "gs-security-national",
    ]

    listed_again = client.get("/api/v1/synthetic/experiments/presets").json()
    saved = {
        item["name"]: item for item in listed_again["presets"]
    }["g1-construction-base-12m"]
    assert saved["experiment_id"] == first.json()["id"]


def test_experiment_preset_unknown_returns_404(client):
    response = client.post("/api/v1/synthetic/experiments/presets/no-such-preset")
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
