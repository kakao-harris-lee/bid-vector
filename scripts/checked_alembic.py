#!/usr/bin/env python3
"""Run Alembic only after verifying the connected database identity."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.services.database_target import (  # noqa: E402
    DatabaseTargetFingerprint,
    probe_database_target,
    validate_database_configuration,
    validate_database_target,
)


class CheckedAlembicResult(BaseModel):
    status: Literal["blocked", "failed", "passed"]
    target: DatabaseTargetFingerprint | None = None
    before: DatabaseTargetFingerprint | None = None
    after: DatabaseTargetFingerprint | None = None
    revision: str | None = None
    check_only: bool = False
    errors: list[str] = []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a non-secret DB fingerprint before running Alembic."
    )
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", default="")
    parser.add_argument("--expected-current-revision", default="")
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--revision", default="head")
    parser.add_argument("--check-only", action="store_true")
    return parser


def _print(result: CheckedAlembicResult) -> None:
    print(result.model_dump_json())


def _configured_database_url() -> str | None:
    explicit = os.environ.get("DATABASE_URL")
    if explicit is not None:
        return explicit
    value = dotenv_values(REPO_ROOT / ".env").get("DATABASE_URL")
    return str(value) if value is not None else None


def _preflight_errors(
    args: argparse.Namespace, before: DatabaseTargetFingerprint
) -> list[str]:
    errors = validate_database_configuration(
        _configured_database_url(),
        split_user=settings.DATABASE_USER,
        split_password=settings.DATABASE_PASSWORD,
        split_host=settings.DATABASE_HOST,
        split_port=settings.DATABASE_PORT,
        split_database=settings.DATABASE_NAME,
    )
    errors.extend(validate_database_target(
        before,
        expected_database=args.expected_database,
        expected_host=args.expected_host or None,
        expected_current_revision=args.expected_current_revision or None,
        expected_fingerprint=args.expected_fingerprint or None,
    ))
    return errors


def _upgrade(
    args: argparse.Namespace, before: DatabaseTargetFingerprint
) -> CheckedAlembicResult:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    target_revision = args.revision
    if args.revision == "head":
        target_revision = ScriptDirectory.from_config(
            alembic_config
        ).get_current_head()
    command.upgrade(alembic_config, args.revision)
    after = probe_database_target(engine)
    post_errors = validate_database_target(
        after,
        expected_database=args.expected_database,
        expected_host=args.expected_host or None,
        expected_fingerprint=before.fingerprint_sha256,
    )
    if after.alembic_revision != target_revision:
        post_errors.append(
            "migration target mismatch: "
            f"expected {target_revision!r}, connected {after.alembic_revision!r}"
        )
    return CheckedAlembicResult(
        status="passed" if not post_errors else "failed",
        before=before, after=after, revision=args.revision, errors=post_errors,
    )


def main() -> int:
    args = build_parser().parse_args()
    before = probe_database_target(engine)
    errors = _preflight_errors(args, before)
    if errors:
        _print(CheckedAlembicResult(status="blocked", target=before, errors=errors))
        return 2
    if args.check_only:
        _print(CheckedAlembicResult(status="passed", target=before, check_only=True))
        return 0
    result = _upgrade(args, before)
    _print(result)
    return 0 if result.status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
