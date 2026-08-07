"""Tests for the base-amount contamination guard in the holdout backtest script."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
    classify_base_basis,
)
from app.services.prediction_dataset import PredictionDatasetService

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


def test_resolve_base_basis_does_not_see_the_ratio_rule():
    """홀드아웃은 비율 규칙을 **의도적으로** 보지 않는다(경계 고정, 배선은 후속).

    이 헬퍼는 저장된 ``base_amount_basis`` 를 읽지 않고 매번 재분류하는데, 분류기의 4번째
    인자(공고 추정가격)를 넘기지 않으므로 백필이 ``suspect-ratio`` 로 재태깅한 행도 여기서는
    다시 ``clean`` 으로 집계된다. 세 호출부 모두 ``project`` 가 스코프 안에 있어 배선 자체는
    가능하지만, 배선하면 홀드아웃 오차 수치가 그 시점에 함께 움직여 "이 PR 의 재태깅은
    측정 수치를 바꾸지 않는다"는 주장과 섞인다. 그래서 defer 하고 경계만 고정한다.

    이 기대값이 바뀌면 홀드아웃 clean 집계의 모집단이 바뀌었다는 뜻이므로, 그때는 오차
    수치 이동을 함께 보고해야 한다.
    """
    historical = HistoricalData(base_amount=140_800_000.0)
    result = TenderResult(winning_amount=0.0, winning_rate=0.0)

    # 같은 값이라도 추정가격을 함께 넘기면 분류기는 suspect-ratio 로 판정한다.
    assert (
        classify_base_basis(140_800_000.0, 0.0, 0.0, 100_000_000.0)
        == BASIS_SUSPECT_RATIO
    )
    # 홀드아웃 경로는 그 인자를 넘기지 않으므로 clean 으로 남는다.
    assert holdouts.resolve_base_basis(historical, result) == BASIS_CLEAN


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


def test_resolve_pricing_base_delegates_to_shared_primitive_null_basis():
    """The basis rule is now single-sourced via get_reliable_base. In the holdout,
    ``basis`` always comes from classify_base_basis (never None), so delegation is
    value-identical; pin that an unknown/NULL basis with an estimate still returns the
    STORED base (matching get_reliable_base's BASE_FALLBACK, not a spurious estimate swap)."""
    project = Project(budget_estimate=25_000_000.0)
    historical = HistoricalData(base_amount=43_996_200.0, base_amount_estimated=50_000_000.0)
    # basis=None is NOT one of the concrete verdicts classify_base_basis emits, but the
    # shared primitive must keep the stored base (only an EXPLICIT non-clean basis swaps).
    value, source = holdouts.resolve_pricing_base(project, historical, None)  # type: ignore[arg-type]
    assert value == 43_996_200.0
    assert source == holdouts.BASE_SOURCE_STORED


def test_evaluate_target_feeds_published_floor_and_unified_text(test_db, monkeypatch):
    """REGRESSION: the holdout predict path now feeds the notice's published 낙찰하한율
    (award_floor_rate, #201) AND the title+description+requirements text
    (build_prediction_text) — the same preprocessing the live path uses. Previously it
    dropped the floor and assembled title+description only (requirements missing, "\\n"
    join), so accuracy was measured on a different input than the bidding pipeline.
    era-correct: award_floor_rate is published at announcement, not future info."""
    project = Project(
        title="항만 준설공사 2단계(규격·가격 동시)",
        description="본문 설명",
        requirements="자격 요건 원문",
        budget_estimate=100_000_000.0,
        category="construction",
        award_floor_rate=0.88,
        notice_number="HOLD-1",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()
    historical = HistoricalData(
        project_id=project.id,
        category="construction",
        base_amount=100_000_000.0,  # clean integer → measured on the stored base
        bid_rate=0.9,
        notice_number="HOLD-1",
    )
    test_db.add(historical)
    test_db.flush()
    result = TenderResult(
        project_id=project.id,
        winning_amount=90_000_000.0,
        winning_rate=0.9,
    )
    test_db.add(result)
    test_db.flush()

    event_at = datetime(2026, 2, 1, tzinfo=UTC)
    target = holdouts.HoldoutTarget(
        group="construction",
        group_source="business_type_code",
        result=result,
        project=project,
        historical=historical,
        event_at=event_at,
        available_at=event_at,
    )

    captured: dict = {}

    def _fake_predict(**kwargs):
        captured.update(kwargs)
        return {
            "predicted_price": 88_000_000.0,
            "predicted_bid_rate": 0.88,
            "bid_rate_candidates": [],
            "procurement_rate_band": None,
        }

    monkeypatch.setattr(holdouts, "predict_price", _fake_predict)

    holdouts.evaluate_target(
        test_db,
        service=PredictionDatasetService(),
        target=target,
        history_limit=10,
        thresholds=(0.003,),
    )

    # The published 하한 now reaches the predictor (max()-only fold downstream).
    assert captured["legal_floor_bid_rate"] == pytest.approx(0.88)
    # The unified assembler carries title + description + requirements (was missing
    # requirements before), matching the live/backtest/smoke predictor input.
    description = captured["description"]
    assert "항만 준설공사 2단계(규격·가격 동시)" in description
    assert "자격 요건 원문" in description


def test_evaluate_target_gates_implausible_floor_from_predictor_but_keeps_it_measured(
    test_db, monkeypatch
):
    """게시 하한 1.00000 은 **predictor 입력에서만** 게이트되고 계측에는 원값이 남는다.

    두 소비가 같은 값을 다르게 써야 한다: 예측 입력은 라이브와 같아야 하므로 게이트를
    타야 하고(1.0 하한이 홀드아웃 정확도 지표를 오염시킨다), 품질 계측은 범위 밖 값을
    버리는 대신 ``published_floor_implausible`` 로 세야 한다(원문 품질 관측).
    """
    project = Project(
        title="하한율 1.0 공고",
        description="본문",
        requirements="요건",
        budget_estimate=100_000_000.0,
        category="construction",
        award_floor_rate=1.0,
        notice_number="HOLD-IMPLAUSIBLE",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()
    historical = HistoricalData(
        project_id=project.id,
        category="construction",
        base_amount=100_000_000.0,
        bid_rate=0.9,
        notice_number="HOLD-IMPLAUSIBLE",
    )
    test_db.add(historical)
    test_db.flush()
    result = TenderResult(
        project_id=project.id, winning_amount=90_000_000.0, winning_rate=0.9
    )
    test_db.add(result)
    test_db.flush()

    event_at = datetime(2026, 2, 1, tzinfo=UTC)
    target = holdouts.HoldoutTarget(
        group="construction",
        group_source="business_type_code",
        result=result,
        project=project,
        historical=historical,
        event_at=event_at,
        available_at=event_at,
    )

    captured: dict = {}

    def _fake_predict(**kwargs):
        captured.update(kwargs)
        return {
            "predicted_price": 88_000_000.0,
            "predicted_bid_rate": 0.88,
            "bid_rate_candidates": [],
            "procurement_rate_band": None,
        }

    monkeypatch.setattr(holdouts, "predict_price", _fake_predict)

    row = holdouts.evaluate_target(
        test_db,
        service=PredictionDatasetService(),
        target=target,
        history_limit=10,
        thresholds=(0.003,),
    )

    # 예측 입력: 게이트 통과 못 함 → 미보고와 같은 자리.
    assert captured["legal_floor_bid_rate"] is None
    # 계측: 원값이 도달해 개연 범위 밖으로 계수된다(계수기가 죽지 않는다).
    assert row["data_quality_details"]["published_floor_implausible"] is True


def test_evaluate_target_no_award_floor_passes_none(test_db, monkeypatch):
    """A holdout target whose notice has no published 하한 passes
    legal_floor_bid_rate=None, so the configured floor is preserved (no spurious clamp)."""
    project = Project(
        title="하한 없는 공고",
        description="본문",
        requirements="요건",
        budget_estimate=100_000_000.0,
        category="construction",
        award_floor_rate=None,
        notice_number="HOLD-2",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()
    historical = HistoricalData(
        project_id=project.id,
        category="construction",
        base_amount=100_000_000.0,
        bid_rate=0.9,
        notice_number="HOLD-2",
    )
    test_db.add(historical)
    test_db.flush()
    result = TenderResult(
        project_id=project.id, winning_amount=90_000_000.0, winning_rate=0.9
    )
    test_db.add(result)
    test_db.flush()

    event_at = datetime(2026, 2, 1, tzinfo=UTC)
    target = holdouts.HoldoutTarget(
        group="construction",
        group_source="business_type_code",
        result=result,
        project=project,
        historical=historical,
        event_at=event_at,
        available_at=event_at,
    )

    captured: dict = {}

    def _fake_predict(**kwargs):
        captured.update(kwargs)
        return {
            "predicted_price": 88_000_000.0,
            "predicted_bid_rate": 0.88,
            "bid_rate_candidates": [],
            "procurement_rate_band": None,
        }

    monkeypatch.setattr(holdouts, "predict_price", _fake_predict)

    holdouts.evaluate_target(
        test_db,
        service=PredictionDatasetService(),
        target=target,
        history_limit=10,
        thresholds=(0.003,),
    )

    assert captured["legal_floor_bid_rate"] is None
