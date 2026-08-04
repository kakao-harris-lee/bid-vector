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


def test_normalize_marks_unconstrained_object_as_open_map():
    # 자유 ``dict`` 필드는 pydantic 버전에 따라 bare ``{"type": "object"}`` 로도 나오는데,
    # 그대로 두면 openapi-typescript 가 키 불허 ``Record<string, never>`` 로 렌더한다.
    schema = {"type": "object"}

    assert sync_openapi_types.normalize_open_objects(schema) == {
        "type": "object",
        "additionalProperties": True,
    }


def test_normalize_leaves_object_with_declared_properties_untouched():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    assert sync_openapi_types.normalize_open_objects(schema) == schema


def test_normalize_leaves_explicitly_closed_object_untouched():
    # ``extra="forbid"`` 모델이 내보내는 ``additionalProperties: false`` 는 의도된 선언이다.
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }

    assert sync_openapi_types.normalize_open_objects(schema) == schema


def test_normalize_leaves_declared_additional_properties_schema_untouched():
    schema = {"type": "object", "additionalProperties": {"type": "number"}}

    assert sync_openapi_types.normalize_open_objects(schema) == schema


def test_normalize_leaves_ref_nodes_untouched():
    # ``$ref`` 의 형태는 참조 대상이 정하므로 여기서 열어주면 안 된다.
    schema = {"$ref": "#/components/schemas/Thing", "type": "object"}

    assert sync_openapi_types.normalize_open_objects(schema) == schema


def test_normalize_reaches_nested_objects_through_properties_lists_and_anyof():
    schema = {
        "components": {
            "schemas": {
                "Report": {
                    "type": "object",
                    "properties": {
                        "metadata": {"type": "object"},
                        "rows": {"type": "array", "items": {"type": "object"}},
                        "summary": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                        "name": {"type": "string"},
                    },
                }
            }
        }
    }

    normalized = sync_openapi_types.normalize_open_objects(schema)

    report = normalized["components"]["schemas"]["Report"]["properties"]
    assert report["metadata"] == {"type": "object", "additionalProperties": True}
    assert report["rows"]["items"] == {"type": "object", "additionalProperties": True}
    assert report["summary"]["anyOf"] == [
        {"type": "object", "additionalProperties": True},
        {"type": "null"},
    ]
    assert report["name"] == {"type": "string"}
    # 감싸고 있는 ``Report`` 자체는 properties 를 선언했으므로 열리지 않는다.
    assert "additionalProperties" not in normalized["components"]["schemas"]["Report"]


def test_normalize_does_not_mutate_input_schema():
    schema = {"type": "object"}

    sync_openapi_types.normalize_open_objects(schema)

    assert schema == {"type": "object"}


def test_normalize_is_idempotent():
    schema = {"paths": {"/x": {"type": "object"}}, "components": {"type": "object"}}

    once = sync_openapi_types.normalize_open_objects(schema)
    twice = sync_openapi_types.normalize_open_objects(once)

    assert twice == once


def test_normalize_preserves_key_order():
    # 키 순서가 흔들리면 ``--check`` 가 실제 드리프트가 아닌 차이를 보고한다.
    schema = {"zeta": {"type": "object"}, "alpha": {}, "mid": {"type": "object"}}

    normalized = sync_openapi_types.normalize_open_objects(schema)

    assert list(normalized.keys()) == ["zeta", "alpha", "mid"]
    assert list(normalized["zeta"].keys()) == ["type", "additionalProperties"]


def test_normalize_passes_through_non_object_types_and_scalars():
    schema = {
        "type": "string",
        "enum": ["json", "csv"],
        "maxLength": 10,
        "nullable": None,
    }

    assert sync_openapi_types.normalize_open_objects(schema) == schema


def test_main_normalizes_schema_before_running_the_generator(tmp_path: Path):
    frontend_dir = tmp_path / "frontend"
    target = frontend_dir / "src" / "shared" / "types" / "openapi.d.ts"
    written: dict = {}

    def schema_provider() -> dict:
        return {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "0.1.0"},
            "paths": {},
            "components": {"schemas": {"Event": {"type": "object"}}},
        }

    def generate(schema_path: Path, output_path: Path, frontend_dir: Path) -> None:
        written.update(json.loads(schema_path.read_text(encoding="utf-8")))
        output_path.write_text("generated types\n", encoding="utf-8")

    code = sync_openapi_types.main(
        ["--frontend-dir", str(frontend_dir), "--output", str(target)],
        schema_provider=schema_provider,
        generator=generate,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == 0
    assert written["components"]["schemas"]["Event"] == {
        "type": "object",
        "additionalProperties": True,
    }


def test_write_openapi_schema_preserves_natural_key_order(tmp_path: Path):
    # The checked-in openapi.d.ts is generated in app.openapi()'s natural
    # (router-registration) order. Sorting keys here would reorder ~9k lines
    # and make --check falsely report drift, so insertion order must be kept.
    schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "0.1.0"},
        "paths": {
            "/zeta": {},
            "/alpha": {},
            "/mid": {},
        },
    }
    output_path = tmp_path / "openapi.json"

    sync_openapi_types.write_openapi_schema(schema, output_path)

    written = output_path.read_text(encoding="utf-8")
    assert list(json.loads(written)["paths"].keys()) == ["/zeta", "/alpha", "/mid"]
    assert (
        written.index('"/zeta"') < written.index('"/alpha"') < written.index('"/mid"')
    )


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
