"""Schema helpers for persisted prediction observability metadata."""

from __future__ import annotations

from sqlalchemy import inspect


def ensure_price_prediction_metadata_schema(engine) -> None:
    """Ensure existing price prediction tables contain observability fields."""
    inspector = inspect(engine)
    if "price_predictions" not in set(inspector.get_table_names()):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("price_predictions")}
    column_statements = {
        "predictor_name": "VARCHAR(100) DEFAULT 'historical_statistical'",
        "predictor_family": "VARCHAR(100) DEFAULT 'statistical'",
        "fallback_reason": "TEXT",
        "selector_name": "VARCHAR(100) DEFAULT 'configured_preference'",
        "selection_reason": "TEXT",
        "backtest_sample_count": "INTEGER DEFAULT 0",
        "backtest_average_absolute_error_rate": "FLOAT",
        "training_window_size": "INTEGER DEFAULT 0",
        "pricing_mode": "VARCHAR(50) DEFAULT 'heuristic'",
        "historical_sample_size": "INTEGER DEFAULT 0",
        "agency_match_sample_size": "INTEGER DEFAULT 0",
        "predicted_bid_rate": "FLOAT DEFAULT 0.0",
        "guardrail_applied": "BOOLEAN DEFAULT FALSE",
        "guardrail_reason": "TEXT",
        "floor_bid_rate": "FLOAT",
        "floor_price": "FLOAT",
    }

    with engine.begin() as connection:
        for column_name, ddl in column_statements.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(f"ALTER TABLE price_predictions ADD COLUMN {column_name} {ddl}")

        if "predictor_name" not in existing_columns:
            connection.exec_driver_sql(
                "UPDATE price_predictions SET predictor_name = 'historical_statistical' "
                "WHERE predictor_name IS NULL OR predictor_name = ''"
            )
        if "predictor_family" not in existing_columns:
            connection.exec_driver_sql(
                "UPDATE price_predictions SET predictor_family = 'statistical' "
                "WHERE predictor_family IS NULL OR predictor_family = ''"
            )
        if "pricing_mode" not in existing_columns:
            connection.exec_driver_sql(
                "UPDATE price_predictions SET pricing_mode = 'heuristic' "
                "WHERE pricing_mode IS NULL OR pricing_mode = ''"
            )
        if "selector_name" not in existing_columns:
            connection.exec_driver_sql(
                "UPDATE price_predictions SET selector_name = 'configured_preference' "
                "WHERE selector_name IS NULL OR selector_name = ''"
            )
