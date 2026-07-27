"""Explainable price-regime metadata and final-candidate selection.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). Every function is relocated verbatim from the original module.
These signals affect ONLY descriptive regime metadata (label / signals /
review_required), never the recommended price — the price-moving band lives in
``resolve_procurement_rate_band`` and runs upstream of this metadata pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.ai.construction_scenario import is_construction_category
from app.ai.predictors import PricePredictionContext
from app.ai.predictors.historical import (
    normalize_category_key,
    resolve_procurement_rate_band,
)


def _attach_price_regime_metadata(
    prediction: dict[str, Any],
    *,
    context: PricePredictionContext,
) -> dict[str, Any]:
    """Attach explainable price-regime and final-candidate selector metadata."""
    annotated_prediction = dict(prediction)
    features = _build_price_regime_features(prediction, context=context)
    selector = _select_recommended_candidate(
        annotated_prediction.get("bid_rate_candidates", []),
        price_regime_label=str(features["price_regime_label"]),
    )
    annotated_prediction["price_regime_features"] = features
    annotated_prediction["price_regime_label"] = features["price_regime_label"]
    annotated_prediction["price_regime_confidence"] = features["price_regime_confidence"]
    annotated_prediction["review_required"] = bool(features["review_required"])
    annotated_prediction["recommended_candidate_label"] = selector["recommended_candidate_label"]
    annotated_prediction["recommended_selector_reason"] = selector["recommended_selector_reason"]
    return annotated_prediction


@dataclass(frozen=True)
class _PriceRegimeRule:
    """One first-match price-regime rule keyed on a boolean signal flag.

    The rule ORDER is the priority (the original elif chain), so this reads as an
    order-preserving rule list — deliberately NOT a dict dispatch, since section
    F of the characterization test locks both the routing order and the emitted
    (label, confidence, review_required) triple.
    """

    signal_flag: str
    label: str
    confidence: float
    review_required: bool


# First-match order == the original elif priority:
#   conflicting > deep_discount > near_100 > floor_bound; else ambiguous fallback.
_PRICE_REGIME_RULES: tuple[_PriceRegimeRule, ...] = (
    _PriceRegimeRule("conflicting", "ambiguous", 0.58, True),
    _PriceRegimeRule("deep_discount", "deep_discount", 0.86, False),
    _PriceRegimeRule("near_100", "near_100", 0.84, False),
    _PriceRegimeRule("floor_bound", "floor_bound", 0.82, False),
)
_PRICE_REGIME_AMBIGUOUS_FALLBACK = _PriceRegimeRule("", "ambiguous", 0.45, True)


def _build_price_regime_features(
    prediction: dict[str, Any],
    *,
    context: PricePredictionContext,
) -> dict[str, Any]:
    """Classify the procurement price regime before interpreting model output."""
    category = normalize_category_key(context.category)
    text = str(context.description or "").strip().lower()
    rate_band = (
        prediction.get("procurement_rate_band")
        or resolve_procurement_rate_band(category=context.category, description=context.description)
    )
    signal_flags = _detect_price_regime_signals(
        category=category,
        text=text,
        rate_band=rate_band,
        award_floor_resolved=_has_resolved_award_floor(prediction),
    )

    matched_rule = next(
        (rule for rule in _PRICE_REGIME_RULES if signal_flags[rule.signal_flag]),
        _PRICE_REGIME_AMBIGUOUS_FALLBACK,
    )
    price_regime_label = matched_rule.label
    confidence = matched_rule.confidence
    review_required = matched_rule.review_required

    return {
        "buyer_sector": "unknown",
        "buyer_type": "unknown",
        "notice_category": category or "unknown",
        "business_type_code": context.business_type_code,
        "business_group": context.business_group,
        "construction_or_service_type": _infer_service_type(text),
        "contract_method": _infer_contract_method(signal_flags),
        "award_method": _infer_award_method(signal_flags),
        "evaluation_method": _infer_evaluation_method(signal_flags),
        "price_submission_mode": _infer_price_submission_mode(signal_flags),
        "denominator_type": "base_amount",
        "legal_floor_bid_rate": prediction.get("legal_floor_bid_rate"),
        "reserve_price_context_available": prediction.get("reserve_price_context") is not None,
        "amount_bucket": _amount_bucket(context.budget),
        "agency_recent_rate_profile": {
            "historical_sample_size": int(prediction.get("historical_sample_size", 0) or 0),
            "agency_match_sample_size": int(prediction.get("agency_match_sample_size", 0) or 0),
        },
        "data_quality_flags": [],
        "procurement_rate_band": rate_band,
        "price_regime_label": price_regime_label,
        "price_regime_confidence": confidence,
        "review_required": review_required,
        "regime_signals": signal_flags["signals"],
    }


# --------------------------------------------------------------------------- #
# Price-regime signal keyword tables (declarative; interpreted by
# ``_detect_price_regime_signals``). Split by class so the title-semantic guards
# can treat each differently: explicit two-stage markers and strong price-
# competition cues always fire, while bare "2단계" and bare 견적/견적제출 are
# gated to suppress construction phase-noise and 수의 quote-submission false
# positives. These affect ONLY descriptive regime metadata (label / signals /
# review_required), never the recommended price — the price-moving band lives in
# ``resolve_procurement_rate_band`` and runs upstream of this metadata pass.
# --------------------------------------------------------------------------- #
_TWO_STAGE_EXPLICIT_MARKERS = (
    "규격·가격",
    "규격 가격",
    "규격가격",
    "가격분리",
    "가격 분리",
    "동시입찰",
    "동시 평가",
)
# Bare "2단계" only counts as 규격·가격 2단계 입찰 when a spec/price-separation cue
# co-occurs (otherwise it is construction phase-2 노이즈: "○○ 2단계 증설공사").
_TWO_STAGE_BARE_COOCCURRENCE_CUES = ("규격", "가격", "동시")
_NEGOTIATION_CUES = ("협상에 의한 계약", "협상", "제안서 평가", "제안")
_DIRECT_NEGOTIATED_CUES = ("수의시담", "수의 시담", "수의계약", "수의 계약", "(수의)", "[수의]")
# No-space form ONLY. "소액수의 견적"(소액수의 negotiated quote whose goods band settles
# at the competitive rate) contains "수의 견적" WITH a space, so the space variant would
# spuriously mark it as near_100 and collide with a competitive band → false conflict.
# "수의견적"(no space) cannot appear inside "소액수의 견적", so it is collision-free.
_NEGOTIATED_QUOTE_CUES = ("수의견적",)
_PRICE_COMPETITION_STRONG_CUES = ("가격입찰", "적격심사", "pq")
_PRICE_COMPETITION_QUOTE_CUES = ("소액수의 견적", "견적 제출", "견적제출")
_NEGOTIATED_RATE_BANDS = frozenset({"service_direct_negotiated", "service_high_negotiated"})

# 공사 수의 견적 가드 — 공사에서 수의 단서 단독은 near_100 가격 메커니즘이 아니다.
#
# 규정 사실: 공사 소액수의 견적제출은 예정가격 결정에 **적격심사 낙찰하한율**(추정가격
# 10억 미만 89.745%)을 그대로 준용하는 "하한 이상 최저가" 경쟁이다. near_100 이 뜻하는
# 협상·수의시담·위탁/운영형(95~100%) 가격 행동과 다르다. 용역/물품 수의시담에는 near_100
# 정의가 유효하므로 가드는 공사에만 적용한다.
#
# 근거(2026-07-27 라벨 감사, 홀드아웃 clean flag-free 표본): 공사 near_100 470건 **전량**이
# 이 direct_negotiated 단서 단독으로 발화했는데, 실낙찰률 ≥0.97 은 9.1% 뿐이고 76.8% 가
# 낙찰하한 +100bp 이내(중앙값 0.9030)로 밀착했다 — 당시 floor_bound 코호트(64.2%)보다도
# 더 하한 밀착이다. 근거·재적용 수치는 docs/operations/procurement-segment-improvement-notes.md.
#
# 발동 조건은 near_100 증거가 수의 단서 **하나뿐**일 때로 좁힌다(협상 단서나 negotiated
# 밴드가 함께 있으면 기존 near_100 을 유지). #240 의 title-semantic 가드들과 같은 층
# (서술 메타데이터)에서만 동작하며 추천 가격에는 관여하지 않는다.
_CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL = "construction_direct_negotiated_not_near_100"
# 재라우팅 전제("법정 하한이 해석되는 행"): guardrail 이 이미 해석해 prediction 에 남긴
# 낙찰하한 근거 필드. 공사에서 이 하한은 적격심사 낙찰하한(공고별 법정 하한 #221 또는
# era-correct tier #197)이므로 "그 위 최저가 경쟁" = floor_bound 와 정합한다. 근거가 없으면
# floor_bound 로 올리지 않고 기존 fallback(ambiguous·review) 규칙에 맡긴다.
_AWARD_FLOOR_EVIDENCE_KEYS = ("legal_floor_bid_rate", "floor_bid_rate")


def _has_bare_two_stage(text: str) -> bool:
    """True when "2단계" appears not immediately preceded by a digit.

    The digit-boundary guard rejects substring matches inside a larger number
    such as "12단계"/"22단계" (which are not 2단계 규격·가격 입찰).
    """
    start = 0
    while True:
        pos = text.find("2단계", start)
        if pos < 0:
            return False
        if pos == 0 or not text[pos - 1].isdigit():
            return True
        start = pos + 1


def _has_two_stage_cue(text: str) -> bool:
    """True when the notice text carries a genuine 2단계(규격·가격 분리) bidding cue.

    Explicit 규격·가격/가격분리/동시입찰 markers always count. Bare "2단계" counts
    only when it clears the digit-boundary guard (not "12단계") AND a spec/price-
    separation cue (규격/가격/동시) co-occurs — otherwise it is construction
    phase-2 노이즈 (e.g. "태봉근린공원(2단계) 통신공사"). When the explicit markers
    are present a co-occurrence cue is already implied, so bare "2단계" is nearly
    inert by design.
    """
    if _contains_any(text, _TWO_STAGE_EXPLICIT_MARKERS):
        return True
    return _has_bare_two_stage(text) and _contains_any(
        text, _TWO_STAGE_BARE_COOCCURRENCE_CUES
    )


def _has_resolved_award_floor(prediction: Mapping[str, Any]) -> bool:
    """공고에 해석된 낙찰하한이 남아 있는가 — 공사 수의 견적 재라우팅의 전제.

    guardrail 단계가 이미 계산해 payload 에 남긴 값만 읽는다(재계산·재해석 없음).
    """
    return any(prediction.get(key) is not None for key in _AWARD_FLOOR_EVIDENCE_KEYS)


def _is_construction_negotiated_quote_only(
    *,
    category: str,
    has_direct: bool,
    has_negotiation: bool,
    rate_band: Any,
) -> bool:
    """공사이면서 near_100 증거가 수의(direct) 단서 하나뿐인가 — 가드 발동 조건.

    협상 단서나 negotiated 밴드가 함께 있으면 증거가 수의 단독이 아니므로 발동하지
    않는다(기존 near_100 유지). 카테고리 판정은 공사 전용 하한 게이트와 같은 단일 출처
    술어(:func:`is_construction_category`)를 재사용한다.
    """
    return (
        is_construction_category(category)
        and has_direct
        and not has_negotiation
        and rate_band not in _NEGOTIATED_RATE_BANDS
    )


def _detect_price_regime_signals(
    *,
    category: str,
    text: str,
    rate_band: Any,
    award_floor_resolved: bool = False,
) -> dict[str, Any]:
    """Return broad mechanism signals used by the price-regime classifier."""
    signals: set[str] = set()
    has_two_stage = _has_two_stage_cue(text)
    has_negotiation = _contains_any(text, _NEGOTIATION_CUES)
    # 수의견적(no space) is a 수의(negotiated) quote context. For non-goods it reads as
    # 수의계약형(near-100), so fold it into the direct-negotiated cues (drives near_100
    # + direct_negotiated signal). Goods 소액수의 견적 is intentionally excluded — the
    # goods band settles it at the competitive rate (GOODS_PRICE_COMPETITIVE_KEYWORDS),
    # so its floor_bound regime stays consistent with the price.
    has_direct = _contains_any(text, _DIRECT_NEGOTIATED_CUES) or (
        category != "goods" and _contains_any(text, _NEGOTIATED_QUOTE_CUES)
    )
    # A bare 견적/견적제출 inside a 수의/negotiated context is 'submit a quote', not
    # competitive price bidding — suppress it so it does not raise a false
    # near_100 ∧ floor_bound conflict (→ ambiguous/review). Genuine 가격입찰/적격심사/pq
    # always fire (strong cues), and can still produce a real conflict.
    negotiated_context = (
        has_direct or has_negotiation or rate_band in _NEGOTIATED_RATE_BANDS
    )
    has_price_competition = _contains_any(text, _PRICE_COMPETITION_STRONG_CUES) or (
        _contains_any(text, _PRICE_COMPETITION_QUOTE_CUES) and not negotiated_context
    )
    has_deep_discount = bool(rate_band == "goods_deep_discount")

    if rate_band in {"service_price_competitive", "goods_price_competitive"} or has_price_competition:
        signals.add("price_competitive")
    if has_two_stage:
        signals.add("two_stage_or_separated")
    if has_negotiation or rate_band == "service_high_negotiated":
        signals.add("negotiated")
    if has_direct or rate_band == "service_direct_negotiated":
        signals.add("direct_negotiated")
    if has_deep_discount:
        signals.add("deep_discount")

    floor_bound = bool(rate_band in {"service_price_competitive", "goods_price_competitive"} or has_price_competition)
    near_100 = bool(rate_band in {"service_high_negotiated", "service_direct_negotiated"} or has_negotiation or has_direct)
    deep_discount = bool(has_deep_discount)
    # 공사 수의 견적 가드: near_100 을 내리고, 낙찰하한이 해석된 행이면 그 하한 위 최저가
    # 경쟁(floor_bound)으로 정합시킨다. 하한 근거가 없으면 아무 규칙도 세우지 않아 기존
    # fallback(ambiguous·review) 로 흐른다. 라우팅 규칙표(_PRICE_REGIME_RULES)는 불변이다.
    if near_100 and _is_construction_negotiated_quote_only(
        category=category,
        has_direct=has_direct,
        has_negotiation=has_negotiation,
        rate_band=rate_band,
    ):
        near_100 = False
        floor_bound = floor_bound or award_floor_resolved
        signals.add(_CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL)
    conflicting = (near_100 and floor_bound) or (deep_discount and near_100)
    if category == "goods" and has_two_stage and _contains_any(text, ("급식", "농산물")):
        deep_discount = True
        floor_bound = False
        signals.add("deep_discount")

    return {
        "floor_bound": floor_bound,
        "near_100": near_100,
        "deep_discount": deep_discount,
        "conflicting": conflicting,
        "signals": sorted(signals),
    }


def _select_recommended_candidate(
    candidates: Any,
    *,
    price_regime_label: str,
) -> dict[str, str]:
    """Select the final candidate label separately from candidate generation."""
    candidate_list = candidates if isinstance(candidates, list) else []
    labels = {str(candidate.get("label")) for candidate in candidate_list if isinstance(candidate, dict)}
    selected_label = "base" if "base" in labels else (next(iter(labels), "base"))
    if price_regime_label == "ambiguous":
        reason = "ambiguous/conflicting price regime; retaining base candidate and requiring review."
    else:
        reason = f"{price_regime_label} regime; retaining {selected_label} candidate after guardrails."
    return {
        "recommended_candidate_label": selected_label,
        "recommended_selector_reason": reason,
    }


def _infer_contract_method(signals: dict[str, Any]) -> str:
    if signals["conflicting"]:
        return "conflicting"
    if signals["deep_discount"]:
        return "two_stage_or_separated"
    if "direct_negotiated" in signals["signals"]:
        return "direct_negotiated"
    if "negotiated" in signals["signals"]:
        return "negotiated"
    if signals["floor_bound"]:
        return "price_competitive"
    return "unknown"


def _infer_award_method(signals: dict[str, Any]) -> str:
    if signals["conflicting"]:
        return "review_required"
    if signals["near_100"]:
        return "negotiated_or_direct"
    if signals["deep_discount"]:
        return "separated_price_competition"
    if signals["floor_bound"]:
        return "price_competition"
    return "unknown"


def _infer_evaluation_method(signals: dict[str, Any]) -> str:
    if "two_stage_or_separated" in signals["signals"]:
        return "two_stage_or_spec_price"
    if signals["near_100"]:
        return "proposal_or_direct"
    if signals["floor_bound"]:
        return "price_or_qualification"
    return "unknown"


def _infer_price_submission_mode(signals: dict[str, Any]) -> str:
    if "two_stage_or_separated" in signals["signals"]:
        return "separated"
    if signals["near_100"]:
        return "negotiated"
    if signals["floor_bound"]:
        return "standard_price"
    return "unknown"


def _infer_service_type(text: str) -> str | None:
    if _contains_any(text, ("해양", "항만", "수심측량", "해저지형", "바다숲", "인공어초")):
        return "marine_engineering"
    if _contains_any(text, ("보험", "차량", "버스", "수학여행")):
        return "transport_or_insurance"
    if _contains_any(text, ("시스템", "소프트웨어", "플랫폼", "클라우드", "유지관리")):
        return "software_or_operations"
    return None


def _amount_bucket(budget: float) -> str:
    value = float(budget or 0.0)
    if value <= 0:
        return "unknown"
    if value < 10_000_000:
        return "lt_10m"
    if value < 30_000_000:
        return "10m_30m"
    if value < 100_000_000:
        return "30m_100m"
    if value < 300_000_000:
        return "100m_300m"
    if value < 1_000_000_000:
        return "300m_1b"
    return "gte_1b"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
