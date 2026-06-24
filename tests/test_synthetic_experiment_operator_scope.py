"""Regression: synthetic experiment results must carry ``operator_id``.

The G-2 evidence ledger (``analytics_reporting._build_g2_synthetic_experiment_summary``)
attributes a synthetic experiment result to a specific operator only when the
persisted ``metrics_json`` contains ``operator_id``. The upstream backtest item
(``synthetic_backtest.list_operators``) carries ``user_id`` but not
``operator_id``; ``mark_completed`` must mirror it so the ledger counts the
result as operator-scoped rather than slug-only (status ``missing``/``mixed_scope``).

This is an attribution fix only: it does not invent settled samples — the
``settled_count`` carried by the engine result is preserved verbatim (honesty
spec, CLAUDE.md §2).
"""

from __future__ import annotations

import json

from app.models.models import (
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.analytics_reporting import AnalyticsReportingService
from app.services.synthetic_experiment import SyntheticExperimentService


def _bootstrap_operator(client):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "synth-scope-operator",
            "email": "synth-scope@example.com",
            "full_name": "Synth Scope Operator",
            "company": "Synth Scope Corp",
            "password": "password123",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_completed_run(db, *, username: str) -> SyntheticExperimentRun:
    experiment = SyntheticExperiment(
        name="g2-operator-scope",
        description="operator-scoped synthetic evidence",
        params_json=json.dumps({"limit": 100}),
        operator_slugs_json=json.dumps([username]),
    )
    db.add(experiment)
    db.flush()
    run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_mark_completed_binds_operator_id_from_user_id(client, test_db):
    """``mark_completed`` should persist ``operator_id`` mirrored from ``user_id``."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    username = operator["username"]

    run = _create_completed_run(test_db, username=username)

    # The engine result item carries ``user_id`` (synthetic_backtest.list_operators)
    # but NOT ``operator_id`` — exactly the shape that produced slug-only evidence.
    result = {
        "operator_count": 1,
        "scenario": "base",
        "category": None,
        "start_at": None,
        "end_at": None,
        "limit": 100,
        "results": [
            {
                "user_id": operator_id,
                "username": username,
                "slug": username,
                "settled_count": 35,
                "would_have_won_count": 18,
                "win_rate_on_settled": 0.5,
                "settlement_items": [
                    {"project_id": 1, "category": "공사", "would_have_won": True},
                ],
            }
        ],
    }

    SyntheticExperimentService(test_db).mark_completed(run.id, result)

    stored = (
        test_db.query(SyntheticExperimentResult)
        .filter(SyntheticExperimentResult.run_id == run.id)
        .all()
    )
    assert len(stored) == 1
    metrics = json.loads(stored[0].metrics_json)
    # The fix: operator_id is mirrored from user_id and persisted.
    assert metrics["operator_id"] == operator_id
    # user_id is preserved alongside so existing consumers are unaffected.
    assert metrics["user_id"] == operator_id
    # Attribution only — the engine-supplied settled_count is untouched.
    assert metrics["settled_count"] == 35
    assert metrics["sample_status"] == "sufficient"


def test_g2_ledger_counts_operator_scoped_synthetic_after_mark_completed(
    client, test_db
):
    """G-2 ledger should count the synthetic result as operator-scoped, not missing."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    username = operator["username"]

    run = _create_completed_run(test_db, username=username)

    result = {
        "operator_count": 1,
        "scenario": "base",
        "results": [
            {
                "user_id": operator_id,
                "username": username,
                "slug": username,
                "settled_count": 35,
                "would_have_won_count": 18,
                "settlement_items": [],
            }
        ],
    }
    SyntheticExperimentService(test_db).mark_completed(run.id, result)

    # Resolve the operator User object for the ledger builder.
    from app.models.models import User

    operator_obj = test_db.query(User).filter(User.id == operator_id).first()
    assert operator_obj is not None

    summary = AnalyticsReportingService().build_g2_evidence_summary(
        test_db,
        window_days=365,
        recent_limit=5,
        operator=operator_obj,
    )
    synthetic = summary["synthetic_experiments"]

    # Before the fix this would be 0 (slug-only) with status "missing"/"mixed_scope".
    assert synthetic["operator_id_scoped_result_count"] == 1
    assert synthetic["slug_only_result_count"] == 0
    assert synthetic["status"] not in {"missing", "mixed_scope"}
    # A sufficient operator-scoped result makes this evidence family G-2 ready.
    assert synthetic["sufficient_operator_result_count"] == 1
    assert synthetic["status"] == "ready"
