"""Golden output-equivalence harness for the price predictors.

CHARACTERIZATION (not correctness): every expected value here is the *current*
implementation's actual output, captured as a frozen golden JSON. The upcoming
refactors (rate_band spec table, keyword rule data-ization, blend-weight table)
must reproduce these byte-for-byte.

The harness feeds fixed synthetic *pure-dict* inputs (no DB, no IO) into
``build_historical_prediction`` and ``build_ensemble_prediction_payload`` and
compares ``json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)``
against ``tests/goldens/predictor/<case>.json``.

Regenerate goldens after an *intended* behavior change:

    PREDICTOR_GOLDEN_REGEN=1 pytest -q tests/test_predictor_output_equivalence.py

``probability_score`` is a price-fit estimate (정직 명세) — nothing here is a
literal win probability.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.ai.predictors.base import PricePredictionContext
from app.ai.predictors.ensemble import (
    build_ensemble_prediction_payload,
    load_ensemble_artifact,
)
from app.ai.predictors.historical import (
    build_heuristic_prediction,
    build_historical_prediction,
)

GOLDEN_DIR = Path(__file__).parent / "goldens" / "predictor"
REGEN = os.environ.get("PREDICTOR_GOLDEN_REGEN") == "1"


# ---------------------------------------------------------------------------
# Hermetic settings: pin every predictor-affecting knob to the current default
# so goldens are reproducible regardless of the host .env, and neutralize the
# manifest-backed group calibration (empty artifact path == empty dict).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _pin_predictor_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PREDICTION_HIGH_RATE_TAIL_ADJUSTMENT_ENABLED", True)
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_BUDGET_MAX", 50_000_000.0)
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_TARGET", 0.93)
    monkeypatch.setattr(settings, "PREDICTION_RESERVE_PRIOR_WEIGHT", 0.2)
    monkeypatch.setattr(settings, "PREDICTION_RESERVE_PRIOR_FULL_CONFIDENCE_SAMPLES", 8)
    # Empty group calibration so build_historical_prediction never blends a manifest prior.
    monkeypatch.setattr(
        "app.ai.predictors.historical.load_group_calibration", lambda: {}, raising=False
    )


# ---------------------------------------------------------------------------
# Non-determinism scrubber. The predictor payloads carry no timestamp / rng
# fields today, so this is an identity on the current outputs; it exists so that
# if a refactor introduces a volatile key the harness stays stable (and the
# scrubber itself is unit-tested below).
# ---------------------------------------------------------------------------
_VOLATILE_KEYS = ("evaluated_at", "generated_at", "timestamp", "created_at", "run_id")


def normalize(payload: Any) -> Any:
    """Recursively drop known-volatile keys so goldens stay deterministic."""
    if isinstance(payload, dict):
        return {
            key: normalize(value)
            for key, value in payload.items()
            if key not in _VOLATILE_KEYS
        }
    if isinstance(payload, list):
        return [normalize(item) for item in payload]
    return payload


def _serialize(payload: Any) -> str:
    return json.dumps(normalize(payload), sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Case builders (pure dict inputs)
# ---------------------------------------------------------------------------
def _summary(
    *,
    sample_size: int,
    mean: float,
    median: float,
    recent_median: float,
    quantile: float,
    std: float,
    recent_sample_size: int = 0,
    recent_upper: float = 0.0,
    upper: float = 0.0,
    ge93: float = 0.0,
    ge95: float = 0.0,
    ge98: float = 0.0,
    agency_match: int = 0,
    reserve_price_context: dict | None = None,
) -> dict[str, Any]:
    return {
        "sample_size": sample_size,
        "mean_bid_rate": mean,
        "median_bid_rate": median,
        "recent_median_bid_rate": recent_median,
        "competitive_quantile_bid_rate": quantile,
        "std_bid_rate": std,
        "recent_sample_size": recent_sample_size,
        "recent_upper_quantile_bid_rate": recent_upper,
        "upper_quantile_bid_rate": upper,
        "recent_rate_ge_0_93_share": ge93,
        "recent_rate_ge_0_95_share": ge95,
        "recent_rate_ge_0_98_share": ge98,
        "agency_match_sample_size": agency_match,
        "reserve_price_context": reserve_price_context,
    }


def _reserve_ctx(*, sample_count: int, implied: float = 0.9, yega: float = 0.95) -> dict[str, Any]:
    return {
        "sample_count": sample_count,
        "estimated_price_sample_count": sample_count,
        "average_reserve_span_rate": 0.03,
        "average_estimated_price_rate": yega,
        "median_estimated_price_rate": yega,
        "median_bid_to_estimated_price_rate": implied,
        "average_selected_number": 7.5,
        "frequent_selected_numbers": [1, 4, 7, 12],
    }


def _historical_case(
    case_id: str,
    *,
    budget: float,
    category: str,
    description: str,
    business_group: str | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "historical",
        "budget": budget,
        "category": category,
        "description": description,
        "business_group": business_group,
        "summary": summary,
    }


# group(3: construction/service/None) x category-rep x sample tier(deep/mid/thin/cold)
# x band-rep(none / service_price_competitive / service_high_negotiated / goods_*)
HISTORICAL_CASES: list[dict[str, Any]] = [
    # --- construction group ---
    _historical_case(
        "hist_construction_deep_none", budget=800_000_000.0, category="construction",
        description="OO 토목공사", business_group="construction",
        summary=_summary(sample_size=25, mean=0.905, median=0.903, recent_median=0.906,
                         quantile=0.9, std=0.012, recent_sample_size=12, recent_upper=0.912,
                         upper=0.911, agency_match=3),
    ),
    _historical_case(
        "hist_construction_deep_hightail", budget=40_250_000.0, category="construction",
        description="원상복구 및 사무실 조성공사", business_group="construction",
        summary=_summary(sample_size=40, mean=0.925, median=0.926, recent_median=0.9028,
                         quantile=0.905, std=0.01, recent_sample_size=20, recent_upper=0.9051,
                         upper=0.905),
    ),
    _historical_case(
        "hist_construction_mid_none", budget=500_000_000.0, category="construction",
        description="OO 건축공사", business_group="construction",
        summary=_summary(sample_size=7, mean=0.9, median=0.902, recent_median=0.903,
                         quantile=0.9, std=0.02),
    ),
    _historical_case(
        "hist_construction_thin_none", budget=300_000_000.0, category="construction",
        description="OO 토목공사", business_group="construction",
        summary=_summary(sample_size=3, mean=0.88, median=0.885, recent_median=0.89,
                         quantile=0.88, std=0.03),
    ),
    _historical_case(
        "hist_construction_cold_none", budget=120_000_000.0, category="construction",
        description="OO 공사", business_group="construction",
        summary=_summary(sample_size=1, mean=0.9, median=0.9, recent_median=0.9,
                         quantile=0.9, std=0.0),
    ),
    # --- service group ---
    _historical_case(
        "hist_service_deep_none", budget=100_000_000.0, category="service",
        description="OO 청소용역", business_group="service",
        summary=_summary(sample_size=20, mean=0.9, median=0.883, recent_median=0.88,
                         quantile=0.83, std=0.04, recent_sample_size=12, recent_upper=0.9,
                         upper=0.9, agency_match=2),
    ),
    _historical_case(
        "hist_service_deep_price_competitive", budget=100_000_000.0, category="service",
        description="폐기물 처리용역", business_group="service",
        summary=_summary(sample_size=20, mean=0.95, median=0.95, recent_median=0.95,
                         quantile=0.95, std=0.01, recent_sample_size=10, recent_upper=0.96,
                         upper=0.96),
    ),
    _historical_case(
        "hist_service_deep_high_negotiated", budget=100_000_000.0, category="service",
        description="협상에 의한 계약 용역", business_group="service",
        summary=_summary(sample_size=20, mean=0.9, median=0.9, recent_median=0.9,
                         quantile=0.9, std=0.02, recent_sample_size=10, recent_upper=0.92,
                         upper=0.92),
    ),
    _historical_case(
        "hist_service_mid_none", budget=80_000_000.0, category="service",
        description="OO 청소용역", business_group="service",
        summary=_summary(sample_size=6, mean=0.9, median=0.9, recent_median=0.9,
                         quantile=0.88, std=0.03),
    ),
    _historical_case(
        "hist_service_thin_price_competitive", budget=60_000_000.0, category="service",
        description="감리 용역", business_group="service",
        summary=_summary(sample_size=2, mean=0.92, median=0.92, recent_median=0.92,
                         quantile=0.92, std=0.02),
    ),
    _historical_case(
        "hist_service_cold_high_negotiated", budget=50_000_000.0, category="service",
        description="시스템 개발 용역", business_group="service",
        summary=_summary(sample_size=1, mean=0.85, median=0.85, recent_median=0.85,
                         quantile=0.85, std=0.0),
    ),
    _historical_case(
        "hist_service_deep_reserve_prior", budget=100_000_000.0, category="service",
        description="OO 청소용역", business_group="service",
        summary=_summary(sample_size=20, mean=0.9, median=0.9, recent_median=0.9,
                         quantile=0.9, std=0.02, recent_sample_size=10,
                         reserve_price_context=_reserve_ctx(sample_count=12, implied=0.88, yega=0.95)),
    ),
    # --- no group (category-keyed path) ---
    _historical_case(
        "hist_nogroup_service_deep_none", budget=100_000_000.0, category="service",
        description="OO 청소용역", business_group=None,
        summary=_summary(sample_size=15, mean=0.9, median=0.903, recent_median=0.905,
                         quantile=0.9, std=0.02),
    ),
    _historical_case(
        "hist_nogroup_construction_deep_none", budget=400_000_000.0, category="construction",
        description="OO 토목공사", business_group=None,
        summary=_summary(sample_size=15, mean=0.9, median=0.903, recent_median=0.905,
                         quantile=0.9, std=0.015),
    ),
    _historical_case(
        "hist_nogroup_goods_deep_price_competitive", budget=100_000_000.0, category="goods",
        description="OO 견적제출 구매", business_group=None,
        summary=_summary(sample_size=15, mean=0.86, median=0.86, recent_median=0.86,
                         quantile=0.86, std=0.02),
    ),
    _historical_case(
        "hist_nogroup_goods_deep_deep_discount", budget=100_000_000.0, category="goods",
        description="2단계 급식 농산물 구매", business_group="goods",
        summary=_summary(sample_size=22, mean=0.8, median=0.8, recent_median=0.8,
                         quantile=0.8, std=0.02, recent_sample_size=10),
    ),
    _historical_case(
        "hist_nogroup_service_thin_none", budget=70_000_000.0, category="service",
        description="OO 청소용역", business_group=None,
        summary=_summary(sample_size=3, mean=0.9, median=0.9, recent_median=0.9,
                         quantile=0.9, std=0.03),
    ),
    _historical_case(
        "hist_nogroup_service_cold_none", budget=45_000_000.0, category="service",
        description="OO 청소용역", business_group=None,
        summary=_summary(sample_size=1, mean=0.88, median=0.88, recent_median=0.88,
                         quantile=0.88, std=0.0),
    ),
]


def _rate_records(rates: list[float], *, base_amount: float = 100_000_000.0) -> list[dict[str, Any]]:
    return [{"bid_rate": rate, "base_amount": base_amount} for rate in rates]


_LSTM_ARTIFACT = {
    "artifact_version": "1",
    "model_version": "v2.0-lstm",
    "sequence_length": 6,
    "input_center": 0.9,
    "input_scale": 0.05,
    "output_scale": 0.03,
    "output_bias": 0.9,
    "scenario_spread_multiplier": 1.1,
    "confidence_bias": 0.03,
    "blend_weights": {"lstm": 0.72, "historical": 0.18, "trend": 0.10},
    "weights": {
        "W_i": [[0.9]], "U_i": [[0.15]], "b_i": [3.0],
        "W_f": [[0.2]], "U_f": [[0.05]], "b_f": [2.8],
        "W_o": [[0.4]], "U_o": [[0.1]], "b_o": [2.5],
        "W_c": [[1.1]], "U_c": [[0.2]], "b_c": [0.0],
        "dense_W": [0.85], "dense_b": [0.0],
    },
}


def _ensemble_case(
    case_id: str,
    *,
    budget: float,
    category: str,
    description: str,
    business_group: str | None,
    rates: list[float],
    artifact: dict[str, Any],
    agency_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "ensemble",
        "budget": budget,
        "category": category,
        "description": description,
        "business_group": business_group,
        "records": _rate_records(rates),
        "artifact": artifact,
        "agency_name": agency_name,
    }


_BASE_ENSEMBLE_ARTIFACT = {
    "artifact_version": "1",
    "model_version": "v2.0-ensemble",
    "sequence_length": 8,
    "momentum_window": 5,
    "scenario_spread_multiplier": 1.05,
    "confidence_bias": 0.02,
    "component_weights": {"historical": 0.5, "momentum": 0.2, "mean_reversion": 0.15, "lstm": 0.15},
}

_ENSEMBLE_ARTIFACT_WITH_LSTM = {**_BASE_ENSEMBLE_ARTIFACT, "lstm_artifact": _LSTM_ARTIFACT}

_ASCENDING = [round(0.90 + i * 0.001, 6) for i in range(16)]
_DESCENDING = [round(0.96 - i * 0.001, 6) for i in range(16)]
_FLAT = [0.9 for _ in range(16)]
_HIGH = [round(0.95 + (i % 3) * 0.005, 6) for i in range(24)]

ENSEMBLE_CASES: list[dict[str, Any]] = [
    _ensemble_case("ens_construction_ascending_none", budget=800_000_000.0, category="construction",
                   description="OO 토목공사", business_group="construction", rates=_ASCENDING,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_construction_descending_none", budget=500_000_000.0, category="construction",
                   description="OO 건축공사", business_group="construction", rates=_DESCENDING,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_service_flat_none", budget=100_000_000.0, category="service",
                   description="OO 청소용역", business_group="service", rates=_FLAT,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_service_ascending_price_competitive", budget=100_000_000.0, category="service",
                   description="폐기물 처리용역", business_group="service", rates=_ASCENDING,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_service_flat_high_negotiated", budget=100_000_000.0, category="service",
                   description="협상에 의한 계약 용역", business_group="service", rates=_FLAT,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_nogroup_service_ascending_none", budget=100_000_000.0, category="service",
                   description="OO 청소용역", business_group=None, rates=_ASCENDING,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_nogroup_construction_flat_none", budget=400_000_000.0, category="construction",
                   description="OO 토목공사", business_group=None, rates=_FLAT,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_goods_ascending_price_competitive", budget=100_000_000.0, category="goods",
                   description="OO 견적제출 구매", business_group="goods", rates=_ASCENDING,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_construction_high_tail", budget=40_250_000.0, category="construction",
                   description="원상복구 및 사무실 조성공사", business_group="construction", rates=_HIGH,
                   artifact=_BASE_ENSEMBLE_ARTIFACT),
    _ensemble_case("ens_service_agency_match", budget=100_000_000.0, category="service",
                   description="OO 청소용역", business_group="service", rates=_FLAT,
                   artifact=_BASE_ENSEMBLE_ARTIFACT, agency_name="한국수산자원공단"),
    # LSTM component exercised via an embedded (pure-python, file-free) artifact.
    _ensemble_case("ens_construction_ascending_with_lstm", budget=800_000_000.0, category="construction",
                   description="OO 토목공사", business_group="construction", rates=_ASCENDING,
                   artifact=_ENSEMBLE_ARTIFACT_WITH_LSTM),
    _ensemble_case("ens_service_flat_with_lstm", budget=100_000_000.0, category="service",
                   description="OO 청소용역", business_group="service", rates=_FLAT,
                   artifact=_ENSEMBLE_ARTIFACT_WITH_LSTM),
]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------
def _run_historical(case: dict[str, Any]) -> dict[str, Any]:
    heuristic = build_heuristic_prediction(
        budget=case["budget"], category=case["category"], description=case["description"]
    )
    return build_historical_prediction(
        budget=case["budget"],
        category=case["category"],
        description=case["description"],
        heuristic_prediction=heuristic,
        historical_summary=case["summary"],
        business_group=case["business_group"],
    )


def _run_ensemble(case: dict[str, Any]) -> dict[str, Any]:
    artifact = load_ensemble_artifact(case["artifact"])
    context = PricePredictionContext(
        budget=case["budget"],
        category=case["category"],
        description=case["description"],
        historical_records=tuple(case["records"]),
        agency_name=case.get("agency_name"),
        business_group=case["business_group"],
    )
    return build_ensemble_prediction_payload(context, artifact=artifact)


_RUNNERS = {"historical": _run_historical, "ensemble": _run_ensemble}


def _run(case: dict[str, Any]) -> dict[str, Any]:
    return _RUNNERS[case["kind"]](case)


def _golden_path(case_id: str) -> Path:
    return GOLDEN_DIR / f"{case_id}.json"


def _assert_golden(case: dict[str, Any]) -> None:
    result = _run(case)
    serialized = _serialize(result)
    path = _golden_path(case["id"])
    if REGEN or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Store a pretty golden but compare on the canonical serialization.
        path.write_text(
            json.dumps(normalize(result), sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert serialized == json.dumps(golden, sort_keys=True, ensure_ascii=False, default=str), (
        f"golden drift for {case['id']}"
    )


ALL_CASES = HISTORICAL_CASES + ENSEMBLE_CASES


@pytest.mark.parametrize("case", ALL_CASES, ids=[c["id"] for c in ALL_CASES])
def test_predictor_output_matches_golden(case):
    _assert_golden(case)


@pytest.mark.parametrize("case", ALL_CASES, ids=[c["id"] for c in ALL_CASES])
def test_predictor_output_is_deterministic(case):
    """Byte-identical across two runs — the precondition for a stable golden."""
    first = _serialize(_run(case))
    second = _serialize(_run(case))
    assert first == second, f"non-deterministic predictor output for {case['id']}"


def test_case_ids_are_unique():
    ids = [c["id"] for c in ALL_CASES]
    assert len(ids) == len(set(ids))


def test_golden_case_count_is_pinned():
    # Lock the coverage size so a future edit can't silently drop cases.
    assert len(HISTORICAL_CASES) == 18
    assert len(ENSEMBLE_CASES) == 12
    assert len(ALL_CASES) == 30


# ---------------------------------------------------------------------------
# normalize() scrubber unit tests
# ---------------------------------------------------------------------------
def test_normalize_drops_volatile_keys_recursively():
    payload = {
        "predicted_bid_rate": 0.9,
        "evaluated_at": "2026-07-16T00:00:00Z",
        "bid_rate_candidates": [
            {"label": "base", "bid_rate": 0.9, "generated_at": "now"},
        ],
        "nested": {"timestamp": 1, "keep": 2},
    }
    assert normalize(payload) == {
        "predicted_bid_rate": 0.9,
        "bid_rate_candidates": [{"label": "base", "bid_rate": 0.9}],
        "nested": {"keep": 2},
    }


def test_normalize_is_identity_on_current_predictor_payload():
    """Current predictor payloads carry no volatile keys — scrubber is a no-op."""
    result = _run(HISTORICAL_CASES[0])
    assert normalize(result) == result


def test_current_outputs_have_no_volatile_keys():
    for case in ALL_CASES:
        result = _run(case)
        assert not (set(_VOLATILE_KEYS) & set(result)), case["id"]
