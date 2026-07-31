"""Tests for operational dashboard reporting."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.models import (
    Analytics,
    CrawlJob,
    DecisionExperimentRun,
    Notification,
    OperatorNotificationChannel,
    OperatorStrategyRun,
    SmokeTestRun,
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.analytics_reporting import (
    AnalyticsReportingService,
    _resolve_g2_evidence_status,
)


def test_ml_manifest_dir_resolves_relative_to_repository_root(monkeypatch):
    """A relative manifest dir must resolve against the repository root.

    ``_ml_manifest_dir`` joins a relative ``ML_RELEASE_MANIFEST_DIR`` onto a
    ``parents``-derived base. Package decomposition that changes this module's
    directory depth silently shifts that base (the #284/#286 regression pointed it
    at ``/app/app`` so the manifest dir went missing). Deriving the expected root
    independently from the ``app`` package pins the arithmetic: if the file depth
    changes again, this assertion catches it.
    """
    import app

    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_DIR", "models/manifests")
    expected_repo_root = Path(app.__file__).resolve().parents[1]

    manifest_dir = AnalyticsReportingService()._ml_manifest_dir()

    assert manifest_dir == expected_repo_root / "models" / "manifests"


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
    strategy_task_failure = next(
        failure for failure in tasks["recent_failures"] if failure["source"] == "strategy_monitor"
    )
    assert strategy_task_failure["detail"] == f"operator_id={operator_id} trigger=scheduled"
    queue_map = {item["queue"]: item for item in tasks["queues"]}
    assert settings.CELERY_OPS_QUEUE in queue_map
    assert "jobs.collect_koneps_notices" in queue_map[settings.CELERY_OPS_QUEUE]["task_names"]
    assert "jobs.monitor_operator_strategy" in queue_map[settings.CELERY_OPS_QUEUE]["task_names"]


def test_g2_evidence_summary_reports_ready_operator_scoped_evidence(client, test_db):
    """G-2 ledger should be ready only when every evidence family is operator-scoped."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    username = operator["username"]
    now = datetime.now(UTC)

    experiment = SyntheticExperiment(
        name="g2-operator-evidence",
        description="operator-scoped synthetic evidence",
        params_json=json.dumps({"limit": 100}),
        operator_slugs_json=json.dumps([username]),
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    test_db.add(experiment)
    test_db.flush()
    synthetic_run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="completed",
        summary_json=json.dumps({"sample_status": "sufficient"}),
        started_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
    )
    test_db.add(synthetic_run)
    test_db.flush()
    test_db.add_all(
        [
            SmokeTestRun(
                started_at=now - timedelta(hours=5),
                completed_at=now - timedelta(hours=4),
                overall_passed=True,
                phases=json.dumps(
                    [
                        {
                            "name": "candidate_generation",
                            "passed": True,
                            "detail": "ok",
                            "evidence": {
                                "operator_id": operator_id,
                                "monitor_run_id": 101,
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                telegram_status="sent",
                created_at=now - timedelta(hours=5),
            ),
            SmokeTestRun(
                started_at=now - timedelta(hours=6),
                completed_at=now - timedelta(hours=5),
                overall_passed=True,
                phases=json.dumps(
                    [
                        {"name": "koneps_collect", "passed": True, "detail": "canonical"},
                        {
                            "name": "candidate_generation",
                            "passed": True,
                            "detail": "other operator",
                            "evidence": {"operator_id": operator_id + 999},
                        },
                    ],
                    ensure_ascii=False,
                ),
                telegram_status="sent",
                created_at=now - timedelta(hours=6),
            ),
            OperatorStrategyRun(
                operator_id=operator_id,
                trigger_source="scheduled",
                status="completed",
                evaluated_project_count=12,
                selected_candidate_count=3,
                persisted_candidate_count=2,
                notification_count=1,
                created_at=now - timedelta(hours=4),
                completed_at=now - timedelta(hours=3),
            ),
            DecisionExperimentRun(
                operator_id=operator_id,
                experiment_key="exp-review-threshold-tighten",
                recommendation_key="review-threshold-tighten",
                status="completed",
                outcome="success",
                priority_rank=1,
                title="G-2 evidence experiment",
                hypothesis="operator scoped",
                suggested_change="raise threshold",
                target_metric="review_submission_rate",
                expected_direction="increase",
                success_criteria="improve",
                guardrail_metric="overall_submission_rate",
                minimum_decision_sample=1,
                duration_days=7,
                baseline_days=7,
                rollback_trigger="guardrail drops",
                baseline_summary=json.dumps({}),
                latest_evaluation=json.dumps({"sample_size": 3}),
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=1),
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
            ),
            Notification(
                user_id=operator_id,
                title="입찰 판단",
                message="operator notification",
                type="recommendation",
                is_read=False,
                created_at=now - timedelta(hours=2),
            ),
            Analytics(
                user_id=operator_id,
                event_type="telegram.delivery",
                event_data=json.dumps({"sent": True, "status": "sent"}),
                timestamp=now - timedelta(hours=2),
            ),
            SyntheticExperimentResult(
                run_id=synthetic_run.id,
                operator_slug=username,
                metrics_json=json.dumps(
                    {
                        "operator_id": operator_id,
                        "settled_count": 35,
                        "missing_settled_count": 0,
                        "sample_status": "sufficient",
                    }
                ),
            ),
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/g2-evidence",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["window_days"] == 30
    assert payload["evidence_status"] == "ready"
    assert payload["blocking_gaps"] == []
    assert payload["smoke"]["status"] == "ready"
    assert payload["smoke"]["counts_toward_g2_ready"] is True
    assert payload["smoke"]["canonical_only_phase_count"] == 1
    assert payload["smoke"]["other_operator_phase_count"] == 1
    assert payload["strategy_monitor"]["completed_count"] == 1
    assert payload["decision_experiments"]["completed_count"] == 1
    assert payload["synthetic_experiments"]["operator_id_scoped_result_count"] == 1
    assert payload["notifications"]["notification_count"] == 1


def test_g2_evidence_summary_flags_canonical_and_slug_only_scope(client, test_db):
    """Canonical smoke and slug-only synthetic evidence must not be reported as G-2 ready."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    username = operator["username"]
    now = datetime.now(UTC)

    experiment = SyntheticExperiment(
        name="g2-slug-only",
        description="slug-only synthetic evidence",
        params_json=json.dumps({"limit": 100}),
        operator_slugs_json=json.dumps([username]),
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    test_db.add(experiment)
    test_db.flush()
    synthetic_run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="completed",
        summary_json=json.dumps({"sample_status": "sufficient"}),
        started_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
    )
    test_db.add(synthetic_run)
    test_db.flush()
    test_db.add_all(
        [
            SmokeTestRun(
                started_at=now - timedelta(hours=5),
                completed_at=now - timedelta(hours=4),
                overall_passed=True,
                phases=json.dumps(
                    [{"name": "koneps_collect", "passed": True, "detail": "ok"}],
                    ensure_ascii=False,
                ),
                telegram_status="sent",
                created_at=now - timedelta(hours=5),
            ),
            OperatorStrategyRun(
                operator_id=operator_id,
                trigger_source="scheduled",
                status="completed",
                evaluated_project_count=10,
                selected_candidate_count=2,
                persisted_candidate_count=1,
                notification_count=1,
                created_at=now - timedelta(hours=4),
                completed_at=now - timedelta(hours=3),
            ),
            DecisionExperimentRun(
                operator_id=operator_id,
                experiment_key="exp-review-threshold-tighten",
                recommendation_key="review-threshold-tighten",
                status="completed",
                outcome="success",
                priority_rank=1,
                title="G-2 mixed-scope experiment",
                hypothesis="operator scoped",
                suggested_change="raise threshold",
                target_metric="review_submission_rate",
                expected_direction="increase",
                success_criteria="improve",
                guardrail_metric="overall_submission_rate",
                minimum_decision_sample=1,
                duration_days=7,
                baseline_days=7,
                rollback_trigger="guardrail drops",
                baseline_summary=json.dumps({}),
                latest_evaluation=json.dumps({}),
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=1),
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
            ),
            Notification(
                user_id=operator_id,
                title="입찰 판단",
                message="operator notification",
                type="recommendation",
                is_read=False,
                created_at=now - timedelta(hours=2),
            ),
            SyntheticExperimentResult(
                run_id=synthetic_run.id,
                operator_slug=username,
                metrics_json=json.dumps(
                    {
                        "settled_count": 35,
                        "missing_settled_count": 0,
                        "sample_status": "sufficient",
                    }
                ),
            ),
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/g2-evidence",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_status"] == "mixed_scope"
    assert payload["smoke"]["status"] == "mixed_scope"
    assert payload["smoke"]["counts_toward_g2_ready"] is False
    assert payload["synthetic_experiments"]["status"] == "mixed_scope"
    assert payload["synthetic_experiments"]["slug_only_result_count"] == 1
    assert any("operator_id" in gap for gap in payload["blocking_gaps"])


def test_g2_evidence_summary_treats_smoke_as_supporting_and_dry_run_policy_as_notification(
    client, test_db
):
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    username = operator["username"]
    now = datetime.now(UTC)

    experiment = SyntheticExperiment(
        name="g2-fastlane",
        description="operator-scoped synthetic evidence",
        params_json=json.dumps({"limit": 100, "settle_actions": ["bid_now", "review"]}),
        operator_slugs_json=json.dumps([username]),
        created_at=now - timedelta(hours=4),
        updated_at=now - timedelta(hours=4),
    )
    test_db.add(experiment)
    test_db.flush()
    synthetic_run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="completed",
        summary_json=json.dumps({"sample_status": "sufficient"}),
        started_at=now - timedelta(hours=3),
        finished_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=3),
    )
    test_db.add(synthetic_run)
    test_db.flush()
    test_db.add_all(
        [
            SmokeTestRun(
                started_at=now - timedelta(hours=6),
                completed_at=now - timedelta(hours=5),
                overall_passed=True,
                phases=json.dumps(
                    [{"name": "koneps_collect", "passed": True, "detail": "canonical"}],
                    ensure_ascii=False,
                ),
                telegram_status="sent",
                created_at=now - timedelta(hours=6),
            ),
            OperatorStrategyRun(
                operator_id=operator_id,
                trigger_source="manual",
                status="completed",
                evaluated_project_count=10,
                selected_candidate_count=2,
                persisted_candidate_count=2,
                notification_count=0,
                created_at=now - timedelta(hours=3),
                completed_at=now - timedelta(hours=2),
            ),
            DecisionExperimentRun(
                operator_id=operator_id,
                experiment_key="exp-review-threshold-tighten",
                recommendation_key="review-threshold-tighten",
                status="completed",
                outcome="inconclusive",
                priority_rank=1,
                title="G-2 dry-run notification policy",
                hypothesis="operator scoped",
                suggested_change="raise threshold",
                target_metric="review_submission_rate",
                expected_direction="increase",
                success_criteria="improve",
                guardrail_metric="overall_submission_rate",
                minimum_decision_sample=1,
                duration_days=7,
                baseline_days=7,
                rollback_trigger="guardrail drops",
                baseline_summary=json.dumps({}),
                latest_evaluation=json.dumps({"sample_size": 2}),
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=1),
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
            ),
            OperatorNotificationChannel(
                operator_id=operator_id,
                channel_type="telegram",
                route_key="telegram:synthetic-test",
                target_label="masked",
                is_active=True,
                dry_run_only=True,
                verified_at=now - timedelta(hours=1),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            ),
            SyntheticExperimentResult(
                run_id=synthetic_run.id,
                operator_slug=username,
                metrics_json=json.dumps(
                    {
                        "operator_id": operator_id,
                        "settled_count": 40,
                        "missing_settled_count": 0,
                        "sample_status": "sufficient",
                    }
                ),
            ),
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/g2-evidence",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_status"] == "ready"
    assert payload["blocking_gaps"] == []
    assert payload["supporting_gaps"]
    assert payload["smoke"]["status"] == "mixed_scope"
    assert payload["smoke"]["counts_toward_g2_ready"] is False
    assert payload["notifications"]["status"] == "ready"
    assert payload["notifications"]["source_run_type"] == "operator_notification_policy"
    assert payload["notifications"]["dry_run_policy_evidence_count"] == 1
    assert payload["notifications"]["evidence_count"] == 1


def test_resolve_g2_evidence_status_missing_when_no_evidence():
    """No evidence resolves to ``missing`` and returns the missing gap only."""
    status, gap = _resolve_g2_evidence_status(
        has_evidence=False,
        has_mixed_scope=True,
        has_ready=True,
        missing_gap="none exists",
        mixed_scope_gap="mixed",
        insufficient_gap="short",
    )
    assert status == "missing"
    assert gap == "none exists"


def test_resolve_g2_evidence_status_mixed_scope_takes_precedence_over_ready():
    """Mixed-scope evidence wins over ready per canonical precedence."""
    status, gap = _resolve_g2_evidence_status(
        has_evidence=True,
        has_mixed_scope=True,
        has_ready=True,
        mixed_scope_gap="slug only",
        insufficient_gap="short",
    )
    assert status == "mixed_scope"
    assert gap == "slug only"


def test_resolve_g2_evidence_status_insufficient_when_evidence_not_ready():
    """Operator-scoped but not-ready evidence resolves to ``insufficient``."""
    status, gap = _resolve_g2_evidence_status(
        has_evidence=True,
        has_mixed_scope=False,
        has_ready=False,
        insufficient_gap="not completed",
    )
    assert status == "insufficient"
    assert gap == "not completed"


def test_resolve_g2_evidence_status_ready_carries_no_blocking_gap():
    """Ready evidence never returns a blocking gap even if gaps are supplied."""
    status, gap = _resolve_g2_evidence_status(
        has_evidence=True,
        has_mixed_scope=False,
        has_ready=True,
        insufficient_gap="short",
        missing_gap="none",
    )
    assert status == "ready"
    assert gap is None


def test_resolve_g2_evidence_status_gaps_default_to_none():
    """Non-ready branches return ``None`` when their gap message is omitted."""
    status, gap = _resolve_g2_evidence_status(
        has_evidence=True,
        has_mixed_scope=False,
        has_ready=False,
    )
    assert status == "insufficient"
    assert gap is None


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
    assert smoke["healthy_streak_target"] == 7
    assert smoke["current_streak_meets_target"] is False
    assert smoke["schedule_enabled"] is True
    assert smoke["failure_category_breakdown"] == {"prediction": 1}

    per_phase = {item["name"]: item for item in smoke["per_phase"]}
    assert set(per_phase) == {
        "koneps_collect",
        "sbert_embedding",
        "predict_price",
        "candidate_generation",
        "telegram_ping",
    }
    assert per_phase["predict_price"]["pass_rate"] == pytest.approx(0.6667, abs=0.0001)
    assert per_phase["predict_price"]["evaluated_count"] == 3
    assert per_phase["koneps_collect"]["pass_rate"] == pytest.approx(1.0, abs=0.0001)

    assert smoke["latest"] is not None
    assert smoke["latest"]["overall_passed"] is True
    assert [p["name"] for p in smoke["latest"]["phases"]] == [
        "koneps_collect", "sbert_embedding", "predict_price", "telegram_ping"
    ]
    latest_phase_evidence = smoke["latest"]["phases"][0]["evidence"]
    assert latest_phase_evidence["evidence_scope"] == "g0_scheduled_smoke"
    assert latest_phase_evidence["operator_scope"] == "canonical_only"
    assert latest_phase_evidence["source_run_type"] == "smoke_test_run"
    assert "G-2 per-operator evidence" in latest_phase_evidence["canonical_only_reason"]

    assert len(smoke["recent_failures"]) == 1
    assert smoke["recent_failures"][0]["failed_phases"] == ["predict_price"]
    assert smoke["recent_failures"][0]["failure_categories"] == ["prediction"]
    assert smoke["recent_failures"][0]["failure_category_breakdown"] == {"prediction": 1}

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
    assert smoke["healthy_streak_target"] == 7
    assert smoke["current_streak_meets_target"] is False
    assert smoke["schedule_enabled"] is False
    assert smoke["failure_category_breakdown"] == {}
    assert smoke["latest"] is None
    assert smoke["recent_failures"] == []
    # all scheduled phases present with zero evaluated_count
    per_phase = {item["name"]: item for item in smoke["per_phase"]}
    assert len(per_phase) == 5
    assert all(item["evaluated_count"] == 0 for item in smoke["per_phase"])

    cards = {card["key"]: card for card in response.json()["cards"]}
    # schedule disabled + no data → honest info tone
    assert cards["smoke_test_streak"]["status"] == "info"


def test_operations_dashboard_smoke_streak_breaks_on_middle_failure(client, test_db, monkeypatch):
    """A failure between passing cycles must break the streak (newest-first P, F, P → 1)."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    # Persist oldest-first so the rows materialize as newest-first P, F, P.
    test_db.add_all([
        SmokeTestRun(
            started_at=now - timedelta(days=2, minutes=1),
            completed_at=now - timedelta(days=2),
            overall_passed=True,
            phases=_phases_json(True, True, True, True),
            telegram_status="sent",
            created_at=now - timedelta(days=2),
        ),
        SmokeTestRun(
            started_at=now - timedelta(days=1, minutes=1),
            completed_at=now - timedelta(days=1),
            overall_passed=False,
            phases=_phases_json(True, True, False, True),
            telegram_status="sent",
            created_at=now - timedelta(days=1),
        ),
        SmokeTestRun(
            started_at=now - timedelta(minutes=1),
            completed_at=now,
            overall_passed=True,
            phases=_phases_json(True, True, True, True),
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
    # Only the most-recent pass counts; the middle failure breaks the streak.
    assert smoke["current_streak"] == 1
    assert smoke["current_streak_meets_target"] is False


def test_operations_dashboard_smoke_g0_healthy_requires_seven_green_cycles(
    client, test_db, monkeypatch
):
    """G-0 healthy status is tied to the roadmap's 7 consecutive green cycles."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    test_db.add_all(
        [
            SmokeTestRun(
                started_at=now - timedelta(days=offset, minutes=1),
                completed_at=now - timedelta(days=offset),
                overall_passed=True,
                phases=_phases_json(True, True, True, True),
                telegram_status="sent",
                created_at=now - timedelta(days=offset),
            )
            for offset in range(7)
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    smoke = payload["smoke_test"]
    assert smoke["current_streak"] == 7
    assert smoke["healthy_streak_target"] == 7
    assert smoke["current_streak_meets_target"] is True
    cards = {card["key"]: card for card in payload["cards"]}
    assert cards["smoke_test_streak"]["status"] == "healthy"


def test_operations_dashboard_smoke_failure_category_breakdown(
    client, test_db, monkeypatch
):
    """Smoke failures should be grouped into fixed roadmap buckets."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    test_db.add_all(
        [
            SmokeTestRun(
                started_at=now - timedelta(days=1, minutes=1),
                completed_at=now - timedelta(days=1),
                overall_passed=False,
                phases=_phases_json_with_skips(
                    ("koneps_collect", False, "service key unauthorized 401"),
                    ("sbert_embedding", False, "skipped — Phase 1 failed"),
                    ("predict_price", False, "skipped — no eligible project"),
                    ("telegram_ping", False, "Telegram API rejected"),
                ),
                telegram_status="failed",
                created_at=now - timedelta(days=1),
            ),
            SmokeTestRun(
                started_at=now - timedelta(minutes=1),
                completed_at=now,
                overall_passed=False,
                phases=_phases_json_with_skips(
                    ("koneps_collect", False, "KONEPS OpenAPI timeout"),
                    ("sbert_embedding", False, "OperationalError no such table"),
                    ("predict_price", False, "guardrail exception"),
                    ("telegram_ping", True, "ok"),
                ),
                telegram_status="sent",
                created_at=now,
            ),
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    assert smoke["failure_category_breakdown"] == {
        "credential": 1,
        "db_schema": 1,
        "koneps_response": 1,
        "no_candidate": 1,
        "prediction": 1,
        "telegram": 1,
    }
    assert smoke["latest"]["phases"][0]["failure_category"] == "koneps_response"
    assert smoke["recent_failures"][0]["failure_category_breakdown"] == {
        "db_schema": 1,
        "koneps_response": 1,
        "prediction": 1,
    }


def test_operations_dashboard_smoke_exposes_actionable_phase_evidence(
    client, test_db, monkeypatch
):
    """Persisted category/action/retry/evidence fields should survive API serialization."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    test_db.add(
        SmokeTestRun(
            started_at=now - timedelta(minutes=1),
            completed_at=now,
            overall_passed=False,
            phases=json.dumps(
                [
                    {
                        "name": "candidate_generation",
                        "passed": False,
                        "detail": "exception: RuntimeError: strategy monitor failed",
                        "failure_category": "candidate_generation",
                        "action_required": "Inspect the strategy monitor run and candidate filters.",
                        "retry_method": "Rerun /api/v1/operator/strategy/monitor.",
                        "skip_reason": "no strategy candidates selected",
                        "evidence": {
                            "monitor_run_id": 77,
                            "operator_id": 42,
                            "evaluated_project_count": 5,
                            "selected_candidate_count": 0,
                            "notification_count": 0,
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            telegram_status="sent",
            created_at=now,
        )
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    phase = smoke["latest"]["phases"][0]
    assert phase["failure_category"] == "candidate_generation"
    assert phase["action_required"] == "Inspect the strategy monitor run and candidate filters."
    assert phase["retry_method"] == "Rerun /api/v1/operator/strategy/monitor."
    assert phase["skip_reason"] == "no strategy candidates selected"
    assert phase["evidence"]["monitor_run_id"] == 77
    assert phase["evidence"]["source_run_type"] == "operator_strategy_monitor"
    assert phase["evidence"]["source_run_id"] == 77
    assert phase["evidence"]["operator_id"] == 42
    assert phase["evidence"]["operator_scope"] == "operator"
    assert phase["evidence"]["source_smoke_run_id"] is not None
    assert smoke["recent_failures"][0]["failure_actions"] == [
        "Inspect the strategy monitor run and candidate filters."
    ]
    assert smoke["recent_failures"][0]["retry_methods"] == [
        "Rerun /api/v1/operator/strategy/monitor."
    ]
    assert smoke["recent_failures"][0]["phase_details"][0]["evidence"]["notification_count"] == 0


def test_operations_dashboard_smoke_tolerates_malformed_phases_json(client, test_db, monkeypatch):
    """A run with non-JSON phases must not crash; latest.phases degrades to []."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    test_db.add_all([
        SmokeTestRun(
            started_at=now - timedelta(days=1, minutes=1),
            completed_at=now - timedelta(days=1),
            overall_passed=True,
            phases=_phases_json(True, True, True, True),
            telegram_status="sent",
            created_at=now - timedelta(days=1),
        ),
        # Most-recent run has malformed phases JSON.
        SmokeTestRun(
            started_at=now - timedelta(minutes=1),
            completed_at=now,
            overall_passed=True,
            phases="not json {[",
            telegram_status="sent",
            created_at=now,
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    assert smoke["cycle_count"] == 2
    # Latest is the malformed run; phases parse to an empty list (no crash).
    assert smoke["latest"] is not None
    assert smoke["latest"]["phases"] == []
    # The malformed cycle contributes nothing; rates come only from the valid run.
    per_phase = {item["name"]: item for item in smoke["per_phase"]}
    assert per_phase["koneps_collect"]["evaluated_count"] == 1
    assert per_phase["koneps_collect"]["pass_rate"] == pytest.approx(1.0, abs=0.0001)
    assert per_phase["predict_price"]["evaluated_count"] == 1


def _phases_json_with_skips(*records):
    """Serialize smoke phases from explicit (name, passed, detail) records."""
    return json.dumps(
        [
            {"name": name, "passed": bool(passed), "detail": detail}
            for name, passed, detail in records
        ],
        ensure_ascii=False,
    )


def test_operations_dashboard_smoke_per_phase_excludes_skipped(client, test_db, monkeypatch):
    """Skipped phase occurrences are excluded from per-phase attempted count and pass rate."""
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    _bootstrap_operator(client)
    now = datetime.now(UTC)

    # predict_price: skipped in two cycles, attempted+passed in one → 1/1.
    # sbert_embedding: skipped in ALL cycles → evaluated_count == 0, pass_rate 0.0.
    skipped_phases = _phases_json_with_skips(
        ("koneps_collect", True, "ok"),
        ("sbert_embedding", False, "skipped — Phase 1 failed"),
        ("predict_price", False, "skipped — no eligible project"),
        ("telegram_ping", True, "ok"),
    )
    attempted_phases = _phases_json_with_skips(
        ("koneps_collect", True, "ok"),
        ("sbert_embedding", False, "skipped — Phase 1 failed"),
        ("predict_price", True, "predicted 1,234,000"),
        ("telegram_ping", True, "ok"),
    )

    test_db.add_all([
        SmokeTestRun(
            started_at=now - timedelta(days=2, minutes=1),
            completed_at=now - timedelta(days=2),
            overall_passed=False,
            phases=skipped_phases,
            telegram_status="sent",
            created_at=now - timedelta(days=2),
        ),
        SmokeTestRun(
            started_at=now - timedelta(days=1, minutes=1),
            completed_at=now - timedelta(days=1),
            overall_passed=False,
            phases=skipped_phases,
            telegram_status="sent",
            created_at=now - timedelta(days=1),
        ),
        SmokeTestRun(
            started_at=now - timedelta(minutes=1),
            completed_at=now,
            overall_passed=True,
            phases=attempted_phases,
            telegram_status="sent",
            created_at=now,
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-dashboard", params={"days": 30, "recent_limit": 5})

    assert response.status_code == 200
    smoke = response.json()["smoke_test"]
    per_phase = {item["name"]: item for item in smoke["per_phase"]}

    # predict_price was attempted in exactly one cycle (and passed); skips excluded.
    assert per_phase["predict_price"]["evaluated_count"] == 1
    assert per_phase["predict_price"]["pass_rate"] == pytest.approx(1.0, abs=0.0001)

    # koneps_collect / telegram_ping ran every cycle.
    assert per_phase["koneps_collect"]["evaluated_count"] == 3
    assert per_phase["telegram_ping"]["evaluated_count"] == 3

    # sbert_embedding was skipped in ALL cycles → honest empty (0 / 0.0).
    assert per_phase["sbert_embedding"]["evaluated_count"] == 0
    assert per_phase["sbert_embedding"]["pass_rate"] == 0.0


def test_operations_dashboard_summarizes_g1_synthetic_validation(client, test_db):
    """Operations report should show G-1 preset/sample state beside smoke telemetry."""
    _bootstrap_operator(client)
    now = datetime.now(UTC)
    experiment = SyntheticExperiment(
        name="g1-construction-base-12m",
        description="G-1 construction preset",
        params_json=json.dumps({"limit": 200, "scenario": "base"}),
        operator_slugs_json=json.dumps(["cn-small-gangwon"]),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    test_db.add(experiment)
    test_db.flush()
    run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="completed",
        summary_json=json.dumps(
            {
                "sample_status": "sufficient",
                "total_settled_count": 128,
                "missing_total_settled_count": 0,
                "insufficient_operators": [],
            }
        ),
        started_at=now - timedelta(hours=1),
        finished_at=now,
        created_at=now,
    )
    test_db.add(run)
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200
    synthetic = response.json()["synthetic_validation"]
    assert synthetic["preset_count"] == 4
    assert synthetic["saved_preset_count"] == 1
    assert synthetic["completed_preset_count"] == 1
    assert synthetic["sufficient_preset_count"] == 1
    assert synthetic["recent_run_count"] == 1
    assert synthetic["recent_completed_count"] == 1
    assert "Canonical G-1 synthetic validation" in synthetic["detail"]
    assert synthetic["latest"]["experiment_name"] == "g1-construction-base-12m"
    assert synthetic["latest"]["total_settled_count"] == 128
    construction = {
        item["name"]: item for item in synthetic["presets"]
    }["g1-construction-base-12m"]
    assert construction["latest_run_status"] == "completed"
    assert construction["sample_status"] == "sufficient"
    cards = {card["key"]: card for card in response.json()["cards"]}
    assert cards["synthetic_g1_presets"]["value"] == 1
    assert cards["synthetic_g1_samples"]["value"] == 1


def test_operations_dashboard_g1_preset_uses_latest_run_across_duplicate_names(
    client, test_db
):
    """Duplicate preset-name rows must not hide the newest completed preset run."""
    _bootstrap_operator(client)
    now = datetime.now(UTC)
    completed_experiment = SyntheticExperiment(
        name="g1-construction-base-12m",
        description="completed duplicate",
        params_json=json.dumps({"limit": 200, "scenario": "base"}),
        operator_slugs_json=json.dumps(["cn-small-gangwon"]),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    empty_duplicate = SyntheticExperiment(
        name="g1-construction-base-12m",
        description="empty duplicate",
        params_json=json.dumps({"limit": 200, "scenario": "base"}),
        operator_slugs_json=json.dumps(["cn-small-gangwon"]),
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    test_db.add_all([completed_experiment, empty_duplicate])
    test_db.flush()
    test_db.add(
        SyntheticExperimentRun(
            experiment_id=completed_experiment.id,
            status="completed",
            summary_json=json.dumps(
                {
                    "sample_status": "sufficient",
                    "total_settled_count": 128,
                    "missing_total_settled_count": 0,
                    "insufficient_operators": [],
                }
            ),
            started_at=now - timedelta(hours=1),
            finished_at=now,
            created_at=now,
        )
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"days": 30, "recent_limit": 5},
    )

    assert response.status_code == 200
    synthetic = response.json()["synthetic_validation"]
    construction = {
        item["name"]: item for item in synthetic["presets"]
    }["g1-construction-base-12m"]
    assert construction["experiment_id"] == completed_experiment.id
    assert construction["latest_run_status"] == "completed"
    assert construction["sample_status"] == "sufficient"
    assert "Canonical G-1 synthetic validation" in synthetic["detail"]
    assert synthetic["completed_preset_count"] == 1
    assert synthetic["sufficient_preset_count"] == 1


def test_notification_summary_keeps_unreadable_delivery_rows_in_the_denominator(
    client, test_db, monkeypatch
):
    """배달 레코드 타입화 후에도 집계 산출이 불변임을 고정한다.

    저장된 payload 는 이제 선언된 계약으로 복원되지만, 해석 불가 행도 **배달 시도는
    있었던 행**이라 성공률 분모에서 사라지면 안 된다(운영 리포트는 빈 모델로 degrade
    하고, 키 없는 payload 를 종전과 같이 ``unknown`` 으로 센다). 피로도 게이트가 같은
    행을 무시하는 것과 의도적으로 다른 정책이다.
    """
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    now = datetime.now(UTC)

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "12345")

    test_db.add_all([
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
            timestamp=now - timedelta(hours=3),
        ),
        Analytics(
            user_id=operator_id,
            event_type="telegram.delivery",
            event_data="{not json at all",
            timestamp=now - timedelta(hours=2),
        ),
        Analytics(
            user_id=operator_id,
            event_type="telegram.delivery",
            # legacy Python repr 행도 계속 읽힌다(ast 폴백).
            event_data=str({
                "notification_id": 3,
                "source": "bid_decision",
                "sent": False,
                "status": "pending_configuration",
                "detail": "Telegram is not configured yet.",
            }),
            timestamp=now - timedelta(hours=1),
        ),
    ])
    test_db.commit()

    summary = AnalyticsReportingService()._build_notification_summary(
        test_db,
        operator_id=operator_id,
        date_from=now - timedelta(days=1),
        recent_limit=5,
    )

    assert summary["telegram_delivery_attempt_count"] == 3
    assert summary["telegram_sent_count"] == 1
    assert summary["telegram_pending_configuration_count"] == 1
    assert summary["telegram_status_counts"] == {
        "pending_configuration": 1,
        "sent": 1,
        "unknown": 1,
    }
    assert summary["telegram_success_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert [item["notification_id"] for item in summary["recent_telegram_failures"]] == [3]
