"""Daily ledger-based G-2 manifest-draft builder + beat write path.

The ``collect_g2_evidence`` beat task writes one ``manifest-draft.json`` per KST
day for the target operators so ``scripts/build_g2_exit_review.py`` can
accumulate ``counted_days`` toward the G-2 exit review WITHOUT a human running
``scripts/collect_g2_evidence.py`` daily. The draft's "pass" verdict is derived
from the per-operator evidence ledger (``build_g2_evidence_summary``), not the
CLI's live endpoint-scope checks. These tests pin the status rule and prove
compatibility with the exit-review builder's consumers.
"""

from __future__ import annotations

import json

# Register ORM table metadata before the test_db fixture calls create_all.
from app.models.models import (  # noqa: F401 — registers table metadata
    Analytics,
    CompanyProfile,
    OperatorStrategy,
    OperatorStrategyRun,
    User,
)
from app.services.g2_evidence_draft import (
    DRAFT_SOURCE,
    build_daily_evidence_draft,
    daily_status_from_target_summaries,
)
from scripts.build_g2_exit_review import (
    _counted_days,
    _merged_operators,
    build_review_manifest,
)

TARGET_IDS = [19, 20, 25]
SECTIONS_READY = {
    "smoke": "ready",
    "strategy_monitor": "ready",
    "decision_experiments": "ready",
    "synthetic_experiments": "ready",
    "notifications": "ready",
}


def _ready(operator_id: int, slug: str) -> dict:
    return {
        "operator_id": operator_id,
        "username": f"synthetic-{slug}",
        "evidence_status": "ready",
        "sections": dict(SECTIONS_READY),
        "blocking_gaps": [],
    }


def _insufficient(operator_id: int, slug: str, gaps: list[str]) -> dict:
    return {
        "operator_id": operator_id,
        "username": f"synthetic-{slug}",
        "evidence_status": "insufficient",
        "sections": {**SECTIONS_READY, "strategy_monitor": "insufficient"},
        "blocking_gaps": list(gaps),
    }


def _errored(operator_id: int, slug: str) -> dict:
    return {
        "operator_id": operator_id,
        "username": f"synthetic-{slug}",
        "error": "RuntimeError",
    }


def _all_ready_draft(run_date_kst: str) -> dict:
    return build_daily_evidence_draft(
        operator_summaries=[
            _ready(19, "alpha"),
            _ready(20, "bravo"),
            _ready(25, "charlie"),
        ],
        target_operator_ids=TARGET_IDS,
        run_date_kst=run_date_kst,
        required_days=7,
    )


# ---------------------------------------------------------------------------
# Status rule + draft shape
# ---------------------------------------------------------------------------


def test_all_targets_ready_is_pass_and_counts_one_day():
    draft = _all_ready_draft("2026-07-09")

    row = draft["daily_status"][0]
    assert row["status"] == "pass"
    assert row["source"] == "app/tasks/jobs.py::collect_g2_evidence"
    assert row["collect_g2_evidence_snapshot"]["status"] == "pass"

    window = draft["evidence_window"]
    assert window["counted_days"] == 1
    assert window["required_days"] == 7
    assert window["start_date"] == "2026-07-03"  # end - (7 - 1)
    assert window["end_date"] == "2026-07-09"

    assert [op["operator_id"] for op in draft["operators"]] == TARGET_IDS
    assert draft["blocking_gaps"] == []
    # Provenance stamps flag the ledger-based beat source everywhere.
    assert draft["basis"]["source"] == DRAFT_SOURCE
    assert all(op["source"] == DRAFT_SOURCE for op in draft["operators"])


def test_one_insufficient_target_is_partial_and_surfaces_blocking_gap():
    draft = build_daily_evidence_draft(
        operator_summaries=[
            _ready(19, "alpha"),
            _ready(20, "bravo"),
            _insufficient(25, "charlie", ["strategy monitor evidence missing"]),
        ],
        target_operator_ids=TARGET_IDS,
        run_date_kst="2026-07-09",
        required_days=7,
    )

    assert draft["daily_status"][0]["status"] == "partial"
    assert draft["evidence_window"]["counted_days"] == 0

    # The insufficient target's ledger gap becomes an OPEN blocking gap row so the
    # exit-review gate refuses to count the review as ready.
    assert len(draft["blocking_gaps"]) == 1
    gap = draft["blocking_gaps"][0]
    assert gap["status"] == "open"
    assert gap["operator_id"] == 25
    assert gap["detail"] == "strategy monitor evidence missing"


