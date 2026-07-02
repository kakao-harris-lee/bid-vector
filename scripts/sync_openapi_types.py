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


def write_openapi_schema(schema: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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

        write_openapi_schema(schema_provider(), schema_path)
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
