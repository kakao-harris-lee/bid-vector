"""Tests for the read-only G-2 evidence collection runner."""

from __future__ import annotations

import io
import json

from scripts.collect_g2_evidence import CollectionConfig, main, run_collection


def _fake_http_client(*, blocking_gaps_by_operator: dict[int, list[str]] | None = None):
    calls: list[dict] = []
    blocking_gaps_by_operator = blocking_gaps_by_operator or {}

    def fake_get_json(
        *,
        base_url: str,
        path: str,
        params: dict,
        token: str | None,
        timeout_seconds: float,
    ) -> dict:
        del timeout_seconds
        operator_id = int(params["operator_id"])
        calls.append(
            {
                "base_url": base_url,
                "path": path,
                "params": dict(params),
                "token": token,
            }
        )
        if path == "/api/v1/operator/profile":
            return {
                "operator_id": operator_id,
                "current_operator_id": operator_id,
                "current_operator_username": f"operator-{operator_id}",
                "profile_configured": operator_id != 103,
            }
        if path == "/api/v1/operator/strategy":
            return {
                "operator_id": operator_id,
                "current_operator_id": operator_id,
                "current_operator_username": f"operator-{operator_id}",
                "strategy_configured": True,
            }
        if path == "/api/v1/operator/notification-channels":
            return {
                "operator_id": operator_id,
                "current_operator_id": operator_id,
                "current_operator_username": f"operator-{operator_id}",
                "channel_count": 1,
                "channels": [
                    {
                        "channel_id": operator_id,
                        "operator_id": operator_id,
                        "channel_type": "telegram",
                        "route_key": f"telegram:operator-{operator_id}",
                        "target_label": "chat ********1234",
                        "is_active": True,
                        "dry_run_only": operator_id != 101,
                        "verified_at": None,
                    }
                ],
            }
        if path == "/api/v1/analytics/g2-evidence":
            gaps = blocking_gaps_by_operator.get(operator_id, [])
            return {
                "operator_id": operator_id,
                "current_operator_id": operator_id,
                "current_operator_username": f"operator-{operator_id}",
                "window_days": params["days"],
                "evidence_status": "insufficient" if gaps else "ready",
                "notifications": {"status": "ready"},
                "blocking_gaps": gaps,
            }
        raise AssertionError(f"unexpected path: {path}")

    fake_get_json.calls = calls
    return fake_get_json


def test_collection_writes_raw_endpoint_files_and_summary(tmp_path):
    fake_get_json = _fake_http_client()
    config = CollectionConfig(
        base_url="http://api.test",
        token="secret-token",
        operator_ids=[101, 102, 103],
        evidence_dir=tmp_path,
        days=14,
        run_id="test-run",
    )

    summary = run_collection(config, http_get_json_func=fake_get_json)

    run_dir = tmp_path / "test-run"
    assert summary["status"] == "ready"
    assert summary["operator_count"] == 3
    assert summary["blocking_gap_count"] == 0
    assert summary["collection_error_count"] == 0
    assert len(fake_get_json.calls) == 12
    assert {call["path"] for call in fake_get_json.calls} == {
        "/api/v1/operator/profile",
        "/api/v1/operator/strategy",
        "/api/v1/operator/notification-channels",
        "/api/v1/analytics/g2-evidence",
    }
    assert all(call["token"] == "secret-token" for call in fake_get_json.calls)

    for operator_id in (101, 102, 103):
        operator_dir = run_dir / f"operator-{operator_id}"
        assert (operator_dir / "profile.json").exists()
        assert (operator_dir / "strategy.json").exists()
        assert (operator_dir / "notification-channels.json").exists()
        assert (operator_dir / "g2-evidence.json").exists()
        persisted_g2 = json.loads(
            (operator_dir / "g2-evidence.json").read_text(encoding="utf-8")
        )
        assert persisted_g2["window_days"] == 14

    persisted_summary = json.loads(
        (run_dir / "g2-evidence-summary.json").read_text(encoding="utf-8")
    )
    assert persisted_summary["operators"][0]["current_operator_id_matches"] is True
    assert persisted_summary["operators"][0]["profile_configured"] is True
    assert persisted_summary["operators"][0]["strategy_configured"] is True
    assert persisted_summary["operators"][0]["notification_channel_count"] == 1
    assert persisted_summary["operators"][0]["notification_channel_status"] == "active"
    assert persisted_summary["operators"][2]["profile_configured"] is False


