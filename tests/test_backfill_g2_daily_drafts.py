"""Reconstruction rule for backfilling daily G-2 ledger drafts from snapshots.

``reconstruct_pass_draft`` rebuilds a ledger draft for a historical
``collect_g2_evidence`` snapshot only when that day genuinely passed (all target
operators ready, zero blocking gaps), reusing ``build_daily_evidence_draft`` as
the single source of truth for the pass rule.
"""

from __future__ import annotations

from scripts.backfill_g2_daily_drafts import reconstruct_pass_draft

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
