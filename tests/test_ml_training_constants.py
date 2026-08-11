"""Characterization tests for training-time artifact hyperparameters.

These lock the *observable effect* of the numeric constants baked into predictor
artifacts (ensemble) and the dataset-quality gates, so a follow-up refactor
that lifts those inline literals into named module constants is provably
behavior-preserving. Every assertion pins a produced output value or a gate
passed/threshold at a boundary — not the literal source text — so it survives a
verbatim declaration change but fails if the value itself drifts.

Pure dict / list inputs only — no torch, no model files, no DB.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.ml_training import PricePredictionTrainingService


def _service() -> PricePredictionTrainingService:
    return PricePredictionTrainingService(repo_root="/tmp/ml-training-constants-char")


def _ensemble(bid_rates: list[float]) -> dict:
    return _service()._build_ensemble_artifact(release_tag="rel", bid_rates=bid_rates)


def _gate(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


# ---------------------------------------------------------------------------
# Ensemble artifact hyperparameters
# ---------------------------------------------------------------------------


def test_ensemble_scenario_spread_flips_at_std_threshold_004():
    """std < 0.04 -> ×1.0 (narrow); std >= 0.04 -> ×1.15 (wide)."""
    # pstdev([0.89,0.90,0.91]) ~= 0.0082 (< 0.04)
    assert _ensemble([0.89, 0.90, 0.91])["scenario_spread_multiplier"] == 1.0
    # pstdev([0.85,0.90,0.95]) ~= 0.0408 (>= 0.04)
    assert _ensemble([0.85, 0.90, 0.95])["scenario_spread_multiplier"] == 1.15


def test_ensemble_confidence_bias_uses_1200_sample_divisor_below_cap():
    assert _ensemble([0.9] * 5)["confidence_bias"] == 5 / 1200


def test_ensemble_confidence_bias_caps_at_006():
    assert _ensemble([0.9] * 100)["confidence_bias"] == 0.06


def test_ensemble_component_weights_are_fixed():
    """sequence-model 축 은퇴 후 남은 세 축의 확률 벡터(상대 비율은 은퇴 전과 같다)."""
    assert _ensemble([0.9] * 5)["component_weights"] == {
        "historical": 0.5 / 0.85,
        "momentum": 0.2 / 0.85,
        "mean_reversion": 0.15 / 0.85,
    }


def test_written_component_weights_are_a_probability_vector():
    """아티팩트에 기록되는 가중치는 합 1 이어야 한다.

    은퇴로 축 하나를 빼면서 비율(합 0.85)을 그대로 적으면, 아티팩트를 손으로 읽는
    운영자에게 "나머지 15%가 어디론가 사라진" 표로 보인다. 읽는 쪽이 재정규화해서
    산출은 같더라도 **기록되는 선언값**은 확률 벡터여야 한다.
    """
    weights = _ensemble([0.9] * 5)["component_weights"]

    assert sum(weights.values()) == pytest.approx(1.0)
    assert "lstm" not in weights


def test_component_weight_ratios_are_unchanged_by_the_retirement():
    """상대 비율 보존 — 재선언이 축들의 상대 세기를 바꾸지 않았다는 증거."""
    weights = _ensemble([0.9] * 5)["component_weights"]

    assert weights["historical"] / weights["momentum"] == pytest.approx(0.5 / 0.2)
    assert weights["momentum"] / weights["mean_reversion"] == pytest.approx(0.2 / 0.15)


def test_declared_weights_are_idempotent_under_renormalization():
    """산출 불변의 근거: 소비자가 한 번 더 정규화해도 값이 움직이지 않는다."""
    from app.ai.predictors.ensemble import _normalize_component_weights

    weights = _ensemble([0.9] * 5)["component_weights"]

    assert _normalize_component_weights(weights) == weights


def test_training_no_longer_emits_a_retired_lstm_artifact():
    """수동 훈련이 은퇴한 모델을 다시 만들어 두지 않는다(스케줄이 꺼져 있어도)."""
    artifact = _ensemble([0.9] * 5)

    assert "lstm_artifact" not in artifact
    assert "lstm_artifact_path" not in artifact
    assert not hasattr(_service(), "_build_lstm_artifact")


# ---------------------------------------------------------------------------
# Sequence-length / momentum-window bounds (shared across artifacts)
# ---------------------------------------------------------------------------


def test_sequence_length_bounds_min3_max12():
    assert _ensemble([0.9, 0.9])["sequence_length"] == 3  # floor
    assert _ensemble([0.9] * 5)["sequence_length"] == 5  # interior == len
    assert _ensemble([0.9] * 100)["sequence_length"] == 12  # cap


def test_momentum_window_bounds_min3_max6():
    assert _ensemble([0.9, 0.9])["momentum_window"] == 3  # floor
    assert _ensemble([0.9] * 5)["momentum_window"] == 5  # interior == len
    assert _ensemble([0.9] * 100)["momentum_window"] == 6  # cap


# ---------------------------------------------------------------------------
# Dataset-quality gate thresholds & scoring weights
# ---------------------------------------------------------------------------


def _quality_report(
    monkeypatch: pytest.MonkeyPatch,
    *,
    summary: dict,
    bid_rates: list[float],
    agency_name: str | None = None,
) -> dict:
    # Pin the settings-derived sample_depth requirement to 6 (3 + 3) so the
    # blocking gate is deterministic and independent of environment config.
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 3)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 3)
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT", 3)
    return _service()._build_dataset_quality_report(
        release_tag="t",
        category=None,
        agency_name=agency_name,
        limit=20,
        bid_rates=bid_rates,
        dataset={"summary": summary},
    )


def test_coverage_gates_threshold_and_flip_at_025(monkeypatch):
    """linked_result & reserve_pattern coverage gates pass at exactly 0.25, fail below."""
    passing = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 8,
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 2,  # 2/8 == 0.25
            "reserve_pattern_sample_count": 2,  # 2/8 == 0.25
        },
        bid_rates=[0.85, 0.95],  # std 0.05 <= 0.08
    )
    assert _gate(passing, "linked_result_coverage")["threshold"] == 0.25
    assert _gate(passing, "linked_result_coverage")["passed"] is True
    assert _gate(passing, "reserve_pattern_coverage")["threshold"] == 0.25
    assert _gate(passing, "reserve_pattern_coverage")["passed"] is True

    failing = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 8,
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 1,  # 1/8 == 0.125 < 0.25
            "reserve_pattern_sample_count": 1,
        },
        bid_rates=[0.85, 0.95],
    )
    assert _gate(failing, "linked_result_coverage")["passed"] is False
    assert _gate(failing, "reserve_pattern_coverage")["passed"] is False


def test_bid_rate_variance_gate_threshold_and_flip_at_008(monkeypatch):
    """bid_rate_variance passes when std <= 0.08, fails when clearly above."""
    summary = {
        "sample_count": 8,
        "project_count": 3,
        "agency_count": 2,
        "linked_result_count": 8,
        "reserve_pattern_sample_count": 8,
    }
    ok = _quality_report(monkeypatch, summary=summary, bid_rates=[0.85, 0.95])  # std 0.05
    assert _gate(ok, "bid_rate_variance")["threshold"] == 0.08
    assert _gate(ok, "bid_rate_variance")["passed"] is True

    high = _quality_report(monkeypatch, summary=summary, bid_rates=[0.80, 1.00])  # std 0.10
    assert _gate(high, "bid_rate_variance")["passed"] is False


def test_diversity_gate_targets_are_3_and_2(monkeypatch):
    """project_diversity targets 3, agency_diversity targets 2 (capped by sample_count)."""
    report = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 8,
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 8,
            "reserve_pattern_sample_count": 8,
        },
        bid_rates=[0.9, 0.9],
    )
    assert _gate(report, "project_diversity")["threshold"] == 3
    assert _gate(report, "agency_diversity")["threshold"] == 2


def test_quality_score_weights_base100_block40_warn10(monkeypatch):
    """score = 100 - 40*blocking - 10*warning."""
    clean = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 6,  # meets required 6 -> sample_depth passes
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 6,
            "reserve_pattern_sample_count": 6,
        },
        bid_rates=[0.9, 0.9],  # std 0 -> variance passes
    )
    assert clean["blocking_issue_count"] == 0
    assert clean["warning_count"] == 0
    assert clean["score"] == 100  # base

    one_blocking = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 5,  # below required 6 -> sample_depth (blocking) fails
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 5,
            "reserve_pattern_sample_count": 5,
        },
        bid_rates=[0.9, 0.9],
    )
    assert one_blocking["blocking_issue_count"] == 1
    assert one_blocking["warning_count"] == 0
    assert one_blocking["score"] == 60  # 100 - 40

    three_warnings = _quality_report(
        monkeypatch,
        summary={
            "sample_count": 8,
            "project_count": 3,
            "agency_count": 2,
            "linked_result_count": 1,  # coverage warning
            "reserve_pattern_sample_count": 1,  # coverage warning
        },
        bid_rates=[0.80, 1.00],  # variance warning (std 0.10)
    )
    assert three_warnings["blocking_issue_count"] == 0
    assert three_warnings["warning_count"] == 3
    assert three_warnings["score"] == 70  # 100 - 3*10
