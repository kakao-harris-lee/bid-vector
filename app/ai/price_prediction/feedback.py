"""Feedback-derived calibration of the predicted price scenarios.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). ``_apply_feedback_calibration`` is relocated verbatim — the
adjustment factor and rounding are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict

from app.ai.predictors.historical import clamp_bid_rate


def _apply_feedback_calibration(prediction: Dict[str, Any], feedback_calibration: Dict[str, Any] | None) -> Dict[str, Any]:
    """Apply a feedback-derived adjustment rate to all price scenarios when available."""
    if not feedback_calibration:
        return prediction

    sample_count = int(feedback_calibration.get("sample_count", 0) or 0)
    adjustment_rate = float(feedback_calibration.get("applied_adjustment_rate", 0.0) or 0.0)
    if sample_count <= 0:
        return prediction

    if abs(adjustment_rate) < 0.0001:
        calibrated_prediction = dict(prediction)
        calibrated_prediction["feedback_calibration"] = feedback_calibration
        return calibrated_prediction

    factor = max(0.5, 1.0 + adjustment_rate)
    calibrated_candidates: list[dict[str, Any]] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        calibrated_bid_rate = clamp_bid_rate(float(candidate.get("bid_rate", 0.0) or 0.0) * factor)
        calibrated_candidates.append({
            **candidate,
            "bid_rate": round(calibrated_bid_rate, 4),
            "predicted_price": round(float(candidate.get("predicted_price", 0.0) or 0.0) * factor, 2),
        })

    base_candidate = next(
        (candidate for candidate in calibrated_candidates if candidate.get("label") == "base"),
        None,
    )
    calibrated_prediction = {
        **prediction,
        "predicted_price": round(float(prediction.get("predicted_price", 0.0) or 0.0) * factor, 2),
        "price_range_min": round(float(prediction.get("price_range_min", 0.0) or 0.0) * factor, 2),
        "price_range_max": round(float(prediction.get("price_range_max", 0.0) or 0.0) * factor, 2),
        "predicted_bid_rate": round(
            float(base_candidate.get("bid_rate")) if base_candidate is not None else float(prediction.get("predicted_bid_rate", 0.0) or 0.0) * factor,
            4,
        ),
        "bid_rate_candidates": calibrated_candidates,
        "feedback_calibration": feedback_calibration,
        "model_version": f"{prediction.get('model_version', 'v1.0')}+feedback",
        "explanation": (
            f"{prediction.get('explanation', '').rstrip('.')} 최근 피드백 보정률 {adjustment_rate:+.2%}를 반영했습니다."
        ).strip(),
    }
    if base_candidate is not None:
        calibrated_prediction["predicted_price"] = round(float(base_candidate.get("predicted_price", 0.0) or 0.0), 2)
    return calibrated_prediction