def test_missing_target_is_fail_but_keeps_full_roster():
    draft = build_daily_evidence_draft(
        operator_summaries=[_ready(19, "alpha"), _ready(20, "bravo")],
        target_operator_ids=TARGET_IDS,
        run_date_kst="2026-07-09",
        required_days=7,
    )

    assert draft["daily_status"][0]["status"] == "fail"
    assert draft["evidence_window"]["counted_days"] == 0
    # Roster still lists all three targets; the missing one is a stub.
    by_id = {op["operator_id"]: op for op in draft["operators"]}
    assert set(by_id) == set(TARGET_IDS)
    assert by_id[25]["evidence_status"] == "missing"
    assert draft["daily_status"][0]["operators"]["25"]["evidence_status"] == "missing"


def test_errored_target_is_fail_and_records_error():
    draft = build_daily_evidence_draft(
        operator_summaries=[
            _ready(19, "alpha"),
            _ready(20, "bravo"),
            _errored(25, "charlie"),
        ],
        target_operator_ids=TARGET_IDS,
        run_date_kst="2026-07-09",
        required_days=7,
    )

    assert draft["daily_status"][0]["status"] == "fail"
    by_id = {op["operator_id"]: op for op in draft["operators"]}
    assert by_id[25]["evidence_status"] == "collection_failed"
    assert by_id[25]["error"] == "RuntimeError"
    assert draft["daily_status"][0]["operators"]["25"]["error"] == "RuntimeError"
    # An errored target contributes no blocking-gap rows.
    assert draft["blocking_gaps"] == []


def test_daily_status_helper_matches_targets_by_operator_id():
    # Summaries out of order + an extra non-target operator are handled by id.
    row = daily_status_from_target_summaries(
        operator_summaries=[
            _ready(25, "charlie"),
            {"operator_id": 999, "username": "synthetic-extra", "error": "X"},
            _ready(19, "alpha"),
            _ready(20, "bravo"),
        ],
        target_operator_ids=TARGET_IDS,
        run_date_kst="2026-07-09",
    )
    assert row["status"] == "pass"
    assert set(row["operators"]) == {"19", "20", "25"}


# ---------------------------------------------------------------------------
# Compatibility with scripts/build_g2_exit_review.py consumers
# ---------------------------------------------------------------------------


def test_pass_draft_feeds_exit_review_counters():
    draft = _all_ready_draft("2026-07-09")
    assert _counted_days(draft["daily_status"]) == 1
    assert len(_merged_operators([draft])) == 3


def test_seven_distinct_pass_days_reach_review_gate():
    drafts = [_all_ready_draft(f"2026-07-{day:02d}") for day in range(3, 10)]
    assert len(drafts) == 7

    manifest = build_review_manifest(drafts, "g2-exit-review-daily-test", 7, 3)
    gate = manifest["review_gate_summary"]
    assert gate["counted_days"] == 7
    assert gate["operator_count"] == 3
    assert gate["open_blocking_gap_count"] == 0
    assert gate["ready_for_review"] is True
    assert manifest["status"] == "ready_for_review"


def test_open_gap_blocks_review_gate_even_with_enough_days():
    ok_days = [_all_ready_draft(f"2026-07-{day:02d}") for day in range(3, 9)]  # 6 pass
    gapped = build_daily_evidence_draft(
        operator_summaries=[
            _ready(19, "alpha"),
            _ready(20, "bravo"),
            _insufficient(25, "charlie", ["strategy monitor evidence missing"]),
        ],
        target_operator_ids=TARGET_IDS,
        run_date_kst="2026-07-09",
        required_days=7,
    )
    manifest = build_review_manifest([*ok_days, gapped], "g2-review-gapped", 7, 3)
    gate = manifest["review_gate_summary"]
    assert gate["counted_days"] == 6  # the partial day is not counted
    assert gate["open_blocking_gap_count"] == 1
    assert gate["ready_for_review"] is False


# ---------------------------------------------------------------------------
# Beat write path (task -> configured draft directory)
# ---------------------------------------------------------------------------


