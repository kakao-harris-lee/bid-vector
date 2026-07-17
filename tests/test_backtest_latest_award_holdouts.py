"""Tests for the base-amount contamination guard in the holdout backtest script."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.models.models import HistoricalData, TenderResult
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    classify_base_basis,
)

# Load the script module by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "backtest_latest_award_holdouts",
    Path(__file__).resolve().parents[1] / "scripts" / "backtest_latest_award_holdouts.py",
)
holdouts = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = holdouts
_SPEC.loader.exec_module(holdouts)

# Real postmortem case: 43,996,200 ÷ 0.88035 = 49,975,805.0775 (예정가-basis 오염).
_YEGA_BASE = 43_996_200 / 0.88035


def test_resolve_base_basis_clean_integer():
    historical = HistoricalData(base_amount=43_996_200.0)
    result = TenderResult(winning_amount=43_000_000.0, winning_rate=0.977)
    assert holdouts.resolve_base_basis(historical, result) == BASIS_CLEAN


def test_resolve_base_basis_derived_yega_reproduces_postmortem():
    historical = HistoricalData(base_amount=_YEGA_BASE)
    result = TenderResult(winning_amount=43_996_200.0, winning_rate=0.88035)
    assert holdouts.resolve_base_basis(historical, result) == BASIS_DERIVED_YEGA


def test_resolve_base_basis_normalizes_percentage_rate():
    """winning_rate stored as a percentage (88.035) still resolves derived-yega."""
    historical = HistoricalData(base_amount=_YEGA_BASE)
    result = TenderResult(winning_amount=43_996_200.0, winning_rate=88.035)
    assert holdouts.resolve_base_basis(historical, result) == BASIS_DERIVED_YEGA


def test_resolve_base_basis_suspect_without_winning():
    historical = HistoricalData(base_amount=12_345_678.4321)
    result = TenderResult(winning_amount=0.0, winning_rate=0.0)
    assert holdouts.resolve_base_basis(historical, result) == BASIS_SUSPECT_FRACTIONAL


def test_resolve_base_basis_uses_raw_rate_not_derived_fallback():
    """Pin that the guard passes the RAW winning_rate, never a derived one.

    The script derives a fallback rate (winning_amount / budget) elsewhere. Feeding
    that into classify_base_basis would be self-fulfilling — base × (amount/base) ==
    amount always matches the derived-yega rule — so every rate-missing row would be
    mislabeled derived-yega. resolve_base_basis must use the raw winning_rate, so a
    row with a missing rate stays suspect (not falsely excluded from the aggregates).
    """
    base = 12_345_678.4321  # fractional, not VAT-derived
    historical = HistoricalData(base_amount=base)
    # winning_amount present but winning_rate missing (raw 0.0 -> normalizes to None)
    result = TenderResult(winning_amount=9_000_000.0, winning_rate=0.0)
    assert holdouts.resolve_base_basis(historical, result) == BASIS_SUSPECT_FRACTIONAL

    # Sanity: had the guard (wrongly) used the derived rate, it WOULD self-match yega.
    derived_rate = 9_000_000.0 / base
    assert classify_base_basis(base, 9_000_000.0, derived_rate) == BASIS_DERIVED_YEGA


def _row(basis: str, err_pct: float) -> dict:
    """Minimal evaluated-target row carrying what aggregate() reads."""
    scenario = {"absolute_amount_error_pct": err_pct, "rate_error_bp": 10.0}
    return {"basis": basis, "recommended": scenario, "closest": scenario}


_THRESHOLDS = (0.003,)


def test_partition_excludes_contaminated_by_default():
    rows = [
        _row(BASIS_CLEAN, 0.001),
        _row(BASIS_DERIVED_YEGA, 0.5),
        _row(BASIS_SUSPECT_FRACTIONAL, 0.9),
    ]
    aggregation_rows, excluded = holdouts.partition_targets_by_basis(
        rows, include_contaminated=False
    )
    assert [r["basis"] for r in aggregation_rows] == [BASIS_CLEAN]
    assert excluded == 2

    summary = holdouts.aggregate(aggregation_rows, _THRESHOLDS)
    # only the clean target feeds the error stats
    assert summary["recommended"]["sample_count"] == 1
    assert summary["recommended"]["mean_absolute_amount_error_pct"] == 0.001
    assert summary["recommended"]["within_counts"]["0.3%"] == 1


def test_partition_include_contaminated_folds_all_in():
    rows = [
        _row(BASIS_CLEAN, 0.001),
        _row(BASIS_DERIVED_YEGA, 0.5),
        _row(BASIS_SUSPECT_FRACTIONAL, 0.9),
    ]
    aggregation_rows, excluded = holdouts.partition_targets_by_basis(
        rows, include_contaminated=True
    )
    assert len(aggregation_rows) == 3
    assert excluded == 0

    summary = holdouts.aggregate(aggregation_rows, _THRESHOLDS)
    assert summary["recommended"]["sample_count"] == 3
    # contaminated high-error rows drag the mean up and stay out of the threshold
    assert summary["recommended"]["mean_absolute_amount_error_pct"] > 0.001
    assert summary["recommended"]["within_counts"]["0.3%"] == 1
