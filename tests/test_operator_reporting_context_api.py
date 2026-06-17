"""Operator-context isolation for reporting read routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    DecisionExperimentRun,
    OperatorStrategy,
    PricePrediction,
    Project,
    TenderResult,
    User,
)


REPORTING_PATHS = (
    "/api/v1/analytics/prediction-feedback",
    "/api/v1/analytics/prediction-observability",
    "/api/v1/analytics/accuracy-report",
    "/api/v1/analytics/decision-funnel",
    "/api/v1/analytics/decision-insights",
    "/api/v1/analytics/recommendation-feedback-labels",
    "/api/v1/analytics/decision-recommendations",
    "/api/v1/operations/decision-samples",
)


def _create_user(
    test_db,
    *,
    username: str,
    password: str = "password123",
    is_admin: bool = False,
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=f"{username} Co",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=is_admin,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _login(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_users(test_db) -> tuple[User, User, User]:
    canonical = _create_user(test_db, username="operator")
    synthetic = _create_user(test_db, username="synthetic-sw-small-seoul")
    other = _create_user(test_db, username="other-operator")
    return canonical, synthetic, other


def _seed_project(test_db, *, title: str, notice_number: str) -> Project:
    project = Project(
        title=title,
        description="reporting context",
        requirements="",
        budget_estimate=100_000_000.0,
        category="software",
        notice_number=notice_number,
        demand_agency="Agency",
        deadline=utc_now() + timedelta(days=1),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _seed_reporting_row(
    test_db,
    *,
    operator: User,
    title: str,
    notice_number: str,
    verdict: str = "useful",
    created_at: datetime | None = None,
) -> tuple[Project, BidDecisionRecord]:
    created_at = created_at or utc_now()
    project = _seed_project(test_db, title=title, notice_number=notice_number)
    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="submitted",
        initial_action="bid_now",
        initial_decision_status="planned",
        first_decided_at=created_at - timedelta(hours=2),
        recommended_amount=91_000_000.0,
        probability_score=0.82,
        matched_score=0.8,
        priority_score=0.78,
        competitiveness_score=0.64,
        budget_capture_score=0.7,
        expected_margin_score=0.66,
        execution_complexity_score=0.35,
        reasoning=f"{title} reasoning",
        created_at=created_at,
        updated_at=created_at,
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)

    prediction = PricePrediction(
        user_id=operator.id,
        project_id=project.id,
        predicted_price=92_000_000.0,
        price_range_min=90_000_000.0,
        price_range_max=94_000_000.0,
        confidence_score=0.7,
        predictor_name=f"{operator.username}-predictor",
        predictor_family="statistical",
        pricing_mode="heuristic",
        created_at=created_at,
    )
    result = TenderResult(
        project_id=project.id,
        winning_company=f"{operator.username} winner",
        winning_amount=90_500_000.0,
        winning_rate=90.5,
        result_status="awarded",
        announced_at=created_at,
    )
    feedback = Analytics(
        user_id=operator.id,
        event_type="recommendation_feedback",
        event_data=json.dumps(
            {
                "decision_record_id": decision.id,
                "project_id": project.id,
                "verdict": verdict,
            }
        ),
        timestamp=created_at + timedelta(minutes=10),
    )
    test_db.add_all([prediction, result, feedback])
    test_db.commit()
    return project, decision


def _decision_experiment_payload(*, started_at: datetime | None = None) -> dict:
    return {
        "experiment_key": "exp-review-threshold-tighten",
        "recommendation_key": "review-threshold-tighten",
        "priority_rank": 1,
        "title": "Review threshold context test",
        "hypothesis": "Raising the review threshold should improve review quality.",
        "suggested_change": "Raise REVIEW_THRESHOLD by a small increment.",
        "target_metric": "review_submission_rate",
        "expected_direction": "increase",
        "success_criteria": "Review submission rate improves.",
        "guardrail_metric": "overall_submission_rate",
        "minimum_decision_sample": 1,
        "duration_days": 7,
        "baseline_days": 7,
        "rollback_trigger": "Overall submission rate drops.",
        "started_at": (started_at or utc_now()).isoformat(),
    }


def _experiment_snapshot(*, decision_count: int = 1, submitted_count: int = 1) -> dict:
    now = utc_now()
    overall_rate = (
        round(submitted_count / decision_count, 4)
        if decision_count > 0
        else None
    )
    return {
        "window_start": (now - timedelta(days=7)).isoformat(),
        "window_end": now.isoformat(),
        "decision_count": decision_count,
        "submitted_count": submitted_count,
        "active_pending_count": max(decision_count - submitted_count, 0),
        "overall_submission_rate": overall_rate,
        "workflow_submission_rate": overall_rate,
        "bid_now_submission_rate": overall_rate,
        "review_submission_rate": overall_rate,
        "auto_submission_rate": None,
        "provided_submission_rate": overall_rate,
        "best_category": "software" if decision_count else None,
        "best_category_submission_rate": overall_rate,
        "worst_category": "software" if decision_count else None,
        "worst_category_submission_rate": overall_rate,
    }


def _seed_experiment_run(
    test_db,
    *,
    operator: User,
    title: str,
    experiment_key: str = "exp-review-threshold-tighten",
    recommendation_key: str = "review-threshold-tighten",
    status: str = "completed",
    outcome: str | None = "success",
    notes: str = "",
) -> DecisionExperimentRun:
    now = utc_now()
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key=experiment_key,
        recommendation_key=recommendation_key,
        status=status,
        outcome=outcome,
        priority_rank=1,
        title=title,
        hypothesis=f"{title} hypothesis",
        suggested_change=f"{title} change",
        target_metric="review_submission_rate",
        expected_direction="increase",
        success_criteria="metric improves",
        guardrail_metric="overall_submission_rate",
        minimum_decision_sample=1,
        duration_days=7,
        baseline_days=7,
        rollback_trigger="guardrail drops",
        notes=notes,
        baseline_summary=json.dumps(_experiment_snapshot()),
        latest_evaluation=json.dumps({}),
        started_at=now - timedelta(days=3),
        ended_at=now - timedelta(days=1) if status == "completed" else None,
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(days=1),
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)
    return run


def _ensure_strategy(
    test_db,
    *,
    operator: User,
    review_threshold: float = 0.45,
    bid_now_threshold: float = 0.7,
) -> OperatorStrategy:
    strategy = OperatorStrategy(
        user_id=operator.id,
        focus_categories="",
        bid_now_threshold=bid_now_threshold,
        review_threshold=review_threshold,
        auto_workload_penalty_multiplier=1.0,
        category_priority_overrides="{}",
    )
    test_db.add(strategy)
    test_db.commit()
    test_db.refresh(strategy)
    return strategy


def test_reporting_routes_allow_canonical_to_target_synthetic(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)
    headers = _login(client, "operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": synthetic.id, "days": 30},
            headers=headers,
        )
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert payload["operator_id"] == synthetic.id, path
        assert payload["current_operator_id"] == synthetic.id, path
        assert payload["current_operator_username"] == "synthetic-sw-small-seoul", path


def test_reporting_routes_403_for_non_privileged_cross_operator(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)
    headers = _login(client, "other-operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": synthetic.id},
            headers=headers,
        )
        assert response.status_code == 403, path


def test_reporting_routes_403_for_unauthenticated_cross_operator(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)

    for path in REPORTING_PATHS:
        response = client.get(path, params={"operator_id": synthetic.id})
        assert response.status_code == 403, path


def test_reporting_routes_404_for_unknown_operator_id(client, test_db):
    _seed_users(test_db)
    headers = _login(client, "operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": 999_999},
            headers=headers,
        )
        assert response.status_code == 404, path


def test_synthetic_reporting_responses_do_not_include_canonical_rows(
    client, test_db
):
    canonical, synthetic, _other = _seed_users(test_db)
    _canonical_project, canonical_decision = _seed_reporting_row(
        test_db,
        operator=canonical,
        title="canonical reporting row",
        notice_number="CANON-001",
        verdict="not_useful",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    synthetic_project, synthetic_decision = _seed_reporting_row(
        test_db,
        operator=synthetic,
        title="synthetic reporting row",
        notice_number="SYNTH-001",
        verdict="useful",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    headers = _login(client, "operator")
    params = {"operator_id": synthetic.id, "days": 30, "limit": 20}

    insights = client.get(
        "/api/v1/analytics/decision-insights",
        params=params,
        headers=headers,
    )
    assert insights.status_code == 200, insights.text
    insight_ids = {
        item["decision_record_id"] for item in insights.json()["recent_decisions"]
    }
    assert insight_ids == {synthetic_decision.id}
    assert canonical_decision.id not in insight_ids

    funnel = client.get(
        "/api/v1/analytics/decision-funnel",
        params=params,
        headers=headers,
    )
    assert funnel.status_code == 200, funnel.text
    funnel_ids = {
        item["decision_record_id"] for item in funnel.json()["recent_submissions"]
    }
    assert funnel_ids == {synthetic_decision.id}

    labels = client.get(
        "/api/v1/analytics/recommendation-feedback-labels",
        params=params,
        headers=headers,
    )
    assert labels.status_code == 200, labels.text
    label_ids = {item["decision_record_id"] for item in labels.json()["items"]}
    assert label_ids == {synthetic_decision.id}

    samples = client.get(
        "/api/v1/operations/decision-samples",
        params=params,
        headers=headers,
    )
    assert samples.status_code == 200, samples.text
    sample_ids = {item["decision_record_id"] for item in samples.json()["samples"]}
    assert sample_ids == {synthetic_decision.id}

    feedback = client.get(
        "/api/v1/analytics/prediction-feedback",
        params=params,
        headers=headers,
    )
    assert feedback.status_code == 200, feedback.text
    feedback_items = feedback.json()["items"]
    assert [item["project_id"] for item in feedback_items] == [synthetic_project.id]
    assert all("canonical" not in item["project_title"] for item in feedback_items)

    observability = client.get(
        "/api/v1/analytics/prediction-observability",
        params=params,
        headers=headers,
    )
    assert observability.status_code == 200, observability.text
    observability_payload = observability.json()
    assert observability_payload["prediction_count"] == 1
    assert observability_payload["operator_id"] == synthetic.id
    assert observability_payload["current_operator_id"] == synthetic.id

    accuracy = client.get(
        "/api/v1/analytics/accuracy-report",
        params=params,
        headers=headers,
    )
    assert accuracy.status_code == 200, accuracy.text
    accuracy_items = accuracy.json()["items"]
    assert [item["project_id"] for item in accuracy_items] == [synthetic_project.id]


def test_decision_experiment_routes_allow_canonical_to_target_synthetic(
    client, test_db
):
    canonical, synthetic, _other = _seed_users(test_db)
    _ensure_strategy(test_db, operator=canonical, review_threshold=0.31)
    synthetic_strategy = _ensure_strategy(
        test_db,
        operator=synthetic,
        review_threshold=0.45,
    )
    headers = _login(client, "operator")
    now = utc_now()

    _seed_reporting_row(
        test_db,
        operator=canonical,
        title="canonical experiment baseline",
        notice_number="CANON-EXP-001",
        created_at=now - timedelta(days=1),
    )
    _seed_reporting_row(
        test_db,
        operator=synthetic,
        title="synthetic experiment baseline",
        notice_number="SYNTH-EXP-001",
        created_at=now - timedelta(days=1),
    )
    canonical_run = _seed_experiment_run(
        test_db,
        operator=canonical,
        title="canonical should not leak",
    )

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        params={"operator_id": synthetic.id},
        headers=headers,
        json=_decision_experiment_payload(started_at=now),
    )
    assert create_response.status_code == 200, create_response.text
    create_payload = create_response.json()
    assert create_payload["operator_id"] == synthetic.id
    assert create_payload["current_operator_id"] == synthetic.id
    assert create_payload["current_operator_username"] == synthetic.username
    assert create_payload["run"]["operator_id"] == synthetic.id
    assert create_payload["baseline_summary"]["decision_count"] == 1
    run_id = create_payload["run"]["id"]

    list_response = client.get(
        "/api/v1/analytics/decision-experiments",
        params={"operator_id": synthetic.id, "limit": 10},
        headers=headers,
    )
    assert list_response.status_code == 200, list_response.text
    list_payload = list_response.json()
    assert list_payload["operator_id"] == synthetic.id
    assert list_payload["current_operator_id"] == synthetic.id
    listed_ids = {item["id"] for item in list_payload["runs"]}
    assert run_id in listed_ids
    assert canonical_run.id not in listed_ids
    assert all(item["operator_id"] == synthetic.id for item in list_payload["runs"])

    blocked_detail = client.get(
        f"/api/v1/analytics/decision-experiments/{canonical_run.id}",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert blocked_detail.status_code == 404

    detail_response = client.get(
        f"/api/v1/analytics/decision-experiments/{run_id}",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["current_operator_id"] == synthetic.id

    evaluate_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run_id}/evaluate",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert evaluate_response.status_code == 202, evaluate_response.text

    from app.tasks.jobs import (
        reevaluate_decision_experiment as reevaluate_decision_experiment_task,
    )

    task_payload = reevaluate_decision_experiment_task.run(
        experiment_run_id=run_id,
        operator_id=synthetic.id,
    )
    assert task_payload["current_operator_id"] == synthetic.id
    assert task_payload["run"]["operator_id"] == synthetic.id

    patch_response = client.patch(
        f"/api/v1/analytics/decision-experiments/{run_id}",
        params={"operator_id": synthetic.id},
        headers=headers,
        json={"status": "completed", "outcome": "success"},
    )
    assert patch_response.status_code == 200, patch_response.text
    assert patch_response.json()["current_operator_id"] == synthetic.id

    apply_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run_id}/apply-thresholds",
        params={"operator_id": synthetic.id},
        headers=headers,
        json={"dry_run": False},
    )
    assert apply_response.status_code == 200, apply_response.text
    apply_payload = apply_response.json()
    assert apply_payload["operator_id"] == synthetic.id
    assert apply_payload["current_operator_id"] == synthetic.id
    assert apply_payload["threshold_updates"][0]["previous_value"] == 0.45
    assert apply_payload["threshold_updates"][0]["suggested_value"] == 0.49

    test_db.refresh(synthetic_strategy)
    canonical_strategy = (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == canonical.id)
        .one()
    )
    assert synthetic_strategy.review_threshold == 0.49
    assert canonical_strategy.review_threshold == 0.31

    strategy_run = _seed_experiment_run(
        test_db,
        operator=synthetic,
        title="synthetic strategy apply",
        experiment_key="exp-workload-auto-calibration",
        recommendation_key="workload-auto-calibration",
    )
    apply_strategy_response = client.post(
        f"/api/v1/analytics/decision-experiments/{strategy_run.id}/apply-strategy",
        params={"operator_id": synthetic.id},
        headers=headers,
        json={"dry_run": False},
    )
    assert apply_strategy_response.status_code == 200, apply_strategy_response.text
    apply_strategy_payload = apply_strategy_response.json()
    assert apply_strategy_payload["operator_id"] == synthetic.id
    assert apply_strategy_payload["current_operator_id"] == synthetic.id
    assert (
        apply_strategy_payload["strategy_updates"][0]["parameter"]
        == "auto_workload_penalty_multiplier"
    )
    assert apply_strategy_payload["strategy_updates"][0]["previous_value"] == 1.0
    assert apply_strategy_payload["strategy_updates"][0]["suggested_value"] == 0.85

    test_db.refresh(synthetic_strategy)
    test_db.refresh(canonical_strategy)
    assert synthetic_strategy.auto_workload_penalty_multiplier == 0.85
    assert canonical_strategy.auto_workload_penalty_multiplier == 1.0


def test_decision_experiment_routes_allow_admin_to_target_synthetic(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)
    _create_user(test_db, username="admin-operator", is_admin=True)
    headers = _login(client, "admin-operator")

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        params={"operator_id": synthetic.id},
        headers=headers,
        json=_decision_experiment_payload(),
    )
    assert create_response.status_code == 200, create_response.text
    run_id = create_response.json()["run"]["id"]

    detail_response = client.get(
        f"/api/v1/analytics/decision-experiments/{run_id}",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    payload = detail_response.json()
    assert payload["operator_id"] == synthetic.id
    assert payload["current_operator_id"] == synthetic.id
    assert payload["run"]["operator_id"] == synthetic.id


def test_decision_experiment_routes_403_for_non_privileged_cross_operator(
    client, test_db
):
    _canonical, synthetic, _other = _seed_users(test_db)
    run = _seed_experiment_run(test_db, operator=synthetic, title="synthetic run")
    headers = _login(client, "other-operator")
    params = {"operator_id": synthetic.id}

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        params=params,
        headers=headers,
        json=_decision_experiment_payload(),
    )
    assert create_response.status_code == 403

    list_response = client.get(
        "/api/v1/analytics/decision-experiments",
        params=params,
        headers=headers,
    )
    assert list_response.status_code == 403

    detail_response = client.get(
        f"/api/v1/analytics/decision-experiments/{run.id}",
        params=params,
        headers=headers,
    )
    assert detail_response.status_code == 403

    patch_response = client.patch(
        f"/api/v1/analytics/decision-experiments/{run.id}",
        params=params,
        headers=headers,
        json={"append_note": "should not apply"},
    )
    assert patch_response.status_code == 403

    evaluate_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/evaluate",
        params=params,
        headers=headers,
    )
    assert evaluate_response.status_code == 403

    apply_thresholds_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/apply-thresholds",
        params=params,
        headers=headers,
        json={"dry_run": True},
    )
    assert apply_thresholds_response.status_code == 403

    apply_strategy_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/apply-strategy",
        params=params,
        headers=headers,
        json={"dry_run": True},
    )
    assert apply_strategy_response.status_code == 403


def test_decision_experiment_routes_404_for_unknown_operator_id(client, test_db):
    canonical, _synthetic, _other = _seed_users(test_db)
    run = _seed_experiment_run(test_db, operator=canonical, title="canonical run")
    headers = _login(client, "operator")
    params = {"operator_id": 999_999}

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        params=params,
        headers=headers,
        json=_decision_experiment_payload(),
    )
    assert create_response.status_code == 404

    list_response = client.get(
        "/api/v1/analytics/decision-experiments",
        params=params,
        headers=headers,
    )
    assert list_response.status_code == 404

    detail_response = client.get(
        f"/api/v1/analytics/decision-experiments/{run.id}",
        params=params,
        headers=headers,
    )
    assert detail_response.status_code == 404

    patch_response = client.patch(
        f"/api/v1/analytics/decision-experiments/{run.id}",
        params=params,
        headers=headers,
        json={"append_note": "unknown target"},
    )
    assert patch_response.status_code == 404

    evaluate_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/evaluate",
        params=params,
        headers=headers,
    )
    assert evaluate_response.status_code == 404

    apply_thresholds_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/apply-thresholds",
        params=params,
        headers=headers,
        json={"dry_run": True},
    )
    assert apply_thresholds_response.status_code == 404

    apply_strategy_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run.id}/apply-strategy",
        params=params,
        headers=headers,
        json={"dry_run": True},
    )
    assert apply_strategy_response.status_code == 404
