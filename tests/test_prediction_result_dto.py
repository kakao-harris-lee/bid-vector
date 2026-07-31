"""``PredictionResult`` DTO contract tests (방어적 DTO 규율, P4.1).

The golden harness (``tests/test_predictor_output_equivalence.py``) proves the
*values* are unchanged. This module proves the *contract properties* that make
the typed output defensive in the first place:

* required fields are actually required (missing → ``ValidationError``),
* unknown keys are rejected instead of silently dropped (``extra="forbid"``),
* the payload is immutable (``frozen=True``),
* validate → serialize is LOSSLESS, including key PRESENCE (an unset optional
  field stays absent rather than becoming ``null``),
* the guardrail floor still binds when the payload travels through the DTO
  (project #1 invariant — nothing prices below the category 낙찰하한).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.predictors.base import (
    PredictionResult,
    PricePredictionContext,
    serialize_prediction_result,
)
from app.ai.price_prediction import predict_price
from app.ai.predictors.historical import HistoricalStatisticalPredictor

# The 17 fields every predictor must supply. Kept as an explicit literal (not
# derived from the model) so a field silently losing its "required" status shows
# up as a test failure rather than as a self-sealing tautology.
REQUIRED_FIELDS = (
    "predicted_price",
    "price_range_min",
    "price_range_max",
    "confidence_score",
    "model_version",
    "pricing_mode",
    "historical_sample_size",
    "agency_match_sample_size",
    "predicted_bid_rate",
    "bid_rate_candidates",
    "reserve_price_context",
    "feedback_calibration",
    "guardrail_applied",
    "guardrail_reason",
    "floor_bid_rate",
    "floor_price",
    "explanation",
)


def _minimal_payload() -> dict:
    """The smallest payload a conforming predictor may emit."""
    return {
        "predicted_price": 91_000_000.0,
        "price_range_min": 90_000_000.0,
        "price_range_max": 92_000_000.0,
        "confidence_score": 0.7,
        "model_version": "test-v1",
        "pricing_mode": "historical_blend",
        "historical_sample_size": 12,
        "agency_match_sample_size": 0,
        "predicted_bid_rate": 0.91,
        "bid_rate_candidates": [
            {"label": "base", "bid_rate": 0.91, "predicted_price": 91_000_000.0},
        ],
        "reserve_price_context": None,
        "feedback_calibration": None,
        "guardrail_applied": False,
        "guardrail_reason": None,
        "floor_bid_rate": None,
        "floor_price": None,
        "explanation": "minimal conforming payload",
    }


# --- happy path -------------------------------------------------------------
def test_minimal_payload_validates():
    result = PredictionResult.model_validate(_minimal_payload())

    assert result.predicted_bid_rate == 0.91
    assert result.pricing_mode == "historical_blend"
    # untouched optionals expose their declared defaults...
    assert result.competitive_target_bid_rate is None
    assert result.review_required is False
    # ...but were never SET, so they must not serialize.
    assert "competitive_target_bid_rate" not in serialize_prediction_result(result)


def test_required_field_set_is_exactly_pinned():
    required = {
        name
        for name, field in PredictionResult.model_fields.items()
        if field.is_required()
    }
    assert required == set(REQUIRED_FIELDS)


# --- sad path ---------------------------------------------------------------
@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_required_field_is_rejected(missing):
    payload = _minimal_payload()
    payload.pop(missing)

    with pytest.raises(ValidationError) as excinfo:
        PredictionResult.model_validate(payload)

    assert missing in str(excinfo.value)


def test_unknown_key_is_rejected_not_ignored():
    """A typo'd key must fail loudly — the whole point of ``extra="forbid"``."""
    payload = _minimal_payload() | {"predicted_bid_rat": 0.91}

    with pytest.raises(ValidationError) as excinfo:
        PredictionResult.model_validate(payload)

    assert "predicted_bid_rat" in str(excinfo.value)


