"""Tests for the local G-2 blocking gap register CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path("scripts/g2_blocking_gap_register.py")


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _run_register(
    *,
    evidence_root: Path,
    output: Path,
    markdown: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--evidence-root",
        str(evidence_root),
        "--output",
        str(output),
    ]
    if markdown is not None:
        args.extend(["--markdown", str(markdown)])
    return subprocess.run(
        args,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def test_register_scans_manifests_and_reports_unresolved_gaps(tmp_path):
    evidence_root = tmp_path / "reports" / "g2-evidence"
    draft_path = _write_json(
        evidence_root / "2026-06-24" / "run-a" / "manifest-draft.json",
        {
            "blocking_gaps": [
                {
                    "gap_id": "GAP-001",
                    "date": "2026-06-24",
                    "operator_id": 202,
                    "category": "missing evidence",
                    "description": "strategy monitor evidence missing",
                    "status": "open",
                    "treatment": "rerun",
                },
                {
                    "gap_id": "GAP-002",
                    "date": "2026-06-24",
                    "operator_id": 203,
                    "category": "mixed data",
                    "description": "mixed scope dashboard evidence",
                    "status": "excluded",
                    "treatment": "documented_not_counted",
                },
            ]
        },
    )
    review_path = _write_json(
        evidence_root / "review" / "manifest.json",
        {
            "blocking_gaps": [
                {
                    "date": "2026-06-25",
                    "operator_id": "204",
                    "category": "Telegram/app notification",
                    "description": "notification target policy still triaged",
                    "status": "triaged",
                    "treatment": "mapping_fixed",
                }
            ]
        },
    )
    output = tmp_path / "gap-register.json"
    markdown = tmp_path / "gap-register.md"

    result = _run_register(
        evidence_root=evidence_root,
        output=output,
        markdown=markdown,
    )

    assert result.returncode == 1, result.stderr
    register = json.loads(output.read_text(encoding="utf-8"))
    assert register["manifest_count"] == 2
    assert register["open_gap_count"] == 2
    assert [row["status"] for row in register["rows"]] == [
        "open",
        "excluded",
        "triaged",
    ]
    assert register["rows"][0] == {
        "gap_id": "GAP-001",
        "operator_id": "202",
        "date": "2026-06-24",
        "category": "missing evidence",
        "status": "open",
        "source_path": str(draft_path.relative_to(evidence_root)),
        "description": "strategy monitor evidence missing",
        "recommended_treatment": "rerun",
    }
    generated_id = register["rows"][2]["gap_id"]
    assert generated_id.startswith("GAP-AUTO-")
    assert register["rows"][2] == {
        "gap_id": generated_id,
        "operator_id": "204",
        "date": "2026-06-25",
        "category": "Telegram/app notification",
        "status": "triaged",
        "source_path": str(review_path.relative_to(evidence_root)),
        "description": "notification target policy still triaged",
        "recommended_treatment": "mapping_fixed",
    }
    markdown_text = markdown.read_text(encoding="utf-8")
    assert "| gap_id | operator_id | date | category | status | treatment | source |" in (
        markdown_text
    )
    assert "| GAP-001 | 202 | 2026-06-24 | missing evidence | open | rerun |" in (
        markdown_text
    )
    assert generated_id in markdown_text


def test_generated_gap_ids_are_stable_across_runs(tmp_path):
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root / "daily" / "manifest-draft.json",
        {
            "blocking_gaps": [
                {
                    "date": "2026-06-25",
                    "operator_id": 301,
                    "category": "no candidates",
                    "description": "no candidates for operator 301",
                    "status": "accepted_hold",
                    "treatment": "hold",
                }
            ]
        },
    )

    first = tmp_path / "register-first.json"
    second = tmp_path / "register-second.json"

    first_result = _run_register(evidence_root=evidence_root, output=first)
    second_result = _run_register(evidence_root=evidence_root, output=second)

    assert first_result.returncode == 1, first_result.stderr
    assert second_result.returncode == 1, second_result.stderr
    first_register = json.loads(first.read_text(encoding="utf-8"))
    second_register = json.loads(second.read_text(encoding="utf-8"))
    first_id = first_register["rows"][0]["gap_id"]
    second_id = second_register["rows"][0]["gap_id"]
    assert first_id == second_id
    assert first_id.startswith("GAP-AUTO-")
    assert first_register["open_gap_count"] == 1
    assert first_register["open_statuses"] == ["accepted_hold", "open", "triaged"]


def test_register_exits_zero_when_only_closed_gap_statuses_remain(tmp_path):
    evidence_root = tmp_path / "evidence"
    _write_json(
        evidence_root / "review" / "manifest.json",
        {
            "blocking_gaps": [
                {
                    "gap_id": "GAP-010",
                    "date": "2026-06-25",
                    "operator_id": None,
                    "category": "missing evidence",
                    "description": "old evidence was excluded from counted days",
                    "status": "excluded",
                    "treatment": "documented_not_counted",
                },
                {
                    "gap_id": "GAP-011",
                    "date": "2026-06-25",
                    "operator_id": 302,
                    "category": "task/broker",
                    "description": "broker check rerun produced evidence",
                    "status": "resolved",
                    "treatment": "rerun",
                },
            ]
        },
    )
    output = tmp_path / "register.json"

    result = _run_register(evidence_root=evidence_root, output=output)

    assert result.returncode == 0, result.stderr
    register = json.loads(output.read_text(encoding="utf-8"))
    assert register["open_gap_count"] == 0
    assert [row["gap_id"] for row in register["rows"]] == ["GAP-010", "GAP-011"]


def test_register_rejects_invalid_inputs(tmp_path):
    output = tmp_path / "register.json"

    result = _run_register(evidence_root=tmp_path / "missing", output=output)

    assert result.returncode == 2
    assert "evidence root" in result.stderr
    assert not output.exists()