def test_collection_writes_manifest_draft_from_collected_files(tmp_path):
    fake_get_json = _fake_http_client()
    config = CollectionConfig(
        base_url="http://api.test",
        token="secret-token",
        operator_ids=[101, 102, 104],
        evidence_dir=tmp_path,
        days=14,
        run_id="20260625T120000Z",
    )

    run_collection(config, http_get_json_func=fake_get_json)

    run_dir = tmp_path / "20260625T120000Z"
    manifest_path = run_dir / "manifest-draft.json"
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    assert manifest["status"] == "draft"
    assert manifest["basis"]["roadmap"] == "docs/roadmap.md"
    assert manifest["basis"]["runbook"] == "docs/operations/g2-evidence-runbook.md"
    assert (
        manifest["basis"]["review_template"]
        == "docs/operations/g2-exit-review-template.md"
    )
    assert manifest["basis"]["basis_commit"]
    assert manifest["evidence_window"]["required_days"] == 14
    assert manifest["evidence_window"]["counted_days"] == 1
    assert len(manifest["operators"]) == 3
    assert manifest["blocking_gaps"] == []
    assert manifest["action_register"] == {
        "dry_run_items": [],
        "approved_execution_items": [],
    }

    first_operator = manifest["operators"][0]
    assert first_operator["operator_id"] == 101
    assert first_operator["operator_scope_status"] == "pass"
    assert first_operator["profile"]["status"] == "pass"
    assert first_operator["strategy"]["status"] == "pass"
    assert first_operator["notification_channel"]["status"] == "pass"
    assert first_operator["evidence_paths"]["g2_evidence"][0].endswith(
        "operator-101/g2-evidence.json"
    )
    assert first_operator["evidence_paths"]["candidate_preview"] == []
    assert first_operator["evidence_paths"]["strategy_monitor"] == []
    assert first_operator["evidence_paths"]["decision_experiments"] == []

    daily_status = manifest["daily_status"][0]
    assert daily_status["status"] == "pass"
    assert daily_status["collect_g2_evidence_snapshot"]["status"] == "pass"
    assert daily_status["collect_g2_evidence_snapshot"]["path"].endswith(
        "g2-evidence-summary.json"
    )
    assert daily_status["operators"]["101"]["g2_evidence_status"] == "ready"
    assert daily_status["operators"]["101"]["blocking_gap_ids"] == []


