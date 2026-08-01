"""Reconstruction rule for backfilling daily G-2 ledger drafts from snapshots.

``reconstruct_pass_draft`` rebuilds a ledger draft for a historical
``collect_g2_evidence`` snapshot only when that day genuinely passed (all target
operators ready, zero blocking gaps), reusing ``build_daily_evidence_draft`` as
the single source of truth for the pass rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.g2_evidence import PersistedG2CollectEvidenceSummary
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
    """Restore a stored snapshot row into the persisted contract.

    Fixtures stay raw dicts on purpose: they stand in for rows written by the
    producer, so the restore path (``extra="ignore"`` + optional fields) is part
    of what these tests exercise.
    """
    return PersistedG2CollectEvidenceSummary.model_validate({"per_operator": ops})


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


def test_unknown_stored_keys_are_ignored_on_restore():
    """A row written by a newer/older producer must not break the backfill."""
    ops = [{**_op(19), "future_key": 1}, _op(20), _op(25)]
    draft = reconstruct_pass_draft(
        event_data=_event(ops),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is not None
    assert draft["daily_status"][0]["status"] == "pass"


def test_unrecorded_blocking_gap_count_fails_closed():
    """A snapshot missing ``blocking_gaps_count`` cannot certify a counted day.

    Restoring the absent key as ``0`` would fabricate "zero blocking gaps" for a
    row that never recorded one, so the target is treated as an incomplete
    snapshot and the day is not counted.
    """
    partial = {key: value for key, value in _op(25).items() if key != "blocking_gaps_count"}
    draft = reconstruct_pass_draft(
        event_data=_event([_op(19), _op(20), partial]),
        target_operator_ids=TARGET_IDS,
        required_days=7,
        run_date_kst="2026-07-06",
    )
    assert draft is None


def test_unrecorded_blocking_gap_count_is_classified_as_incomplete(monkeypatch):
    """왜 통과하지 않았는지까지 고정한다 — "gap 0" 오독이 아니라 불완전 스냅샷 분류.

    통과하지 않은 날은 draft 가 버려지므로, 판정 입력(``operator_summaries``)을 직접
    붙잡아 그 target 이 ``incomplete_snapshot`` 에러로 분류됐는지 확인한다.
    """
    import scripts.backfill_g2_daily_drafts as mod

    captured: dict = {}
    real_builder = mod.build_daily_evidence_draft

    def _spy(*, operator_summaries, **kwargs):
        captured["summaries"] = operator_summaries
        return real_builder(operator_summaries=operator_summaries, **kwargs)

    monkeypatch.setattr(mod, "build_daily_evidence_draft", _spy)

    partial = {key: value for key, value in _op(25).items() if key != "blocking_gaps_count"}
    assert (
        reconstruct_pass_draft(
            event_data=_event([_op(19), _op(20), partial]),
            target_operator_ids=TARGET_IDS,
            required_days=7,
            run_date_kst="2026-07-06",
        )
        is None
    )

    by_id = {item["operator_id"]: item for item in captured["summaries"]}
    assert by_id[25]["error"] == "incomplete_snapshot"
    # 완전한 스냅샷은 그대로 정상 셀로 남는다(불완전 분류가 번지지 않는다).
    assert "error" not in by_id[19]
    assert by_id[19]["blocking_gaps"] == []


def test_undecodable_snapshot_row_is_not_counted():
    """A corrupted ``event_data`` row degrades to "no snapshot", never to a pass."""
    out = select_pass_drafts(
        snapshots=[(_MORNING, PersistedG2CollectEvidenceSummary())],
        target_operator_ids=TARGET_IDS,
        required_days=7,
    )
    assert out == {}


# 2026-07-06: 01:00 UTC = 10:00 KST, 13:00 UTC = 22:00 KST (same KST date).
_MORNING = datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc)
_EVENING = datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)
_READY = _event([_op(19), _op(20), _op(25)])
_FLICKER = _event([_op(19), _op(20), _op(25, status="insufficient")])


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
