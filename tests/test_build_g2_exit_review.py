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
    assert (output_dir / "exit-review.md").read_text(encoding="utf-8").endswith(
        "G-2 exit: pending\n"
    )


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
    assert (output_dir / "exit-review.md").read_text(encoding="utf-8").endswith(
        "G-2 exit: pending\n"
    )


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
