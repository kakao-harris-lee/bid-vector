from __future__ import annotations

import json
import sys

from app.models.models import HistoricalData
from app.services.base_amount_basis import BASIS_CLEAN, BASIS_DERIVED_YEGA
from scripts import backtest_price_predictors as backtest_script
from scripts.backtest_price_predictors import load_records


def test_backtest_records_default_to_clean_base_amount_basis(test_db):
    test_db.add_all(
        [
            HistoricalData(
                notice_number="CLEAN",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=BASIS_CLEAN,
            ),
            HistoricalData(
                notice_number="DERIVED",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=BASIS_DERIVED_YEGA,
            ),
            HistoricalData(
                notice_number="UNCLASSIFIED",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=None,
            ),
        ]
    )
    test_db.commit()

    records = load_records(
        test_db,
        category="service",
        start_at=None,
        end_at=None,
        limit=10,
    )

    assert [record.notice_number for record in records] == ["CLEAN"]


def test_backtest_records_require_explicit_any_to_include_unclassified(test_db):
    test_db.add(
        HistoricalData(
            notice_number="UNCLASSIFIED",
            category="service",
            base_amount=100.0,
            bid_rate=0.9,
            base_amount_basis=None,
        )
    )
    test_db.commit()

    records = load_records(
        test_db,
        category="service",
        start_at=None,
        end_at=None,
        limit=10,
        base_amount_basis=None,
    )

    assert [record.notice_number for record in records] == ["UNCLASSIFIED"]


def test_backtest_cli_writes_flat_promotion_gate_report(tmp_path, monkeypatch):
    class _Db:
        def close(self):
            pass

    output_path = tmp_path / "backtest.json"
    monkeypatch.setattr(backtest_script, "SessionLocal", _Db)
    monkeypatch.setattr(backtest_script, "load_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        backtest_script,
        "build_predictor_backtest_report",
        lambda *args, **kwargs: {
            "status": "completed",
            "sample_count": 5,
            "guardrail_rate": 0.0,
            "fallback_rate": 0.0,
            "best_predictor_key": "historical",
            "best_average_absolute_error_rate": 0.01,
            "results": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_price_predictors.py", "--out", str(output_path)],
    )

    assert backtest_script.main() == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["status"] == "completed"
    assert report["best_predictor_key"] == "historical"
    assert "backtest" not in report
    assert report["settings"]["base_amount_basis"] == BASIS_CLEAN
    assert report["dataset_quality_status"] == "warning"
