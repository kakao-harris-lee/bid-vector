"""Tests for the G-2 exit review readiness checker."""

from __future__ import annotations

import io
import json
from pathlib import Path

from app.services.g2_evidence_draft import DAILY_STATUS_SOURCE
from scripts.check_g2_exit_readiness import main


def _path(operator_id: int, file_name: str, *, date: str = "2026-06-24") -> str:
    return f"reports/g2-evidence/{date}/run-1/operator-{operator_id}/{file_name}"


def _operator(operator_id: int, username: str) -> dict:
    return {
        "operator_id": operator_id,
        "username": username,
        "company": f"Company {operator_id}",
        "is_synthetic": True,
        "operator_scope_status": "pass",
        "profile": {
            "status": "pass",
            "path": _path(operator_id, "profile.json"),
            "required_fields_present": True,
        },
        "strategy": {
            "status": "pass",
            "path": _path(operator_id, "strategy.json"),
            "thresholds_valid": True,
        },
        "notification_channel": {
            "status": "pass",
            "mode": "dry_run_only",
            "path": _path(operator_id, "notification-channels.json"),
            "masked_target_present": True,
            "raw_secret_absent": True,
        },
        "evidence_paths": {
            "g2_evidence": [_path(operator_id, "g2-evidence.json")],
            "candidate_preview": [_path(operator_id, "strategy-candidates.json")],
            "strategy_monitor": [_path(operator_id, "strategy-monitor.json")],
            "decision_experiments": [_path(operator_id, "decision-experiments.json")],
            "decision_apply_dry_run": [
                _path(
                    operator_id,
                    "decision-experiment-apply-strategy-dry-run.json",
                )
            ],
            "operations_dashboard": [
                _path(operator_id, "operator-dashboard.json"),
                _path(operator_id, "operations-dashboard.json"),
            ],
        },
    }


def _daily_row(date: str, operator_ids: list[int]) -> dict:
    return {
        "date": date,
        "status": "pass",
        "summary": f"daily evidence {date}",
        "collect_g2_evidence_snapshot": {
            "status": "pass",
            "path": f"reports/g2-evidence/{date}/run-1/g2-evidence-summary.json",
            "source": "scripts/collect_g2_evidence.py",
        },
        "operators": {
            str(operator_id): {
                "profile": "pass",
                "strategy": "pass",
                "notification_channel": "pass",
                "candidate_preview": "pass",
                "strategy_monitor": "pass",
                "decision_experiment": "pass",
                "g2_evidence_status": "ready",
                "blocking_gap_ids": [],
            }
            for operator_id in operator_ids
        },
        "dry_run_item_ids": [],
        "approved_execution_item_ids": [],
        "excluded_evidence": [],
    }


def _stray_fail_row(date: str, operator_ids: list[int]) -> dict:
    """A stray fastlane ``fail`` draft for a date that is separately counted.

    Mirrors the 2026-07-03 fastlane run that produced an early ``fail`` draft
    (with per-operator ``missing`` sub-statuses) alongside a later clean ``pass``
    draft. The fail draft is not counted evidence.
    """
    row = _daily_row(date, operator_ids)
    row["status"] = "fail"
    row["collect_g2_evidence_snapshot"]["status"] = "fail"
    row["collect_g2_evidence_snapshot"]["path"] = (
        f"reports/g2-evidence/{date}/run-0/g2-evidence-summary.json"
    )
    for operator_id in operator_ids:
        row["operators"][str(operator_id)]["candidate_preview"] = "missing"
        row["operators"][str(operator_id)]["strategy_monitor"] = "missing"
    return row


def _ledger_only_row(date: str, operator_ids: list[int]) -> dict:
    """A ledger-only ``collect_g2_evidence`` beat draft row (PR#155).

    These beat drafts intentionally omit a file path and carry the ledger cell
    shape (``evidence_status``/``sections``) rather than the fastlane cell shape.
    """
    return {
        "date": date,
        "status": "pass",
        "summary": f"collect_g2_evidence beat ledger snapshot {date}",
        "source": DAILY_STATUS_SOURCE,
        "collect_g2_evidence_snapshot": {
            "status": "pass",
            "source": DAILY_STATUS_SOURCE,
        },
        "operators": {
            str(operator_id): {
                "evidence_status": "ready",
                "sections": {
                    "smoke": "mixed_scope",
                    "strategy_monitor": "ready",
                    "decision_experiments": "ready",
                    "synthetic_experiments": "ready",
                    "notifications": "ready",
                },
            }
            for operator_id in operator_ids
        },
    }


