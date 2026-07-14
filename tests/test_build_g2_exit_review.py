"""Tests for the local G-2 exit review bundle builder."""

from __future__ import annotations

import io
import json
from pathlib import Path

from scripts.build_g2_exit_review import main, write_review_bundle


def _write_manifest_draft(
    path: Path,
    *,
    review_id: str,
    date: str,
    status: str = "pass",
    basis_commit: str = "commit-old",
    operators: list[dict] | None = None,
    blocking_gaps: list[dict] | None = None,
    dry_run_items: list[dict] | None = None,
    approved_execution_items: list[dict] | None = None,
) -> Path:
    manifest = {
        "review_id": review_id,
        "manifest_version": 1,
        "status": "draft",
        "basis": {
            "roadmap": "docs/roadmap.md",
            "runbook": "docs/operations/g2-evidence-runbook.md",
            "review_template": "docs/operations/g2-exit-review-template.md",
            "basis_commit": basis_commit,
        },
        "evidence_window": {
            "start_date": date,
            "end_date": date,
            "required_days": 7,
            "observed_days": 1,
            "counted_days": 1 if status == "pass" else 0,
            "timezone": "Asia/Seoul",
        },
        "operators": operators or [],
        "daily_status": [
            {
                "date": date,
                "status": status,
                "summary": f"daily evidence {date}",
                "operators": {},
                "dry_run_item_ids": [],
                "approved_execution_item_ids": [],
                "excluded_evidence": [],
            }
        ],
        "blocking_gaps": blocking_gaps or [],
        "action_register": {
            "dry_run_items": dry_run_items or [],
            "approved_execution_items": approved_execution_items or [],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _operator(operator_id: int, username: str) -> dict:
    return {
        "operator_id": operator_id,
        "username": username,
        "company": f"Company {operator_id}",
        "is_synthetic": username.startswith("synthetic-"),
        "operator_scope_status": "pass",
        "profile": {"status": "pass", "path": f"profile-{operator_id}.json"},
        "strategy": {"status": "pass", "path": f"strategy-{operator_id}.json"},
        "notification_channel": {
            "status": "pass",
            "path": f"notifications-{operator_id}.json",
        },
        "evidence_paths": {"g2_evidence": [f"g2-{operator_id}.json"]},
    }


def test_build_review_bundle_combines_daily_manifest_drafts(tmp_path):
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review-20260625"
    _write_manifest_draft(
        evidence_root / "2026-06-24" / "manifest-draft.json",
        review_id="daily-20260624",
        date="2026-06-24",
        basis_commit="commit-20260624",
        operators=[
            _operator(101, "synthetic-alpha"),
            _operator(102, "synthetic-bravo"),
        ],
        dry_run_items=[{"item_id": "DRY-001", "status": "completed"}],
    )
    _write_manifest_draft(
        evidence_root / "2026-06-23" / "manifest-draft.json",
        review_id="daily-20260623",
        date="2026-06-23",
        basis_commit="commit-20260623",
        operators=[
            _operator(102, "synthetic-bravo-updated"),
            _operator(103, "synthetic-charlie"),
        ],
        approved_execution_items=[{"item_id": "APP-001", "status": "approved"}],
    )

    result = write_review_bundle(
        evidence_root=evidence_root,
        review_id="review-20260625",
        output_dir=output_dir,
        min_days=2,
        min_operators=3,
    )

    assert result == {
        "manifest": output_dir / "manifest.json",
        "exit_review": output_dir / "exit-review.md",
    }
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_id"] == "review-20260625"
    assert manifest["status"] == "ready_for_review"
    assert manifest["basis"]["basis_commit"] == "commit-20260624"
    assert [operator["operator_id"] for operator in manifest["operators"]] == [
        101,
        102,
        103,
    ]
    assert [row["date"] for row in manifest["daily_status"]] == [
        "2026-06-23",
        "2026-06-24",
    ]
    assert manifest["review_gate_summary"] == {
        "counted_days": 2,
        "required_days": 2,
        "operator_count": 3,
        "required_operator_count": 3,
        "open_blocking_gap_count": 0,
        "ready_for_review": True,
    }
    assert manifest["action_register"]["dry_run_items"] == [
        {"item_id": "DRY-001", "status": "completed"}
    ]
    assert manifest["action_register"]["approved_execution_items"] == [
        {"item_id": "APP-001", "status": "approved"}
    ]
    exit_review = (output_dir / "exit-review.md").read_text(encoding="utf-8")
    assert "- Unresolved blocking gaps: 0" in exit_review
    assert exit_review.endswith("G-2 exit: pending\n")


def _ledger_only_operator(operator_id: int, username: str) -> dict:
    """A daily ``collect_g2_evidence`` beat draft entry: rolling-ledger status only,
    no file-backed identity (no company/profile/strategy/notification_channel)."""
    return {
        "operator_id": operator_id,
        "username": username,
        "evidence_status": "ready",
        "source": "collect_g2_evidence_beat",
        "sections": {"decision_experiments": "ready", "smoke": "mixed_scope"},
        "blocking_gap_ids": [],
    }


def test_merge_keeps_file_backed_identity_under_newer_ledger_only_draft(tmp_path):
    """A newer ledger-only daily draft must not clobber the file-backed operator
    identity from an earlier fastlane draft; volatile status still tracks newest."""
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review"
    targets = [
        (19, "synthetic-gs-cleaning-metro"),
        (20, "synthetic-gs-security-national"),
        (25, "synthetic-cn-electric-telecom-national"),
    ]
    _write_manifest_draft(
        evidence_root / "2026-07-04" / "manifest-draft.json",
        review_id="fastlane-20260704",
        date="2026-07-04",
        operators=[_operator(oid, uname) for oid, uname in targets],
    )
    _write_manifest_draft(
        evidence_root / "2026-07-14" / "manifest-draft.json",
        review_id="daily-20260714",
        date="2026-07-14",
        operators=[_ledger_only_operator(oid, uname) for oid, uname in targets],
    )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review",
        output_dir=output_dir,
        min_days=2,
        min_operators=3,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    operators = {op["operator_id"]: op for op in manifest["operators"]}
    assert set(operators) == {19, 20, 25}
    op19 = operators[19]
    # File-backed identity survives the newer ledger-only overwrite.
    assert op19["company"] == "Company 19"
    assert op19["profile"] == {"status": "pass", "path": "profile-19.json"}
    assert op19["strategy"]["status"] == "pass"
    assert op19["notification_channel"]["status"] == "pass"
    assert op19["operator_scope_status"] == "pass"
    # Volatile fields still reflect the newest (ledger) draft.
    assert op19["evidence_status"] == "ready"
    assert op19["sections"]["smoke"] == "mixed_scope"
    assert op19["source"] == "collect_g2_evidence_beat"


def test_merge_empty_field_does_not_clobber_earlier_non_empty(tmp_path):
    """An empty value in a newer draft must not erase a richer earlier value."""
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review"
    _write_manifest_draft(
        evidence_root / "2026-07-04" / "manifest-draft.json",
        review_id="a",
        date="2026-07-04",
        operators=[_operator(19, "synthetic-a")],
    )
    sparse = _operator(19, "synthetic-a")
    sparse["company"] = ""
    sparse["profile"] = {}
    sparse["strategy"] = {"status": "pass", "path": "strategy-19-new.json"}
    _write_manifest_draft(
        evidence_root / "2026-07-14" / "manifest-draft.json",
        review_id="b",
        date="2026-07-14",
        operators=[sparse],
    )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review",
        output_dir=output_dir,
        min_days=1,
        min_operators=1,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    op19 = manifest["operators"][0]
    # Empty company / empty profile in the newer draft do not erase earlier values.
    assert op19["company"] == "Company 19"
    assert op19["profile"] == {"status": "pass", "path": "profile-19.json"}
    # A non-empty newer value still wins.
    assert op19["strategy"]["path"] == "strategy-19-new.json"


def test_merge_three_draft_chain_folds_correctly(tmp_path):
    """A fastlane -> ledger -> ledger fold keeps file-backed identity and tracks the
    newest volatile status across more than two drafts."""
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review"
    _write_manifest_draft(
        evidence_root / "2026-07-04" / "manifest-draft.json",
        review_id="fastlane",
        date="2026-07-04",
        operators=[_operator(19, "synthetic-a")],
    )
    mid = _ledger_only_operator(19, "synthetic-a")
    mid["sections"] = {"smoke": "mixed_scope"}
    _write_manifest_draft(
        evidence_root / "2026-07-09" / "manifest-draft.json",
        review_id="ledger-mid",
        date="2026-07-09",
        operators=[mid],
    )
    newest = _ledger_only_operator(19, "synthetic-a")
    newest["evidence_status"] = "insufficient"
    _write_manifest_draft(
        evidence_root / "2026-07-14" / "manifest-draft.json",
        review_id="ledger-new",
        date="2026-07-14",
        operators=[newest],
    )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review",
        output_dir=output_dir,
        min_days=1,
        min_operators=1,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    op19 = manifest["operators"][0]
    # File-backed identity from the first draft survives two later ledger drafts.
    assert op19["company"] == "Company 19"
    assert op19["profile"]["path"] == "profile-19.json"
    # Volatile status reflects the newest (third) draft, not the middle one.
    assert op19["evidence_status"] == "insufficient"


def test_merge_does_not_fabricate_identity_for_ledger_only_operator(tmp_path):
    """An operator that appears only in ledger-only drafts must not gain file-backed
    identity; the readiness gate then honestly fails instead of passing on nothing."""
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review"
    _write_manifest_draft(
        evidence_root / "2026-07-13" / "manifest-draft.json",
        review_id="ledger-a",
        date="2026-07-13",
        operators=[_ledger_only_operator(19, "synthetic-a")],
    )
    _write_manifest_draft(
        evidence_root / "2026-07-14" / "manifest-draft.json",
        review_id="ledger-b",
        date="2026-07-14",
        operators=[_ledger_only_operator(19, "synthetic-a")],
    )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review",
        output_dir=output_dir,
        min_days=1,
        min_operators=1,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    op19 = manifest["operators"][0]
    assert "company" not in op19
    assert "profile" not in op19
    assert "strategy" not in op19
    assert "notification_channel" not in op19


def test_merge_preserves_zero_and_false_and_lets_them_overwrite(tmp_path):
    """0 / False are meaningful (non-empty): they are preserved against an empty
    incoming value and can themselves overwrite an earlier value."""
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review"
    older = _operator(19, "synthetic-a")
    older["settled_count"] = 240  # non-empty earlier value
    older["is_synthetic"] = True
    _write_manifest_draft(
        evidence_root / "2026-07-04" / "manifest-draft.json",
        review_id="a",
        date="2026-07-04",
        operators=[older],
    )
    newer = _operator(19, "synthetic-a")
    newer["settled_count"] = 0  # 0 must overwrite 240
    newer["is_synthetic"] = False  # False must overwrite True
    _write_manifest_draft(
        evidence_root / "2026-07-14" / "manifest-draft.json",
        review_id="b",
        date="2026-07-14",
        operators=[newer],
    )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review",
        output_dir=output_dir,
        min_days=1,
        min_operators=1,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    op19 = manifest["operators"][0]
    assert op19["settled_count"] == 0
    assert op19["is_synthetic"] is False


def test_review_bundle_stays_draft_with_open_gaps(tmp_path):
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review-with-gap"
    for offset, operator_id in enumerate((201, 202, 203), start=1):
        _write_manifest_draft(
            evidence_root / f"2026-06-2{offset}" / "manifest-draft.json",
            review_id=f"daily-2026062{offset}",
            date=f"2026-06-2{offset}",
            operators=[_operator(operator_id, f"synthetic-{operator_id}")],
            blocking_gaps=(
                [
                    {
                        "gap_id": "GAP-001",
                        "date": "2026-06-22",
                        "operator_id": 202,
                        "source": "g2-evidence.blocking_gaps",
                        "category": "missing evidence",
                        "description": "strategy monitor evidence missing",
                        "status": "open",
                        "treatment": "rerun",
                    }
                ]
                if operator_id == 202
                else []
            ),
        )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review-with-gap",
        output_dir=output_dir,
        min_days=3,
        min_operators=3,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "draft"
    assert manifest["review_gate_summary"]["ready_for_review"] is False
    assert manifest["review_gate_summary"]["open_blocking_gap_count"] == 1
    assert (
        (output_dir / "exit-review.md")
        .read_text(encoding="utf-8")
        .endswith("G-2 exit: pending\n")
    )


def test_review_bundle_stays_draft_with_triaged_and_accepted_hold_gaps(tmp_path):
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "review-with-held-gaps"
    gap_statuses = {
        301: "triaged",
        302: "accepted_hold",
    }
    for offset, operator_id in enumerate((301, 302, 303), start=1):
        gap_status = gap_statuses.get(operator_id)
        blocking_gaps = []
        if gap_status:
            blocking_gaps = [
                {
                    "gap_id": f"GAP-{operator_id}",
                    "date": f"2026-06-2{offset}",
                    "operator_id": operator_id,
                    "source": "g2-evidence.blocking_gaps",
                    "category": "missing evidence",
                    "description": f"gap for operator {operator_id}",
                    "status": gap_status,
                    "treatment": ("hold" if gap_status == "accepted_hold" else "rerun"),
                }
            ]
        _write_manifest_draft(
            evidence_root / f"2026-06-2{offset}" / "manifest-draft.json",
            review_id=f"daily-2026062{offset}",
            date=f"2026-06-2{offset}",
            operators=[_operator(operator_id, f"synthetic-{operator_id}")],
            blocking_gaps=blocking_gaps,
        )

    write_review_bundle(
        evidence_root=evidence_root,
        review_id="review-with-held-gaps",
        output_dir=output_dir,
        min_days=3,
        min_operators=3,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "draft"
    assert manifest["review_gate_summary"]["ready_for_review"] is False
    assert manifest["review_gate_summary"]["open_blocking_gap_count"] == 2
    assert [gap["status"] for gap in manifest["blocking_gaps"]] == [
        "triaged",
        "accepted_hold",
    ]


def test_cli_rejects_when_no_manifest_drafts(tmp_path):
    evidence_root = tmp_path / "reports" / "g2-evidence"
    output_dir = evidence_root / "empty-review"
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--evidence-root",
            str(evidence_root),
            "--review-id",
            "empty-review",
            "--output-dir",
            str(output_dir),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert "manifest-draft.json" in stderr.getvalue()
    assert stdout.getvalue() == ""
    assert not (output_dir / "manifest.json").exists()
    assert not (output_dir / "exit-review.md").exists()