def test_result_is_frozen():
    result = PredictionResult.model_validate(_minimal_payload())

    with pytest.raises(ValidationError):
        result.predicted_bid_rate = 0.99  # type: ignore[misc]


def test_wrong_type_is_rejected():
    payload = _minimal_payload() | {"bid_rate_candidates": "not-a-list"}

    with pytest.raises(ValidationError):
        PredictionResult.model_validate(payload)


# --- lossless round-trip (key PRESENCE is contract) -------------------------
def test_round_trip_preserves_narrow_payload_exactly():
    payload = _minimal_payload()

    assert serialize_prediction_result(PredictionResult.model_validate(payload)) == payload


def test_round_trip_preserves_explicit_null_vs_absent():
    """``None`` supplied and field-absent are DIFFERENT states, both preserved."""
    payload = _minimal_payload() | {"procurement_rate_band": None}
    round_tripped = serialize_prediction_result(PredictionResult.model_validate(payload))

    assert "procurement_rate_band" in round_tripped
    assert round_tripped["procurement_rate_band"] is None
    # a sibling optional that was never supplied stays absent
    assert "competitive_target_bid_rate" not in round_tripped


def test_round_trip_preserves_full_pipeline_payload():
    """Every declared field set at once still round-trips key-for-key.

    The optional block is spelled out rather than machine-filled so this doubles
    as the readable definition of the complete post-pipeline payload. Note the
    four stage flags are non-nullable ``bool`` on purpose — the guardrail,
    granularity and regime stages all write ``bool(...)``, never ``None``.
    """
    payload = _minimal_payload() | {
        # statistical predictors
        "competitive_target_bid_rate": 0.91,
        "procurement_rate_band": "service_price_competitive",
        "high_rate_tail_adjustment": {"reason": "service_recent_high_rate_tail"},
        # guardrail stage
        "legal_floor_bid_rate": 0.87,
        "floor_guardrail_source": "category",
        "floor_safety_margin_rate": 0.001,
        "safe_floor_bid_rate": 0.871,
        "safe_floor_price": 87_100_000.0,
        "ceiling_bid_rate": 0.93,
        "ceiling_price": 93_000_000.0,
        "floor_from_agency": False,
        "ceiling_from_agency": True,
        # granularity stage
        "bid_price_granularity": 10,
        "bid_price_rounding_mode": "floor",
        "price_granularity_applied": True,
        # price-regime stage
        "price_regime_features": {"price_regime_label": "price_competition"},
        "price_regime_label": "price_competition",
        "price_regime_confidence": 0.62,
        "review_required": False,
        "recommended_candidate_label": "base",
        "recommended_selector_reason": "regime default",
        # predictor-selection metadata
        "predictor_name": "historical_statistical",
        "predictor_family": "statistical",
        "fallback_reason": None,
        "selector_name": "configured_preference",
        "selection_reason": "Configured historical baseline selected.",
        "backtest_sample_count": 0,
        "backtest_average_absolute_error_rate": None,
        "backtest_report": {"results": []},
        "training_window_size": 12,
    }
    round_tripped = serialize_prediction_result(PredictionResult.model_validate(payload))

    # the literal above must stay exhaustive as fields are added
    assert set(payload) == set(PredictionResult.model_fields)
    assert set(round_tripped) == set(PredictionResult.model_fields)
    assert round_tripped == payload


