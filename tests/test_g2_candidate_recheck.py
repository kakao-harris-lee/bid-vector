"""G-2 candidate re-check beat schedule + read-only sweep task.

The task is an *observation* tool: it re-runs the read-only
``preview_candidates`` for every active synthetic operator and records a single
``g2_candidate_recheck`` analytics evidence event. It must never call the heavy
``execute_monitoring`` nor write to operator data
(``operator_strategy_runs`` / ``bid_decision_records`` / notifications).
"""

from __future__ import annotations

import json

# Import ORM models at module top so SQLAlchemy registers their tables on
# Base.metadata before the `test_db` fixture runs create_all (matches the
# codebase convention used by the smoke-test schedule tests).
from app.models.models import (  # noqa: F401 — registers table metadata
    Analytics,
    CompanyProfile,
    OperatorStrategy,
    OperatorStrategyRun,
    Project,
    User,
)
from app.tasks.celery_app import (
    G2_CANDIDATE_RECHECK_TASK_NAME,
    build_celery_runtime_config,
    build_g2_candidate_recheck_beat_schedule,
)


# ---------------------------------------------------------------------------
# Beat schedule builder
# ---------------------------------------------------------------------------


def test_g2_recheck_schedule_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED", False)
    assert build_g2_candidate_recheck_beat_schedule() == {}


def test_g2_recheck_schedule_builds_when_enabled(monkeypatch):
    from celery.schedules import crontab

    from app.core.config import settings

    monkeypatch.setattr(settings, "G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "G2_CANDIDATE_RECHECK_HOUR_KST", 21)
    monkeypatch.setattr(settings, "G2_CANDIDATE_RECHECK_MINUTE", 0)

    schedule = build_g2_candidate_recheck_beat_schedule()
    entry = schedule["g2_candidate_recheck_daily"]

    assert entry["task"] == G2_CANDIDATE_RECHECK_TASK_NAME
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {21}
    assert entry["schedule"].minute == {0}


def test_g2_recheck_schedule_included_in_runtime_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED", True)

    beat = build_celery_runtime_config()["beat_schedule"]
    assert "g2_candidate_recheck_daily" in beat


# ---------------------------------------------------------------------------
# Task body
# ---------------------------------------------------------------------------


def _seed_synthetic_operator(test_db, *, slug: str, is_active: bool = True) -> User:
    """Create a synthetic-* operator with a profile + strategy (read targets)."""
    user = User(
        username=f"synthetic-{slug}",
        email=f"{slug}@synthetic.test.local",
        full_name=f"테스트 {slug}",
        company=f"회사 {slug}",
        hashed_password="x",
        is_active=is_active,
    )
    test_db.add(user)
    test_db.flush()
    test_db.add(CompanyProfile(user_id=user.id))
    test_db.add(OperatorStrategy(user_id=user.id))
    test_db.commit()
    test_db.refresh(user)
    return user


def _patch_session(monkeypatch, test_db):
    """Make the task session factory return the test session (kept open)."""

    class _NoCloseSession:
        def __init__(self, db):
            self._db = db

        def __getattr__(self, name):
            return getattr(self._db, name)

        def close(self):  # task always closes in finally; keep the fixture alive
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _NoCloseSession(test_db))


def test_g2_recheck_previews_each_operator_and_logs_one_event(test_db, monkeypatch):
    """Sweep calls preview per synthetic operator and writes one analytics event."""
    import app.services.opportunity_monitoring as monitoring_mod
    from app.tasks.jobs import run_g2_candidate_recheck

    op_a = _seed_synthetic_operator(test_db, slug="alpha")
    op_b = _seed_synthetic_operator(test_db, slug="bravo")
    # Inactive synthetic operator must be skipped.
    _seed_synthetic_operator(test_db, slug="charlie", is_active=False)

    previewed: list[int] = []

    def fake_preview(self, db, *, limit=None, high_priority_only=None, operator=None):
        previewed.append(int(operator.id))
        # alpha returns 2 candidates, bravo returns 0.
        returned = 2 if operator.id == op_a.id else 0
        return {
            "operator_id": operator.id,
            "evaluated_project_count": 5,
            "returned_candidate_count": returned,
            "high_priority_only": False,
            "candidates": [],
        }

    monkeypatch.setattr(
        monitoring_mod.StrategyMonitoringService, "preview_candidates", fake_preview
    )

    # execute_monitoring must never be reached by this read-only sweep.
    def _forbidden(*args, **kwargs):
        raise AssertionError("execute_monitoring must not be called by the re-check")

    monkeypatch.setattr(
        monitoring_mod.StrategyMonitoringService, "execute_monitoring", _forbidden
    )

    _patch_session(monkeypatch, test_db)

    result = run_g2_candidate_recheck()

    # Only the two active synthetic operators were previewed (deterministic order).
    assert previewed == [op_a.id, op_b.id]
    assert result["operator_count"] == 2
    assert result["total_candidates"] == 2
    assert result["operators_with_candidates"] == 1
    assert result["error_count"] == 0

    # Exactly one analytics evidence event recorded.
    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "g2_candidate_recheck")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].event_data)
    assert payload["total_candidates"] == 2
    assert payload["operators_with_candidates"] == 1
    usernames = {row["username"] for row in payload["per_operator"]}
    assert usernames == {"synthetic-alpha", "synthetic-bravo"}

    # Read-only invariant: no operator strategy runs were written.
    assert test_db.query(OperatorStrategyRun).count() == 0


