from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import DEFAULT_DATABASE_URL, Settings
from app.core.constants import POSTGRES_DRIVERNAME
from app.services.database_target import (
    probe_database_target,
    validate_database_configuration,
    validate_database_target,
)


def test_database_target_fingerprint_is_stable_and_non_secret(tmp_path):
    database_path = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('abc123')")
        )

    first = probe_database_target(engine)
    second = probe_database_target(engine)

    assert first == second
    assert first.database == str(database_path)
    assert first.alembic_revision == "abc123"
    assert len(first.fingerprint_sha256) == 64
    assert "password" not in str(first.as_dict()).lower()


def test_database_target_validation_reports_each_mismatch(tmp_path):
    database_path = tmp_path / "target.db"
    target = probe_database_target(create_engine(f"sqlite:///{database_path}"))

    errors = validate_database_target(
        target,
        expected_database="wrong.db",
        expected_host="db.internal",
        expected_current_revision="head",
        expected_fingerprint="0" * 64,
    )

    assert len(errors) == 4
    assert any("database mismatch" in error for error in errors)
    assert any("host mismatch" in error for error in errors)
    assert any("revision mismatch" in error for error in errors)
    assert any("fingerprint mismatch" in error for error in errors)


def test_database_configuration_rejects_url_and_split_target_mismatch():
    errors = validate_database_configuration(
        "postgresql+psycopg://biduser:secret@staging-db:5432/bid_vector_db",
        split_user="biduser",
        split_password="secret",
        split_host="production-db",
        split_port=5432,
        split_database="bid_vector_db",
    )

    assert errors == ["DATABASE_URL and split DATABASE_* settings disagree"]


def test_database_configuration_accepts_matching_url_and_split_target():
    errors = validate_database_configuration(
        "postgresql+psycopg://biduser:p%40ss@db:5432/bid_vector_db",
        split_user="biduser",
        split_password="p@ss",
        split_host="db",
        split_port=5432,
        split_database="bid_vector_db",
    )

    assert errors == []


def test_database_configuration_rejects_url_query_options_with_split_settings():
    errors = validate_database_configuration(
        "postgresql+psycopg://biduser:secret@db:5432/bid_vector_db?options=-csearch_path%3Dtenant",
        split_user="biduser",
        split_password="secret",
        split_host="db",
        split_port=5432,
        split_database="bid_vector_db",
    )

    assert errors == [
        "DATABASE_URL query options cannot be preserved when split DATABASE_* settings are active"
    ]


def test_composed_url_and_configuration_gate_share_one_driver():
    """URL 조립부(config)와 그 URL 을 검증하는 게이트가 같은 드라이버를 봐야 한다.

    둘이 갈리면 게이트는 아무도 조립하지 않는 드라이버와 비교하게 되고, 실제 불일치는
    조용히 통과한다.
    """
    composed = Settings(
        DATABASE_USER="biduser",
        DATABASE_PASSWORD="p@ss",
        DATABASE_HOST="db",
        DATABASE_PORT=5432,
        DATABASE_NAME="bid_vector_db",
    ).DATABASE_URL

    assert make_url(composed).drivername == POSTGRES_DRIVERNAME
    assert DEFAULT_DATABASE_URL.startswith(f"{POSTGRES_DRIVERNAME}://")
    assert (
        validate_database_configuration(
            composed,
            split_user="biduser",
            split_password="p@ss",
            split_host="db",
            split_port=5432,
            split_database="bid_vector_db",
        )
        == []
    )
