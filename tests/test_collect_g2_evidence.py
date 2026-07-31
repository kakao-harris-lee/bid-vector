"""G-2 evidence collection beat schedule + read-only snapshot task.

The task is a pure *observation* tool: once daily it snapshots the per-operator
G-2 evidence ledger (via ``AnalyticsReportingService.build_g2_evidence_summary``)
into a single ``collect_g2_evidence`` analytics evidence event so ``counted_days``
can accumulate toward the G-2 exit review. It must never run monitors, write to
operator data (``operator_strategy_runs`` / ``bid_decision_records`` /
notifications), call external services, or send Telegram.
"""

from __future__ import annotations

import json

# Import ORM models at module top so SQLAlchemy registers their tables on
# Base.metadata before the `test_db` fixture runs create_all (matches the
# codebase convention used by the candidate-recheck schedule tests).
from app.models.models import (  # noqa: F401 — registers table metadata
    Analytics,
    CompanyProfile,
    OperatorStrategy,
    OperatorStrategyRun,
    Project,
    User,
)
from app.tasks.celery_app import (
    COLLECT_G2_EVIDENCE_TASK_NAME,
    build_celery_runtime_config,
    build_collect_g2_evidence_beat_schedule,
)


# ---------------------------------------------------------------------------
# Beat schedule builder
# ---------------------------------------------------------------------------


def test_collect_g2_evidence_schedule_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED", False)
    assert build_collect_g2_evidence_beat_schedule() == {}


def test_collect_g2_evidence_schedule_builds_when_enabled(monkeypatch):
    from celery.schedules import crontab

    from app.core.config import settings

    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_HOUR_KST", 22)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_MINUTE", 0)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_WINDOW_DAYS", 21)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_RECENT_LIMIT", 7)

    schedule = build_collect_g2_evidence_beat_schedule()
    entry = schedule["collect_g2_evidence_daily"]

    assert entry["task"] == COLLECT_G2_EVIDENCE_TASK_NAME
    assert isinstance(entry["schedule"], crontab)
    # Celery beat runs timezone="Asia/Seoul"/enable_utc=False -> hour IS KST.
    assert entry["schedule"].hour == {22}
    assert entry["schedule"].minute == {0}
    # Config window/recent settings must feed the scheduled run (not dead settings).
    assert entry["kwargs"] == {"window_days": 21, "recent_limit": 7}


def test_collect_g2_evidence_schedule_clamps_out_of_range(monkeypatch):
    from celery.schedules import crontab

    from app.core.config import settings

    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_HOUR_KST", 99)
    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_MINUTE", -5)

    entry = build_collect_g2_evidence_beat_schedule()["collect_g2_evidence_daily"]
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {23}
    assert entry["schedule"].minute == {0}


def test_collect_g2_evidence_schedule_included_in_runtime_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED", True)

    beat = build_celery_runtime_config()["beat_schedule"]
    assert "collect_g2_evidence_daily" in beat


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


