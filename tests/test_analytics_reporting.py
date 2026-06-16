"""Tests for operational dashboard reporting."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.models.models import (
    Analytics,
    CrawlJob,
    Notification,
    OperatorStrategyRun,
    SmokeTestRun,
)


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
    assert cards["task_broker_health"]["status"] == "watch"
    assert cards["task_failure_rate"]["status"] == "critical"

    tasks = payload["tasks"]
    assert tasks["broker"]["transport"] == "memory"
    assert tasks["broker"]["health_status"] == "watch"
    assert tasks["runtime"]["inline_ml_tasks_allowed"] is False
    assert tasks["tracked_task_count"] == 6
    assert tasks["completed_count"] == 3
    assert tasks["failed_count"] == 2
    assert tasks["retry_count"] == 0
    assert tasks["running_count"] == 1
    assert tasks["failure_rate"] == pytest.approx(0.3333, abs=0.0001)
    assert "broker_not_production_ready" in tasks["risk_flags"]
    assert "task_failures_detected" in tasks["risk_flags"]
    assert {failure["source"] for failure in tasks["recent_failures"]} == {"crawl", "strategy_monitor"}
    queue_map = {item["queue"]: item for item in tasks["queues"]}
    assert settings.CELERY_OPS_QUEUE in queue_map
    assert "jobs.collect_koneps_notices" in queue_map[settings.CELERY_OPS_QUEUE]["task_names"]
    assert "jobs.monitor_operator_strategy" in queue_map[settings.CELERY_OPS_QUEUE]["task_names"]


def test_operations_dashboard_reports_external_broker_and_stale_tasks(client, test_db, monkeypatch):
    """Operations dashboard should expose external broker diagnostics and stale task risk."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    now = datetime.now(UTC)

    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "amqp://bidvector:secret@rabbitmq:5672/bidvector")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "db+postgresql+psycopg://biduser:secret@db:5432/bid_vector_db")
    monkeypatch.setattr(settings, "CELERY_ALLOW_INLINE_ML_TASKS", False)
    monkeypatch.setattr(settings, "CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP", True)

    test_db.add_all([
        CrawlJob(
            source="koneps",
            target_date="2026-05-12",
            status="queued",
            result_count=0,
            created_at=now - timedelta(minutes=20),
        ),
        OperatorStrategyRun(
            operator_id=operator_id,
            task_id="strategy-task-1",
            trigger_source="scheduled",
            status="running",
            evaluated_project_count=0,
            selected_candidate_count=0,
            persisted_candidate_count=0,
            notification_count=0,
            created_at=now - timedelta(minutes=40),
            started_at=now - timedelta(minutes=31),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    payload = response.json()
    tasks = payload["tasks"]
    assert tasks["broker"]["url"] == "amqp://bidvector:***@rabbitmq:5672/bidvector"
    assert tasks["result_backend"]["url"] == "db+postgresql+psycopg://biduser:***@db:5432/bid_vector_db"
    assert tasks["broker"]["health_status"] == "healthy"
    assert tasks["result_backend"]["health_status"] == "healthy"
    assert tasks["runtime"]["health_status"] == "healthy"
    assert tasks["queued_count"] == 1
    assert tasks["running_count"] == 1
    assert tasks["retry_count"] == 0
    assert tasks["stale_task_count"] == 2
    assert tasks["backlog_status"] == "critical"
    assert "stale_tasks_detected" in tasks["risk_flags"]
    assert tasks["recent_delayed_tasks"][0]["age_seconds"] >= tasks["stale_task_threshold_seconds"]

    cards = {card["key"]: card for card in payload["cards"]}
    assert cards["task_broker_health"]["status"] == "healthy"
    assert cards["task_stale_queue"]["status"] == "critical"


def test_operations_dashboard_reports_telegram_and_ml_release_cards(client, test_db, tmp_path, monkeypatch):
    """Operations dashboard should summarize Telegram delivery telemetry and release manifests."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    now = datetime.now(UTC)

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE", False)

    manifest_path = tmp_path / "2026-05-12-release.json"
    manifest_path.write_text(
        json.dumps(
            {
                "release_tag": "2026-05-12-release",
                "validated_on": now.isoformat(),
                "recommended_docker_target": "api-training",
                "promotion_gate": {
                    "predictor_backtest": {
                        "status": "passed",
                        "passed": True,
                        "thresholds": {
                            "policy": "standard",
                            "min_dataset_quality_status": "warning",
                        },
                        "best_predictor_key": "ensemble",
                        "best_predictor_name": "ensemble_blend",
                        "metrics": {
                            "sample_count": 8,
                            "average_absolute_error_rate": 0.012,
                            "dataset_quality_status": "passed",
                            "best_predictor_key": "ensemble",
                            "best_predictor_name": "ensemble_blend",
                        },
                        "reasons": ["Predictor backtest gate passed."],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    test_db.add_all([
        Notification(
            user_id=operator_id,
            title="입찰 판단 · 프로젝트 1",
            message="decision notification",
            type="recommendation",
            is_read=False,
            created_at=now - timedelta(hours=3),
        ),
        Notification(
            user_id=operator_id,
            title="투찰 완료 · 프로젝트 1",
            message="bid notification",
            type="bid_update",
            is_read=True,
            created_at=now - timedelta(hours=2),
        ),
        Analytics(
            user_id=operator_id,
            event_type="telegram.delivery",
            event_data=json.dumps({
                "notification_id": 1,
                "source": "bid_decision",
                "sent": True,
                "status": "sent",
                "detail": "Telegram delivery succeeded.",
            }),
            timestamp=now - timedelta(hours=2),
        ),
        Analytics(
            user_id=operator_id,
            event_type="telegram.delivery",
            event_data=json.dumps({
                "notification_id": 2,
                "source": "bid_submission",
                "sent": False,
                "status": "failed",
                "detail": "Telegram API rejected the message.",
            }),
            timestamp=now - timedelta(hours=1),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    payload = response.json()
    notifications = payload["notifications"]
    assert notifications["notification_count"] == 2
    assert notifications["unread_count"] == 1
    assert notifications["decision_notification_count"] == 1
    assert notifications["bid_submission_notification_count"] == 1
    assert notifications["telegram_configured"] is True
    assert notifications["telegram_delivery_attempt_count"] == 2
    assert notifications["telegram_sent_count"] == 1
    assert notifications["telegram_failed_count"] == 1
    assert notifications["telegram_success_rate"] == pytest.approx(0.5, abs=0.0001)
    assert notifications["telegram_status"] == "critical"
    assert notifications["telegram_status_counts"] == {"failed": 1, "sent": 1}
    assert notifications["recent_telegram_failures"][0]["source"] == "bid_submission"

    ml_release = payload["ml_release"]
    assert ml_release["manifest_count"] == 1
    assert ml_release["latest_release_tag"] == "2026-05-12-release"
    assert ml_release["latest_signature_status"] == "missing"
    assert ml_release["latest_gate_status"] == "passed"
    assert ml_release["latest_gate_passed"] is True
    assert ml_release["latest_gate_policy"] == "standard"
    assert ml_release["latest_dataset_quality_status"] == "passed"
    assert ml_release["latest_best_predictor_key"] == "ensemble"
    assert ml_release["latest_backtest_sample_count"] == 8
    assert ml_release["latest_backtest_average_absolute_error_rate"] == pytest.approx(0.012, abs=0.0001)
    assert ml_release["status"] == "watch"
    assert ml_release["backtest_status"] == "healthy"

    cards = {card["key"]: card for card in payload["cards"]}
    assert cards["telegram_delivery_rate"]["status"] == "critical"
    assert cards["telegram_delivery_rate"]["value"] == pytest.approx(0.5, abs=0.0001)
    assert cards["ml_release_gate"]["status"] == "watch"
    assert cards["ml_backtest_samples"]["status"] == "healthy"


def test_operations_dashboard_uses_manifest_validation_time_for_latest_release(
    client, tmp_path, monkeypatch
):
    """The release dashboard should not treat a recently touched old manifest as latest."""
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE", False)

    newer_manifest = {
        "release_tag": "2026-05-16-price-v1",
        "validated_on": now.isoformat(),
        "signature": {"algorithm": "HMAC-SHA256", "digest": "invalid"},
        "promotion_gate": {
            "predictor_backtest": {
                "status": "passed",
                "passed": True,
                "thresholds": {"policy": "standard"},
                "best_predictor_key": "ensemble",
                "metrics": {
                    "sample_count": 5,
                    "average_absolute_error_rate": 0.0028,
                    "dataset_quality_status": "warning",
                },
            }
        },
    }
    older_manifest = {
        "release_tag": "2026-05-15-price-v1",
        "validated_on": (now - timedelta(days=1)).isoformat(),
        "promotion_gate": {
            "predictor_backtest": {
                "status": "passed",
                "passed": True,
                "thresholds": {"policy": "standard"},
                "best_predictor_key": "lstm",
                "metrics": {
                    "sample_count": 4,
                    "average_absolute_error_rate": 0.004,
                    "dataset_quality_status": "passed",
                },
            }
        },
    }
    (tmp_path / "2026-05-16-price-v1.json").write_text(
        json.dumps(newer_manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "2026-05-15-price-v1.json").write_text(
        json.dumps(older_manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ml_release"]["latest_release_tag"] == "2026-05-16-price-v1"
    assert payload["ml_release"]["latest_signature_status"] == "invalid"
    assert payload["ml_release"]["status"] == "watch"
    cards = {card["key"]: card for card in payload["cards"]}
    assert cards["ml_release_gate"]["detail"] == (
        "Latest manifest 2026-05-16-price-v1 has an invalid optional signature."
    )


def _phases_json(*passes):
    """Serialize trimmed smoke phases for a run, one (name, passed) per phase."""
    names = ["koneps_collect", "sbert_embedding", "predict_price", "telegram_ping"]
    return json.dumps(
        [
            {"name": name, "passed": bool(passed), "detail": "ok" if passed else "fail"}
            for name, passed in zip(names, passes)
        ],
        ensure_ascii=False,
    )


def test_operations_dashboard_summarizes_smoke_cycles(client, test_db, monkeypatch):
    """Smoke summary should report pass rate, streak, per-phase rates, and failures."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    # Newest-first the runs will be: pass (now), pass (-1d), fail (-2d).
    # current_streak = 2 (two most-recent passing cycles before the failure).
    test_db.add_all([
        SmokeTestRun(
            started_at=now - timedelta(days=2, minutes=1),
            completed_at=now - timedelta(days=2),
            overall_passed=False,
            phases=_phases_json(True, True, False, True),
            telegram_message_id=None,
            telegram_status="sent",
            created_at=now - timedelta(days=2),
        ),
        SmokeTestRun(
            started_at=now - timedelta(days=1, minutes=1),
            completed_at=now - timedelta(days=1),
            overall_passed=True,
            phases=_phases_json(True, True, True, True),
            telegram_message_id=11,
            telegram_status="sent",
            created_at=now - timedelta(days=1),
        ),
        SmokeTestRun(
            started_at=now - timedelta(minutes=1),
            completed_at=now,
            overall_passed=True,
            phases=_phases_json(True, True, True, True),
            telegram_message_id=12,
            telegram_status="sent",
            created_at=now,
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    assert smoke["cycle_count"] == 3
    assert smoke["passed_count"] == 2
    assert smoke["failed_count"] == 1
    assert smoke["pass_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert smoke["current_streak"] == 2
    assert smoke["schedule_enabled"] is True

    per_phase = {item["name"]: item for item in smoke["per_phase"]}
    assert set(per_phase) == {"koneps_collect", "sbert_embedding", "predict_price", "telegram_ping"}
    assert per_phase["predict_price"]["pass_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert per_phase["predict_price"]["evaluated_count"] == 3
    assert per_phase["koneps_collect"]["pass_rate"] == pytest.approx(1.0, abs=0.0001)

    assert smoke["latest"] is not None
    assert smoke["latest"]["overall_passed"] is True
    assert [p["name"] for p in smoke["latest"]["phases"]] == [
        "koneps_collect", "sbert_embedding", "predict_price", "telegram_ping"
    ]

    assert len(smoke["recent_failures"]) == 1
    assert smoke["recent_failures"][0]["failed_phases"] == ["predict_price"]

    cards = {card["key"]: card for card in response.json()["cards"]}
    assert "smoke_test_streak" in cards
    assert "smoke_test_pass_rate" in cards
    assert cards["smoke_test_pass_rate"]["value"] == pytest.approx(0.6667, abs=0.0001)


def test_operations_dashboard_smoke_empty_window_is_honest(client, test_db, monkeypatch):
    """Empty window → honest zeros, latest=None, no crash; schedule flag reflected."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", False)
    _bootstrap_operator(client)

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    assert smoke["cycle_count"] == 0
    assert smoke["passed_count"] == 0
    assert smoke["failed_count"] == 0
    assert smoke["pass_rate"] == 0.0
    assert smoke["current_streak"] == 0
    assert smoke["schedule_enabled"] is False
    assert smoke["latest"] is None
    assert smoke["recent_failures"] == []
    # all 4 phases present with zero evaluated_count
    per_phase = {item["name"]: item for item in smoke["per_phase"]}
    assert len(per_phase) == 4
    assert all(item["evaluated_count"] == 0 for item in smoke["per_phase"])

    cards = {card["key"]: card for card in response.json()["cards"]}
    # schedule disabled + no data → honest info tone
    assert cards["smoke_test_streak"]["status"] == "info"
