"""Database compatibility helpers for project similarity storage."""

from sqlalchemy import inspect

from app.services.project_similarity_constants import PROJECT_VECTOR_DIMENSIONS


def ensure_project_vector_schema(engine) -> None:
    """Ensure pgvector extension, embedding columns, and HNSW index exist."""
    if engine.dialect.name != "postgresql":
        return
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS semantic_text TEXT DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_payload TEXT DEFAULT '[]'",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(255)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ",
        f"ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding VECTOR({PROJECT_VECTOR_DIMENSIONS})",
        "CREATE INDEX IF NOT EXISTS ix_projects_embedding_hnsw ON projects USING hnsw (embedding vector_cosine_ops)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def ensure_project_metadata_schema(engine) -> None:
    """Ensure crawl-link metadata columns exist in already-created databases."""
    inspector = inspect(engine)
    if "projects" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("projects")}
    columns = {
        "notice_number": "VARCHAR(100)", "source_url": "TEXT",
        "issuing_agency": "VARCHAR(255)", "demand_agency": "VARCHAR(255)",
    }
    with engine.begin() as connection:
        for name, ddl in columns.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")
        if engine.dialect.name in {"sqlite", "postgresql"}:
            for name in ("notice_number", "issuing_agency", "demand_agency"):
                connection.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_projects_{name} ON projects ({name})"
                )