def _manifest(*, status: str = "ready_for_review") -> dict:
    operator_ids = [101, 102, 103]
    return {
        "review_id": "g2-exit-20260625",
        "manifest_version": 1,
        "status": status,
        "basis": {
            "roadmap": "docs/roadmap.md",
            "runbook": "docs/operations/g2-evidence-runbook.md",
            "review_template": "docs/operations/g2-exit-review-template.md",
            "basis_commit": "abc123",
        },
        "evidence_window": {
            "start_date": "2026-06-23",
            "end_date": "2026-06-24",
            "required_days": 2,
            "observed_days": 2,
            "counted_days": 2,
            "timezone": "Asia/Seoul",
        },
        "operators": [
            _operator(101, "synthetic-alpha"),
            _operator(102, "synthetic-bravo"),
            _operator(103, "synthetic-charlie"),
        ],
        "daily_status": [
            _daily_row("2026-06-23", operator_ids),
            _daily_row("2026-06-24", operator_ids),
        ],
        "blocking_gaps": [],
        "action_register": {
            "dry_run_items": [
                {
                    "item_id": "DRY-001",
                    "date": "2026-06-24",
                    "result": "pass",
                    "output_path": "reports/g2-evidence/2026-06-24/run-1/dry-run.json",
                }
            ],
            "approved_execution_items": [],
        },
    }


def _write_manifest(repo_root: Path, manifest: dict) -> Path:
    manifest_path = repo_root / "reports" / "g2-evidence" / "review" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _write_referenced_files(
    repo_root: Path,
    manifest: dict,
    *,
    skip: set[str] = None,
    contents: dict[str, dict] = None,
):
    skip = skip or set()
    contents = contents or {}
    paths: set[str] = set()
    for operator in manifest["operators"]:
        for section in ("profile", "strategy", "notification_channel"):
            paths.add(operator[section]["path"])
        for evidence_paths in operator["evidence_paths"].values():
            paths.update(evidence_paths)
    for row in manifest["daily_status"]:
        snapshot_path = row["collect_g2_evidence_snapshot"].get("path")
        if snapshot_path:
            paths.add(snapshot_path)
    for item in manifest["action_register"]["dry_run_items"]:
        paths.add(item["output_path"])

    for relative_path in paths - skip:
        path = repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = contents.get(relative_path, {})
        if (
            relative_path.endswith("notification-channels.json")
            and relative_path not in contents
        ):
            operator_id = int(relative_path.split("/operator-")[1].split("/")[0])
            operator = next(
                item
                for item in manifest["operators"]
                if item["operator_id"] == operator_id
            )
            payload = {
                "operator_id": operator_id,
                "current_operator_id": operator_id,
                "current_operator_username": operator["username"],
                "channel_count": 1,
                "channels": [
                    {
                        "operator_id": operator_id,
                        "channel_type": "telegram",
                        "route_key": f"telegram:operator-{operator_id}",
                        "target_label": "chat ********1234",
                        "is_active": True,
                        "dry_run_only": True,
                    }
                ],
            }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def test_cli_writes_ready_report_when_all_exit_gates_pass(tmp_path, monkeypatch):
    manifest = _manifest()
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest)
    output_path = tmp_path / "reports" / "g2-evidence" / "review" / "readiness.json"
    stdout = io.StringIO()
    stderr = io.StringIO()

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert stdout.getvalue() == ""
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is True
    assert report["counts"] == {
        "counted_days": 2,
        "required_days": 2,
        "operator_count": 3,
        "required_operator_count": 3,
        "open_blocking_gap_count": 0,
        "notification_failure_count": 0,
        "missing_evidence_path_count": 0,
    }
    assert {gate["gate_id"]: gate["passed"] for gate in report["gates"]} == {
        "operator_independence": True,
        "routing_isolation": True,
        "admin_surface_separation": True,
        "user_surface_focus": True,
    }
    assert report["open_blocking_gaps"] == []
    assert report["notification_failures"] == []
    assert report["missing_evidence_paths"] == []
    assert report["failure_summary"] == {
        "global_failures": [],
        "failed_gates": [],
    }


def test_cli_blocks_ready_manifest_with_accepted_hold_gap(tmp_path, monkeypatch):
    manifest = _manifest()
    manifest["blocking_gaps"] = [
        {
            "gap_id": "GAP-HOLD-001",
            "date": "2026-06-24",
            "operator_id": 103,
            "source": "reviewer",
            "category": "no candidates",
            "description": "operator is on accepted hold pending new candidates",
            "status": "accepted_hold",
            "treatment": "hold",
        }
    ]
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest)
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["counts"]["open_blocking_gap_count"] == 1
    assert report["global_checks"]["no_open_blocking_gaps"] is False
    assert report["open_blocking_gaps"] == manifest["blocking_gaps"]


