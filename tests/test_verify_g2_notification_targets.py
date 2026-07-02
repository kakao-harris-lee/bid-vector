"""Tests for the local G-2 notification target verifier."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts.verify_g2_notification_targets import main


def _write_channels(
    root: Path,
    *,
    operator_id: int,
    username: str,
    channels: list[dict],
    current_operator_id: int | None = None,
    extra: dict | None = None,
) -> Path:
    payload = {
        "operator_id": operator_id,
        "current_operator_id": (
            operator_id if current_operator_id is None else current_operator_id
        ),
        "current_operator_username": username,
        "channel_count": len(channels),
        "channels": channels,
    }
    if extra:
        payload.update(extra)
    path = (
        root
        / "2026-06-25"
        / "run-a"
        / f"operator-{operator_id}"
        / "notification-channels.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _telegram_channel(
    operator_id: int,
    *,
    target_label: str = "chat ********0346",
    route_key: str | None = None,
    is_active: bool = True,
    dry_run_only: bool | None = True,
) -> dict:
    channel = {
        "channel_id": operator_id,
        "operator_id": operator_id,
        "channel_type": "telegram",
        "route_key": route_key or f"telegram:operator-{operator_id}",
        "target_label": target_label,
        "is_active": is_active,
        "source": "operator_notification_channels",
    }
    if dry_run_only is not None:
        channel["dry_run_only"] = dry_run_only
    return channel


def _run_verifier(root: Path, output: Path, *extra_args: str):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--evidence-root",
            str(root),
            "--output",
            str(output),
            *extra_args,
        ],
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_verifier_accepts_masked_canonical_active_and_synthetic_dry_run(tmp_path):
    evidence_root = tmp_path / "evidence"
    summary_path = tmp_path / "summary.json"
    markdown_path = tmp_path / "summary.md"
    _write_channels(
        evidence_root,
        operator_id=1,
        username="operator",
        channels=[
            _telegram_channel(
                1,
                target_label="******0346",
                route_key="telegram:legacy-configured-chat",
                dry_run_only=False,
            )
        ],
    )
    _write_channels(
        evidence_root,
        operator_id=101,
        username="synthetic-sw-small-seoul",
        channels=[_telegram_channel(101, dry_run_only=True)],
    )

    code, _stdout, stderr = _run_verifier(
        evidence_root,
        summary_path,
        "--markdown",
        str(markdown_path),
    )

    assert code == 0
    assert stderr == ""
    summary = _load_summary(summary_path)
    assert summary["status"] == "pass"
    assert summary["file_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["warning_count"] == 0
    assert summary["issues"] == []
    modes = {row["operator_id"]: row["mode"] for row in summary["operators"]}
    assert modes == {1: "active", 101: "dry_run_only"}
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "| operator_id | username | mode | status | issues | path |" in markdown
    assert "synthetic-sw-small-seoul" in markdown


def test_verifier_reports_unsafe_targets_missing_policy_and_mismatches(tmp_path):
    evidence_root = tmp_path / "evidence"
    summary_path = tmp_path / "summary.json"
    _write_channels(
        evidence_root,
        operator_id=201,
        username="synthetic-raw",
        channels=[
            _telegram_channel(
                201,
                target_label="chat_id=1594710346",
                route_key="telegram:1594710346",
                dry_run_only=True,
            )
        ],
    )
    _write_channels(
        evidence_root,
        operator_id=202,
        username="synthetic-missing",
        channels=[],
    )
    _write_channels(
        evidence_root,
        operator_id=203,
        username="synthetic-policy-missing",
        channels=[_telegram_channel(203, dry_run_only=None)],
    )
    _write_channels(
        evidence_root,
        operator_id=204,
        username="synthetic-mismatch",
        current_operator_id=999,
        channels=[_telegram_channel(205, dry_run_only=True)],
    )

    code, _stdout, stderr = _run_verifier(evidence_root, summary_path)

    assert code == 1
    assert stderr == ""
    summary = _load_summary(summary_path)
    assert summary["status"] == "fail"
    issue_codes = {issue["code"] for issue in summary["issues"]}
    assert {
        "raw_secret_like_target",
        "missing_channels",
        "missing_dry_run_or_skip_policy",
        "operator_mismatch",
    }.issubset(issue_codes)
    assert summary["failure_count"] == len(
        [issue for issue in summary["issues"] if issue["severity"] == "failure"]
    )
    by_operator = {row["operator_id"]: row for row in summary["operators"]}
    assert by_operator[202]["mode"] == "missing"
    assert by_operator[204]["status"] == "fail"
    assert [
        issue["severity"]
        for issue in summary["issues"]
        if issue["code"] == "operator_mismatch"
    ] == ["failure", "failure"]


def test_verifier_reports_raw_secret_like_targets_inside_metadata(tmp_path):
    evidence_root = tmp_path / "evidence"
    summary_path = tmp_path / "summary.json"
    channel = _telegram_channel(401, dry_run_only=True)
    channel["metadata"] = {
        "device_token": "ExponentPushToken[abcdef1234567890]",
        "target_values": [
            {"value": "device_token=abcdef1234567890"},
        ],
    }
    _write_channels(
        evidence_root,
        operator_id=401,
        username="synthetic-metadata",
        channels=[channel],
        extra={
            "metadata": {
                "notification_target": "chat_id=1594710346",
                "notification_targets": ["chat_id=2594710346"],
                "telegram": {"chat_id": "3594710346"},
            },
        },
    )

    code, _stdout, stderr = _run_verifier(evidence_root, summary_path)

    assert code == 1
    assert stderr == ""
    summary = _load_summary(summary_path)
    raw_target_issues = [
        issue
        for issue in summary["issues"]
        if issue["code"] == "raw_secret_like_target"
    ]
    assert {issue["field"] for issue in raw_target_issues} == {
        "metadata.notification_target",
        "metadata.notification_targets[0]",
        "metadata.telegram.chat_id",
        "metadata.device_token",
        "metadata.target_values[0].value",
    }
    assert {issue["severity"] for issue in raw_target_issues} == {"failure"}


@pytest.mark.parametrize(
    "policy",
    [
        {"status": "skipped_synthetic_operator"},
        {"delivery_policy": "telegram_channel_dry_run"},
    ],
)
def test_empty_channels_with_explicit_skip_or_dry_run_policy_passes(
    tmp_path, policy
):
    evidence_root = tmp_path / "evidence"
    summary_path = tmp_path / "summary.json"
    _write_channels(
        evidence_root,
        operator_id=402,
        username="synthetic-no-channel",
        channels=[],
        extra=policy,
    )

    code, _stdout, stderr = _run_verifier(evidence_root, summary_path)

    assert code == 0
    assert stderr == ""
    summary = _load_summary(summary_path)
    assert summary["status"] == "pass"
    assert summary["failure_count"] == 0
    assert summary["warning_count"] == 0
    assert summary["issues"] == []
    assert summary["operators"][0]["mode"] == "skipped"


def test_allow_active_noncanonical_downgrades_active_telegram_to_warning(tmp_path):
    evidence_root = tmp_path / "evidence"
    default_summary_path = tmp_path / "default.json"
    allowed_summary_path = tmp_path / "allowed.json"
    _write_channels(
        evidence_root,
        operator_id=301,
        username="synthetic-active",
        channels=[_telegram_channel(301, dry_run_only=False)],
    )

    default_code, _stdout, _stderr = _run_verifier(evidence_root, default_summary_path)
    allowed_code, _stdout, _stderr = _run_verifier(
        evidence_root,
        allowed_summary_path,
        "--allow-active-noncanonical",
    )

    assert default_code == 1
    default_summary = _load_summary(default_summary_path)
    assert [
        issue["severity"]
        for issue in default_summary["issues"]
        if issue["code"] == "active_noncanonical_telegram"
    ] == ["failure"]

    assert allowed_code == 0
    allowed_summary = _load_summary(allowed_summary_path)
    assert allowed_summary["status"] == "pass"
    assert allowed_summary["failure_count"] == 0
    assert allowed_summary["operators"][0]["status"] == "warning"
    assert allowed_summary["operators"][0]["issue_codes"] == [
        "active_noncanonical_telegram"
    ]
    assert [
        issue["severity"]
        for issue in allowed_summary["issues"]
        if issue["code"] == "active_noncanonical_telegram"
    ] == ["warning"]
    assert allowed_summary["warning_count"] == 1


def test_cli_returns_2_for_invalid_input_without_writing_outputs(tmp_path):
    output_path = tmp_path / "summary.json"
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--evidence-root",
            str(tmp_path / "missing-root"),
            "--output",
            str(output_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "evidence root" in stderr.getvalue()
    assert not output_path.exists()
