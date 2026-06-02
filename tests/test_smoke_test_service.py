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
    monkeypatch.setattr(svc, "_phase_telegram_ping", lambda **kw: _mk_phase("telegram_ping", True, "sent"))

    report = svc.run(db=MagicMock())
    assert report.overall_passed is True
    assert [p["name"] for p in report.phases] == ["koneps_collect", "sbert_embedding", "predict_price", "telegram_ping"]


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
    # Phase 4 still attempted (the point of the smoke is to verify Telegram even on failure)
    assert by_name["telegram_ping"]["passed"] is True


def _mk_phase(name, passed, detail, **data):
    from app.services.smoke_test import PhaseResult
    return PhaseResult(name=name, passed=passed, detail=detail, data=data)
