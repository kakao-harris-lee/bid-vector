"""Guardrail application — clamp candidates/base to the resolved band.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). Every function is relocated verbatim from the original module,
so the candidate/base clamp arithmetic, the construction-anchor stance bridge,
the auditable guardrail reason, and the bid-price granularity rounding are
unchanged. ``_resolve_guardrail_context``/``GuardrailContext`` are imported from
the sibling context module; all other call sites keep their original form.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict

from app.ai import guardrail_core
from app.ai.guardrail_core import GuardrailConfig
from app.ai.price_prediction.guardrails_context import (
    GuardrailContext,
    _floor_guardrail_label,
    _resolve_guardrail_context,
)
from app.core.config import settings


def _apply_prediction_guardrails(
    prediction: Dict[str, Any],
    *,
    budget: float,
    category: str | None,
    business_group: str | None = None,
    legal_floor_bid_rate: float | None = None,
    agency_name: str | None = None,
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
) -> Dict[str, Any]:
    """Apply category/group/agency bid-rate guardrails after all statistical adjustments."""
    guarded_prediction = dict(prediction)
    guardrail_context = _resolve_guardrail_context(
        budget=budget,
        category=category,
        business_group=business_group,
        legal_floor_bid_rate=legal_floor_bid_rate,
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
    _apply_guardrail_metadata(guarded_prediction, guardrail_context)

    if (
        guardrail_context.floor_bid_rate is None
        and guardrail_context.ceiling_bid_rate is None
    ) or budget <= 0:
        return guarded_prediction

    floor_applied_labels, ceiling_applied_labels = _clamp_prediction_to_band(
        guarded_prediction,
        prediction,
        budget=budget,
        context=guardrail_context,
    )

    if guardrail_context.construction_scenario_anchor is not None:
        _annotate_construction_scenario_anchor(guarded_prediction, guardrail_context)

    if not floor_applied_labels and not ceiling_applied_labels:
        return guarded_prediction

    _mark_guardrail_application(
        guarded_prediction,
        context=guardrail_context,
        floor_labels=floor_applied_labels,
        ceiling_labels=ceiling_applied_labels,
    )
    return guarded_prediction


def _clamp_prediction_to_band(
    guarded_prediction: Dict[str, Any],
    prediction: Dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[list[str], list[str]]:
    """후보(있으면)나 base 예측을 밴드로 clamp하고 floor/ceiling 적용 라벨을 돌려준다.

    원 분기(후보 우선, 없으면 base)의 순서·경계를 그대로 유지한다. 후보 경로는
    _guard_bid_rate_candidates가 산출한 라벨을, base 경로는 _apply_base_prediction_guardrails
    반환 라벨을 그대로 넘긴다 — clamp 산식은 어느 쪽도 변경하지 않는다.
    """
    guarded_candidates, floor_applied_labels, ceiling_applied_labels = (
        _guard_bid_rate_candidates(prediction, budget=budget, context=context)
    )
    if guarded_candidates:
        _apply_guarded_candidate_prediction(guarded_prediction, guarded_candidates)
        return floor_applied_labels, ceiling_applied_labels
    return _apply_base_prediction_guardrails(
        guarded_prediction,
        prediction,
        budget=budget,
        context=context,
    )


def _annotate_construction_scenario_anchor(
    guarded_prediction: Dict[str, Any],
    context: GuardrailContext,
) -> None:
    """공사 floor 앵커 적용을 설명 문구에 정직하게 남긴다(정직 명세 §2).

    낙찰 확률/단정 표현 없이, 앵커가 '과거 실낙찰 초과분 백분위수(historical percentile)
    기반'임과 공격 시나리오의 낙하(실격) 위험을 드러낸다.
    """
    anchor = context.construction_scenario_anchor or {}
    floor = context.floor_bid_rate
    if floor is None:
        return
    aggressive_rate = float(anchor.get("aggressive", floor))
    note = (
        f"공사 법정 낙찰하한 {floor:.3%} 위에 과거 실낙찰 초과분 백분위수(p25/p50/p75, "
        f"표본 기반 캘리브레이션)로 공격/추천/안전 시나리오를 앵커했습니다. 공격 시나리오"
        f"({aggressive_rate:.3%})는 최저적격 경쟁 타깃으로, 실현 낙찰하한이 더 높으면 "
        f"낙(실격) 위험이 있습니다."
    )
    guarded_prediction["explanation"] = _append_explanation_note(
        str(guarded_prediction.get("explanation", "") or ""),
        note,
    )


def _apply_guardrail_metadata(
    guarded_prediction: Dict[str, Any],
    context: GuardrailContext,
) -> None:
    guarded_prediction["guardrail_applied"] = False
    guarded_prediction["guardrail_reason"] = None
    guarded_prediction["legal_floor_bid_rate"] = (
        round(context.normalized_legal_floor_bid_rate, 6)
        if context.normalized_legal_floor_bid_rate is not None
        else None
    )
    guarded_prediction["floor_guardrail_source"] = context.floor_guardrail_source
    guarded_prediction["floor_bid_rate"] = (
        round(context.floor_bid_rate, 6) if context.floor_bid_rate is not None else None
    )
    guarded_prediction["floor_price"] = context.floor_price
    guarded_prediction["floor_safety_margin_rate"] = (
        round(max(0.0, context.safe_floor_bid_rate - context.floor_bid_rate), 6)
        if context.safe_floor_bid_rate is not None and context.floor_bid_rate is not None
        else None
    )
    guarded_prediction["safe_floor_bid_rate"] = (
        round(context.safe_floor_bid_rate, 6)
        if context.safe_floor_bid_rate is not None
        else None
    )
    guarded_prediction["safe_floor_price"] = context.safe_floor_price
    guarded_prediction["ceiling_bid_rate"] = (
        round(context.ceiling_bid_rate, 6)
        if context.ceiling_bid_rate is not None
        else None
    )
    guarded_prediction["ceiling_price"] = context.ceiling_price
    guarded_prediction["floor_from_agency"] = bool(context.floor_from_agency)
    guarded_prediction["ceiling_from_agency"] = bool(context.ceiling_from_agency)


def _guard_bid_rate_candidates(
    prediction: Dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    guarded_candidates: list[dict[str, Any]] = []
    floor_applied_labels: list[str] = []
    ceiling_applied_labels: list[str] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        guarded_candidate, floor_applied, ceiling_applied = _guard_candidate_bid_rate(
            candidate,
            budget=budget,
            context=context,
        )
        if floor_applied:
            floor_applied_labels.append(str(candidate.get("label") or "base"))
        if ceiling_applied:
            ceiling_applied_labels.append(str(candidate.get("label") or "base"))
        guarded_candidates.append(guarded_candidate)
    return guarded_candidates, floor_applied_labels, ceiling_applied_labels


# 후보 taxonomy(통계 spread)와 시나리오 stance(투찰 전략)는 축이 다르다: 후보
# conservative=최저 추정가(낮은 율)=최경쟁=공격 stance, base=추천, aggressive=최고 추정가
# (높은 율)=낙하 안전=안전 stance. 값 기준(offset 오름차순)으로도 일치한다. 룩업으로
# 브리지를 선언한다(§4.5.2). 앵커가 켜졌을 때만 사용된다.
_CANDIDATE_LABEL_TO_SCENARIO_STANCE = {
    "conservative": "aggressive",
    "base": "recommended",
    "aggressive": "safe",
}


def _resolve_candidate_target_rate(
    candidate: dict[str, Any],
    *,
    context: GuardrailContext,
    original_bid_rate: float,
) -> float:
    """공사 앵커가 켜지면 후보 stance 의 floor-앵커 rate 로, 아니면 원래 rate 로 target 결정."""
    anchor = context.construction_scenario_anchor
    if not anchor:
        return original_bid_rate
    stance = _CANDIDATE_LABEL_TO_SCENARIO_STANCE.get(str(candidate.get("label") or "base"))
    if stance is None:
        return original_bid_rate
    anchored_rate = anchor.get(stance)
    return float(anchored_rate) if anchored_rate is not None else original_bid_rate


def _guard_candidate_bid_rate(
    candidate: dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[dict[str, Any], bool, bool]:
    original_bid_rate = float(candidate.get("bid_rate", 0.0) or 0.0)
    # 앵커 target(공사 floor 앵커) 또는 원래 rate 를 기존 guardrail clamp 에 그대로 통과시킨다
    # (후보 clamp 재사용 — 병렬 경로 없음). 결과는 항상 [safe_floor, ceiling] 안이다.
    target_bid_rate = _resolve_candidate_target_rate(
        candidate,
        context=context,
        original_bid_rate=original_bid_rate,
    )
    guarded_bid_rate = _clamp_rate_to_guardrails(
        target_bid_rate,
        floor_bid_rate=context.safe_floor_bid_rate,
        ceiling_bid_rate=context.ceiling_bid_rate,
    )
    guardrail_changed = abs(guarded_bid_rate - original_bid_rate) > 1e-9
    pre_guardrail_price = float(candidate.get("predicted_price", 0.0) or 0.0)
    return (
        {
            **candidate,
            "bid_rate": round(guarded_bid_rate, 4),
            "predicted_price": round(float(budget) * guarded_bid_rate, 2),
            "guardrail_applied": guardrail_changed,
            "pre_guardrail_bid_rate": round(original_bid_rate, 4)
            if guardrail_changed
            else None,
            "pre_guardrail_price": round(pre_guardrail_price, 2)
            if guardrail_changed
            else None,
        },
        context.safe_floor_bid_rate is not None
        and guarded_bid_rate > original_bid_rate + 1e-9,
        context.ceiling_bid_rate is not None
        and guarded_bid_rate < original_bid_rate - 1e-9,
    )


def _apply_guarded_candidate_prediction(
    guarded_prediction: Dict[str, Any],
    guarded_candidates: list[dict[str, Any]],
) -> None:
    base_candidate = next(
        (candidate for candidate in guarded_candidates if candidate.get("label") == "base"),
        guarded_candidates[0],
    )
    guarded_prediction["bid_rate_candidates"] = guarded_candidates
    guarded_prediction["predicted_bid_rate"] = round(
        float(base_candidate.get("bid_rate", 0.0) or 0.0),
        4,
    )
    guarded_prediction["predicted_price"] = round(
        float(base_candidate.get("predicted_price", 0.0) or 0.0),
        2,
    )
    guarded_prediction["price_range_min"] = min(
        candidate["predicted_price"] for candidate in guarded_candidates
    )
    guarded_prediction["price_range_max"] = max(
        candidate["predicted_price"] for candidate in guarded_candidates
    )


def _apply_base_prediction_guardrails(
    guarded_prediction: Dict[str, Any],
    prediction: Dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[list[str], list[str]]:
    original_bid_rate = float(prediction.get("predicted_bid_rate", 0.0) or 0.0)
    guarded_bid_rate = _clamp_rate_to_guardrails(
        original_bid_rate,
        floor_bid_rate=context.safe_floor_bid_rate,
        ceiling_bid_rate=context.ceiling_bid_rate,
    )
    guarded_prediction["predicted_bid_rate"] = round(guarded_bid_rate, 4)
    guarded_prediction["predicted_price"] = round(float(budget) * guarded_bid_rate, 2)
    price_candidates = [
        guarded_prediction["predicted_price"],
        _clamp_price_to_guardrails(
            float(prediction.get("price_range_min", 0.0) or 0.0),
            context.floor_price,
            context.ceiling_price,
        ),
        _clamp_price_to_guardrails(
            float(prediction.get("price_range_max", 0.0) or 0.0),
            context.floor_price,
            context.ceiling_price,
        ),
    ]
    guarded_prediction["price_range_min"] = min(price_candidates)
    guarded_prediction["price_range_max"] = max(price_candidates)
    return (
        ["base"]
        if context.safe_floor_bid_rate is not None
        and guarded_bid_rate > original_bid_rate + 1e-9
        else [],
        ["base"]
        if context.ceiling_bid_rate is not None
        and guarded_bid_rate < original_bid_rate - 1e-9
        else [],
    )


def _mark_guardrail_application(
    guarded_prediction: Dict[str, Any],
    *,
    context: GuardrailContext,
    floor_labels: list[str],
    ceiling_labels: list[str],
) -> None:
    guardrail_reason = _build_guardrail_reason(
        floor_bid_rate=context.floor_bid_rate,
        safe_floor_bid_rate=context.safe_floor_bid_rate,
        floor_guardrail_source=context.floor_guardrail_source,
        ceiling_bid_rate=context.ceiling_bid_rate,
        floor_labels=floor_labels,
        ceiling_labels=ceiling_labels,
        floor_from_agency=context.floor_from_agency,
        ceiling_from_agency=context.ceiling_from_agency,
        agency_name=context.agency_name,
    )
    guarded_prediction["guardrail_applied"] = True
    guarded_prediction["guardrail_reason"] = guardrail_reason
    guarded_prediction["model_version"] = _append_model_version_suffix(
        str(guarded_prediction.get("model_version", "v1.0")),
        "guardrail",
    )
    guarded_prediction["explanation"] = _append_explanation_note(
        str(guarded_prediction.get("explanation", "") or ""),
        guardrail_reason,
    )


def _apply_bid_price_granularity(prediction: Dict[str, Any], *, budget: float) -> Dict[str, Any]:
    """Round final bid prices to an operator-facing currency unit."""
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.apply_bid_price_granularity(prediction, budget=budget, config=config)


def _clamp_rate_to_guardrails(
    bid_rate: float,
    *,
    floor_bid_rate: float | None,
    ceiling_bid_rate: float | None,
) -> float:
    guarded_bid_rate = float(bid_rate or 0.0)
    if floor_bid_rate is not None:
        guarded_bid_rate = max(guarded_bid_rate, floor_bid_rate)
    if ceiling_bid_rate is not None:
        guarded_bid_rate = min(guarded_bid_rate, ceiling_bid_rate)
    return guarded_bid_rate


def _clamp_price_to_guardrails(price: float, floor_price: float | None, ceiling_price: float | None) -> float:
    guarded_price = float(price or 0.0)
    if floor_price is not None:
        guarded_price = max(guarded_price, floor_price)
    if ceiling_price is not None:
        guarded_price = min(guarded_price, ceiling_price)
    return round(guarded_price, 2)


def _build_guardrail_reason(
    *,
    floor_bid_rate: float | None,
    safe_floor_bid_rate: float | None,
    floor_guardrail_source: str | None,
    ceiling_bid_rate: float | None,
    floor_labels: list[str],
    ceiling_labels: list[str],
    floor_from_agency: bool = False,
    ceiling_from_agency: bool = False,
    agency_name: str | None = None,
) -> str:
    """Build an auditable guardrail reason.

    Each edge is attributed to its ACTUAL source: when the binding floor/ceiling is the
    agency-keyed value (it tightened the band beyond the category/group rate), the edge
    reads "발주처 … 기관별 투찰률 밴드"; otherwise it stays "업종별"/"공고별 법정".
    """
    agency_label = str(agency_name or "").strip()
    agency_segment = f"발주처{f' {agency_label}' if agency_label else ''} 기관별 투찰률 밴드의"
    reasons: list[str] = []
    if floor_bid_rate is not None and floor_labels:
        unique_labels = list(dict.fromkeys(floor_labels))
        if floor_from_agency:
            floor_label = f"{agency_segment} 최소 투찰률"
        else:
            floor_label = _floor_guardrail_label(floor_guardrail_source)
        if safe_floor_bid_rate is not None and safe_floor_bid_rate > floor_bid_rate + 1e-9:
            reasons.append(
                f"{floor_label} {floor_bid_rate:.3%}에 안전마진 "
                f"{(safe_floor_bid_rate - floor_bid_rate) * 100:.2f}pp를 더한 "
                f"{safe_floor_bid_rate:.3%} 가드레일을 적용해 "
                f"{', '.join(unique_labels)} 시나리오를 상향 보정했습니다."
            )
        else:
            reasons.append(
                f"{floor_label} {floor_bid_rate:.3%} 가드레일을 적용해 "
                f"{', '.join(unique_labels)} 시나리오를 상향 보정했습니다."
            )
    if ceiling_bid_rate is not None and ceiling_labels:
        unique_labels = list(dict.fromkeys(ceiling_labels))
        ceiling_label = (
            f"{agency_segment} 최대 투찰률" if ceiling_from_agency else "업종별 최대 투찰률"
        )
        reasons.append(
            f"{ceiling_label} {ceiling_bid_rate:.2%} 가드레일을 적용해 "
            f"{', '.join(unique_labels)} 시나리오를 하향 보정했습니다."
        )
    return " ".join(reasons)


def _append_model_version_suffix(model_version: str, suffix: str) -> str:
    """Append a model-version suffix once without duplicating it."""
    token = f"+{suffix}"
    if token in model_version:
        return model_version
    if suffix == "guardrail" and model_version.endswith("+feedback"):
        return f"{model_version[:-len('+feedback')]}{token}+feedback"
    return f"{model_version}{token}"


def _append_explanation_note(explanation: str, note: str) -> str:
    """Append a readable note to an explanation sentence."""
    cleaned_explanation = str(explanation or "").strip()
    cleaned_note = str(note or "").strip()
    if not cleaned_note:
        return cleaned_explanation
    if not cleaned_explanation:
        return cleaned_note
    if cleaned_explanation.endswith("."):
        return f"{cleaned_explanation} {cleaned_note}"
    return f"{cleaned_explanation} {cleaned_note}"
