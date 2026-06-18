"""Tests for the synthetic Experiment Lab API + run lifecycle (Phase 1)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.models.models import (
    SyntheticExperiment,
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


def _sample_gap_summary(
    *,
    preset_name: str,
    lacking_groups: list[dict],
    category: str | None = None,
    limit: int = 200,
    synthetic_only: bool = True,
    non_synthetic_operator_slugs: list[str] | None = None,
):
    report_status = (
        "insufficient_sample" if synthetic_only else "canonical_synthetic_mixed"
    )
    return {
        "scenario": "base",
        "category": category,
        "start_at": "2025-01-01T00:00:00+00:00",
        "end_at": "2025-12-31T23:59:59+00:00",
        "limit": limit,
        "sample_report": {
            "preset_name": preset_name,
            "synthetic_only": synthetic_only,
            "non_synthetic_operator_slugs": non_synthetic_operator_slugs or [],
            "report_status": report_status,
            "ready_for_repeatable_reporting": False,
            "lacking_groups": lacking_groups,
        },
    }


def _persist_experiment_run(
    test_db,
    *,
    name: str,
    status: str = "completed",
    summary: dict | None = None,
    params: dict | None = None,
):
    experiment = SyntheticExperiment(
        name=name,
        params_json=json.dumps(
            params
            or {
                "start_at": "2025-01-01T00:00:00+00:00",
                "end_at": "2025-12-31T23:59:59+00:00",
                "limit": 200,
                "scenario": "base",
                "settle_actions": False,
            }
        ),
        operator_slugs_json="[]",
    )
    test_db.add(experiment)
    test_db.flush()
    run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status=status,
        finished_at=datetime(2026, 1, 1, 12, 0, 0),
        summary_json=json.dumps(summary) if summary is not None else None,
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)
    return run


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


def test_sample_gap_plan_aggregates_recent_completed_lacking_groups(client, test_db):
    first = _persist_experiment_run(
        test_db,
        name="g1-software-base-12m",
        summary=_sample_gap_summary(
            preset_name="g1-software-base-12m",
            category="software",
            lacking_groups=[
                {
                    "dimension": "preset",
                    "key": "g1-software-base-12m",
                    "settled_count": 82,
                    "sample_target": 100,
                    "missing_settled_count": 18,
                },
                {
                    "dimension": "category",
                    "key": "software",
                    "settled_count": 10,
                    "sample_target": 30,
                    "missing_settled_count": 20,
                },
                {
                    "dimension": "business_type",
                    "key": "software",
                    "settled_count": 9,
                    "sample_target": 30,
                    "missing_settled_count": 21,
                },
                {
                    "dimension": "budget_band",
                    "key": "lt_1eok",
                    "settled_count": 4,
                    "sample_target": 30,
                    "missing_settled_count": 26,
                },
            ],
        ),
    )
    second = _persist_experiment_run(
        test_db,
        name="g1-software-base-12m",
        summary=_sample_gap_summary(
            preset_name="g1-software-base-12m",
            category="software",
            lacking_groups=[
                {
                    "dimension": "category",
                    "key": "software",
                    "settled_count": 18,
                    "sample_target": 30,
                    "missing_settled_count": 12,
                },
            ],
        ),
    )

    response = client.get("/api/v1/synthetic/experiments/sample-gaps?max_runs=10")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scanned_completed_run_count"] == 2
    assert body["source_run_count"] == 2
    assert body["legacy_summary_run_count"] == 0
    assert body["warnings"] == []

    category_gap = next(
        item
        for item in body["gaps"]
        if item["dimension"] == "category" and item["key"] == "software"
    )
    assert category_gap["missing_settled_count"] == 20
    assert category_gap["total_missing_settled_count"] == 32
    assert category_gap["source_run_count"] == 2
    assert set(category_gap["related_run_ids"]) == {first.id, second.id}
    assert category_gap["recommendation"]["preset_name"] == "g1-software-base-12m"
    assert category_gap["recommendation"]["params"]["category"] == "software"
    assert {
        action["code"] for action in category_gap["recommendation"]["actions"]
    } >= {"rerun_related_preset", "expand_category_window"}


def test_sample_gap_plan_ignores_failed_and_flags_legacy_and_mixed_data(
    client, test_db
):
    _persist_experiment_run(
        test_db,
        name="failed-gap",
        status="failed",
        summary=_sample_gap_summary(
            preset_name="failed-gap",
            category="construction",
            lacking_groups=[
                {
                    "dimension": "category",
                    "key": "construction",
                    "settled_count": 0,
                    "sample_target": 30,
                    "missing_settled_count": 30,
                }
            ],
        ),
    )
    legacy = _persist_experiment_run(
        test_db,
        name="legacy-summary",
        summary={"sample_status": "insufficient_sample"},
    )
    mixed = _persist_experiment_run(
        test_db,
        name="g1-service-base-12m",
        summary=_sample_gap_summary(
            preset_name="g1-service-base-12m",
            category=None,
            synthetic_only=False,
            non_synthetic_operator_slugs=["operator"],
            lacking_groups=[
                {
                    "dimension": "business_type",
                    "key": "service",
                    "settled_count": 3,
                    "sample_target": 30,
                    "missing_settled_count": 27,
                }
            ],
        ),
    )

    response = client.get("/api/v1/synthetic/experiments/sample-gaps?max_runs=10")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scanned_completed_run_count"] == 2
    assert body["source_run_count"] == 1
    assert body["legacy_summary_run_count"] == 1
    assert all(item["key"] != "construction" for item in body["gaps"])

    warning_codes = {warning["code"] for warning in body["warnings"]}
    assert warning_codes == {
        "canonical_synthetic_mixed",
        "legacy_summary_without_sample_report",
    }
    legacy_warning = next(
        warning
        for warning in body["warnings"]
        if warning["code"] == "legacy_summary_without_sample_report"
    )
    assert legacy_warning["run_ids"] == [legacy.id]

    mixed_gap = next(
        item
        for item in body["gaps"]
        if item["dimension"] == "business_type" and item["key"] == "service"
    )
    assert mixed_gap["warnings"] == ["canonical_synthetic_mixed"]
    assert mixed_gap["related_run_ids"] == [mixed.id]
    assert mixed_gap["related_runs"][0]["synthetic_only"] is False
    assert {
        action["code"] for action in mixed_gap["recommendation"]["actions"]
    } >= {"review_operator_mix", "rerun_synthetic_only"}


def test_sample_gap_run_candidate_builds_existing_preset_payload(client, test_db):
    run = _persist_experiment_run(
        test_db,
        name="g1-software-base-12m",
        summary=_sample_gap_summary(
            preset_name="g1-software-base-12m",
            category="software",
            lacking_groups=[
                {
                    "dimension": "category",
                    "key": "software",
                    "settled_count": 18,
                    "sample_target": 30,
                    "missing_settled_count": 12,
                },
            ],
        ),
    )

    response = client.post(
        "/api/v1/synthetic/experiments/sample-gaps/candidates",
        json={"dimension": "category", "key": "software", "max_runs": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gap"]["dimension"] == "category"
    assert body["gap"]["key"] == "software"
    assert body["action_code"] == "rerun_related_preset"
    assert body["preset_name"] == "g1-software-base-12m"
    assert body["experiment_id"] == run.experiment_id
    assert body["latest_run_id"] == run.id
    assert body["next_step"] == "run_existing_experiment"
    assert body["run_allowed"] is True
    assert body["params"]["category"] == "software"
    assert body["experiment_payload"]["params"]["category"] == "software"
    assert body["operator_slugs"] == [
        "sw-small-seoul",
        "sw-mid-metro",
        "sw-large-national",
    ]
    assert body["experiment_payload"]["operator_slugs"] == body["operator_slugs"]


def test_sample_gap_run_candidate_blocks_mixed_data_before_run(client, test_db):
    _persist_experiment_run(
        test_db,
        name="g1-service-base-12m",
        summary=_sample_gap_summary(
            preset_name="g1-service-base-12m",
            category=None,
            synthetic_only=False,
            non_synthetic_operator_slugs=["operator"],
            lacking_groups=[
                {
                    "dimension": "business_type",
                    "key": "service",
                    "settled_count": 3,
                    "sample_target": 30,
                    "missing_settled_count": 27,
                }
            ],
        ),
    )

    response = client.post(
        "/api/v1/synthetic/experiments/sample-gaps/candidates",
        json={"dimension": "business_type", "key": "service", "max_runs": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action_code"] == "rerun_synthetic_only"
    assert body["next_step"] == "resolve_mixed_data"
    assert body["run_allowed"] is False
    assert body["blocked_by_warnings"] == ["canonical_synthetic_mixed"]
    assert body["warnings"] == ["canonical_synthetic_mixed"]
    assert "canonical/operator data mixed" in body["message"]


def test_sample_gap_run_candidate_returns_404_for_unknown_gap(client):
    response = client.post(
        "/api/v1/synthetic/experiments/sample-gaps/candidates",
        json={"dimension": "category", "key": "missing", "max_runs": 10},
    )

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
