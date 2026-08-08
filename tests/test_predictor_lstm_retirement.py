"""LSTM predictor 은퇴 계약.

운영자 판정(2026-08-09): 예정가는 15개 복수예비가격 중 4개를 추첨·평균해 정해지는
복권형 생성이라 사정률 시계열에 학습할 신호가 없다. 그래서 sequence-model predictor 는
서빙·학습·릴리스 경로에서 은퇴한다.

여기서 고정하는 것은 "지워졌다"가 아니라 **지워진 뒤에도 안전한가**다.

* 레지스트리에 ``lstm`` 키가 없다.
* 선호 설정이 아직 ``lstm`` 을 가리켜도(운영 ``.env`` 는 이 PR 에서 건드리지 않는다)
  historical 로 폴백하고, 그 사유가 "알 수 없는 키"가 아니라 **은퇴**로 남는다.
* 디스크에 남아 있는 과거 ensemble 아티팩트가 ``lstm_artifact`` 를 그대로 들고 있어도
  로딩·추론이 깨지지 않고, 그 축만 무시된다.
* 낙찰하한 red line 은 은퇴 뒤에도 불변이다.
"""

from __future__ import annotations

import importlib
import json

import pytest

from app.ai.price_prediction import predict_price
from app.ai.predictors.registry import (
    build_default_predictor_registry,
    normalize_predictor_registry,
)
from app.core.config import settings


def _bid_rate_history(count: int, *, base_rate: float = 0.912, step: float = 0.001) -> list[dict[str, float]]:
    return [
        {
            "bid_rate": round(base_rate + (index * step), 6),
            "base_amount": 100_000_000.0,
            "predicted_price": round(100_000_000.0 * (base_rate + (index * step)), 2),
        }
        for index in range(count)
    ]


def _write_retired_shape_ensemble_artifact(tmp_path) -> str:
    """과거 학습이 실제로 써 두고 간 모양 — lstm 블록이 그대로 들어 있다."""
    artifact_path = tmp_path / "ensemble_artifact.json"
    artifact_path.write_text(
        json.dumps(
            {
                "artifact_version": "1",
                "model_version": "v2.0-ensemble",
                "sequence_length": 8,
                "momentum_window": 5,
                "scenario_spread_multiplier": 1.05,
                "confidence_bias": 0.02,
                "component_weights": {
                    "historical": 0.5,
                    "momentum": 0.2,
                    "mean_reversion": 0.15,
                    "lstm": 0.15,
                },
                "lstm_artifact_path": "../lstm/price-predictor-20260802T090001Z.json",
                "lstm_artifact": {
                    "artifact_version": "1",
                    "model_version": "v2.0-lstm",
                    "sequence_length": 6,
                    "input_center": 0.9,
                    "input_scale": 0.05,
                    "output_scale": 0.03,
                    "output_bias": 0.9,
                    "blend_weights": {"lstm": 0.72, "historical": 0.18, "trend": 0.10},
                    "weights": {
                        "W_i": [[0.9]], "U_i": [[0.15]], "b_i": [3.0],
                        "W_f": [[0.2]], "U_f": [[0.05]], "b_f": [2.8],
                        "W_o": [[0.4]], "U_o": [[0.1]], "b_o": [2.5],
                        "W_c": [[1.1]], "U_c": [[0.2]], "b_c": [0.0],
                        "dense_W": [0.85], "dense_b": [0.0],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(artifact_path)


def test_default_registry_has_no_retired_lstm_key():
    registry = build_default_predictor_registry()

    assert "lstm" not in registry
    assert set(registry) == {"historical", "ensemble"}


def test_lstm_module_is_gone():
    """모듈 자체가 사라져야 한다 — dead code 로 남기면 다음 사람이 다시 등록한다."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.ai.predictors.lstm")


def test_predictors_package_no_longer_exports_lstm_predictor():
    import app.ai.predictors as predictors

    assert not hasattr(predictors, "LSTMBidRatePredictor")
    assert "LSTMBidRatePredictor" not in predictors.__all__


@pytest.mark.parametrize("preference", ["lstm", "lstm_sequence", "sequence"])
def test_retired_lstm_preference_falls_back_to_historical_with_honest_reason(
    monkeypatch, preference
):
    """``.env`` 가 아직 lstm 을 가리켜도 코드 계약이 historical 을 세운다.

    사유는 "unknown preference"가 아니라 **은퇴**로 남아야 한다. 운영자가 로그에서
    "오타인가 은퇴인가"를 되묻지 않게 하는 것이 이 assert 의 목적이다.
    """
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", preference)

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="retired lstm preference",
        historical_records=_bid_rate_history(10),
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["predictor_family"] == "statistical"
    reason = prediction["fallback_reason"]
    assert reason is not None
    assert "retired" in reason.lower()
    assert "lstm" in reason.lower()
    assert "unknown" not in reason.lower()


def test_injected_registry_may_still_supply_a_custom_key_named_lstm(monkeypatch):
    """은퇴는 기본 레지스트리의 사실이지, 주입 seam 을 막는 하드 게이트가 아니다."""
    from app.ai.predictors.historical import HistoricalStatisticalPredictor

    registry = normalize_predictor_registry({"historical": HistoricalStatisticalPredictor()})

    assert "lstm" not in registry
    assert "ensemble" not in registry


def test_stale_ensemble_artifact_with_embedded_lstm_still_loads_and_ignores_it(
    monkeypatch, tmp_path
):
    """디스크의 과거 산출물은 지우지 않는다 — 읽을 때 조용히 무시되어야 한다."""
    import app.ai.predictors.historical as historical
    from app.ai.predictors.ensemble import load_ensemble_artifact

    artifact_path = _write_retired_shape_ensemble_artifact(tmp_path)
    monkeypatch.setattr(historical, "load_group_calibration", lambda: {})

    artifact = load_ensemble_artifact(artifact_path)

    assert "lstm" not in artifact["component_weights"]
    # 남은 세 축으로 재정규화된다(0.5/0.2/0.15 → /0.85).
    assert artifact["component_weights"] == pytest.approx(
        {"historical": 0.5 / 0.85, "momentum": 0.2 / 0.85, "mean_reversion": 0.15 / 0.85}
    )

    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", artifact_path)

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="stale ensemble artifact inference",
        historical_records=_bid_rate_history(12),
    )

    assert prediction["predictor_name"] == "ensemble_blend"
    assert prediction["fallback_reason"] is None
    assert "lstm" not in prediction["explanation"].lower()


def test_retirement_does_not_move_the_construction_legal_floor(monkeypatch):
    """red line: 은퇴 뒤에도 법정 낙찰하한 미만 추천은 차단된다."""
    from datetime import date

    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "lstm")

    prediction = predict_price(
        budget=500_000_000.0,
        category="construction",
        description="OO 토목공사 법정하한 검증",
        historical_records=[{"bid_rate": 0.80, "base_amount": 100_000_000.0} for _ in range(20)],
        business_group="construction",
        estimation_amount=500_000_000.0,
        reference_date=date(2026, 2, 1),
    )

    floor_bid_rate = prediction["floor_bid_rate"]
    assert floor_bid_rate is not None
    assert prediction["predicted_bid_rate"] >= floor_bid_rate
    for candidate in prediction["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= floor_bid_rate