def test_cli_reports_hold_reasons_when_exit_gates_fail(tmp_path, monkeypatch):
    manifest = _manifest(status="ready_for_review")
    manifest["evidence_window"]["counted_days"] = 1
    manifest["operators"] = manifest["operators"][:2]
    manifest["operators"][1]["notification_channel"]["status"] = "fail"
    manifest["operators"][1]["evidence_paths"]["g2_evidence"] = []
    manifest["operators"][1]["evidence_paths"]["operations_dashboard"] = [
        _path(102, "operations-dashboard.json")
    ]
    manifest["daily_status"][0]["status"] = "partial"
    manifest["daily_status"][0]["operators"]["102"]["notification_channel"] = "fail"
    manifest["daily_status"][0]["operators"]["102"]["g2_evidence_status"] = "missing"
    manifest["blocking_gaps"] = [
        {
            "gap_id": "GAP-001",
            "date": "2026-06-23",
            "operator_id": 102,
            "source": "g2-evidence.blocking_gaps",
            "category": "Telegram/app notification",
            "description": "notification route failed",
            "status": "open",
            "treatment": "rerun",
        },
        {
            "gap_id": "GAP-002",
            "date": "2026-06-24",
            "operator_id": 102,
            "source": "reviewer",
            "category": "missing evidence",
            "description": "user surface inspection missing",
            "status": "triaged",
            "treatment": "rerun",
        },
    ]
    missing_profile = manifest["operators"][0]["profile"]["path"]
    output_path = tmp_path / "readiness.json"
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest, skip={missing_profile})
    stdout = io.StringIO()
    stderr = io.StringIO()

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert stdout.getvalue() == ""
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["counts"]["counted_days"] == 1
    assert report["counts"]["operator_count"] == 2
    assert report["counts"]["open_blocking_gap_count"] == 2
    assert report["counts"]["notification_failure_count"] >= 1
    assert report["counts"]["missing_evidence_path_count"] >= 2
    assert {gap["gap_id"] for gap in report["open_blocking_gaps"]} == {
        "GAP-001",
        "GAP-002",
    }
    assert any(
        item["operator_id"] == 102 and item["status"] == "fail"
        for item in report["notification_failures"]
    )
    missing_paths = {item["path"] for item in report["missing_evidence_paths"]}
    assert missing_profile in missing_paths
    assert "operators[102].evidence_paths.g2_evidence" in missing_paths
    assert {gate["gate_id"]: gate["passed"] for gate in report["gates"]} == {
        "operator_independence": False,
        "routing_isolation": False,
        "admin_surface_separation": False,
        "user_surface_focus": False,
    }
    assert report["failure_summary"]["global_failures"] == [
        {
            "check": "counted_days_ready",
            "reason": "counted_days 1 is below required 2",
        },
        {
            "check": "pass_daily_status_count_ready",
            "reason": "pass_daily_status_count 1 is below required 2",
        },
        {
            "check": "operator_count_ready",
            "reason": "operator_count 2 is below required 3",
        },
        {
            "check": "no_open_blocking_gaps",
            "reason": "2 unresolved blocking gap(s)",
        },
        {
            "check": "no_notification_failures",
            "reason": f"{report['counts']['notification_failure_count']} notification failure(s)",
        },
        {
            "check": "no_missing_evidence_paths",
            "reason": f"{report['counts']['missing_evidence_path_count']} missing evidence path(s)",
        },
    ]
    assert [item["gate_id"] for item in report["failure_summary"]["failed_gates"]] == [
        "operator_independence",
        "routing_isolation",
        "admin_surface_separation",
        "user_surface_focus",
    ]
    assert all(item["failures"] for item in report["failure_summary"]["failed_gates"])


def test_cli_blocks_ready_manifest_when_snapshot_status_fails(tmp_path, monkeypatch):
    manifest = _manifest()
    manifest["daily_status"][0]["collect_g2_evidence_snapshot"]["status"] = "fail"
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest)
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["global_checks"]["all_collect_snapshots_passed"] is False
    assert report["collect_snapshot_failures"] == [
        {
            "date": "2026-06-23",
            "location": "daily_status[0].collect_g2_evidence_snapshot.status",
            "path": "reports/g2-evidence/2026-06-23/run-1/g2-evidence-summary.json",
            "status": "fail",
            "reason": "collect_snapshot_status_not_pass",
        }
    ]


