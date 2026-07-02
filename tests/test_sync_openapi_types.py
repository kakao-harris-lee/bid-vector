from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys

from scripts import sync_openapi_types


def _schema() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "0.1.0"},
        "paths": {},
    }


def test_check_mode_reports_drift_without_overwriting_target(tmp_path: Path):
    frontend_dir = tmp_path / "frontend"
    target = frontend_dir / "src" / "shared" / "types" / "openapi.d.ts"
    target.parent.mkdir(parents=True)
    target.write_text("old types\n", encoding="utf-8")

    def generate(schema_path: Path, output_path: Path, frontend_dir: Path) -> None:
        assert json.loads(schema_path.read_text(encoding="utf-8")) == _schema()
        assert frontend_dir == tmp_path / "frontend"
        output_path.write_text("new types\n", encoding="utf-8")

    stderr = io.StringIO()
    code = sync_openapi_types.main(
        [
            "--check",
            "--frontend-dir",
            str(frontend_dir),
            "--output",
            str(target),
        ],
        schema_provider=_schema,
        generator=generate,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == "old types\n"
    assert "OpenAPI types are out of sync" in stderr.getvalue()
    assert "npm --prefix frontend run sync-types" in stderr.getvalue()


def test_check_mode_passes_when_generated_types_match_target(tmp_path: Path):
    frontend_dir = tmp_path / "frontend"
    target = frontend_dir / "src" / "shared" / "types" / "openapi.d.ts"
    target.parent.mkdir(parents=True)
    target.write_text("same types\n", encoding="utf-8")

    def generate(schema_path: Path, output_path: Path, frontend_dir: Path) -> None:
        assert schema_path.exists()
        assert frontend_dir == tmp_path / "frontend"
        output_path.write_text("same types\n", encoding="utf-8")

    stdout = io.StringIO()
    code = sync_openapi_types.main(
        [
            "--check",
            "--frontend-dir",
            str(frontend_dir),
            "--output",
            str(target),
        ],
        schema_provider=_schema,
        generator=generate,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert "OpenAPI types are up to date" in stdout.getvalue()


def test_write_mode_replaces_target_with_generated_types(tmp_path: Path):
    frontend_dir = tmp_path / "frontend"
    target = frontend_dir / "src" / "shared" / "types" / "openapi.d.ts"

    def generate(schema_path: Path, output_path: Path, frontend_dir: Path) -> None:
        assert schema_path.exists()
        assert frontend_dir == tmp_path / "frontend"
        output_path.write_text("generated types\n", encoding="utf-8")

    stdout = io.StringIO()
    code = sync_openapi_types.main(
        [
            "--frontend-dir",
            str(frontend_dir),
            "--output",
            str(target),
        ],
        schema_provider=_schema,
        generator=generate,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert target.read_text(encoding="utf-8") == "generated types\n"
    assert "Wrote OpenAPI types" in stdout.getvalue()


def test_openapi_typescript_runner_uses_frontend_npm_exec(tmp_path: Path):
    schema_path = tmp_path / "openapi.json"
    output_path = tmp_path / "openapi.d.ts"
    frontend_dir = tmp_path / "frontend"
    calls: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> None:
        calls.append(command)
        assert check is True

    sync_openapi_types.run_openapi_typescript(
        schema_path=schema_path,
        output_path=output_path,
        frontend_dir=frontend_dir,
        run=fake_run,
    )

    assert calls == [
        [
            "npm",
            "--prefix",
            str(frontend_dir),
            "exec",
            "openapi-typescript",
            "--",
            str(schema_path),
            "-o",
            str(output_path),
        ]
    ]


def test_ensure_repo_root_on_path_prepends_repo_root(monkeypatch):
    monkeypatch.setattr(sys, "path", [])

    sync_openapi_types.ensure_repo_root_on_path()

    assert sys.path[0] == str(sync_openapi_types.REPO_ROOT)


def test_configure_openapi_export_env_defaults_to_local_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    sync_openapi_types.configure_openapi_export_env()

    assert os.environ["DATABASE_URL"] == "sqlite:///:memory:"


def test_configure_openapi_export_env_preserves_explicit_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///custom.db")

    sync_openapi_types.configure_openapi_export_env()

    assert os.environ["DATABASE_URL"] == "sqlite:///custom.db"


def test_frontend_package_exposes_sync_type_scripts():
    package_json = json.loads(
        Path("frontend/package.json").read_text(encoding="utf-8")
    )

    assert package_json["scripts"]["sync-types"] == "python ../scripts/sync_openapi_types.py"
    assert package_json["scripts"]["check:sync-types"] == (
        "python ../scripts/sync_openapi_types.py --check"
    )