def test_collect_g2_evidence_snapshots_each_operator_and_logs_one_event(
    test_db, monkeypatch
):
    """Sweep summarizes canonical + each synthetic operator; writes one event."""
    from app.core.single_user import ensure_operator_account
    from app.services.analytics_reporting import AnalyticsReportingService
    from app.tasks.jobs import collect_g2_evidence

    _patch_session(monkeypatch, test_db)

    # Canonical operator (resolved by the task) + two active synthetic operators.
    operator = ensure_operator_account(test_db)
    op_a = _seed_synthetic_operator(test_db, slug="alpha")
    op_b = _seed_synthetic_operator(test_db, slug="bravo")
    # Inactive synthetic operator must be skipped.
    _seed_synthetic_operator(test_db, slug="charlie", is_active=False)

    summarized: list[int] = []

    def fake_summary(self, db, *, window_days=30, recent_limit=5, operator=None):
        summarized.append(int(operator.id))
        status = "ready" if operator.id == op_a.id else "insufficient"
        gaps = [] if status == "ready" else ["missing strategy monitor evidence"]
        return {
            "operator_id": int(operator.id),
            "window_days": window_days,
            "evidence_status": status,
            "smoke": {"status": "ready"},
            "strategy_monitor": {"status": status},
            "decision_experiments": {"status": "missing"},
            "synthetic_experiments": {"status": "ready"},
            "notifications": {"status": "ready"},
            "blocking_gaps": gaps,
        }

    monkeypatch.setattr(
        AnalyticsReportingService, "build_g2_evidence_summary", fake_summary
    )

    result = collect_g2_evidence()

    # Canonical operator first, then both active synthetic operators.
    assert summarized == [operator.id, op_a.id, op_b.id]
    assert result["operator_count"] == 3
    assert result["ready_count"] == 1
    assert result["error_count"] == 0
    assert result["generated_window_days"] == 30

    # Exactly one analytics evidence event recorded.
    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "collect_g2_evidence")
        .all()
    )
    assert len(events) == 1
    # The summary row is scoped to the canonical operator.
    assert events[0].user_id == operator.id

    payload = json.loads(events[0].event_data)
    assert payload["operator_count"] == 3
    assert payload["ready_count"] == 1
    usernames = {row["username"] for row in payload["per_operator"]}
    assert {"synthetic-alpha", "synthetic-bravo"}.issubset(usernames)

    by_id = {row["operator_id"]: row for row in payload["per_operator"]}
    alpha_row = by_id[op_a.id]
    assert alpha_row["evidence_status"] == "ready"
    assert alpha_row["blocking_gaps_count"] == 0
    assert alpha_row["sections"]["strategy_monitor"] == "ready"

    bravo_row = by_id[op_b.id]
    assert bravo_row["evidence_status"] == "insufficient"
    assert bravo_row["blocking_gaps_count"] == 1
    assert bravo_row["sections"]["strategy_monitor"] == "insufficient"

    # Read-only invariant: no operator strategy runs were written.
    assert test_db.query(OperatorStrategyRun).count() == 0


def test_collect_g2_evidence_continues_when_one_operator_raises(test_db, monkeypatch):
    """A single operator summary failure is recorded but does not abort the sweep."""
    from app.core.single_user import ensure_operator_account
    from app.services.analytics_reporting import AnalyticsReportingService
    from app.tasks.jobs import collect_g2_evidence

    _patch_session(monkeypatch, test_db)

    operator = ensure_operator_account(test_db)
    op_a = _seed_synthetic_operator(test_db, slug="alpha")

    seen: list[int] = []

    def fake_summary(self, db, *, window_days=30, recent_limit=5, operator=None):
        seen.append(int(operator.id))
        if operator.id == op_a.id:
            raise RuntimeError("boom: evidence summary failed for alpha")
        return {
            "operator_id": int(operator.id),
            "window_days": window_days,
            "evidence_status": "ready",
            "smoke": {"status": "ready"},
            "strategy_monitor": {"status": "ready"},
            "decision_experiments": {"status": "ready"},
            "synthetic_experiments": {"status": "ready"},
            "notifications": {"status": "ready"},
            "blocking_gaps": [],
        }

    monkeypatch.setattr(
        AnalyticsReportingService, "build_g2_evidence_summary", fake_summary
    )

    result = collect_g2_evidence()

    # Both operators were attempted despite the synthetic one raising.
    assert seen == [operator.id, op_a.id]
    assert result["operator_count"] == 2
    assert result["error_count"] == 1
    assert result["ready_count"] == 1  # only the canonical operator was ready

    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "collect_g2_evidence")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].event_data)
    by_username = {row["username"]: row for row in payload["per_operator"]}
    assert "error" in by_username["synthetic-alpha"]

    # Read-only invariant holds even on partial failure.
    assert test_db.query(OperatorStrategyRun).count() == 0
