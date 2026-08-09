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
from datetime import date
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
from app.ai.price_prediction import predict_price

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
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_MIN_RATE", 0.925)
    monkeypatch.setattr(settings, "PREDICTION_RESERVE_PRIOR_WEIGHT", 0.2)
    monkeypatch.setattr(settings, "PREDICTION_RESERVE_PRIOR_FULL_CONFIDENCE_SAMPLES", 8)
    # predict_price guardrail / granularity knobs (GAP A/C goldens depend on these).
    monkeypatch.setattr(settings, "PREDICTION_FLOOR_SAFETY_MARGIN_RATE", 0.001)
    monkeypatch.setattr(settings, "PREDICTION_BID_PRICE_GRANULARITY", 10)
    monkeypatch.setattr(settings, "PREDICTION_BID_PRICE_GRANULARITY_MIN_BUDGET", 1_000_000.0)
    # Agency band + E[사정률] conversion pinned so the base-alignment golden is
    # reproducible regardless of the host .env (base 정합화 P0).
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MINIMUM_BID_RATES", {"한국수산자원공단": 0.8806})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MAXIMUM_BID_RATES", {"한국수산자원공단": 0.882})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {"한국수산자원공단": 0.9952})
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
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
    # --- GAP C: numeric high-rate tail ACTUALLY fires and flows into the
    #     candidates / predicted_price / explanation / high_rate_tail_adjustment.
    #     (Synthetic summaries: low base-driving rates but a high recent tail — the
    #     dict fields are independent, which is exactly what exercises the branch.)
    _historical_case(
        "hist_goods_tail_fires", budget=100_000_000.0, category="goods",
        description="OO 물품 구매", business_group="goods",
        summary=_summary(sample_size=25, mean=0.85, median=0.85, recent_median=0.96,
                         quantile=0.85, std=0.02, recent_sample_size=12, recent_upper=0.98,
                         upper=0.98, ge95=0.40, ge98=0.25),
    ),
    _historical_case(
        "hist_service_tail_fires", budget=100_000_000.0, category="service",
        description="OO 청소용역", business_group="service",
        summary=_summary(sample_size=25, mean=0.9, median=0.9, recent_median=0.94,
                         quantile=0.9, std=0.02, recent_sample_size=12, recent_upper=0.95,
                         upper=0.95, ge93=0.40),
    ),
]


def _rate_records(rates: list[float], *, base_amount: float = 100_000_000.0) -> list[dict[str, Any]]:
    return [{"bid_rate": rate, "base_amount": base_amount} for rate in rates]


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
    "component_weights": {"historical": 0.5, "momentum": 0.2, "mean_reversion": 0.15},
}


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
]


# GAP D (P4.1): the two predictor payload shapes that had NO golden at all.
#
# ``build_heuristic_prediction`` emits a
# *narrower* key set than historical/ensemble — they carry no
# ``competitive_target_bid_rate`` / ``procurement_rate_band`` /
# ``high_rate_tail_adjustment``. That difference is precisely what the
# PredictionResult DTO must preserve (key PRESENCE, not just values), so both
# shapes are pinned before the typed-output refactor.
def _heuristic_case(
    case_id: str, *, budget: float, category: str, description: str
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "heuristic",
        "budget": budget,
        "category": category,
        "description": description,
    }


HEURISTIC_CASES: list[dict[str, Any]] = [
    _heuristic_case(
        "heur_service_short_description", budget=100_000_000.0, category="service",
        description="OO 청소용역",
    ),
    # >200 chars flips confidence_score to 0.9 and pushes the complexity factor up.
    _heuristic_case(
        "heur_software_long_description", budget=500_000_000.0, category="software",
        description="시스템 구축 " * 40,
    ),
    # budget == 0 → every rate/price collapses to 0.0 (division guard path).
    _heuristic_case(
        "heur_zero_budget", budget=0.0, category="other", description="예산 미상 공고",
    ),
]


