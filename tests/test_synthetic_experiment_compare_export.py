"""Tests for Phase 4 of the synthetic Experiment Lab.

Covers the A/B run comparison endpoint, the per-run CSV export, and the single
custom-operator GET (edit-form prefill). The heavy backtest engine is stubbed so
two runs land deterministic, comparable per-operator metrics.

``win_rate_on_settled`` is a PRICE-ONLY estimate (NOT an actual award); the tests
assert it is carried through unchanged and that a one-sided ``None`` yields a
``None`` delta.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.services.synthetic_backtest import SyntheticBacktestService


def _result_payload(results, **kwargs):
    return {
        "operator_count": len(results),
        "category": kwargs.get("category"),
        "start_at": None,
        "end_at": None,
        "limit": kwargs.get("limit", 100),
        "scenario": kwargs.get("scenario", "base"),
        "results": results,
    }


# Two scenario fixtures so run-A and run-B differ per operator. ``solo`` exists
# only in run A; ``newcomer`` only in run B (drives only_in_a / only_in_b).
_RUN_A_RESULTS = [
    {
        "slug": "aggressive",
        "business_type": "construction",
        "candidate_count": 10,
        "paper_bid_count": 8,
        "settled_count": 5,
        "skipped_by_strategy_count": 2,
        "would_have_won_price_only_count": 2,
        "win_rate_on_settled": 0.4,
        "win_rate_on_candidates": 0.2,
        "bid_submission_rate": 0.8,
        "average_absolute_bid_rate_error": 0.05,
        "average_absolute_amount_error_rate": 0.03,
        "settlement_items": [],
    },
    {
        "slug": "conservative",
        "business_type": "service",
        "candidate_count": 6,
        "paper_bid_count": 3,
        "settled_count": 0,  # no settled rows -> win_rate None on this side
        "skipped_by_strategy_count": 3,
        "would_have_won_price_only_count": 0,
        "win_rate_on_settled": None,
        "win_rate_on_candidates": 0.0,
        "bid_submission_rate": 0.5,
        "average_absolute_bid_rate_error": None,
        "average_absolute_amount_error_rate": None,
        "settlement_items": [],
    },
    {
        "slug": "solo",
        "business_type": "service",
        "candidate_count": 1,
        "settled_count": 1,
        "win_rate_on_settled": 1.0,
        "bid_submission_rate": 1.0,
        "average_absolute_bid_rate_error": 0.01,
        "settlement_items": [],
    },
]

_RUN_B_RESULTS = [
    {
        "slug": "aggressive",
        "business_type": "construction",
        "candidate_count": 10,
        "paper_bid_count": 9,
        "settled_count": 6,
        "skipped_by_strategy_count": 1,
        "would_have_won_price_only_count": 4,
        "win_rate_on_settled": 0.6,
        "win_rate_on_candidates": 0.4,
        "bid_submission_rate": 0.9,
        "average_absolute_bid_rate_error": 0.02,
        "average_absolute_amount_error_rate": 0.01,
        "settlement_items": [],
    },
    {
        "slug": "conservative",
        "business_type": "service",
        "candidate_count": 6,
        "paper_bid_count": 4,
        "settled_count": 3,
        "skipped_by_strategy_count": 2,
        "would_have_won_price_only_count": 1,
        "win_rate_on_settled": 0.3333,  # numeric here; None on run A
        "win_rate_on_candidates": 0.16,
        "bid_submission_rate": 0.66,
        "average_absolute_bid_rate_error": 0.04,
        "average_absolute_amount_error_rate": 0.02,
        "settlement_items": [],
    },
    {
        "slug": "newcomer",
        "business_type": "goods",
        "candidate_count": 2,
        "settled_count": 2,
        "win_rate_on_settled": 0.5,
        "bid_submission_rate": 1.0,
        "average_absolute_bid_rate_error": 0.07,
        "settlement_items": [],
    },
]


@pytest.fixture
def two_runs(client, monkeypatch):
    """Create one experiment with two completed runs (A then B) and return ids."""
    payloads = iter([_RUN_A_RESULTS, _RUN_B_RESULTS])

    def fake_run_for_all(self, db, **kwargs):
        return _result_payload(next(payloads), **kwargs)

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", fake_run_for_all)

    created = client.post(
        "/api/v1/synthetic/experiments",
        json={
            "name": "phase4 compare",
            "params": {"limit": 10, "scenario": "base"},
            "operator_slugs": [],
        },
    ).json()
    experiment_id = created["id"]

    run_a = client.post(f"/api/v1/synthetic/experiments/{experiment_id}/runs").json()
    run_b = client.post(f"/api/v1/synthetic/experiments/{experiment_id}/runs").json()
    return {
        "experiment_id": experiment_id,
        "run_a_id": run_a["id"],
        "run_b_id": run_b["id"],
    }


# --- compare ------------------------------------------------------------------


def test_compare_runs_ok_with_delta(client, two_runs):
    response = client.get(
        "/api/v1/synthetic/experiments/compare",
        params={"run_a": two_runs["run_a_id"], "run_b": two_runs["run_b_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["run_a"]["id"] == two_runs["run_a_id"]
    assert body["run_b"]["id"] == two_runs["run_b_id"]
    assert body["run_a"]["experiment_id"] == two_runs["experiment_id"]

    # Slug intersection only (aggressive, conservative); solo/newcomer are one-sided.
    by_slug = {op["operator_slug"]: op for op in body["operators"]}
    assert set(by_slug) == {"aggressive", "conservative"}
    assert body["only_in_a"] == ["solo"]
    assert body["only_in_b"] == ["newcomer"]

    # aggressive: B - A deltas (b higher across the board).
    aggressive = by_slug["aggressive"]
    assert aggressive["a"]["win_rate_on_settled"] == 0.4
    assert aggressive["b"]["win_rate_on_settled"] == 0.6
    assert aggressive["delta"]["win_rate_on_settled"] == pytest.approx(0.2)
    assert aggressive["delta"]["bid_submission_rate"] == pytest.approx(0.9 - 0.8)
    assert aggressive["delta"]["average_absolute_bid_rate_error"] == pytest.approx(
        0.02 - 0.05
    )
    # settled_count is carried per-side but not part of the delta payload.
    assert aggressive["a"]["settled_count"] == 5
    assert aggressive["b"]["settled_count"] == 6
    assert "settled_count" not in aggressive["delta"]


def test_compare_runs_one_sided_win_rate_delta_is_null(client, two_runs):
    response = client.get(
        "/api/v1/synthetic/experiments/compare",
        params={"run_a": two_runs["run_a_id"], "run_b": two_runs["run_b_id"]},
    )
    assert response.status_code == 200
    by_slug = {op["operator_slug"]: op for op in response.json()["operators"]}

    # conservative has win_rate None on run A -> delta must be None even though
    # run B has a numeric win_rate.
    conservative = by_slug["conservative"]
    assert conservative["a"]["win_rate_on_settled"] is None
    assert conservative["b"]["win_rate_on_settled"] == 0.3333
    assert conservative["delta"]["win_rate_on_settled"] is None
    # But bid_submission_rate is present on both sides -> numeric delta.
    assert conservative["delta"]["bid_submission_rate"] == pytest.approx(0.66 - 0.5)


def test_compare_runs_missing_run_returns_404(client, two_runs):
    response = client.get(
        "/api/v1/synthetic/experiments/compare",
        params={"run_a": two_runs["run_a_id"], "run_b": 999999},
    )
    assert response.status_code == 404


def test_compare_runs_requires_both_query_params(client):
    response = client.get(
        "/api/v1/synthetic/experiments/compare",
        params={"run_a": 1},
    )
    assert response.status_code == 422


# --- CSV export ---------------------------------------------------------------


def test_export_run_csv_ok(client, two_runs):
    response = client.get(
        f"/api/v1/synthetic/experiments/{two_runs['experiment_id']}"
        f"/runs/{two_runs['run_a_id']}/export.csv"
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert (
        f'filename="experiment-{two_runs["experiment_id"]}'
        f'-run-{two_runs["run_a_id"]}.csv"'
    ) in disposition

    reader = csv.DictReader(io.StringIO(response.text))
    assert reader.fieldnames[0] == "operator_slug"
    assert "win_rate_on_settled" in reader.fieldnames
    rows = list(reader)
    by_slug = {row["operator_slug"]: row for row in rows}
    assert {"aggressive", "conservative", "solo"} == set(by_slug)

    aggressive = by_slug["aggressive"]
    assert aggressive["business_type"] == "construction"
    assert aggressive["settled_count"] == "5"
    assert aggressive["win_rate_on_settled"] == "0.4"

    # None metrics serialize to an empty cell (conservative has no error metrics).
    conservative = by_slug["conservative"]
    assert conservative["win_rate_on_settled"] == ""
    assert conservative["average_absolute_bid_rate_error"] == ""


def test_export_run_csv_missing_run_returns_404(client, two_runs):
    response = client.get(
        f"/api/v1/synthetic/experiments/{two_runs['experiment_id']}"
        f"/runs/999999/export.csv"
    )
    assert response.status_code == 404


def test_export_run_csv_header_only_for_resultless_run(client, monkeypatch):
    """A completed-but-empty run exports a header-only CSV (HTTP 200, no rows)."""

    def fake_run_for_all(self, db, **kwargs):
        return _result_payload([], **kwargs)

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", fake_run_for_all)
    created = client.post(
        "/api/v1/synthetic/experiments",
        json={"name": "empty", "params": {"limit": 5}},
    ).json()
    run = client.post(f"/api/v1/synthetic/experiments/{created['id']}/runs").json()

    response = client.get(
        f"/api/v1/synthetic/experiments/{created['id']}" f"/runs/{run['id']}/export.csv"
    )
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.text))
    assert reader.fieldnames[0] == "operator_slug"
    assert list(reader) == []


# --- single custom-operator GET (edit-form prefill) ---------------------------


def test_get_custom_operator_ok(client):
    created = client.post(
        "/api/v1/synthetic/custom-operators",
        json={
            "name": "Phase4 Co",
            "business_type": "service",
            "focus_categories": ["software"],
            "bid_now_threshold": 0.7,
        },
    ).json()
    slug = created["slug"]

    response = client.get(f"/api/v1/synthetic/custom-operators/{slug}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slug"] == slug
    assert body["is_custom"] is True
    assert body["display_name"] == "Phase4 Co"
    assert body["focus_categories"] == ["software"]
    assert body["bid_now_threshold"] == pytest.approx(0.7)


def test_get_custom_operator_missing_returns_404(client):
    response = client.get("/api/v1/synthetic/custom-operators/custom-does-not-exist")
    assert response.status_code == 404


def test_get_custom_operator_preset_is_protected(client):
    """A preset slug (no ``custom-`` prefix) is protected (400), not returned."""
    client.post("/api/v1/synthetic/operators/seed", json={})
    operators = client.get("/api/v1/synthetic/operators").json()["operators"]
    preset = next(op for op in operators if not op["slug"].startswith("custom-"))

    response = client.get(f"/api/v1/synthetic/custom-operators/{preset['slug']}")
    assert response.status_code == 400
