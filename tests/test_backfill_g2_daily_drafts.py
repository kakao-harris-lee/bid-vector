"""Reconstruction rule for backfilling daily G-2 ledger drafts from snapshots.

``reconstruct_pass_draft`` rebuilds a ledger draft for a historical
``collect_g2_evidence`` snapshot only when that day genuinely passed (all target
operators ready, zero blocking gaps), reusing ``build_daily_evidence_draft`` as
the single source of truth for the pass rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.backfill_g2_daily_drafts import reconstruct_pass_draft, select_pass_drafts

TARGET_IDS = [19, 20, 25]
_SECTIONS = {
    "smoke": "ready",
    "strategy_monitor": "ready",
    "decision_experiments": "ready",
    "synthetic_experiments": "ready",
    "notifications": "ready",
}


def _op(operator_id, *, status="ready", gaps=0, slug="op"):
    return {
        "operator_id": operator_id,
        "username": f"synthetic-{slug}",
        "evidence_status": status,
        "blocking_gaps_count": gaps,
        "sections": dict(_SECTIONS),
    }


def _event(ops):
    return {"per_operator": ops}


def test_all_targets_ready_reconstructs_a_pass_draft():
    draft = reconstruct_pass_draft(
        event_data=_event([_op(19), _op(20), _op(25)]),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is not None
    assert draft["daily_status"][0]["status"] == "pass"
    assert draft["daily_status"][0]["date"] == "2026-07-06"
    assert draft["evidence_window"]["counted_days"] == 1
    assert [o["operator_id"] for o in draft["operators"]] == TARGET_IDS


def test_one_insufficient_target_does_not_reconstruct():
    draft = reconstruct_pass_draft(
        event_data=_event([_op(19), _op(20, status="insufficient"), _op(25)]),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is None


def test_blocking_gap_count_blocks_reconstruction():
    draft = reconstruct_pass_draft(
        event_data=_event([_op(19), _op(20), _op(25, gaps=1)]),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is None


def test_missing_target_snapshot_does_not_reconstruct():
    draft = reconstruct_pass_draft(
        event_data=_event([_op(19), _op(20)]),  # 25 missing
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is None


def test_errored_target_does_not_reconstruct():
    ops = [_op(19), _op(20), {"operator_id": 25, "username": "x", "error": "RuntimeError"}]
    draft = reconstruct_pass_draft(
        event_data=_event(ops),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is None


# 2026-07-06: 01:00 UTC = 10:00 KST, 13:00 UTC = 22:00 KST (same KST date).
_MORNING = datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc)
_EVENING = datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)
_READY = {"per_operator": [_op(19), _op(20), _op(25)]}
_FLICKER = {"per_operator": [_op(19), _op(20), _op(25, status="insufficient")]}


def test_flicker_uses_last_snapshot_of_day_and_drops_it():
    """ready -> insufficient by the day's last snapshot must NOT be counted."""
    out = select_pass_drafts(
        snapshots=[(_MORNING, _READY), (_EVENING, _FLICKER)],
        target_operator_ids=TARGET_IDS,
        required_days=7,
    )
    assert "2026-07-06" not in out


def test_flicker_last_snapshot_pass_is_counted():
    """insufficient -> ready by the day's last snapshot IS counted (latest wins)."""
    out = select_pass_drafts(
        snapshots=[(_MORNING, _FLICKER), (_EVENING, _READY)],
        target_operator_ids=TARGET_IDS,
        required_days=7,
    )
    assert "2026-07-06" in out
    assert out["2026-07-06"]["daily_status"][0]["status"] == "pass"


def test_main_apply_is_idempotent(tmp_path, monkeypatch, capsys):
    """--apply writes once; a second --apply skips the existing file."""
    import scripts.backfill_g2_daily_drafts as mod
    from app.core.config import settings

    draft = reconstruct_pass_draft(
        event_data=_READY,
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    monkeypatch.setattr(mod, "_passing_dates", lambda db, **kw: {"2026-07-06": draft})
    monkeypatch.setattr(settings, "G2_EVIDENCE_TARGET_OPERATOR_IDS", "19,20,25")
    # Absolute right operand overrides the REPO_ROOT join inside main().
    monkeypatch.setattr(settings, "G2_EVIDENCE_DAILY_DRAFT_DIR", str(tmp_path))

    path = tmp_path / "2026-07-06" / "manifest-draft.json"

    # Dry-run writes nothing.
    assert mod.main([]) == 0
    assert not path.exists()

    # First apply writes the file.
    assert mod.main(["--apply"]) == 0
    assert path.exists()

    # Second apply skips the existing file (idempotent, not rewritten).
    capsys.readouterr()
    assert mod.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "wrote (0)" in out
    assert "skipped existing (1)" in out
