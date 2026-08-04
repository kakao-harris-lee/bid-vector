#!/usr/bin/env python3
"""Generate or verify frontend OpenAPI TypeScript definitions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIR = REPO_ROOT / "frontend"
DEFAULT_OUTPUT = DEFAULT_FRONTEND_DIR / "src" / "shared" / "types" / "openapi.d.ts"

SchemaProvider = Callable[[], dict[str, Any]]
Generator = Callable[[Path, Path, Path], None]


def ensure_repo_root_on_path() -> None:
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def configure_openapi_export_env() -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify frontend/src/shared/types/openapi.d.ts.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify generated types match the checked-in file without writing it.",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=DEFAULT_FRONTEND_DIR,
        help="Frontend package directory used for npm/openapi-typescript.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the generated openapi.d.ts file.",
    )
    return parser


def default_schema_provider() -> dict[str, Any]:
    configure_openapi_export_env()
    ensure_repo_root_on_path()
    from app.main import app

    return app.openapi()


# pydantic 2.5 는 자유 ``dict`` 필드를 bare ``{"type": "object"}`` 로 내보내지만 이후 버전은
# ``additionalProperties: true`` 를 함께 내보낸다. openapi-typescript 는 전자를 키를 하나도
# 허용하지 않는 ``Record<string, never>`` 로, 후자를 ``{[key: string]: unknown}`` 으로 렌더해서
# 같은 소스가 생성 환경에 따라 다른 openapi.d.ts 를 만들고 ``--check`` 가 플립플롭한다.
# 형태를 선언하지 않은 객체를 생성 직후 열린 맵으로 고정해 그 버전 차이를 흡수한다.
OBJECT_TYPE = "object"
ADDITIONAL_PROPERTIES_KEY = "additionalProperties"
OBJECT_SHAPE_KEYS = ("properties", ADDITIONAL_PROPERTIES_KEY)
OPEN_OBJECT_ADDITIONAL_PROPERTIES = True

# 스키마 트리의 노드. 임의 JSON 이라 구체 모델로 좁힐 수는 없지만, 걸어야 하는 것이
# "JSON 값"이라는 계약은 ``Any`` 보다 정확하게 표현된다.
JsonValue = str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]


def is_unconstrained_object(node: dict[str, JsonValue]) -> bool:
    """형태를 선언하지 않은 ``type: "object"`` 노드인가(= 자유 ``dict`` 필드인가)."""
    if node.get("type") != OBJECT_TYPE:
        return False
    # ``$ref`` 노드의 형태는 참조 대상이 정하므로 여기서 덮어쓰지 않는다.
    if "$ref" in node:
        return False
    return not any(key in node for key in OBJECT_SHAPE_KEYS)


def normalize_open_objects(node: JsonValue) -> JsonValue:
    """형태 미선언 객체에 ``additionalProperties`` 를 주입한 스키마 사본을 돌려준다.

    입력 트리를 변경하지 않는 순수 함수이고, ``--check`` 가 순서 차이를 드리프트로
    오인하지 않도록 키 순서를 보존한다(주입 키는 노드 끝에 붙는다).
    """
    if isinstance(node, list):
        return [normalize_open_objects(item) for item in node]
    if not isinstance(node, dict):
        return node

    normalized = {key: normalize_open_objects(value) for key, value in node.items()}
    if is_unconstrained_object(node):
        normalized[ADDITIONAL_PROPERTIES_KEY] = OPEN_OBJECT_ADDITIONAL_PROPERTIES
    return normalized


def write_openapi_schema(schema: dict[str, Any], output_path: Path) -> None:
    # Preserve the natural (router-registration) key order of app.openapi(); the
    # checked-in openapi.d.ts is generated in that order, so sorting keys here
    # would reorder ~9k lines and make --check falsely report drift.
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_openapi_typescript(
    *,
    schema_path: Path,
    output_path: Path,
    frontend_dir: Path,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    run(
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
        ],
        check=True,
    )


def _read_text_if_exists(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    schema_provider: SchemaProvider = default_schema_provider,
    generator: Generator = run_openapi_typescript,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    frontend_dir = args.frontend_dir.resolve()
    output_path = args.output.resolve()

    with tempfile.TemporaryDirectory(prefix="bid-vector-openapi-") as tmp:
        tmp_dir = Path(tmp)
        schema_path = tmp_dir / "openapi.json"
        generated_path = tmp_dir / "openapi.d.ts"

        write_openapi_schema(normalize_open_objects(schema_provider()), schema_path)
        generator(
            schema_path=schema_path,
            output_path=generated_path,
            frontend_dir=frontend_dir,
        )

        generated = generated_path.read_text(encoding="utf-8")
        current = _read_text_if_exists(output_path)
        if args.check:
            if current != generated:
                print(
                    "OpenAPI types are out of sync. Run "
                    "`npm --prefix frontend run sync-types` and commit the result.",
                    file=stderr,
                )
                return 1
            print("OpenAPI types are up to date.", file=stdout)
            return 0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated_path, output_path)
        print(f"Wrote OpenAPI types to {output_path}", file=stdout)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
