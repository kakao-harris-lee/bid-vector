"""Price prediction AI module -- public surface (delegator).

The former single-file ``app/ai/price_prediction.py`` (1,277 lines) was
decomposed by responsibility (§4.5 size limit) into this package:

* ``orchestration``       -- ``predict_price`` entry point + predictor selection
* ``feedback``            -- feedback-derived calibration
* ``price_regime``        -- explainable price-regime metadata / candidate select
* ``guardrails_context``  -- floor/ceiling/agency/era-tier band resolution
* ``guardrails_apply``    -- clamp candidates/base to the band + auditable reason
* ``insights``            -- historical market-insight statistics

Every function/class was relocated VERBATIM, so the guardrail arithmetic, the
max()-only legal-floor fold, the #256 rate delegation, and every recommended
price are byte-for-byte unchanged. This ``__init__`` re-exports the entire
original top-level surface (public + the private symbols imported by
paper_bidding_backtest, bid_summary, opportunity_analysis, and the test suite)
so every historical ``from app.ai.price_prediction import ...`` and
``price_prediction.<name>`` access path keeps resolving. ``settings`` is
re-exported for the ``price_prediction.settings`` monkeypatch seam.
"""

from __future__ import annotations

from app.core.config import settings

from app.ai.price_prediction.feedback import _apply_feedback_calibration
from app.ai.price_prediction.guardrails_apply import (
    _CANDIDATE_LABEL_TO_SCENARIO_STANCE,
    _annotate_construction_scenario_anchor,
    _append_explanation_note,
    _append_model_version_suffix,
    _apply_base_prediction_guardrails,
    _apply_bid_price_granularity,
    _apply_guarded_candidate_prediction,
    _apply_guardrail_metadata,
    _apply_prediction_guardrails,
    _build_guardrail_reason,
    _clamp_price_to_guardrails,
    _clamp_rate_to_guardrails,
    _guard_bid_rate_candidates,
    _guard_candidate_bid_rate,
    _mark_guardrail_application,
    _resolve_candidate_target_rate,
)
from app.ai.price_prediction.guardrails_context import (
    GuardrailContext,
    _floor_guardrail_label,
    _guardrail_price,
    _max_optional_rate,
    _normalize_optional_bid_rate,
    _resolve_agency_bid_rate,
    _resolve_ceiling_bid_rate,
    _resolve_floor_bid_rate,
    _resolve_floor_guardrail_source,
    _resolve_guardrail_context,
)
from app.ai.price_prediction.insights import get_price_insights
from app.ai.price_prediction.orchestration import (
    _PREDICTOR_KEY_ALIASES,
    _attach_predictor_metadata,
    _merge_fallback_reason,
    _normalize_predictor_key,
    _run_predictor,
    _select_predictor,
    _select_predictor_by_backtest,
    predict_price,
)
from app.ai.price_prediction.price_regime import (
    _AWARD_FLOOR_EVIDENCE_KEYS,
    _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL,
    _DIRECT_NEGOTIATED_CUES,
    _NEGOTIATED_QUOTE_CUES,
    _NEGOTIATED_RATE_BANDS,
    _NEGOTIATION_CUES,
    _PRICE_COMPETITION_QUOTE_CUES,
    _PRICE_COMPETITION_STRONG_CUES,
    _PRICE_REGIME_AMBIGUOUS_FALLBACK,
    _PRICE_REGIME_RULES,
    _TWO_STAGE_BARE_COOCCURRENCE_CUES,
    _TWO_STAGE_EXPLICIT_MARKERS,
    _PriceRegimeRule,
    _amount_bucket,
    _attach_price_regime_metadata,
    _build_price_regime_features,
    _contains_any,
    _detect_price_regime_signals,
    _has_bare_two_stage,
    _has_resolved_award_floor,
    _has_two_stage_cue,
    _infer_award_method,
    _is_construction_negotiated_quote_only,
    _infer_contract_method,
    _infer_evaluation_method,
    _infer_price_submission_mode,
    _infer_service_type,
    _select_recommended_candidate,
)

__all__ = [
    "settings",
    "GuardrailContext",
    "_PREDICTOR_KEY_ALIASES",
    "_PRICE_REGIME_AMBIGUOUS_FALLBACK",
    "_PRICE_REGIME_RULES",
    "_PriceRegimeRule",
    "_AWARD_FLOOR_EVIDENCE_KEYS",
    "_CANDIDATE_LABEL_TO_SCENARIO_STANCE",
    "_CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL",
    "_DIRECT_NEGOTIATED_CUES",
    "_NEGOTIATED_QUOTE_CUES",
    "_NEGOTIATED_RATE_BANDS",
    "_NEGOTIATION_CUES",
    "_PRICE_COMPETITION_QUOTE_CUES",
    "_PRICE_COMPETITION_STRONG_CUES",
    "_TWO_STAGE_BARE_COOCCURRENCE_CUES",
    "_TWO_STAGE_EXPLICIT_MARKERS",
    "predict_price",
    "get_price_insights",
    "_select_predictor",
    "_select_predictor_by_backtest",
    "_normalize_predictor_key",
    "_run_predictor",
    "_merge_fallback_reason",
    "_attach_predictor_metadata",
    "_attach_price_regime_metadata",
    "_build_price_regime_features",
    "_has_bare_two_stage",
    "_has_resolved_award_floor",
    "_has_two_stage_cue",
    "_is_construction_negotiated_quote_only",
    "_detect_price_regime_signals",
    "_select_recommended_candidate",
    "_infer_contract_method",
    "_infer_award_method",
    "_infer_evaluation_method",
    "_infer_price_submission_mode",
    "_infer_service_type",
    "_amount_bucket",
    "_contains_any",
    "_apply_feedback_calibration",
    "_apply_prediction_guardrails",
    "_annotate_construction_scenario_anchor",
    "_resolve_guardrail_context",
    "_guardrail_price",
    "_apply_guardrail_metadata",
    "_guard_bid_rate_candidates",
    "_resolve_candidate_target_rate",
    "_guard_candidate_bid_rate",
    "_apply_guarded_candidate_prediction",
    "_apply_base_prediction_guardrails",
    "_mark_guardrail_application",
    "_apply_bid_price_granularity",
    "_clamp_rate_to_guardrails",
    "_clamp_price_to_guardrails",
    "_build_guardrail_reason",
    "_normalize_optional_bid_rate",
    "_max_optional_rate",
    "_resolve_floor_guardrail_source",
    "_floor_guardrail_label",
    "_resolve_agency_bid_rate",
    "_resolve_floor_bid_rate",
    "_resolve_ceiling_bid_rate",
    "_append_model_version_suffix",
    "_append_explanation_note",
]