def test_cli_uses_notification_channel_file_verifier_for_readiness(
    tmp_path,
    monkeypatch,
):
    manifest = _manifest()
    manifest_path = _write_manifest(tmp_path, manifest)
    unsafe_notification_path = manifest["operators"][1]["notification_channel"]["path"]
    _write_referenced_files(
        tmp_path,
        manifest,
        contents={
            unsafe_notification_path: {
                "operator_id": 102,
                "current_operator_id": 102,
                "current_operator_username": "synthetic-bravo",
                "channel_count": 1,
                "channels": [
                    {
                        "operator_id": 102,
                        "channel_type": "telegram",
                        "route_key": "telegram:1594710346",
                        "target_label": "chat_id=1594710346",
                        "is_active": True,
                        "dry_run_only": False,
                    }
                ],
            }
        },
    )
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["global_checks"]["no_notification_failures"] is False
    reasons = {item["reason"] for item in report["notification_failures"]}
    assert "notification_file_raw_secret_like_target" in reasons
    assert "notification_file_active_noncanonical_telegram" in reasons


def test_cli_reports_malformed_notification_evidence_as_not_ready(
    tmp_path,
    monkeypatch,
):
    manifest = _manifest()
    manifest_path = _write_manifest(tmp_path, manifest)
    malformed_notification_path = manifest["operators"][0]["notification_channel"][
        "path"
    ]
    _write_referenced_files(tmp_path, manifest)
    (tmp_path / malformed_notification_path).write_text("{not-json\n", encoding="utf-8")
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["global_checks"]["no_notification_failures"] is False
    assert any(
        item["reason"] == "notification_file_invalid_input"
        and item["path"] == malformed_notification_path
        and "not valid JSON" in item["message"]
        for item in report["notification_failures"]
    )


def test_cli_ignores_stray_fail_row_outside_counted_evidence(tmp_path, monkeypatch):
    """A stray fastlane ``fail`` draft on an otherwise-counted date must not red
    the readiness gate: it is not part of the counted evidence window."""
    operator_ids = [101, 102, 103]
    manifest = _manifest()
    manifest["daily_status"] = [
        _stray_fail_row("2026-06-23", operator_ids),
        _daily_row("2026-06-23", operator_ids),
        _daily_row("2026-06-24", operator_ids),
    ]
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest)
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is True
    routing = next(
        gate for gate in report["gates"] if gate["gate_id"] == "routing_isolation"
    )
    assert routing["passed"] is True
    assert routing["failures"] == []
    assert report["daily_evidence_window"]["excluded_rows"] == 1
    assert report["daily_evidence_window"]["counted_pass_dates"] == [
        "2026-06-23",
        "2026-06-24",
    ]


def test_cli_accepts_ledger_only_beat_days_without_paths(tmp_path, monkeypatch):
    """Ledger-only beat drafts (PR#155) carry no file path and use the ledger
    cell shape; they must be accepted and annotated, not red the gate."""
    operator_ids = [101, 102, 103]
    manifest = _manifest()
    manifest["daily_status"] = [
        _daily_row("2026-06-23", operator_ids),
        _ledger_only_row("2026-06-24", operator_ids),
    ]
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest)
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is True
    assert report["global_checks"]["no_missing_evidence_paths"] is True
    assert report["missing_evidence_paths"] == []
    routing = next(
        gate for gate in report["gates"] if gate["gate_id"] == "routing_isolation"
    )
    assert routing["passed"] is True
    window = report["daily_evidence_window"]
    assert window["ledger_only_days"] == ["2026-06-24"]
    assert window["file_backed_days"] == ["2026-06-23"]
    assert window["excluded_rows"] == 0


def test_cli_still_flags_file_backed_counted_row_missing_snapshot_path(
    tmp_path,
    monkeypatch,
):
    """Guard: a file-backed (fastlane) counted pass row whose snapshot path does
    not resolve must still red the gate. The window fix must never weaken the
    file-backed path check."""
    operator_ids = [101, 102, 103]
    manifest = _manifest()
    missing_snapshot_path = (
        "reports/g2-evidence/2026-06-24/run-1/never-written-summary.json"
    )
    manifest["daily_status"][1]["collect_g2_evidence_snapshot"][
        "path"
    ] = missing_snapshot_path
    manifest_path = _write_manifest(tmp_path, manifest)
    _write_referenced_files(tmp_path, manifest, skip={missing_snapshot_path})
    output_path = tmp_path / "readiness.json"

    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--min-days",
            "2",
            "--min-operators",
            "3",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["global_checks"]["no_missing_evidence_paths"] is False
    missing = {
        (item["location"], item["reason"])
        for item in report["missing_evidence_paths"]
    }
    assert (
        "daily_status[1].collect_g2_evidence_snapshot.path",
        "path_does_not_exist",
    ) in missing


def test_cli_returns_two_and_writes_error_report_for_invalid_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]\n", encoding="utf-8")
    output_path = tmp_path / "readiness.json"
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        ["--manifest", str(manifest_path), "--output", str(output_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "manifest" in stderr.getvalue()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ready_for_human_review"] is False
    assert report["valid_manifest"] is False
    assert report["invalid_manifest_errors"] == ["manifest must contain a JSON object"]
