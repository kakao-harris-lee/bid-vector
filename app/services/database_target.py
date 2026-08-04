"""Non-secret database identity checks for migration and deployment gates."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url

from app.core.constants import POSTGRES_DRIVERNAME


@dataclass(frozen=True)
class DatabaseTargetFingerprint:
    dialect: str
    configured_host: str | None
    configured_port: int | None
    database: str
    schema: str | None
    server_address: str | None
    server_port: int | None
    alembic_revision: str | None
    fingerprint_sha256: str

    def as_dict(self) -> dict[str, str | int | None]:
        return asdict(self)


def probe_database_target(engine: Engine) -> DatabaseTargetFingerprint:
    """Read the connected target identity without exposing credentials."""
    with engine.connect() as connection:
        dialect = connection.dialect.name
        if dialect == "postgresql":
            row = connection.execute(
                text(
                    "SELECT current_database(), current_schema(), "
                    "inet_server_addr()::text, inet_server_port()"
                )
            ).one()
            database, schema, server_address, server_port = row
        else:
            database = str(connection.engine.url.database or "")
            schema = None
            server_address = None
            server_port = None

        alembic_revision = None
        if inspect(connection).has_table("alembic_version"):
            alembic_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

        identity = {
            "dialect": dialect,
            "configured_host": connection.engine.url.host,
            "configured_port": connection.engine.url.port,
            "database": str(database),
            "schema": str(schema) if schema is not None else None,
            "server_address": (
                str(server_address) if server_address is not None else None
            ),
            "server_port": int(server_port) if server_port is not None else None,
        }
    canonical_identity = "\0".join(
        f"{key}={identity[key] if identity[key] is not None else ''}"
        for key in sorted(identity)
    )
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return DatabaseTargetFingerprint(
        **identity,
        alembic_revision=(
            str(alembic_revision) if alembic_revision is not None else None
        ),
        fingerprint_sha256=digest,
    )


def validate_database_target(
    target: DatabaseTargetFingerprint,
    *,
    expected_database: str,
    expected_host: str | None = None,
    expected_current_revision: str | None = None,
    expected_fingerprint: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if target.database != expected_database:
        errors.append(
            f"database mismatch: expected {expected_database!r}, connected {target.database!r}"
        )
    if expected_host and target.configured_host != expected_host:
        errors.append(
            "database host mismatch: "
            f"expected {expected_host!r}, configured {target.configured_host!r}"
        )
    if (
        expected_current_revision is not None
        and target.alembic_revision != expected_current_revision
    ):
        errors.append(
            "alembic revision mismatch: "
            f"expected {expected_current_revision!r}, connected {target.alembic_revision!r}"
        )
    if expected_fingerprint and target.fingerprint_sha256 != expected_fingerprint:
        errors.append(
            "database fingerprint mismatch: "
            f"expected {expected_fingerprint!r}, connected {target.fingerprint_sha256!r}"
        )
    return errors


def validate_database_configuration(
    database_url: str | None,
    *,
    split_user: str | None,
    split_password: str | None,
    split_host: str | None,
    split_port: int | None,
    split_database: str | None,
) -> list[str]:
    """Reject ambiguous DATABASE_URL and split DATABASE_* targets."""
    split_values = (
        split_user,
        split_password,
        split_host,
        split_port,
        split_database,
    )
    if not database_url or not all(value is not None for value in split_values):
        return []
    try:
        url = make_url(database_url)
    except Exception:  # noqa: BLE001 - report a non-secret configuration error
        return ["DATABASE_URL is invalid and cannot be compared to DATABASE_* settings"]
    if url.query:
        return [
            "DATABASE_URL query options cannot be preserved when split DATABASE_* settings are active"
        ]
    if url.drivername != POSTGRES_DRIVERNAME:
        return ["DATABASE_URL driver disagrees with split DATABASE_* settings"]
    url_identity = (url.username, url.password, url.host, url.port, url.database)
    split_identity = (
        split_user,
        split_password,
        split_host,
        split_port,
        split_database,
    )
    if url_identity != split_identity:
        return ["DATABASE_URL and split DATABASE_* settings disagree"]
    return []
