"""Tests for the base-amount contamination guard in the holdout backtest script."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.models.models import HistoricalData, Project, TenderResult
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


def test_base_amount_falls_back_to_budget_estimate_not_missing_attr():
    """Regression: a falsy stored base_amount must not touch a missing project attr.

    ``Project``'s only base/budget field is ``budget_estimate`` (추정가격); it has
    neither ``budget_amount`` nor ``estimated_price`` (that one lives on
    ``PaperBidSettlement``). The old fallback referenced both missing attributes, so
    any target whose ``historical.base_amount`` was 0/None raised ``AttributeError``
    and aborted the entire run — the latent crash that blocked clean-only broad re-runs.
    """
    project = Project(budget_estimate=25_000_000.0)
    assert not hasattr(project, "budget_amount")  # pin the root cause
    assert not hasattr(project, "estimated_price")

    historical = HistoricalData(base_amount=0.0)  # falsy -> must hit the fallback
    # No AttributeError, and the fallback resolves to the real project field.
    assert holdouts.base_amount(project, historical) == 25_000_000.0

    historical_none = HistoricalData(base_amount=None)
    assert holdouts.base_amount(project, historical_none) == 25_000_000.0


def test_base_amount_prefers_stored_base_amount():
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=43_996_200.0)
    assert holdouts.base_amount(project, historical) == 43_996_200.0


def test_resolve_pricing_base_clean_keeps_stored_ignores_estimate():
    """Clean rows measure on the stored 기초금액 even if an estimate exists."""
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=43_996_200.0, base_amount_estimated=50_000_000.0)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_CLEAN)
    assert value == 43_996_200.0
    assert source == holdouts.BASE_SOURCE_STORED


def test_resolve_pricing_base_derived_yega_swaps_in_estimate():
    """derived-yega + estimate -> use the recovered 기초금액, measuring on 기초금액-basis.

    The stored base is the 예정가-역산 pollution (_YEGA_BASE); the estimate is the
    복수예비가격 midpoint recovery. Error must be measured against the estimate, not
    the 예정가-basis stored value.
    """
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=_YEGA_BASE, base_amount_estimated=44_000_000.0)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_DERIVED_YEGA)
    assert value == 44_000_000.0
    assert source == holdouts.BASE_SOURCE_ESTIMATED
    # The swap actually shifts the basis: win/estimate is near par, win/예정가-base is not.
    win = 43_996_200.0
    assert abs(win / value - 0.9999) < 0.02  # 기초금액-basis ~near-par
    assert win / _YEGA_BASE < 0.9  # the polluted 예정가-basis rate is much lower


def test_resolve_pricing_base_derived_yega_without_estimate_keeps_stored():
    """No estimate -> existing behavior (stored base, contaminated but unchanged)."""
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=_YEGA_BASE, base_amount_estimated=None)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_DERIVED_YEGA)
    assert value == _YEGA_BASE
    assert source == holdouts.BASE_SOURCE_STORED


def test_resolve_pricing_base_suspect_no_stored_uses_estimate():
    """suspect + missing stored base + estimate present -> estimate wins."""
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=0.0, base_amount_estimated=30_000_000.0)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_SUSPECT_FRACTIONAL)
    assert value == 30_000_000.0
    assert source == holdouts.BASE_SOURCE_ESTIMATED


def test_resolve_pricing_base_falls_back_to_project_budget():
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=None, base_amount_estimated=None)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_SUSPECT_FRACTIONAL)
    assert value == 25_000_000.0
    assert source == holdouts.BASE_SOURCE_PROJECT


def test_resolve_pricing_base_none_when_no_base_available():
    project = Project()  # budget_estimate is None
    historical = HistoricalData(base_amount=None, base_amount_estimated=None)
    value, source = holdouts.resolve_pricing_base(project, historical, BASIS_SUSPECT_FRACTIONAL)
    assert value is None
    assert source == holdouts.BASE_SOURCE_NONE


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
