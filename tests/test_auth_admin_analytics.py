"""Tests for operator-first auth, analytics, and legacy admin compatibility."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import get_password_hash, verify_password
from app.models.models import BidDecisionRecord, PricePrediction, TenderResult


def _bootstrap_operator(client, username: str = "solo-operator", email: str = "solo@example.com", password: str = "password123"):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Solo Operator",
            "company": "Solo Bid Corp",
            "password": password,
        },
    )
    return response


def test_password_hash_defaults_to_pbkdf2_and_verifies_round_trip():
    """New password hashes should avoid bcrypt-only runtime dependencies and still verify correctly."""
    password = "operator-password-123"

    hashed_password = get_password_hash(password)

    assert hashed_password.startswith("$pbkdf2-sha256$")
    assert verify_password(password, hashed_password) is True
    assert verify_password("wrong-password", hashed_password) is False


def test_verify_password_accepts_legacy_bcrypt_hashes():
    """Legacy bcrypt hashes should remain usable after switching the default hash scheme."""
    bcrypt = pytest.importorskip("bcrypt")
    password = "legacy-password"
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    assert verify_password(password, hashed_password) is True
    assert verify_password("wrong-password", hashed_password) is False


def test_bootstrap_operator_and_get_me(client):
    """Bootstrap should initialize the singleton operator and expose it via /me."""
    response = _bootstrap_operator(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "solo-operator"
    assert payload["email"] == "solo@example.com"

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["username"] == "solo-operator"
    assert me_payload["is_active"] is True


def test_legacy_register_alias_rejects_second_operator(client):
    """The legacy register alias should reject creating a second operator in single-user mode."""
    first = _bootstrap_operator(client)
    assert first.status_code == 200

    second = client.post(
        "/api/v1/auth/register",
        json={
            "username": "another-operator",
            "email": "another@example.com",
            "full_name": "Another Operator",
            "company": "Another Corp",
            "password": "password123",
        },
    )

    assert second.status_code == 400
    assert "single-user mode already has an operator account" in second.json()["detail"].lower()


def test_session_login_accepts_json_body_and_returns_operator_metadata(client):
    """Session creation should work from a JSON body and return operator metadata."""
    bootstrap = _bootstrap_operator(client, username="session-operator", email="session@example.com", password="secret123")
    assert bootstrap.status_code == 200

    response = client.post(
        "/api/v1/auth/session",
        json={"username": "session-operator", "password": "secret123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["operator_id"] >= 1
    assert payload["username"] == "session-operator"


def test_operator_analytics_endpoints_report_single_user_stats(client):
    """Analytics endpoints should report operator-centric counts and keep the legacy alias compatible."""
    bootstrap = _bootstrap_operator(client, username="analytics-operator", email="analytics@example.com")
    operator_id = bootstrap.json()["id"]

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Analytics Project",
            "description": "Used to verify operator analytics",
            "requirements": "Deliver dashboard metrics",
            "budget_estimate": 100000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    bid_response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 95000.0,
            "proposed_timeline": 10,
            "description": "Operator bid for analytics verification",
        },
    )
    assert bid_response.status_code == 200

    log_response = client.post(
        "/api/v1/analytics/event",
        json={
            "event_type": "dashboard_opened",
            "event_data": {"source": "tests"},
        },
    )
    assert log_response.status_code == 200

    summary = client.get("/api/v1/analytics/summary")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["operator_id"] == operator_id
    assert summary_payload["total_projects"] == 1
    assert summary_payload["total_bids"] == 1
    assert summary_payload["total_events"] == 1
    assert summary_payload["mode"] == "single_operator"

    operator_stats = client.get("/api/v1/analytics/operator-stats")
    assert operator_stats.status_code == 200
    operator_stats_payload = operator_stats.json()
    assert operator_stats_payload["operator_id"] == operator_id
    assert operator_stats_payload["total_bids"] == 1
    assert operator_stats_payload["bids_count"] == 1
    assert operator_stats_payload["mode"] == "single_operator"

    legacy_stats = client.get("/api/v1/analytics/user-stats/999")
    assert legacy_stats.status_code == 200
    legacy_stats_payload = legacy_stats.json()
    assert legacy_stats_payload["operator_id"] == operator_id
    assert legacy_stats_payload["requested_user_id"] == 999
    assert legacy_stats_payload["mode"] == "single_operator"


def test_prediction_feedback_endpoint_summarizes_accuracy_against_tender_results(client, test_db):
    """Prediction feedback endpoint should compare latest predictions and decisions with actual winning amounts."""
    bootstrap = _bootstrap_operator(client, username="feedback-operator", email="feedback@example.com")
    operator_id = bootstrap.json()["id"]

    first_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Feedback Project One",
            "description": "Used for prediction feedback analytics",
            "requirements": "Collect winning amount feedback",
            "budget_estimate": 110000000.0,
            "category": "software",
        },
    ).json()["id"]
    second_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Feedback Project Two",
            "description": "Used for recommendation feedback analytics",
            "requirements": "Collect decision feedback",
            "budget_estimate": 100000000.0,
            "category": "software",
        },
    ).json()["id"]

    test_db.add_all([
        PricePrediction(
            user_id=operator_id,
            project_id=first_project,
            predicted_price=101000000.0,
            price_range_min=99000000.0,
            price_range_max=103000000.0,
            confidence_score=0.82,
            model_version="v1.1-historical",
        ),
        PricePrediction(
            user_id=operator_id,
            project_id=second_project,
            predicted_price=90000000.0,
            price_range_min=88000000.0,
            price_range_max=94000000.0,
            confidence_score=0.74,
            model_version="v1.1-historical",
        ),
        BidDecisionRecord(
            project_id=first_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            recommended_amount=102000000.0,
            probability_score=0.9,
            matched_score=0.84,
            priority_score=0.91,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            reasoning="첫 번째 피드백 검증용 추천값입니다.",
        ),
        BidDecisionRecord(
            project_id=second_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            recommended_amount=95000000.0,
            probability_score=0.72,
            matched_score=0.8,
            priority_score=0.69,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.25,
            reasoning="두 번째 피드백 검증용 추천값입니다.",
        ),
        TenderResult(
            project_id=first_project,
            winning_company="테스트 주식회사",
            winning_amount=103000000.0,
            winning_rate=95.2,
            result_status="awarded",
            announced_at=datetime.now(UTC) - timedelta(days=3),
        ),
        TenderResult(
            project_id=second_project,
            winning_company="추천 개선 주식회사",
            winning_amount=100000000.0,
            winning_rate=94.8,
            result_status="awarded",
            announced_at=datetime.now(UTC) - timedelta(days=2),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/prediction-feedback", params={"days": 30, "limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["result_count"] == 2
    assert payload["prediction_sample_count"] == 2
    assert payload["recommendation_sample_count"] == 2
    assert payload["average_prediction_error_rate"] == pytest.approx(0.0597, abs=0.0001)
    assert payload["average_recommendation_error_rate"] == pytest.approx(0.0299, abs=0.0001)
    assert payload["prediction_within_1_percent_count"] == 0
    assert payload["prediction_within_3_percent_count"] == 1
    assert payload["recommendation_within_1_percent_count"] == 1
    assert payload["recommendation_within_3_percent_count"] == 1
    assert payload["recommendation_better_than_prediction_count"] == 2
    assert payload["items"][0]["project_id"] == second_project
    assert payload["items"][0]["recommendation_improved_vs_prediction"] is True
    assert payload["items"][1]["project_id"] == first_project
    assert payload["items"][1]["prediction_error_rate"] == pytest.approx(0.0194, abs=0.0001)


def test_prediction_feedback_endpoint_returns_empty_summary_when_no_linked_results_exist(client):
    """Prediction feedback endpoint should return an empty but well-formed payload when no comparisons are available."""
    bootstrap = _bootstrap_operator(client, username="empty-feedback-operator", email="empty-feedback@example.com")
    operator_id = bootstrap.json()["id"]

    response = client.get("/api/v1/analytics/prediction-feedback")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["result_count"] == 0
    assert payload["prediction_sample_count"] == 0
    assert payload["recommendation_sample_count"] == 0
    assert payload["average_prediction_error_rate"] is None
    assert payload["average_recommendation_error_rate"] is None
    assert payload["items"] == []


def test_decision_insights_endpoint_summarizes_persisted_decision_signals(client, test_db):
    """Decision insights endpoint should aggregate persisted decision scores for tuning and review."""
    bootstrap = _bootstrap_operator(client, username="decision-insights-operator", email="decision-insights@example.com")
    operator_id = bootstrap.json()["id"]

    first_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Decision Insights Project One",
            "description": "First decision insight sample",
            "requirements": "Need persistent score metadata",
            "budget_estimate": 110000000.0,
            "category": "software",
        },
    ).json()["id"]
    second_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Decision Insights Project Two",
            "description": "Second decision insight sample",
            "requirements": "Need persistent score metadata",
            "budget_estimate": 98000000.0,
            "category": "software",
        },
    ).json()["id"]

    test_db.add_all([
        BidDecisionRecord(
            project_id=first_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            recommended_amount=104000000.0,
            probability_score=0.9,
            matched_score=0.84,
            priority_score=0.88,
            urgency_score=0.8,
            competitiveness_score=0.79,
            budget_capture_score=0.95,
            expected_margin_score=0.81,
            execution_complexity_score=0.44,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            workload_source="provided",
            score_breakdown='{"expected_margin_signal": 0.81, "execution_complexity_signal": 0.44, "total_penalty": 0.1}',
            reasoning="첫 번째 분석용 decision record",
        ),
        BidDecisionRecord(
            project_id=second_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            recommended_amount=91000000.0,
            probability_score=0.72,
            matched_score=0.79,
            priority_score=0.63,
            urgency_score=0.55,
            competitiveness_score=0.68,
            budget_capture_score=0.93,
            expected_margin_score=0.66,
            execution_complexity_score=0.71,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            workload_source="auto",
            score_breakdown='{"expected_margin_signal": 0.66, "execution_complexity_signal": 0.71, "total_penalty": 0.18}',
            reasoning="두 번째 분석용 decision record",
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/decision-insights", params={"days": 30, "limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["result_count"] == 2
    assert payload["high_priority_count"] == 1
    assert payload["bid_now_count"] == 1
    assert payload["review_count"] == 1
    assert payload["submitted_count"] == 1
    assert payload["auto_workload_count"] == 1
    assert payload["provided_workload_count"] == 1
    assert payload["average_priority_score"] == pytest.approx(0.755, abs=0.0001)
    assert payload["average_expected_margin_score"] == pytest.approx(0.735, abs=0.0001)
    assert payload["average_execution_complexity_score"] == pytest.approx(0.575, abs=0.0001)
    assert payload["status_breakdown"]["submitted"] == 1
    assert payload["status_breakdown"]["reviewing"] == 1
    assert payload["action_breakdown"]["bid_now"] == 1
    assert payload["action_breakdown"]["review"] == 1
    assert payload["recent_decisions"][0]["project_id"] == second_project
    assert payload["recent_decisions"][1]["project_id"] == first_project


def test_decision_funnel_endpoint_tracks_entry_paths_and_submission_rates(client, test_db):
    """Decision funnel endpoint should preserve initial workflow paths and summarize submission conversion rates."""
    bootstrap = _bootstrap_operator(client, username="decision-funnel-operator", email="decision-funnel@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": f"Decision Funnel Project {index}",
                "description": "Used for decision funnel analytics",
                "requirements": "Preserve initial workflow metadata",
                "budget_estimate": 100000000.0 + (index * 1000000.0),
                "category": "software",
            },
        ).json()["id"]
        for index in range(1, 5)
    ]

    test_db.add_all([
        BidDecisionRecord(
            project_id=project_ids[0],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="bid_now",
            initial_decision_status="planned",
            first_decided_at=now - timedelta(hours=30),
            recommended_amount=96000000.0,
            probability_score=0.91,
            matched_score=0.85,
            priority_score=0.89,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            reasoning="즉시 투찰 후 제출 완료",
            created_at=now - timedelta(hours=30),
            updated_at=now - timedelta(hours=18),
        ),
        BidDecisionRecord(
            project_id=project_ids[1],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(hours=10),
            recommended_amount=94500000.0,
            probability_score=0.74,
            matched_score=0.79,
            priority_score=0.67,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.24,
            reasoning="검토 후 제출 완료",
            created_at=now - timedelta(hours=10),
            updated_at=now - timedelta(hours=4),
        ),
        BidDecisionRecord(
            project_id=project_ids[2],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(hours=8),
            recommended_amount=93000000.0,
            probability_score=0.66,
            matched_score=0.72,
            priority_score=0.61,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            reasoning="아직 review 대기 중",
            created_at=now - timedelta(hours=8),
            updated_at=now - timedelta(hours=1),
        ),
        BidDecisionRecord(
            project_id=project_ids[3],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="bid_now",
            initial_decision_status="submitted",
            first_decided_at=now - timedelta(hours=1),
            recommended_amount=98000000.0,
            probability_score=0.0,
            matched_score=0.0,
            priority_score=1.0,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.0,
            reasoning="사전 판단 없이 직접 제출",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/decision-funnel", params={"days": 30, "limit": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["decision_count"] == 4
    assert payload["project_count"] == 4
    assert payload["active_pending_count"] == 1
    assert payload["submitted_count"] == 3
    assert payload["skipped_count"] == 0
    assert payload["entry_bid_now_count"] == 1
    assert payload["entry_review_count"] == 2
    assert payload["entry_skip_count"] == 0
    assert payload["direct_submitted_count"] == 1
    assert payload["submitted_after_bid_now_count"] == 1
    assert payload["submitted_after_review_count"] == 1
    assert payload["submitted_after_skip_count"] == 0
    assert payload["overall_submission_rate"] == pytest.approx(0.75, abs=0.0001)
    assert payload["workflow_submission_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert payload["bid_now_submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert payload["review_submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert payload["average_hours_to_submit"] == pytest.approx(6.0, abs=0.0001)
    assert len(payload["recent_submissions"]) == 2
    assert payload["recent_submissions"][0]["project_id"] == project_ids[3]
    assert payload["recent_submissions"][0]["initial_decision_status"] == "submitted"
    assert payload["recent_submissions"][0]["hours_to_submit"] == pytest.approx(0.0, abs=0.0001)
    assert payload["recent_submissions"][1]["project_id"] == project_ids[1]
    assert payload["recent_submissions"][1]["initial_action"] == "review"
    assert payload["recent_submissions"][1]["current_decision_status"] == "submitted"


def test_decision_funnel_endpoint_includes_trend_and_segment_breakdowns(client, test_db):
    """Decision funnel endpoint should expose trend buckets plus category/workload/agency breakdowns."""
    bootstrap = _bootstrap_operator(client, username="decision-funnel-breakdown-operator", email="decision-funnel-breakdown@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_specs = [
        {
            "title": "Funnel Breakdown Software One",
            "category": "software",
            "issuing_agency": "조달청",
            "demand_agency": "서울특별시교육청",
        },
        {
            "title": "Funnel Breakdown Software Two",
            "category": "software",
            "issuing_agency": "조달청",
            "demand_agency": "서울특별시교육청",
        },
        {
            "title": "Funnel Breakdown Security One",
            "category": "security",
            "issuing_agency": "행정안전부",
            "demand_agency": "국토교통부",
        },
        {
            "title": "Funnel Breakdown Security Two",
            "category": "security",
            "issuing_agency": "행정안전부",
            "demand_agency": "국토교통부",
        },
    ]
    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": spec["title"],
                "description": "Used for decision funnel breakdown analytics",
                "requirements": "Need trend and segment breakdowns",
                "budget_estimate": 100000000.0,
                "category": spec["category"],
                "issuing_agency": spec["issuing_agency"],
                "demand_agency": spec["demand_agency"],
            },
        ).json()["id"]
        for spec in project_specs
    ]

    test_db.add_all([
        BidDecisionRecord(
            project_id=project_ids[0],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="bid_now",
            initial_decision_status="planned",
            first_decided_at=now - timedelta(days=26),
            recommended_amount=96500000.0,
            probability_score=0.9,
            matched_score=0.86,
            priority_score=0.9,
            expected_margin_score=0.83,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            workload_source="provided",
            reasoning="소프트웨어 즉시 제출 완료",
            created_at=now - timedelta(days=26),
            updated_at=now - timedelta(days=24),
        ),
        BidDecisionRecord(
            project_id=project_ids[1],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=21),
            recommended_amount=95200000.0,
            probability_score=0.74,
            matched_score=0.79,
            priority_score=0.68,
            expected_margin_score=0.71,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.24,
            workload_source="auto",
            reasoning="소프트웨어 review 후 제출",
            created_at=now - timedelta(days=21),
            updated_at=now - timedelta(days=19),
        ),
        BidDecisionRecord(
            project_id=project_ids[2],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=6),
            recommended_amount=93800000.0,
            probability_score=0.66,
            matched_score=0.72,
            priority_score=0.61,
            expected_margin_score=0.63,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            workload_source="auto",
            reasoning="보안 카테고리 review 대기 중",
            created_at=now - timedelta(days=6),
            updated_at=now - timedelta(days=4),
        ),
        BidDecisionRecord(
            project_id=project_ids[3],
            operator_id=operator_id,
            pursue_bid=False,
            action="skip",
            decision_status="skipped",
            initial_action="skip",
            initial_decision_status="skipped",
            first_decided_at=now - timedelta(days=3),
            recommended_amount=92000000.0,
            probability_score=0.32,
            matched_score=0.41,
            priority_score=0.29,
            expected_margin_score=0.52,
            current_active_bids=3,
            max_active_bids=3,
            current_workload_score=0.88,
            workload_source="provided",
            reasoning="보안 카테고리 skip",
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=2),
        ),
    ])
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/decision-funnel",
        params={"days": 30, "limit": 5, "breakdown_limit": 5, "trend_bucket_days": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["trend_bucket_days"] == 10
    assert payload["breakdown_limit_applied"] == 5
    assert len(payload["trend"]) == 2
    assert payload["trend"][0]["decision_count"] == 2
    assert payload["trend"][0]["submitted_count"] == 2
    assert payload["trend"][0]["entry_bid_now_count"] == 1
    assert payload["trend"][0]["entry_review_count"] == 1
    assert payload["trend"][0]["submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert payload["trend"][1]["decision_count"] == 2
    assert payload["trend"][1]["submitted_count"] == 0
    assert payload["trend"][1]["entry_review_count"] == 1
    assert payload["trend"][1]["entry_skip_count"] == 1
    assert payload["trend"][1]["submission_rate"] == pytest.approx(0.0, abs=0.0001)

    category_breakdown = {item["segment"]: item for item in payload["category_breakdown"]}
    assert category_breakdown["software"]["decision_count"] == 2
    assert category_breakdown["software"]["submitted_count"] == 2
    assert category_breakdown["software"]["submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert category_breakdown["security"]["decision_count"] == 2
    assert category_breakdown["security"]["submitted_count"] == 0
    assert category_breakdown["security"]["review_submission_rate"] == pytest.approx(0.0, abs=0.0001)

    workload_breakdown = {item["segment"]: item for item in payload["workload_source_breakdown"]}
    assert workload_breakdown["provided"]["decision_count"] == 2
    assert workload_breakdown["provided"]["submitted_count"] == 1
    assert workload_breakdown["provided"]["entry_skip_count"] == 1
    assert workload_breakdown["provided"]["submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert workload_breakdown["auto"]["decision_count"] == 2
    assert workload_breakdown["auto"]["submitted_count"] == 1
    assert workload_breakdown["auto"]["entry_review_count"] == 2
    assert workload_breakdown["auto"]["review_submission_rate"] == pytest.approx(0.5, abs=0.0001)

    agency_breakdown = {item["segment"]: item for item in payload["agency_breakdown"]}
    assert agency_breakdown["서울특별시교육청"]["decision_count"] == 2
    assert agency_breakdown["서울특별시교육청"]["submitted_count"] == 2
    assert agency_breakdown["국토교통부"]["decision_count"] == 2
    assert agency_breakdown["국토교통부"]["submitted_count"] == 0


def test_decision_funnel_endpoint_compares_current_and_previous_periods(client, test_db):
    """Decision funnel endpoint should expose previous-period summary and period-over-period deltas."""
    bootstrap = _bootstrap_operator(client, username="decision-funnel-comparison-operator", email="decision-funnel-comparison@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": f"Decision Funnel Comparison Project {index}",
                "description": "Used for period comparison analytics",
                "requirements": "Need current vs previous funnel comparison",
                "budget_estimate": 100000000.0 + (index * 1000000.0),
                "category": "software",
            },
        ).json()["id"]
        for index in range(1, 6)
    ]

    test_db.add_all([
        BidDecisionRecord(
            project_id=project_ids[0],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="bid_now",
            initial_decision_status="planned",
            first_decided_at=now - timedelta(days=5),
            recommended_amount=96000000.0,
            probability_score=0.89,
            matched_score=0.84,
            priority_score=0.88,
            expected_margin_score=0.82,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            reasoning="현재 기간 bid_now 제출",
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=4),
        ),
        BidDecisionRecord(
            project_id=project_ids[1],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=3),
            recommended_amount=95000000.0,
            probability_score=0.76,
            matched_score=0.8,
            priority_score=0.7,
            expected_margin_score=0.74,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            reasoning="현재 기간 review 후 제출",
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=2, hours=12),
        ),
        BidDecisionRecord(
            project_id=project_ids[2],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=1),
            recommended_amount=94000000.0,
            probability_score=0.67,
            matched_score=0.73,
            priority_score=0.62,
            expected_margin_score=0.65,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.24,
            reasoning="현재 기간 review 대기",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        ),
        BidDecisionRecord(
            project_id=project_ids[3],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="bid_now",
            initial_decision_status="planned",
            first_decided_at=now - timedelta(days=22),
            recommended_amount=93500000.0,
            probability_score=0.83,
            matched_score=0.79,
            priority_score=0.8,
            expected_margin_score=0.71,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.12,
            reasoning="직전 기간 bid_now 제출",
            created_at=now - timedelta(days=22),
            updated_at=now - timedelta(days=20),
        ),
        BidDecisionRecord(
            project_id=project_ids[4],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=18),
            recommended_amount=92500000.0,
            probability_score=0.64,
            matched_score=0.7,
            priority_score=0.59,
            expected_margin_score=0.62,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.26,
            reasoning="직전 기간 review 대기",
            created_at=now - timedelta(days=18),
            updated_at=now - timedelta(days=18),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/decision-funnel", params={"days": 14, "limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["decision_count"] == 3
    assert payload["project_count"] == 3
    assert payload["submitted_count"] == 2
    assert payload["active_pending_count"] == 1
    assert payload["overall_submission_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert payload["workflow_submission_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert payload["bid_now_submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert payload["review_submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert payload["average_hours_to_submit"] == pytest.approx(18.0, abs=0.0001)
    assert payload["current_period_start"]
    assert payload["current_period_end"]

    previous_period = payload["previous_period"]
    assert previous_period["decision_count"] == 2
    assert previous_period["project_count"] == 2
    assert previous_period["submitted_count"] == 1
    assert previous_period["active_pending_count"] == 1
    assert previous_period["overall_submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert previous_period["workflow_submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert previous_period["bid_now_submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert previous_period["review_submission_rate"] == pytest.approx(0.0, abs=0.0001)
    assert previous_period["average_hours_to_submit"] == pytest.approx(48.0, abs=0.0001)
    assert previous_period["period_start"]
    assert previous_period["period_end"]

    comparison = payload["comparison"]
    assert comparison["decision_count_delta"] == 1
    assert comparison["project_count_delta"] == 1
    assert comparison["submitted_count_delta"] == 1
    assert comparison["active_pending_count_delta"] == 0
    assert comparison["skipped_count_delta"] == 0
    assert comparison["overall_submission_rate_delta"] == pytest.approx(0.1667, abs=0.0001)
    assert comparison["workflow_submission_rate_delta"] == pytest.approx(0.1667, abs=0.0001)
    assert comparison["bid_now_submission_rate_delta"] == pytest.approx(0.0, abs=0.0001)
    assert comparison["review_submission_rate_delta"] == pytest.approx(0.5, abs=0.0001)
    assert comparison["average_hours_to_submit_delta"] == pytest.approx(-30.0, abs=0.0001)
    assert comparison["current_period_start"]
    assert comparison["current_period_end"]
    assert comparison["previous_period_start"]
    assert comparison["previous_period_end"]


def test_decision_recommendations_endpoint_returns_actionable_threshold_and_segment_guidance(client, test_db):
    """Decision recommendations endpoint should translate funnel signals into actionable tuning guidance."""
    bootstrap = _bootstrap_operator(client, username="decision-recommendation-operator", email="decision-recommendation@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_specs = [
        {
            "title": "Recommendation Software BidNow A",
            "category": "software",
            "demand_agency": "서울특별시교육청",
            "workload_source": "provided",
            "initial_action": "bid_now",
            "initial_status": "planned",
            "current_action": "bid_now",
            "current_status": "submitted",
            "first_decided_at": now - timedelta(days=5),
            "updated_at": now - timedelta(days=4),
        },
        {
            "title": "Recommendation Software Review Submit",
            "category": "software",
            "demand_agency": "서울특별시교육청",
            "workload_source": "provided",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "bid_now",
            "current_status": "submitted",
            "first_decided_at": now - timedelta(days=4),
            "updated_at": now - timedelta(days=3),
        },
        {
            "title": "Recommendation Security Review Waiting A",
            "category": "security",
            "demand_agency": "국토교통부",
            "workload_source": "auto",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "review",
            "current_status": "reviewing",
            "first_decided_at": now - timedelta(days=3),
            "updated_at": now - timedelta(days=2),
        },
        {
            "title": "Recommendation Security Review Skip",
            "category": "security",
            "demand_agency": "국토교통부",
            "workload_source": "auto",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "skip",
            "current_status": "skipped",
            "first_decided_at": now - timedelta(days=2),
            "updated_at": now - timedelta(days=2),
        },
        {
            "title": "Recommendation Security Review Waiting B",
            "category": "security",
            "demand_agency": "국토교통부",
            "workload_source": "auto",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "review",
            "current_status": "reviewing",
            "first_decided_at": now - timedelta(days=1),
            "updated_at": now - timedelta(days=1),
        },
        {
            "title": "Recommendation Software BidNow B",
            "category": "software",
            "demand_agency": "서울특별시교육청",
            "workload_source": "provided",
            "initial_action": "bid_now",
            "initial_status": "planned",
            "current_action": "bid_now",
            "current_status": "submitted",
            "first_decided_at": now - timedelta(days=6),
            "updated_at": now - timedelta(days=5),
        },
        {
            "title": "Recommendation Previous Review Submit A",
            "category": "software",
            "demand_agency": "서울특별시교육청",
            "workload_source": "provided",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "bid_now",
            "current_status": "submitted",
            "first_decided_at": now - timedelta(days=20),
            "updated_at": now - timedelta(days=18),
        },
        {
            "title": "Recommendation Previous Review Submit B",
            "category": "security",
            "demand_agency": "국토교통부",
            "workload_source": "auto",
            "initial_action": "review",
            "initial_status": "reviewing",
            "current_action": "bid_now",
            "current_status": "submitted",
            "first_decided_at": now - timedelta(days=19),
            "updated_at": now - timedelta(days=17),
        },
    ]

    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": spec["title"],
                "description": "Used for decision recommendation analytics",
                "requirements": "Need actionable tuning guidance",
                "budget_estimate": 100000000.0,
                "category": spec["category"],
                "demand_agency": spec["demand_agency"],
            },
        ).json()["id"]
        for spec in project_specs
    ]

    decision_records = []
    for project_id, spec in zip(project_ids, project_specs, strict=True):
        decision_records.append(
            BidDecisionRecord(
                project_id=project_id,
                operator_id=operator_id,
                pursue_bid=spec["current_status"] != "skipped",
                action=spec["current_action"],
                decision_status=spec["current_status"],
                initial_action=spec["initial_action"],
                initial_decision_status=spec["initial_status"],
                first_decided_at=spec["first_decided_at"],
                recommended_amount=95000000.0,
                probability_score=0.75,
                matched_score=0.78,
                priority_score=0.74 if spec["category"] == "software" else 0.55,
                expected_margin_score=0.76 if spec["category"] == "software" else 0.61,
                current_active_bids=0 if spec["workload_source"] == "provided" else 1,
                max_active_bids=3,
                current_workload_score=0.12 if spec["workload_source"] == "provided" else 0.48,
                workload_source=spec["workload_source"],
                reasoning="추천 로직 검증용 decision record",
                created_at=spec["first_decided_at"],
                updated_at=spec["updated_at"],
            )
        )

    test_db.add_all(decision_records)
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/decision-recommendations",
        params={"days": 14, "breakdown_limit": 5, "trend_bucket_days": 7, "recommendation_limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["decision_count"] == 6
    assert payload["submitted_count"] == 3
    assert payload["active_pending_count"] == 2
    assert payload["overall_submission_rate"] == pytest.approx(0.5, abs=0.0001)
    assert payload["review_submission_rate"] == pytest.approx(0.25, abs=0.0001)
    assert payload["recommendation_count"] == 3
    assert payload["recommendation_limit_applied"] == 5
    assert payload["experiment_count"] == 3
    assert payload["headline"]
    assert payload["comparison"]["review_submission_rate_delta"] == pytest.approx(-0.75, abs=0.0001)
    assert payload["recommended_next_experiment"]["experiment_key"] == "exp-review-threshold-tighten"
    assert payload["recommended_next_experiment"]["priority_rank"] == 1
    assert payload["recommended_next_experiment"]["target_metric"] == "review_submission_rate"
    assert len(payload["experiments"]) == 3
    assert [item["priority_rank"] for item in payload["experiments"]] == [1, 2, 3]

    recommendations = {item["key"]: item for item in payload["recommendations"]}
    assert set(recommendations) == {
        "review-threshold-tighten",
        "workload-auto-calibration",
        "category-focus-shift",
    }

    review_rec = recommendations["review-threshold-tighten"]
    assert review_rec["severity"] == "action"
    assert "REVIEW_THRESHOLD" in review_rec["suggested_adjustment"]
    assert review_rec["supporting_metrics"]["entry_review_count"] == 4
    assert review_rec["supporting_metrics"]["current_review_submission_rate"] == pytest.approx(0.25, abs=0.0001)
    assert review_rec["experiment_plan"]["experiment_key"] == "exp-review-threshold-tighten"
    assert review_rec["experiment_plan"]["recommendation_key"] == "review-threshold-tighten"
    assert review_rec["experiment_plan"]["expected_direction"] == "increase"
    assert review_rec["experiment_plan"]["minimum_decision_sample"] == 4
    assert review_rec["experiment_plan"]["duration_days"] == 14
    assert "롤백" not in review_rec["experiment_plan"]["rollback_trigger"] or review_rec["experiment_plan"]["rollback_trigger"]

    workload_rec = recommendations["workload-auto-calibration"]
    assert workload_rec["severity"] == "action"
    assert "load_penalty" in workload_rec["suggested_adjustment"]
    assert workload_rec["supporting_metrics"]["auto_decision_count"] == 3
    assert workload_rec["supporting_metrics"]["auto_submission_rate"] == pytest.approx(0.0, abs=0.0001)
    assert workload_rec["supporting_metrics"]["provided_submission_rate"] == pytest.approx(1.0, abs=0.0001)
    assert workload_rec["experiment_plan"]["experiment_key"] == "exp-workload-auto-calibration"
    assert workload_rec["experiment_plan"]["guardrail_metric"] == "active_pending_count"

    category_rec = recommendations["category-focus-shift"]
    assert category_rec["severity"] == "action"
    assert "security" in category_rec["summary"]
    assert "software" in category_rec["summary"]
    assert category_rec["supporting_metrics"]["worst_category"] == "security"
    assert category_rec["supporting_metrics"]["best_category"] == "software"
    assert category_rec["experiment_plan"]["experiment_key"] == "exp-category-focus-shift"
    assert category_rec["experiment_plan"]["duration_days"] == 21


def test_decision_experiment_run_endpoints_create_list_and_detail(client, test_db):
    """Decision experiment endpoints should persist a planned experiment with baseline analytics and list it for dashboard tracking."""
    bootstrap = _bootstrap_operator(client, username="decision-experiment-operator", email="decision-experiment@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": f"Decision Experiment Baseline Project {index}",
                "description": "Used for decision experiment baseline analytics",
                "requirements": "Need persisted experiment snapshots",
                "budget_estimate": 100000000.0,
                "category": "software" if index <= 2 else "security",
                "demand_agency": "서울특별시교육청" if index <= 2 else "국토교통부",
            },
        ).json()["id"]
        for index in range(1, 5)
    ]

    test_db.add_all([
        BidDecisionRecord(
            project_id=project_ids[0],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=18),
            recommended_amount=96000000.0,
            probability_score=0.8,
            matched_score=0.78,
            priority_score=0.77,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.12,
            workload_source="provided",
            reasoning="baseline review submit",
            created_at=now - timedelta(days=18),
            updated_at=now - timedelta(days=17),
        ),
        BidDecisionRecord(
            project_id=project_ids[1],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=17),
            recommended_amount=95500000.0,
            probability_score=0.74,
            matched_score=0.75,
            priority_score=0.68,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            workload_source="provided",
            reasoning="baseline review waiting",
            created_at=now - timedelta(days=17),
            updated_at=now - timedelta(days=16),
        ),
        BidDecisionRecord(
            project_id=project_ids[2],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=16),
            recommended_amount=95000000.0,
            probability_score=0.7,
            matched_score=0.72,
            priority_score=0.61,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.45,
            workload_source="auto",
            reasoning="baseline auto review waiting",
            created_at=now - timedelta(days=16),
            updated_at=now - timedelta(days=15),
        ),
        BidDecisionRecord(
            project_id=project_ids[3],
            operator_id=operator_id,
            pursue_bid=False,
            action="skip",
            decision_status="skipped",
            initial_action="skip",
            initial_decision_status="skipped",
            first_decided_at=now - timedelta(days=15),
            recommended_amount=94000000.0,
            probability_score=0.31,
            matched_score=0.4,
            priority_score=0.28,
            current_active_bids=2,
            max_active_bids=3,
            current_workload_score=0.7,
            workload_source="auto",
            reasoning="baseline skip",
            created_at=now - timedelta(days=15),
            updated_at=now - timedelta(days=14, hours=1),
        ),
    ])
    test_db.commit()

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        json={
            "experiment_key": "exp-review-threshold-tighten",
            "recommendation_key": "review-threshold-tighten",
            "priority_rank": 1,
            "title": "Review threshold 상향 실험",
            "hypothesis": "review 진입 기준을 높이면 review 전환율이 개선됩니다.",
            "suggested_change": "REVIEW_THRESHOLD를 0.04 높입니다.",
            "target_metric": "review_submission_rate",
            "expected_direction": "increase",
            "success_criteria": "review 전환율이 +0.10p 이상 개선되면 성공",
            "guardrail_metric": "overall_submission_rate",
            "minimum_decision_sample": 4,
            "duration_days": 14,
            "baseline_days": 14,
            "rollback_trigger": "overall_submission_rate가 0.05p 이상 하락하면 롤백",
            "started_at": (now - timedelta(days=14)).isoformat(),
            "notes": "dashboard에서 바로 실행한 테스트 실험",
        },
    )

    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert create_payload["run"]["operator_id"] == operator_id
    assert create_payload["run"]["experiment_key"] == "exp-review-threshold-tighten"
    assert create_payload["run"]["status"] == "running"
    assert create_payload["run"]["outcome"] is None
    assert create_payload["run"]["notes"] == "dashboard에서 바로 실행한 테스트 실험"
    assert create_payload["baseline_summary"]["decision_count"] == 4
    assert create_payload["baseline_summary"]["submitted_count"] == 1
    assert create_payload["baseline_summary"]["review_submission_rate"] == pytest.approx(0.3333, abs=0.0001)
    assert create_payload["baseline_summary"]["overall_submission_rate"] == pytest.approx(0.25, abs=0.0001)
    assert create_payload["baseline_summary"]["auto_submission_rate"] == pytest.approx(0.0, abs=0.0001)

    run_id = create_payload["run"]["id"]

    list_response = client.get("/api/v1/analytics/decision-experiments", params={"limit": 10})
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["operator_id"] == operator_id
    assert list_payload["result_count"] == 1
    assert list_payload["active_count"] == 1
    assert list_payload["completed_count"] == 0
    assert list_payload["rolled_back_count"] == 0
    assert list_payload["runs"][0]["id"] == run_id
    assert list_payload["runs"][0]["latest_evaluation"] is None

    detail_response = client.get(f"/api/v1/analytics/decision-experiments/{run_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["run"]["id"] == run_id
    assert detail_payload["run"]["status"] == "running"
    assert detail_payload["baseline_summary"]["best_category"] == "software"
    assert detail_payload["baseline_summary"]["worst_category"] == "security"


def test_decision_experiment_evaluate_endpoint_completes_successful_run(client, test_db):
    """Evaluating a finished experiment should compare current performance to baseline and persist a completed outcome."""
    bootstrap = _bootstrap_operator(client, username="decision-experiment-eval-operator", email="decision-experiment-eval@example.com")
    operator_id = bootstrap.json()["id"]
    now = datetime.now(UTC)

    project_ids = [
        client.post(
            "/api/v1/projects/",
            json={
                "title": f"Decision Experiment Eval Project {index}",
                "description": "Used for decision experiment evaluation analytics",
                "requirements": "Need baseline vs current experiment comparison",
                "budget_estimate": 100000000.0,
                "category": "software",
                "demand_agency": "서울특별시교육청",
            },
        ).json()["id"]
        for index in range(1, 9)
    ]

    baseline_records = [
        BidDecisionRecord(
            project_id=project_ids[0],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=32),
            recommended_amount=96000000.0,
            probability_score=0.8,
            matched_score=0.78,
            priority_score=0.77,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.12,
            workload_source="provided",
            reasoning="baseline review submit",
            created_at=now - timedelta(days=32),
            updated_at=now - timedelta(days=31),
        ),
        BidDecisionRecord(
            project_id=project_ids[1],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=31),
            recommended_amount=95500000.0,
            probability_score=0.75,
            matched_score=0.74,
            priority_score=0.68,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.22,
            workload_source="provided",
            reasoning="baseline review waiting a",
            created_at=now - timedelta(days=31),
            updated_at=now - timedelta(days=30),
        ),
        BidDecisionRecord(
            project_id=project_ids[2],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=30),
            recommended_amount=95000000.0,
            probability_score=0.71,
            matched_score=0.72,
            priority_score=0.62,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.2,
            workload_source="provided",
            reasoning="baseline review waiting b",
            created_at=now - timedelta(days=30),
            updated_at=now - timedelta(days=29),
        ),
        BidDecisionRecord(
            project_id=project_ids[3],
            operator_id=operator_id,
            pursue_bid=False,
            action="skip",
            decision_status="skipped",
            initial_action="skip",
            initial_decision_status="skipped",
            first_decided_at=now - timedelta(days=29),
            recommended_amount=94000000.0,
            probability_score=0.3,
            matched_score=0.41,
            priority_score=0.28,
            current_active_bids=2,
            max_active_bids=3,
            current_workload_score=0.7,
            workload_source="auto",
            reasoning="baseline skip",
            created_at=now - timedelta(days=29),
            updated_at=now - timedelta(days=28),
        ),
    ]
    current_records = [
        BidDecisionRecord(
            project_id=project_ids[4],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=18),
            recommended_amount=96500000.0,
            probability_score=0.83,
            matched_score=0.8,
            priority_score=0.79,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            workload_source="provided",
            reasoning="current review submit a",
            created_at=now - timedelta(days=18),
            updated_at=now - timedelta(days=17),
        ),
        BidDecisionRecord(
            project_id=project_ids[5],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=17),
            recommended_amount=96800000.0,
            probability_score=0.84,
            matched_score=0.81,
            priority_score=0.8,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.11,
            workload_source="provided",
            reasoning="current review submit b",
            created_at=now - timedelta(days=17),
            updated_at=now - timedelta(days=16),
        ),
        BidDecisionRecord(
            project_id=project_ids[6],
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=16),
            recommended_amount=97000000.0,
            probability_score=0.85,
            matched_score=0.82,
            priority_score=0.81,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.12,
            workload_source="provided",
            reasoning="current review submit c",
            created_at=now - timedelta(days=16),
            updated_at=now - timedelta(days=15),
        ),
        BidDecisionRecord(
            project_id=project_ids[7],
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            initial_action="review",
            initial_decision_status="reviewing",
            first_decided_at=now - timedelta(days=15),
            recommended_amount=95200000.0,
            probability_score=0.73,
            matched_score=0.74,
            priority_score=0.66,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.2,
            workload_source="provided",
            reasoning="current review waiting",
            created_at=now - timedelta(days=15),
            updated_at=now - timedelta(days=14),
        ),
    ]

    test_db.add_all(baseline_records + current_records)
    test_db.commit()

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        json={
            "experiment_key": "exp-review-threshold-tighten",
            "recommendation_key": "review-threshold-tighten",
            "priority_rank": 1,
            "title": "Review threshold 상향 실험",
            "hypothesis": "review 후보 품질을 높이면 전환율이 개선됩니다.",
            "suggested_change": "REVIEW_THRESHOLD를 0.04 높입니다.",
            "target_metric": "review_submission_rate",
            "expected_direction": "increase",
            "success_criteria": "review 전환율이 +0.10p 이상 개선되면 성공",
            "guardrail_metric": "overall_submission_rate",
            "minimum_decision_sample": 4,
            "duration_days": 14,
            "baseline_days": 14,
            "rollback_trigger": "overall_submission_rate가 0.05p 이상 하락하면 롤백",
            "started_at": (now - timedelta(days=20)).isoformat(),
        },
    )
    assert create_response.status_code == 200
    run_id = create_response.json()["run"]["id"]

    evaluate_response = client.post(f"/api/v1/analytics/decision-experiments/{run_id}/evaluate")
    assert evaluate_response.status_code == 202
    queued_payload = evaluate_response.json()
    assert queued_payload["task_name"] == "ml.reevaluate_decision_experiment"
    assert queued_payload["status"] == "queued"
    assert queued_payload["poll_url"].endswith(queued_payload["task_id"])

    from app.tasks.jobs import reevaluate_decision_experiment as reevaluate_decision_experiment_task

    evaluate_payload = reevaluate_decision_experiment_task.run(experiment_run_id=run_id)
    assert evaluate_payload["run"]["id"] == run_id
    assert evaluate_payload["run"]["status"] == "completed"
    assert evaluate_payload["run"]["outcome"] == "success"
    assert evaluate_payload["run"]["last_evaluated_at"]

    latest_evaluation = evaluate_payload["run"]["latest_evaluation"]
    assert latest_evaluation["sample_size"] == 4
    assert latest_evaluation["minimum_sample_reached"] is True
    assert latest_evaluation["baseline_target_value"] == pytest.approx(0.3333, abs=0.0001)
    assert latest_evaluation["current_target_value"] == pytest.approx(0.75, abs=0.0001)
    assert latest_evaluation["target_delta"] == pytest.approx(0.4167, abs=0.0001)
    assert latest_evaluation["baseline_guardrail_value"] == pytest.approx(0.25, abs=0.0001)
    assert latest_evaluation["current_guardrail_value"] == pytest.approx(0.75, abs=0.0001)
    assert latest_evaluation["guardrail_delta"] == pytest.approx(0.5, abs=0.0001)
    assert latest_evaluation["recommended_action"] == "complete"
    assert latest_evaluation["summary"]
    assert latest_evaluation["current_summary"]["decision_count"] == 4
    assert latest_evaluation["current_summary"]["submitted_count"] == 3
    assert latest_evaluation["current_summary"]["review_submission_rate"] == pytest.approx(0.75, abs=0.0001)

    filtered_response = client.get("/api/v1/analytics/decision-experiments", params={"status": "completed"})
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["result_count"] == 1
    assert filtered_payload["completed_count"] == 1
    assert filtered_payload["runs"][0]["id"] == run_id


def test_decision_experiment_patch_endpoint_updates_notes_and_rolls_back_run(client):
    """Operators should be able to append notes and manually roll back an experiment run."""
    _bootstrap_operator(client, username="decision-experiment-manual-operator", email="decision-experiment-manual@example.com")
    now = datetime.now(UTC)

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        json={
            "experiment_key": "exp-review-threshold-tighten",
            "recommendation_key": "review-threshold-tighten",
            "priority_rank": 1,
            "title": "Review threshold 상향 실험",
            "hypothesis": "manual lifecycle control test",
            "suggested_change": "REVIEW_THRESHOLD를 소폭 상향합니다.",
            "target_metric": "review_submission_rate",
            "expected_direction": "increase",
            "success_criteria": "review 전환율 개선",
            "guardrail_metric": "overall_submission_rate",
            "minimum_decision_sample": 3,
            "duration_days": 7,
            "baseline_days": 7,
            "rollback_trigger": "overall_submission_rate 하락 시 롤백",
            "started_at": (now - timedelta(days=1)).isoformat(),
        },
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run"]["id"]

    patch_response = client.patch(
        f"/api/v1/analytics/decision-experiments/{run_id}",
        json={
            "status": "rolled_back",
            "append_note": "manual rollback executed from dashboard",
        },
    )

    assert patch_response.status_code == 200
    payload = patch_response.json()
    assert payload["run"]["id"] == run_id
    assert payload["run"]["status"] == "rolled_back"
    assert payload["run"]["outcome"] == "rollback"
    assert payload["run"]["ended_at"] is not None
    assert "manual rollback executed from dashboard" in payload["run"]["notes"]


def test_decision_experiment_apply_thresholds_updates_operator_strategy_and_decision_logic(client):
    """Successful threshold experiments should feed back into the persisted operator strategy."""
    _bootstrap_operator(client, username="decision-threshold-apply-operator", email="decision-threshold-apply@example.com")
    now = datetime.now(UTC)
    marginal_payload = {
        "project_id": 999,
        "recommended_amount": 82000000.0,
        "budget_estimate": 88000000.0,
        "probability_score": 0.48,
        "matched_score": 0.54,
        "deadline_hours_remaining": 48,
        "current_active_bids": 1,
        "max_active_bids": 3,
        "current_workload_score": 0.2,
    }

    before_response = client.post("/api/v1/operations/bid-decision", json=marginal_payload)
    assert before_response.status_code == 200
    assert before_response.json()["action"] == "review"

    create_response = client.post(
        "/api/v1/analytics/decision-experiments",
        json={
            "experiment_key": "exp-review-threshold-tighten",
            "recommendation_key": "review-threshold-tighten",
            "priority_rank": 1,
            "title": "Review threshold 상향 실험",
            "hypothesis": "successful feedback loop test",
            "suggested_change": "REVIEW_THRESHOLD를 0.04 높입니다.",
            "target_metric": "review_submission_rate",
            "expected_direction": "increase",
            "success_criteria": "review 전환율 개선",
            "guardrail_metric": "overall_submission_rate",
            "minimum_decision_sample": 3,
            "duration_days": 7,
            "baseline_days": 7,
            "rollback_trigger": "overall_submission_rate 하락 시 롤백",
            "started_at": (now - timedelta(days=3)).isoformat(),
        },
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run"]["id"]

    complete_response = client.patch(
        f"/api/v1/analytics/decision-experiments/{run_id}",
        json={
            "status": "completed",
            "outcome": "success",
            "append_note": "manual success confirmation for threshold apply",
        },
    )

    assert complete_response.status_code == 200
    assert complete_response.json()["run"]["status"] == "completed"
    assert complete_response.json()["run"]["outcome"] == "success"

    apply_response = client.post(
        f"/api/v1/analytics/decision-experiments/{run_id}/apply-thresholds",
        json={"dry_run": False},
    )

    assert apply_response.status_code == 200
    apply_payload = apply_response.json()
    assert apply_payload["run_id"] == run_id
    assert apply_payload["applied"] is True
    assert apply_payload["dry_run"] is False
    assert apply_payload["latest_outcome"] == "success"
    assert apply_payload["threshold_updates"][0]["parameter"] == "review_threshold"
    assert apply_payload["threshold_updates"][0]["previous_value"] == pytest.approx(0.45, abs=0.0001)
    assert apply_payload["threshold_updates"][0]["suggested_value"] == pytest.approx(0.49, abs=0.0001)
    assert apply_payload["strategy_thresholds"]["bid_now_threshold"] == pytest.approx(0.7, abs=0.0001)
    assert apply_payload["strategy_thresholds"]["review_threshold"] == pytest.approx(0.49, abs=0.0001)

    strategy_response = client.get("/api/v1/operator/strategy")
    assert strategy_response.status_code == 200
    strategy_payload = strategy_response.json()
    assert strategy_payload["bid_now_threshold"] == pytest.approx(0.7, abs=0.0001)
    assert strategy_payload["review_threshold"] == pytest.approx(0.49, abs=0.0001)

    after_response = client.post("/api/v1/operations/bid-decision", json=marginal_payload)
    assert after_response.status_code == 200
    assert after_response.json()["action"] == "skip"


def test_legacy_admin_routes_return_single_operator_snapshot(client):
    """Legacy admin routes should now expose singleton operator state instead of multi-user administration."""
    bootstrap = _bootstrap_operator(client, username="admin-operator", email="admin@example.com", password="adminpass123")
    operator_id = bootstrap.json()["id"]

    users_response = client.get("/api/v1/admin/users")
    assert users_response.status_code == 200
    users_payload = users_response.json()
    assert len(users_payload) == 1
    assert users_payload[0]["id"] == operator_id
    assert users_payload[0]["username"] == "admin-operator"

    stats_response = client.get("/api/v1/admin/stats")
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["operator_id"] == operator_id
    assert stats_payload["total_users"] == 1
    assert stats_payload["active_users"] == 1
    assert stats_payload["mode"] == "single_operator"

    deactivate_response = client.put(f"/api/v1/admin/users/{operator_id}/deactivate")
    assert deactivate_response.status_code == 200
    deactivate_payload = deactivate_response.json()
    assert deactivate_payload["status"] == "operator deactivated"
    assert deactivate_payload["requested_user_id"] == operator_id

    failed_session = client.post(
        "/api/v1/auth/session",
        json={"username": "admin-operator", "password": "adminpass123"},
    )
    assert failed_session.status_code == 403