class _NoCloseSession:
    """Wrap the test session so the task's ``finally: db.close()`` is a no-op."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):  # keep the fixture session alive across the task call
        pass


def _seed_target_operator(test_db, *, operator_id: int, slug: str) -> User:
    user = User(
        id=operator_id,
        username=f"synthetic-{slug}",
        email=f"{slug}@synthetic.test.local",
        full_name=f"테스트 {slug}",
        company=f"회사 {slug}",
        hashed_password="x",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def test_beat_writes_daily_draft_to_configured_dir(tmp_path, test_db, monkeypatch):
    """With the flag on and not in test-env, the task writes one draft file."""
    import app.tasks.jobs as jobs_mod
    from app.core.config import settings
    from app.core.single_user import ensure_operator_account
    from app.core.time import kst_now
    from app.services.analytics_reporting import AnalyticsReportingService
    from app.tasks.jobs import collect_g2_evidence

    monkeypatch.setattr(jobs_mod, "SessionLocal", lambda: _NoCloseSession(test_db))

    ensure_operator_account(test_db)
    for operator_id, slug in zip(TARGET_IDS, ("alpha", "bravo", "charlie")):
        _seed_target_operator(test_db, operator_id=operator_id, slug=slug)

    def fake_summary(self, db, *, window_days=30, recent_limit=5, operator=None):
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
    # Bypass the test-env write skip and redirect writes to an absolute tmp dir
    # (an absolute right operand overrides the repo_root join in the task).
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "G2_EVIDENCE_WRITE_DAILY_DRAFT", True)
    monkeypatch.setattr(settings, "G2_EVIDENCE_TARGET_OPERATOR_IDS", "19,20,25")
    monkeypatch.setattr(
        settings, "G2_EVIDENCE_DAILY_DRAFT_DIR", str(tmp_path / "daily")
    )

    collect_g2_evidence()

    draft_path = (
        tmp_path / "daily" / kst_now().date().isoformat() / "manifest-draft.json"
    )
    assert draft_path.exists()
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft["daily_status"][0]["status"] == "pass"
    assert [op["operator_id"] for op in draft["operators"]] == TARGET_IDS
    assert draft["evidence_window"]["counted_days"] == 1
    # Read-only invariant: the draft write touches no operator data.
    assert test_db.query(OperatorStrategyRun).count() == 0


def test_beat_skips_draft_write_in_test_environment(tmp_path, test_db, monkeypatch):
    """ENVIRONMENT=test must not pollute the working tree with a draft file."""
    import app.tasks.jobs as jobs_mod
    from app.core.config import settings
    from app.core.single_user import ensure_operator_account
    from app.services.analytics_reporting import AnalyticsReportingService
    from app.tasks.jobs import collect_g2_evidence

    monkeypatch.setattr(jobs_mod, "SessionLocal", lambda: _NoCloseSession(test_db))
    ensure_operator_account(test_db)
    _seed_target_operator(test_db, operator_id=19, slug="alpha")

    def fake_summary(self, db, *, window_days=30, recent_limit=5, operator=None):
        return {
            "operator_id": int(operator.id),
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
    # settings.ENVIRONMENT stays "test" (conftest default); write must be skipped.
    monkeypatch.setattr(settings, "G2_EVIDENCE_WRITE_DAILY_DRAFT", True)
    monkeypatch.setattr(
        settings, "G2_EVIDENCE_DAILY_DRAFT_DIR", str(tmp_path / "daily")
    )

    collect_g2_evidence()

    assert not (tmp_path / "daily").exists()


def test_draft_write_failure_does_not_abort_sweep(tmp_path, test_db, monkeypatch):
    """A draft-write exception must not roll back the analytics event or the sweep."""
    import app.tasks.jobs as jobs_mod
    from app.core.config import settings
    from app.core.single_user import ensure_operator_account
    from app.services.analytics_reporting import AnalyticsReportingService
    from app.tasks.jobs import collect_g2_evidence

    monkeypatch.setattr(jobs_mod, "SessionLocal", lambda: _NoCloseSession(test_db))
    ensure_operator_account(test_db)
    _seed_target_operator(test_db, operator_id=19, slug="alpha")

    def fake_summary(self, db, *, window_days=30, recent_limit=5, operator=None):
        return {
            "operator_id": int(operator.id),
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
    monkeypatch.setattr(settings, "G2_EVIDENCE_WRITE_DAILY_DRAFT", True)

    def _boom(*, target_summaries):
        raise RuntimeError("disk full")

    monkeypatch.setattr(jobs_mod, "_write_g2_daily_evidence_draft", _boom)

    # The task must swallow the write failure and still return + keep the event.
    result = collect_g2_evidence()

    assert result["error_count"] == 0
    assert test_db.query(Analytics).filter_by(event_type="collect_g2_evidence").count() == 1


def test_shrunk_target_roster_cannot_pass():
    """Fewer than MIN_OPERATORS_FLOOR ready targets is partial, never pass."""
    row = daily_status_from_target_summaries(
        operator_summaries=[_ready(19, "alpha"), _ready(20, "bravo")],
        target_operator_ids=[19, 20],
        run_date_kst="2026-07-09",
    )
    # Only two targets are ready — below the floor of 3 — so no counted day.
    assert row["status"] == "partial"


def test_config_target_operator_ids_parses_dedups_and_filters():
    """CSV parser drops blank/non-int/non-positive tokens and dedups in order."""
    from app.core.config import settings

    original = settings.G2_EVIDENCE_TARGET_OPERATOR_IDS
    try:
        settings.G2_EVIDENCE_TARGET_OPERATOR_IDS = "19, 20, 20, x, -1, 0, 25"
        assert settings.g2_evidence_target_operator_ids == [19, 20, 25]
        settings.G2_EVIDENCE_TARGET_OPERATOR_IDS = ""
        assert settings.g2_evidence_target_operator_ids == []
    finally:
        settings.G2_EVIDENCE_TARGET_OPERATOR_IDS = original