def test_model_copy_unknown_key_hole_is_closed():
    """A typo'd update key must raise, not silently vanish.

    ``model_copy(update=...)`` skips validation entirely, so pydantic drops an
    unknown key without complaint — that bypasses ``extra="forbid"`` and would
    re-admit the quiet-typo defect class this DTO exists to prevent. The first
    assertions pin the raw pydantic behaviour (so this test still means something
    if that ever changes); the rest pin the orchestration guard layered over it.
    """
    from app.ai.price_prediction.orchestration import _copy_with_validated_keys

    result = PredictionResult.model_validate(_minimal_payload())

    # raw pydantic: the typo disappears silently — this is the hole.
    silently_dropped = result.model_copy(update={"predictor_nam": "typo"})
    assert "predictor_nam" not in serialize_prediction_result(silently_dropped)
    assert silently_dropped.predictor_name is None

    # the guard turns that silent drop into an explicit failure...
    with pytest.raises(ValueError, match="predictor_nam"):
        _copy_with_validated_keys(result, {"predictor_nam": "typo"})

    # ...while a legitimate update still passes through untouched.
    updated = _copy_with_validated_keys(
        result, {"predictor_name": "historical_statistical"}
    )
    assert updated.predictor_name == "historical_statistical"
    assert serialize_prediction_result(updated)["predictor_name"] == "historical_statistical"


def test_immutable_update_leaves_the_original_untouched():
    """``model_copy`` is how stages annotate — no post-hoc mutation of the input."""
    original = PredictionResult.model_validate(_minimal_payload())

    updated = original.model_copy(update={"predictor_name": "historical_statistical"})

    assert updated.predictor_name == "historical_statistical"
    assert original.predictor_name is None
    assert "predictor_name" not in serialize_prediction_result(original)
    assert serialize_prediction_result(updated)["predictor_name"] == "historical_statistical"


# --- the real predictors satisfy the contract -------------------------------
def test_historical_predictor_returns_a_validated_result():
    context = PricePredictionContext(
        budget=100_000_000.0,
        category="service",
        description="OO 청소용역",
        historical_records=tuple({"bid_rate": 0.905} for _ in range(12)),
    )

    result = HistoricalStatisticalPredictor().predict(context)

    assert isinstance(result, PredictionResult)
    assert result.predicted_bid_rate > 0
    # the statistical predictor DOES carry the fields the heuristic path omits
    assert "competitive_target_bid_rate" in serialize_prediction_result(result)


def test_heuristic_path_returns_a_validated_result_without_statistical_fields():
    context = PricePredictionContext(
        budget=100_000_000.0,
        category="service",
        description="이력 없는 신규 용역 공고",
        historical_records=(),
    )

    payload = serialize_prediction_result(HistoricalStatisticalPredictor().predict(context))

    assert payload["pricing_mode"] == "heuristic"
    for key in ("competitive_target_bid_rate", "procurement_rate_band", "high_rate_tail_adjustment"):
        assert key not in payload


# --- guardrail regression THROUGH the typed pipeline ------------------------
def test_guardrail_floor_still_binds_through_the_dto():
    """Project #1 invariant: no scenario prices below the category 낙찰하한.

    Deep low-rate construction history (~0.75) sits far below the 0.87 floor, so
    the guardrail must lift every candidate. Guarding this here (not only in the
    goldens) keeps the invariant legible after the output became typed.
    """
    prediction = predict_price(
        budget=100_000_000.0,
        category="construction",
        description="낙찰하한 검증 토목공사",
        historical_records=tuple({"bid_rate": 0.75} for _ in range(20)),
    )

    assert prediction["guardrail_applied"] is True
    assert prediction["guardrail_reason"]
    safe_floor = prediction["safe_floor_bid_rate"]
    assert prediction["predicted_bid_rate"] >= safe_floor - 1e-9
    for candidate in prediction["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= safe_floor - 1e-9, candidate


def test_predict_price_still_returns_a_plain_dict():
    """The public boundary contract is unchanged — services keep receiving a dict."""
    prediction = predict_price(
        budget=100_000_000.0,
        category="service",
        description="OO 청소용역",
        historical_records=tuple({"bid_rate": 0.905} for _ in range(12)),
    )

    assert isinstance(prediction, dict)
    assert not isinstance(prediction, PredictionResult)
    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["training_window_size"] == prediction["historical_sample_size"]
    # metadata that only the auto selector produces stays absent
    assert "backtest_report" not in prediction
