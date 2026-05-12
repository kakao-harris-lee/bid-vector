"""Schema helpers for persisted operator strategy tuning settings."""

from __future__ import annotations

from sqlalchemy import inspect

from app.core.single_user import DEFAULT_OPERATOR_BID_NOW_THRESHOLD, DEFAULT_OPERATOR_REVIEW_THRESHOLD


def ensure_operator_strategy_schema(engine) -> None:
    """Ensure operator strategy tables contain persisted decision-threshold fields."""
    inspector = inspect(engine)
    if "operator_strategies" not in set(inspector.get_table_names()):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("operator_strategies")}
    column_statements = {
        "bid_now_threshold": f"FLOAT DEFAULT {DEFAULT_OPERATOR_BID_NOW_THRESHOLD}",
        "review_threshold": f"FLOAT DEFAULT {DEFAULT_OPERATOR_REVIEW_THRESHOLD}",
    }

    with engine.begin() as connection:
        for column_name, ddl in column_statements.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(f"ALTER TABLE operator_strategies ADD COLUMN {column_name} {ddl}")

        if "bid_now_threshold" not in existing_columns:
            connection.exec_driver_sql(
                f"UPDATE operator_strategies SET bid_now_threshold = {DEFAULT_OPERATOR_BID_NOW_THRESHOLD} "
                "WHERE bid_now_threshold IS NULL"
            )
        if "review_threshold" not in existing_columns:
            connection.exec_driver_sql(
                f"UPDATE operator_strategies SET review_threshold = {DEFAULT_OPERATOR_REVIEW_THRESHOLD} "
                "WHERE review_threshold IS NULL"
            )