"""Base contracts for price prediction predictors.

Both ends of the predictor contract live here: :class:`PricePredictionContext`
(immutable input) and :class:`PredictionResult` (validated output). Keeping them
together is deliberate — a predictor is exactly "context in, result out", and a
reader should not have to open a second module to learn half of the contract.

Why the output is a model and no longer a raw ``dict``
------------------------------------------------------
``predict()`` used to return ``dict[str, Any]``, and that dict was relayed
through the whole pipeline (feedback → guardrails → granularity → price regime →
predictor metadata) with each stage *mutating* it by string key. A typo'd key was
silently ignored and a missing key silently became a default, which is precisely
the class of defect the 방어적 DTO 규율 targets. ``PredictionResult`` closes that
hole: unknown keys are rejected (``extra="forbid"``) and the payload is immutable
(``frozen=True``), so a stage can only produce a *new* result, never quietly
corrupt the previous one.

Key PRESENCE is part of the contract, not only key values
---------------------------------------------------------
The pipeline stages *add* keys progressively, and the narrow heuristic-fallback
payload legitimately omits keys that the historical/ensemble payloads carry.
Emitting those as ``null`` instead would change the operator-facing payload. So
every stage-conditional field is declared optional and
``serialize_prediction_result`` dumps with ``exclude_unset=True`` — a field that
was never set stays ABSENT, exactly as before. ``tests/goldens/predictor/`` pins
this byte-for-byte (see ``_HISTORICAL_ONLY_KEYS`` in
``tests/test_predictor_output_equivalence.py``).

Required vs optional was derived by enumerating every golden payload rather than
guessed: the 17 required fields are the ones present in *all* goldens (i.e. every
producer already emits them), and the rest are stage-conditional. Note that
several required fields are *nullable* — ``reserve_price_context`` must be
supplied by the producer but may legitimately be ``None``; "the producer decided
there is none" and "the producer forgot" are different states.

.. note::
   이 도출은 원래 44개 골든(heuristic 3 + **standalone LSTM 3** + historical/
   ensemble/predict_price)에서 나왔다. sequence-model 은퇴(2026-08-09)로 골든은
   39개가 됐고, **삭제된 5건 중 3건이 narrow-shape(LSTM) 였다** — 즉 "필수 17"의
   도출 근거 집합 자체가 바뀌었다. 남은 narrow-shape 생산자는 heuristic fallback
   하나뿐이며, 그 payload 가 여전히 17 필드를 모두 싣기 때문에 필수 집합은 불변이다.

   무엇이 이것을 고정하는가(정확히):

   * **필드 존재**는 이 모델 자신이 강제한다 — 17 필드가 required 라 하나라도 빠지면
     ``PredictionResult`` 복원이 ``ValidationError`` 로 실패한다.
   * **narrow payload 의 실제 바이트**는 ``tests/goldens/predictor/heur_*.json`` 3건이
     byte-for-byte 로 고정한다.
   * ``test_narrow_payload_omits_historical_only_keys`` 는 그 **반대편**(historical 전용
     키의 부재)만 단언한다 — 17 필드의 존재를 고정하지 **않는다**.

정직 명세 (§2) is unchanged by this typing: ``predicted_bid_rate`` /
``competitive_target_bid_rate`` remain 가격 적합도 추정, never a literal
P(낙찰), and no guardrail field is weakened or bypassed here — this module only
declares the shape the guardrail stage writes into.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.schemas._base import FrozenStrictModel

__all__ = [
    "BasePricePredictor",
    "PredictionResult",
    "PredictorAvailability",
    "PricePredictionContext",
    "serialize_prediction_result",
]


@dataclass(frozen=True)
class PricePredictionContext:
    """Immutable input payload shared across predictor implementations."""

    budget: float
    category: str
    description: str
    historical_records: tuple[object, ...]
    agency_name: str | None = None
    business_type_code: str | None = None
    business_group: str | None = None

    @property
    def historical_sample_size(self) -> int:
        """Return the number of available historical rows."""
        return len(self.historical_records)


@dataclass(frozen=True)
class PredictorAvailability:
    """Describe whether a predictor is currently runnable."""

    available: bool
    reason: str | None = None


class PredictionResult(FrozenStrictModel):
    """One validated price-prediction payload.

    Fields are grouped by the pipeline stage that produces them. Everything below
    the "predictor core" block is optional because it is stamped by a *later*
    stage (or only by a richer predictor), and an unset field must serialize as
    ABSENT rather than ``null`` — see the module docstring.
    """

    # --- predictor core: every predictor MUST produce these -------------------
    # (present in all goldens; the nullable ones still require an explicit
    # value, so "no reserve pattern" is distinguishable from "forgot the key")
    predicted_price: float
    price_range_min: float
    price_range_max: float
    confidence_score: float
    model_version: str
    pricing_mode: str
    historical_sample_size: int
    agency_match_sample_size: int
    predicted_bid_rate: float
    bid_rate_candidates: list[dict[str, Any]]
    reserve_price_context: dict[str, Any] | None
    feedback_calibration: dict[str, Any] | None
    guardrail_applied: bool
    guardrail_reason: str | None
    floor_bid_rate: float | None
    floor_price: float | None
    explanation: str

    # --- statistical predictors only (absent from the heuristic fallback)
    competitive_target_bid_rate: float | None = None
    procurement_rate_band: str | None = None
    high_rate_tail_adjustment: dict[str, Any] | None = None

    # --- guardrail stage (``_apply_prediction_guardrails``) -------------------
    legal_floor_bid_rate: float | None = None
    floor_guardrail_source: str | None = None
    floor_safety_margin_rate: float | None = None
    safe_floor_bid_rate: float | None = None
    safe_floor_price: float | None = None
    ceiling_bid_rate: float | None = None
    ceiling_price: float | None = None
    floor_from_agency: bool = False
    ceiling_from_agency: bool = False

    # --- bid-price granularity stage (``_apply_bid_price_granularity``) -------
    bid_price_granularity: int | None = None
    bid_price_rounding_mode: str | None = None
    price_granularity_applied: bool = False

    # --- price-regime stage (``_attach_price_regime_metadata``) ---------------
    price_regime_features: dict[str, Any] | None = None
    price_regime_label: str | None = None
    price_regime_confidence: float | None = None
    review_required: bool = False
    recommended_candidate_label: str | None = None
    recommended_selector_reason: str | None = None

    # --- predictor-selection metadata (``_attach_predictor_metadata``) --------
    predictor_name: str | None = None
    predictor_family: str | None = None
    fallback_reason: str | None = None
    selector_name: str | None = None
    selection_reason: str | None = None
    backtest_sample_count: int | None = None
    backtest_average_absolute_error_rate: float | None = None
    backtest_report: dict[str, Any] | None = None
    training_window_size: int | None = None


def serialize_prediction_result(result: PredictionResult) -> dict[str, Any]:
    """Demote one result to the plain-``dict`` boundary contract (single point).

    ``exclude_unset=True`` is load-bearing, not a micro-optimization: it keeps a
    field that no stage ever set ABSENT instead of ``null``, which is what makes
    the narrow heuristic payload byte-identical to the pre-typing output.
    Every demotion goes through this helper so the requirement is stated once and
    cannot be forgotten at an individual call site.
    """
    return result.model_dump(exclude_unset=True)


class BasePricePredictor(ABC):
    """Base class for all predictor implementations."""

    name = "base"
    family = "generic"

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """Return whether the predictor can run for the given context."""
        return PredictorAvailability(available=True)

    @abstractmethod
    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """Return a validated prediction payload."""
