from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from scripts.run_g2_synthetic_evidence import main, operator_scope_summary


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSyntheticExperimentService:
    def __init__(self, candidate: dict[str, Any], materialize_result=None) -> None:
        self.candidate = candidate
        self.materialize_result = materialize_result or {
            "status": "queued",
            "candidate": candidate,
            "experiment": {"id": 12},
            "run": {"id": 34, "status": "queued"},
        }
        self.materialize_count = 0

    def build_sample_gap_plan(self, *, max_runs: int = 20) -> dict[str, Any]:
        return {
            "gap_count": 1,
            "source_run_count": max_runs,
            "warnings": [],
            "gaps": [
                {
                    "dimension": "preset",
                    "key": "sample-preset",
                    "recommendation": {"preset_name": "sample-preset"},
                }
            ],
        }

    def build_sample_gap_run_candidate(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: str | None = None,
    ) -> dict[str, Any]:
        assert dimension == "preset"
        assert key == "sample-preset"
        assert max_runs == 20
        assert action_code is None
        return self.candidate

    def materialize_sample_gap_candidate_run(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: str | None = None,
    ) -> dict[str, Any]:
        assert dimension == "preset"
        assert key == "sample-preset"
        assert max_runs == 20
        assert action_code is None
        self.materialize_count += 1
        return self.materialize_result


def _candidate(*, ready: bool) -> dict[str, Any]:
    return {
        "operator_id_scope_ready": ready,
        "operator_targets": [
            {
                "slug": "alpha",
                "operator_id": 101,
                "operator_id_scope_ready": True,
            },
            {
                "slug": "missing",
                "operator_id": None,
                "operator_id_scope_ready": False,
            },
        ],
        "unresolved_operator_targets": [
            {"slug": "missing", "reason": "not_found"},
        ],
        "run_allowed": True,
        "warnings": [],
        "blocked_by_warnings": [],
    }


def _run_with_fake_service(capsys, fake_service: _FakeSyntheticExperimentService):
    session = _FakeSession()

    code = main(
        ["--preset", "sample-preset"],
        session_factory=lambda: session,
        service_factory=lambda db: fake_service,
    )

    captured = capsys.readouterr()
    return code, json.loads(captured.out), session


def test_dry_run_outputs_operator_scope_summary(capsys):
    fake_service = _FakeSyntheticExperimentService(_candidate(ready=False))

    code, payload, session = _run_with_fake_service(capsys, fake_service)

    assert code == 0
    assert session.closed is True
    assert payload["status"] == "planned"
    assert payload["operator_scope"] == {
        "operator_id_scope_ready": False,
        "operator_targets": fake_service.candidate["operator_targets"],
        "unresolved_operator_targets": fake_service.candidate[
            "unresolved_operator_targets"
        ],
    }


def test_operator_scope_summary_derives_unresolved_targets_when_missing() -> None:
    candidate = _candidate(ready=False)
    candidate.pop("unresolved_operator_targets")

    summary = operator_scope_summary(candidate)

    assert summary["operator_id_scope_ready"] is False
    assert summary["unresolved_operator_targets"] == [
        {
            "slug": "missing",
            "operator_id": None,
            "operator_id_scope_ready": False,
        }
    ]


def test_write_blocks_when_operator_scope_not_ready(capsys):
    fake_service = _FakeSyntheticExperimentService(_candidate(ready=False))

    session = _FakeSession()
    code = main(
        ["--write", "--preset", "sample-preset"],
        session_factory=lambda: session,
        service_factory=lambda db: fake_service,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert session.closed is True
    assert fake_service.materialize_count == 0
    assert payload["status"] == "blocked_operator_scope"
    assert payload["write_performed"] is False
    assert payload["operator_scope"]["operator_id_scope_ready"] is False


def test_write_preserves_mixed_data_block_before_operator_scope(capsys):
    candidate = _candidate(ready=False)
    candidate["run_allowed"] = False
    candidate["blocked_by_warnings"] = ["canonical_synthetic_mixed"]
    fake_service = _FakeSyntheticExperimentService(candidate)

    session = _FakeSession()
    code = main(
        ["--write", "--preset", "sample-preset"],
        session_factory=lambda: session,
        service_factory=lambda db: fake_service,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert session.closed is True
    assert fake_service.materialize_count == 0
    assert payload["status"] == "blocked"
    assert payload["write_performed"] is False
    assert payload["candidate"] == candidate


def test_write_materializes_when_operator_scope_ready(capsys):
    candidate = _candidate(ready=True)
    candidate["operator_targets"][1]["operator_id"] = 202
    candidate["operator_targets"][1]["operator_id_scope_ready"] = True
    candidate["unresolved_operator_targets"] = []
    fake_service = _FakeSyntheticExperimentService(candidate)

    session = _FakeSession()
    code = main(
        ["--write", "--preset", "sample-preset"],
        session_factory=lambda: session,
        service_factory=lambda db: fake_service,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert session.closed is True
    assert fake_service.materialize_count == 1
    assert payload["status"] == "queued"
    assert payload["write_performed"] is True


def test_help_does_not_import_database_dependencies() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_g2_synthetic_evidence.py"
    code = textwrap.dedent(
        f"""
        import builtins
        import runpy
        import sys

        script = {str(script)!r}
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in {{
                "app.core.database",
                "app.services.synthetic_experiment",
            }}:
                raise AssertionError(f"unexpected eager import: {{name}}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import
        sys.argv = [script, "--help"]
        runpy.run_path(script, run_name="__main__")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: run_g2_synthetic_evidence.py" in result.stdout
