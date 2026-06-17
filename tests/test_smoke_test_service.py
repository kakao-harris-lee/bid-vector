"""Smoke test schedule + service."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.tasks.celery_app import (
    SMOKE_TEST_TASK_NAME,
    build_smoke_test_beat_schedule,
)


def test_smoke_test_schedule_empty_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", False)
    assert build_smoke_test_beat_schedule() == {}


def test_smoke_test_schedule_builds_when_enabled(monkeypatch):
    from celery.schedules import crontab
    from app.core.config import settings
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "SMOKE_TEST_HOUR_UTC", 7)
    monkeypatch.setattr(settings, "SMOKE_TEST_MINUTE", 0)
    schedule = build_smoke_test_beat_schedule()
    entry = schedule["smoke_test_daily"]
    assert entry["task"] == SMOKE_TEST_TASK_NAME
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {7}
    assert entry["schedule"].minute == {0}


def test_smoke_service_reports_phases_with_full_chain(monkeypatch):
    """Service produces one PhaseResult per phase even when later phases skip."""
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    svc = KonepsTelegramSmokeTestService()
    # Mock phase methods individually
    monkeypatch.setattr(svc, "_phase_koneps_collect", lambda db: _mk_phase("koneps_collect", True, "collected 5"))
    monkeypatch.setattr(svc, "_phase_sbert_embedding", lambda db: _mk_phase("sbert_embedding", True, "id=99", project={"id": 99, "title": "t", "budget_estimate": 100.0}))
    monkeypatch.setattr(svc, "_phase_predict_price", lambda db, p: _mk_phase("predict_price", True, "rate=0.9"))
    monkeypatch.setattr(svc, "_phase_candidate_generation", lambda db: _mk_phase("candidate_generation", True, "run_id=10 selected=1"))
    monkeypatch.setattr(svc, "_phase_telegram_ping", lambda **kw: _mk_phase("telegram_ping", True, "sent"))

    report = svc.run(db=MagicMock())
    assert report.overall_passed is True
    assert [p["name"] for p in report.phases] == [
        "koneps_collect",
        "sbert_embedding",
        "predict_price",
        "candidate_generation",
        "telegram_ping",
    ]


def test_smoke_service_skips_downstream_when_collect_fails(monkeypatch):
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    svc = KonepsTelegramSmokeTestService()
    monkeypatch.setattr(svc, "_phase_koneps_collect", lambda db: _mk_phase("koneps_collect", False, "exception"))
    monkeypatch.setattr(svc, "_phase_telegram_ping", lambda **kw: _mk_phase("telegram_ping", True, "sent"))

    report = svc.run(db=MagicMock())
    # Phases 2 and 3 should be marked skipped (passed=False)
    by_name = {p["name"]: p for p in report.phases}
    assert by_name["sbert_embedding"]["passed"] is False
    assert "skipped" in by_name["sbert_embedding"]["detail"]
    assert by_name["predict_price"]["passed"] is False
    assert by_name["candidate_generation"]["passed"] is False
    assert by_name["candidate_generation"]["failure_category"] == "koneps_response"
    # Telegram still attempted (the point of the smoke is to verify Telegram even on failure)
    assert by_name["telegram_ping"]["passed"] is True


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0.69, False),  # below floor band
        (0.70, True),   # lower edge
        (0.90, True),   # typical
        (1.00, True),   # upper edge == max guardrail ceiling
        (1.01, False),  # above ceiling — clamp didn't apply, smoke must fail
        (1.10, False),  # old upper bound must no longer pass
    ],
)
def test_predict_price_phase_band_rejects_above_ceiling(monkeypatch, rate, expected):
    """predict_price phase passes iff 0.7 <= rate <= 1.0 (guardrail ceiling)."""
    # `_phase_predict_price` imports its deps inside the function from their
    # origin modules, so patch there (not on app.services.smoke_test).
    import app.ai.business_group as bg_mod
    import app.ai.price_prediction as pp_mod
    import app.services.backtest_cutoff as cutoff_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _StubProject:
        id = 1
        budget_estimate = 100_000_000.0
        title = "t"
        description = ""
        requirements = ""
        category = "소프트웨어"
        business_type_code = None
        issuing_agency = None
        demand_agency = None

    class _StubQuery:
        def filter(self, *a, **k):
            return self

        def one(self):
            return _StubProject()

    class _StubDB:
        def query(self, *a, **k):
            return _StubQuery()

    class _StubCutoff:
        def resolve_data_cutoff_at(self, *a, **k):
            return None

        def load_price_history_at_cutoff(self, *a, **k):
            return []

    monkeypatch.setattr(bg_mod, "resolve_business_group", lambda code: None)
    monkeypatch.setattr(cutoff_mod, "BacktestCutoffService", _StubCutoff)
    monkeypatch.setattr(
        pp_mod,
        "predict_price",
        lambda **kw: {"predicted_bid_rate": rate, "predictor_name": "stub"},
    )

    svc = KonepsTelegramSmokeTestService()
    res = svc._phase_predict_price(_StubDB(), {"id": 1})
    assert res.passed is expected


def test_candidate_generation_phase_records_notification_skip_reason(monkeypatch):
    """A zero-notification monitor run can pass when the skip reason is explicit."""
    import app.services.opportunity_monitoring as monitoring_mod
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    class _StubMonitoringService:
        SCHEDULED_TRIGGER_SOURCE = "scheduled"

        def execute_monitoring(self, db, *, request, trigger_source):
            return {
                "monitor_run_id": 77,
                "evaluated_project_count": 5,
                "selected_candidate_count": 0,
                "persisted_candidate_count": 0,
                "notification_count": 0,
                "new_candidate_count": 0,
            }

    monkeypatch.setattr(monitoring_mod, "StrategyMonitoringService", _StubMonitoringService)

    result = KonepsTelegramSmokeTestService()._phase_candidate_generation(MagicMock())

    assert result.passed is True
    assert result.failure_category == ""
    assert result.skip_reason == "no strategy candidates selected"
    assert result.data["monitor_run_id"] == 77
    assert "skip_reason=no strategy candidates selected" in result.detail


def test_persist_report_inserts_trimmed_row(test_db):
    """persist_report inserts a SmokeTestRun, trims per-phase data, parses timestamps."""
    import json

    from app.models.models import SmokeTestRun
    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="2026-06-16T07:00:00+00:00",
        completed_at="2026-06-16T07:01:30+00:00",
        overall_passed=True,
        phases=[
            {"name": "koneps_collect", "passed": True, "detail": "collected 5", "data": {"collected_count": 5}},
            {"name": "telegram_ping", "passed": True, "detail": "sent", "data": {"delivery": {"big": "payload"}}},
        ],
        telegram_message_id=4242,
        telegram_status="sent",
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    assert run.id is not None
    assert run.overall_passed is True
    assert run.telegram_message_id == 4242
    assert run.telegram_status == "sent"
    assert run.started_at is not None and run.completed_at is not None

    stored = json.loads(run.phases)
    assert stored == [
        {"name": "koneps_collect", "passed": True, "detail": "collected 5", "evidence": {"collected_count": 5}},
        {"name": "telegram_ping", "passed": True, "detail": "sent"},
    ]
    # bulky per-phase data dict must be dropped
    assert all("data" not in phase for phase in stored)

    rows = test_db.query(SmokeTestRun).all()
    assert len(rows) == 1


def test_persist_report_tolerates_empty_timestamps_and_null_telegram(test_db):
    """persist_report must not raise when timestamps are empty and Telegram is null.

    ENVIRONMENT=test skips Telegram, so telegram_message_id is None.
    """
    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="",
        completed_at="",
        overall_passed=False,
        phases=[{"name": "koneps_collect", "passed": False, "detail": "exception"}],
        telegram_message_id=None,
        telegram_status="",
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    assert run.id is not None
    assert run.started_at is None
    assert run.completed_at is None
    assert run.overall_passed is False
    assert run.telegram_message_id is None
    assert run.telegram_status is None


def test_persist_report_keeps_actionable_failure_evidence(test_db):
    """Failed phases keep category/action/retry/skip evidence without bulky data."""
    import json

    from app.services.smoke_test import KonepsTelegramSmokeTestService, SmokeTestReport

    report = SmokeTestReport(
        started_at="2026-06-16T07:00:00+00:00",
        completed_at="2026-06-16T07:01:30+00:00",
        overall_passed=False,
        phases=[
            {
                "name": "candidate_generation",
                "passed": False,
                "detail": "exception: RuntimeError: strategy monitor failed",
                "failure_category": "candidate_generation",
                "action_required": "Inspect the strategy monitor run and candidate filters.",
                "retry_method": "Rerun /api/v1/operator/strategy/monitor.",
                "skip_reason": "no strategy candidates selected",
                "data": {
                    "monitor_run_id": 77,
                    "evaluated_project_count": 4,
                    "selected_candidate_count": 0,
                    "large_results": [{"ignore": True}],
                },
            }
        ],
    )

    run = KonepsTelegramSmokeTestService().persist_report(test_db, report)

    stored = json.loads(run.phases)
    assert stored == [
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
                "evaluated_project_count": 4,
                "selected_candidate_count": 0,
            },
        }
    ]


def _mk_phase(name, passed, detail, **data):
    from app.services.smoke_test import PhaseResult
    return PhaseResult(name=name, passed=passed, detail=detail, data=data)