def test_g2_recheck_records_event_when_no_synthetic_operators(test_db, monkeypatch):
    """An empty sweep still completes and logs one zero-count evidence event.

    With no active synthetic operators (none seeded, or all inactive) the task
    must not crash and must still record a single ``g2_candidate_recheck`` event
    carrying ``operator_count=0`` / ``total_candidates=0`` / ``per_operator=[]``.
    """
    import app.services.opportunity_monitoring as monitoring_mod
    from app.tasks.jobs import run_g2_candidate_recheck

    # Only inactive synthetic operators exist -> the sweep set is empty.
    _seed_synthetic_operator(test_db, slug="dormant", is_active=False)

    def _must_not_preview(self, db, **kwargs):
        raise AssertionError("preview_candidates must not run with no active operators")

    monkeypatch.setattr(
        monitoring_mod.StrategyMonitoringService,
        "preview_candidates",
        _must_not_preview,
    )
    _patch_session(monkeypatch, test_db)

    result = run_g2_candidate_recheck()

    assert result["operator_count"] == 0
    assert result["total_candidates"] == 0
    assert result["operators_with_candidates"] == 0
    assert result["error_count"] == 0
    assert result["per_operator"] == []

    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "g2_candidate_recheck")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].event_data)
    assert payload["operator_count"] == 0
    assert payload["total_candidates"] == 0
    assert payload["per_operator"] == []

    # Read-only invariant holds for the empty sweep too.
    assert test_db.query(OperatorStrategyRun).count() == 0


def test_g2_recheck_continues_when_one_operator_raises(test_db, monkeypatch):
    """A single operator preview failure is recorded but does not abort the sweep."""
    import app.services.opportunity_monitoring as monitoring_mod
    from app.tasks.jobs import run_g2_candidate_recheck

    op_a = _seed_synthetic_operator(test_db, slug="alpha")
    op_b = _seed_synthetic_operator(test_db, slug="bravo")

    seen: list[int] = []

    def fake_preview(self, db, *, limit=None, high_priority_only=None, operator=None):
        seen.append(int(operator.id))
        if operator.id == op_a.id:
            raise RuntimeError("boom: preview failed for alpha")
        return {
            "operator_id": operator.id,
            "evaluated_project_count": 3,
            "returned_candidate_count": 1,
            "high_priority_only": False,
            "candidates": [],
        }

    monkeypatch.setattr(
        monitoring_mod.StrategyMonitoringService, "preview_candidates", fake_preview
    )
    _patch_session(monkeypatch, test_db)

    result = run_g2_candidate_recheck()

    # Both operators were attempted despite the first raising.
    assert seen == [op_a.id, op_b.id]
    assert result["operator_count"] == 2
    assert result["error_count"] == 1
    # bravo still contributed its candidate.
    assert result["total_candidates"] == 1
    assert result["operators_with_candidates"] == 1

    # The event still records the failed operator with an error marker.
    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "g2_candidate_recheck")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].event_data)
    by_username = {row["username"]: row for row in payload["per_operator"]}
    assert "error" in by_username["synthetic-alpha"]
    assert by_username["synthetic-bravo"]["returned_candidate_count"] == 1

    # Read-only invariant holds even on partial failure.
    assert test_db.query(OperatorStrategyRun).count() == 0
