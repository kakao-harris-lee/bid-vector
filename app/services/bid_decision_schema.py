"""Schema helpers for persisted bid-decision metadata."""

from __future__ import annotations

from sqlalchemy import inspect


def ensure_bid_decision_schema(engine) -> None:
    """Ensure new bid-decision metadata columns exist in already-created databases."""
    inspector = inspect(engine)
    if "bid_decision_records" not in set(inspector.get_table_names()):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("bid_decision_records")}
    column_statements = {
        "initial_action": "VARCHAR(50) DEFAULT 'skip'",
        "initial_decision_status": "VARCHAR(50) DEFAULT 'planned'",
        "first_decided_at": "TIMESTAMP",
        "urgency_score": "FLOAT DEFAULT 0.0",
        "competitiveness_score": "FLOAT DEFAULT 0.0",
        "budget_capture_score": "FLOAT DEFAULT 0.0",
        "expected_margin_score": "FLOAT DEFAULT 0.0",
        "execution_complexity_score": "FLOAT DEFAULT 0.0",
        "workload_source": "VARCHAR(20) DEFAULT 'provided'",
        "score_breakdown": "TEXT DEFAULT '{}'",
    }

    with engine.begin() as connection:
        for column_name, ddl in column_statements.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(f"ALTER TABLE bid_decision_records ADD COLUMN {column_name} {ddl}")

        if "first_decided_at" not in existing_columns:
            connection.exec_driver_sql("UPDATE bid_decision_records SET first_decided_at = created_at WHERE first_decided_at IS NULL")
        if "initial_action" not in existing_columns:
            connection.exec_driver_sql("UPDATE bid_decision_records SET initial_action = COALESCE(action, 'skip') WHERE initial_action IS NULL OR initial_action = ''")
        if "initial_decision_status" not in existing_columns:
            connection.exec_driver_sql("UPDATE bid_decision_records SET initial_decision_status = COALESCE(decision_status, 'planned') WHERE initial_decision_status IS NULL OR initial_decision_status = ''")