def test_collection_converts_blocking_gaps_to_open_manifest_entries(tmp_path):
    fake_get_json = _fake_http_client(
        blocking_gaps_by_operator={
            202: [
                "Strategy monitor evidence is missing for operator_id=202 on 2026-06-24.",
                "Mixed scope evidence found for operator_id=202.",
            ]
        }
    )
    config = CollectionConfig(
        base_url="http://api.test",
        token="secret-token",
        operator_ids=[201, 202, 203],
        evidence_dir=tmp_path,
        days=30,
        run_id="20260625T120000Z",
    )

    run_collection(config, http_get_json_func=fake_get_json)

    manifest = json.loads(
        (tmp_path / "20260625T120000Z" / "manifest-draft.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["evidence_window"]["counted_days"] == 0
    assert manifest["daily_status"][0]["status"] == "partial"
    assert manifest["daily_status"][0]["operators"]["202"]["blocking_gap_ids"] == [
        "GAP-001",
        "GAP-002",
    ]
    assert manifest["blocking_gaps"] == [
        {
            "gap_id": "GAP-001",
            "date": "2026-06-24",
            "operator_id": 202,
            "source": "g2-evidence.blocking_gaps",
            "category": "missing evidence",
            "description": (
                "Strategy monitor evidence is missing for operator_id=202 "
                "on 2026-06-24."
            ),
            "status": "open",
            "treatment": "rerun",
        },
        {
            "gap_id": "GAP-002",
            "date": "2026-06-25",
            "operator_id": 202,
            "source": "g2-evidence.blocking_gaps",
            "category": "mixed data",
            "description": "Mixed scope evidence found for operator_id=202.",
            "status": "open",
            "treatment": "documented_not_counted",
        },
    ]


def test_manifest_does_not_count_mixed_or_missing_evidence_as_pass(tmp_path):
    def fake_get_json(**kwargs):
        payload = _fake_http_client()(**kwargs)
        operator_id = int(kwargs["params"]["operator_id"])
        path = kwargs["path"]
        if operator_id == 302 and path == "/api/v1/operator/profile":
            payload["current_operator_id"] = 999
        if operator_id == 303 and path == "/api/v1/analytics/g2-evidence":
            payload["evidence_status"] = "mixed_scope"
            payload["blocking_gaps"] = []
        return payload

    config = CollectionConfig(
        base_url="http://api.test",
        token="secret-token",
        operator_ids=[301, 302, 303],
        evidence_dir=tmp_path,
        days=30,
        run_id="20260625T120000Z",
    )

    run_collection(config, http_get_json_func=fake_get_json)

    manifest = json.loads(
        (tmp_path / "20260625T120000Z" / "manifest-draft.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["evidence_window"]["counted_days"] == 0
    assert manifest["daily_status"][0]["status"] == "partial"
    by_operator = {item["operator_id"]: item for item in manifest["operators"]}
    assert by_operator[302]["operator_scope_status"] == "mixed_scope"
    assert by_operator[302]["profile"]["status"] == "mixed_scope"
    assert manifest["daily_status"][0]["operators"]["303"][
        "g2_evidence_status"
    ] == "mixed_scope"


def test_main_returns_nonzero_when_fail_on_blocking_gaps(tmp_path):
    fake_get_json = _fake_http_client(
        blocking_gaps_by_operator={
            202: ["Synthetic experiment evidence is missing for operator_id=202."]
        }
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--base-url",
            "http://api.test",
            "--token",
            "secret-token",
            "--operator-id",
            "201",
            "--operator-id",
            "202",
            "--operator-id",
            "203",
            "--evidence-dir",
            str(tmp_path),
            "--run-id",
            "gap-run",
            "--fail-on-blocking-gaps",
        ],
        http_get_json_func=fake_get_json,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 3
    assert "TODO 1: Synthetic experiment evidence is missing" in stdout.getvalue()
    assert "--fail-on-blocking-gaps" in stderr.getvalue()
    persisted_summary = json.loads(
        (tmp_path / "gap-run" / "g2-evidence-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted_summary["status"] == "blocking_gaps"
    assert persisted_summary["blocking_gap_todos"] == [
        {
            "operator_id": 202,
            "gap_index": 1,
            "todo": "Synthetic experiment evidence is missing for operator_id=202.",
        }
    ]


def test_main_loads_operators_file_and_token_env(tmp_path, monkeypatch):
    operators_file = tmp_path / "operators.json"
    operators_file.write_text(
        json.dumps(
            {
                "operators": [
                    {"operator_id": "301"},
                    {"id": 302},
                    303,
                    303,
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKEN", "env-token")
    fake_get_json = _fake_http_client()

    code = main(
        [
            "--base-url",
            "http://api.test",
            "--operators-file",
            str(operators_file),
            "--evidence-dir",
            str(tmp_path),
            "--run-id",
            "file-run",
        ],
        http_get_json_func=fake_get_json,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert all(call["token"] == "env-token" for call in fake_get_json.calls)
    persisted_summary = json.loads(
        (tmp_path / "file-run" / "g2-evidence-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["operator_id"] for item in persisted_summary["operators"]] == [
        301,
        302,
        303,
    ]