# GAP A: end-to-end predict_price cases where the category/group guardrail
# ACTUALLY fires (guardrail_applied == True) — the anchor for the project #1
# invariant ("a recommendation below the category 낙찰하한 is blocked"). The
# guardrail is NOT disabled or bypassed; the inputs are built so the pre-guardrail
# rate lands outside the band and the guardrail must clamp it back.
def _predict_price_case(
    case_id: str,
    *,
    budget: float,
    category: str,
    description: str,
    rates: list[float],
    business_group: str | None = None,
    agency_name: str | None = None,
    legal_floor_bid_rate: float | None = None,
    estimation_amount: float | None = None,
    reference_date: date | None = None,
    feedback_calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "predict_price",
        "budget": budget,
        "category": category,
        "description": description,
        "records": _rate_records(rates),
        "business_group": business_group,
        "agency_name": agency_name,
        "legal_floor_bid_rate": legal_floor_bid_rate,
        "estimation_amount": estimation_amount,
        "reference_date": reference_date,
        "feedback_calibration": feedback_calibration,
    }


PREDICT_PRICE_CASES: list[dict[str, Any]] = [
    # Floor raise: deep low-rate construction history (~0.75) sits below the 0.87
    # category floor, so the guardrail lifts every scenario to the safe floor.
    _predict_price_case(
        "predict_guardrail_floor_raise_construction",
        budget=100_000_000.0, category="construction",
        description="낙찰하한 검증 토목공사", rates=[0.75 for _ in range(20)],
        business_group=None,
    ),
    # Ceiling lower: high-rate construction history (~0.985) with the construction
    # group ceiling 0.93 — the guardrail clamps the recommendation down to 0.93.
    _predict_price_case(
        "predict_guardrail_ceiling_lower_construction",
        budget=810_000_000.0, category="construction",
        description="제주대학교 안전환경개선 건축공사", rates=[0.985 for _ in range(60)],
        business_group="construction", agency_name="제주대학교",
    ),
    # base 정합화(P0): the 한국수산자원공단 예정가-basis band [0.8806, 0.882] is
    # converted to a 기초금액 basis via E[사정률]=0.9952 BEFORE it clamps the
    # 사업금액-based bid. Locks that the effective band edges land ~0.44%p BELOW the
    # raw band (floor 0.8806→0.876, ceiling 0.882→0.877), which is the fix for the
    # 52위/25위 postmortem. The service history (~0.905) prices above the converted
    # ceiling, so the ceiling binds and the recommendation resolves deterministically
    # inside the aligned band. budget is the real 사업금액 from the postmortem notice.
    _predict_price_case(
        "predict_agency_band_assessment_marine",
        budget=88_042_000.0, category="service",
        description="어초어장 조성 관리 용역", rates=[0.905 for _ in range(20)],
        business_group="service", agency_name="한국수산자원공단",
    ),
    # 공사 법정 낙찰하한 tier (2026-01-30 +2%p): a 5억 (<10억) construction notice
    # dated after the revision resolves the 신율 tier 0.89745 (예정가 basis × default
    # E[사정률] 1.0 == no-op). The deep low-rate history (~0.80) prices below it, so
    # the tier floor lifts every scenario to 0.89745 — the fix for flat 0.87 undershoot.
    _predict_price_case(
        "predict_construction_legal_floor_tier_new",
        budget=500_000_000.0, category="construction",
        description="OO 토목공사 법정하한 검증", rates=[0.80 for _ in range(20)],
        business_group="construction",
        estimation_amount=500_000_000.0, reference_date=date(2026, 2, 1),
    ),
    # GAP D (P4.1): the heuristic payload flowing through the FULL predict_price
    # pipeline (feedback → guardrail → granularity → regime → predictor metadata).
    # No historical records, so ``HistoricalStatisticalPredictor.predict`` returns the
    # narrow heuristic payload — this is the only golden that pins which keys are
    # ABSENT end-to-end (no competitive_target_bid_rate / procurement_rate_band /
    # high_rate_tail_adjustment). Presence, not just value, is the DTO contract.
    _predict_price_case(
        "predict_heuristic_no_history",
        budget=100_000_000.0, category="service",
        description="이력 없는 신규 용역 공고", rates=[],
        business_group="service",
    ),
    # GAP D (P4.1): the feedback-calibration stage actually applies (non-trivial
    # adjustment rate), so ``feedback_calibration`` is a populated dict and
    # ``model_version`` carries the +feedback suffix through the guardrail suffix fold.
    _predict_price_case(
        "predict_feedback_calibration_applied",
        budget=100_000_000.0, category="service",
        description="OO 청소용역", rates=[0.905 for _ in range(20)],
        business_group="service",
        feedback_calibration={
            "sample_count": 12,
            "applied_adjustment_rate": -0.015,
            "raw_adjustment_rate": -0.02,
        },
    ),
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


def _run_heuristic(case: dict[str, Any]) -> dict[str, Any]:
    return build_heuristic_prediction(
        budget=case["budget"], category=case["category"], description=case["description"]
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


def _run_predict_price(case: dict[str, Any]) -> dict[str, Any]:
    result = predict_price(
        budget=case["budget"],
        category=case["category"],
        description=case["description"],
        historical_records=tuple(case["records"]),
        agency_name=case.get("agency_name"),
        business_group=case.get("business_group"),
        legal_floor_bid_rate=case.get("legal_floor_bid_rate"),
        estimation_amount=case.get("estimation_amount"),
        reference_date=case.get("reference_date"),
        feedback_calibration=case.get("feedback_calibration"),
    )
    # backtest_report (auto selector only) is a large nested structure not present
    # under the historical preference; drop defensively so a config flip can't bloat
    # the golden. It is absent in the test env (preferred predictor == historical).
    result.pop("backtest_report", None)
    return result


_RUNNERS = {
    "historical": _run_historical,
    "heuristic": _run_heuristic,
    "ensemble": _run_ensemble,
    "predict_price": _run_predict_price,
}


def _run(case: dict[str, Any]) -> dict[str, Any]:
    return _RUNNERS[case["kind"]](case)


def _golden_path(case_id: str) -> Path:
    return GOLDEN_DIR / f"{case_id}.json"


def _assert_golden(case: dict[str, Any]) -> None:
    result = _run(case)
    serialized = _serialize(result)
    path = _golden_path(case["id"])
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Store a pretty golden but compare on the canonical serialization.
        path.write_text(
            json.dumps(normalize(result), sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif not path.exists():
        # A missing golden without an explicit regen is a HARD failure — never
        # auto-create it, so an accidental deletion can't be masked as a pass.
        raise AssertionError(
            f"missing golden for {case['id']} at {path}; "
            f"run PREDICTOR_GOLDEN_REGEN=1 pytest to (re)generate intentionally."
        )
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert serialized == json.dumps(golden, sort_keys=True, ensure_ascii=False, default=str), (
        f"golden drift for {case['id']}"
    )


ALL_CASES = (
    HISTORICAL_CASES + HEURISTIC_CASES + ENSEMBLE_CASES + PREDICT_PRICE_CASES
)


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
    assert len(HISTORICAL_CASES) == 20
    # -2: the two ens_*_with_lstm cases retired with the sequence-model axis
    # (2026-08-09); the remaining 10 are byte-identical to their pre-retirement goldens.
    assert len(ENSEMBLE_CASES) == 10
    # GAP D (P4.1): the heuristic fallback payload SHAPE, which emits a narrower key
    # set than historical/ensemble — the regression anchor for the PredictionResult
    # key-presence contract.
    assert len(HEURISTIC_CASES) == 3
    # +1: predict_agency_band_assessment_marine locks the E[사정률] base-alignment path
    # (base 정합화 P0) — the first golden exercising the agency band THROUGH the guardrail.
    # +1: predict_construction_legal_floor_tier_new locks the 2026-01-30 construction
    # legal 낙찰하한 tier (구간·시행일 인지) raising the floor above the flat 0.87.
    # +2 (P4.1): predict_heuristic_no_history pins the narrow heuristic payload through
    # the FULL pipeline, predict_feedback_calibration_applied pins the feedback stage.
    assert len(PREDICT_PRICE_CASES) == 6
    # 44 -> 39: sequence-model 은퇴로 lstm_* 골든 3건 + ens_*_with_lstm 2건이 사라졌다.
    assert len(ALL_CASES) == 39


# ---------------------------------------------------------------------------
# GAP A anchor: guardrail actually fires (project #1 invariant, made legible
# beyond the raw golden JSON so a careless regen can't quietly erase it).
# ---------------------------------------------------------------------------
def _case_by_id(case_id: str) -> dict[str, Any]:
    return next(case for case in ALL_CASES if case["id"] == case_id)


def test_guardrail_floor_raise_actually_fires():
    result = _run(_case_by_id("predict_guardrail_floor_raise_construction"))
    assert result["guardrail_applied"] is True
    assert result["guardrail_reason"]  # non-empty auditable reason
    assert result["floor_bid_rate"] == 0.87
    floor = result["safe_floor_bid_rate"]
    # every scenario is lifted to at least the safe floor — nothing prices below 낙찰하한.
    assert result["predicted_bid_rate"] >= floor - 1e-9
    for candidate in result["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= floor - 1e-9, candidate


def test_guardrail_ceiling_lower_actually_fires():
    result = _run(_case_by_id("predict_guardrail_ceiling_lower_construction"))
    assert result["guardrail_applied"] is True
    assert result["ceiling_bid_rate"] == 0.93
    assert result["predicted_bid_rate"] <= 0.93 + 1e-9
    for candidate in result["bid_rate_candidates"]:
        assert candidate["bid_rate"] <= 0.93 + 1e-9, candidate


def test_construction_legal_floor_tier_actually_fires():
    # 2026-01-30 신율: a 5억 (<10억) construction notice floor is the 0.89745 tier,
    # ABOVE the flat 0.87 — proves the tier binds and lifts the recommendation.
    result = _run(_case_by_id("predict_construction_legal_floor_tier_new"))
    assert result["guardrail_applied"] is True
    assert result["floor_bid_rate"] == 0.89745
    floor = result["safe_floor_bid_rate"]
    assert result["predicted_bid_rate"] >= floor - 1e-9
    for candidate in result["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= floor - 1e-9, candidate


# ---------------------------------------------------------------------------
# GAP C anchor: the numeric high-rate tail actually fires and its footprint
# reaches predicted_bid_rate / candidates / explanation.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id, reason",
    [
        ("hist_goods_tail_fires", "goods_recent_high_rate_tail"),
        ("hist_service_tail_fires", "service_recent_high_rate_tail"),
    ],
)
def test_high_rate_tail_golden_actually_fires(case_id, reason):
    result = _run(_case_by_id(case_id))
    adjustment = result["high_rate_tail_adjustment"]
    assert adjustment is not None
    assert adjustment["reason"] == reason
    # the lift reached the recommended rate and the explanation notes it.
    assert result["predicted_bid_rate"] > adjustment["original_bid_rate"]
    assert "최근 고율 낙찰 분포를 반영해" in result["explanation"]


# ---------------------------------------------------------------------------
# GAP D anchor (P4.1): the heuristic payload is NARROWER than the
# historical/ensemble one. Made legible beyond the raw golden JSON so a
# typed-output refactor cannot silently start emitting these keys as ``null`` —
# key PRESENCE is part of the predictor contract, not only key values.
# ---------------------------------------------------------------------------
_HISTORICAL_ONLY_KEYS = (
    "competitive_target_bid_rate",
    "procurement_rate_band",
    "high_rate_tail_adjustment",
)


@pytest.mark.parametrize(
    "case_id", [case["id"] for case in HEURISTIC_CASES]
)
def test_narrow_payload_omits_historical_only_keys(case_id):
    result = _run(_case_by_id(case_id))
    for key in _HISTORICAL_ONLY_KEYS:
        assert key not in result, f"{case_id} must not carry '{key}'"


def test_predict_price_heuristic_path_keeps_narrow_key_set():
    """The narrow heuristic payload survives the full pipeline without gaining nulls."""
    result = _run(_case_by_id("predict_heuristic_no_history"))
    assert result["pricing_mode"] == "heuristic"
    assert result["historical_sample_size"] == 0
    for key in _HISTORICAL_ONLY_KEYS:
        assert key not in result, f"heuristic predict_price must not carry '{key}'"
    # ...while every pipeline stage still stamped its own keys.
    assert result["predictor_name"] == "historical_statistical"
    assert "safe_floor_bid_rate" in result
    assert "price_granularity_applied" in result
    assert "price_regime_label" in result
    assert result["training_window_size"] == 0


def test_predict_price_feedback_calibration_actually_applies():
    result = _run(_case_by_id("predict_feedback_calibration_applied"))
    assert result["feedback_calibration"]["sample_count"] == 12
    assert "+feedback" in result["model_version"]


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
