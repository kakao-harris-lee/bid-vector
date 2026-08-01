"""Procurement rate-band inference from notice text."""

from __future__ import annotations

from app.ai.predictors.historical.statistics import normalize_category_key
from app.ai.predictors.procurement_band_rules import (
    SERVICE_BAND_RULES,
    BandKeywordRule,
    build_goods_band_rules,
    resolve_band,
    title_line,
)
from app.ai.predictors.rate_band_spec import (
    apply_band_to_base,
    apply_band_to_candidates,
)


def apply_procurement_rate_band(base_rate: float, *, category: str, description: str) -> float:
    """Apply procurement subtype bid-rate bands inferred from notice text."""
    rate_band = resolve_procurement_rate_band(category=category, description=description)
    return apply_band_to_base(base_rate, rate_band)


def apply_procurement_candidate_band(
    *,
    conservative_rate: float,
    base_rate: float,
    aggressive_rate: float,
    rate_band: str | None,
) -> tuple[float, float, float]:
    """Keep all scenarios consistent with the inferred procurement band."""
    return apply_band_to_candidates(
        conservative_rate=conservative_rate,
        base_rate=base_rate,
        aggressive_rate=aggressive_rate,
        rate_band=rate_band,
    )


def resolve_procurement_rate_band(*, category: str, description: str) -> str | None:
    """Infer broad procurement bidding bands from the notice text."""
    normalized_category = normalize_category_key(category)
    normalized_text = str(description or "").strip().lower()
    if not normalized_text:
        return None

    if normalized_category == "goods":
        return _resolve_goods_procurement_rate_band(normalized_text)

    if normalized_category not in {"service", "technical-service", "general-service", "software"}:
        return None
    return _resolve_service_procurement_rate_band(normalized_text)


def _resolve_rate_band(
    rules: tuple[BandKeywordRule, ...], normalized_text: str
) -> str | None:
    """Run a rule table against the notice text, deriving the title-scoped span.

    The single interpreter behind the two named resolvers below — they differ
    only in the rule table, which stays declared in ``procurement_band_rules``
    (§4.5-3). First-match priority and the title/body scope split live in
    ``resolve_band`` and are untouched here.
    """
    return resolve_band(
        rules,
        text=normalized_text,
        title=title_line(normalized_text),
    )


def _resolve_goods_procurement_rate_band(normalized_text: str) -> str | None:
    return _resolve_rate_band(GOODS_BAND_RULES, normalized_text)


def _resolve_service_procurement_rate_band(normalized_text: str) -> str | None:
    return _resolve_rate_band(SERVICE_BAND_RULES, normalized_text)


def _is_goods_deep_discount_notice(normalized_text: str) -> bool:
    """Detect narrow goods notices that settle below the usual 84~90% goods band."""
    has_two_stage = any(keyword in normalized_text for keyword in ("2단계", "규격·가격", "규격 가격", "규격가격"))
    has_food_purchase = any(keyword in normalized_text for keyword in ("급식", "농산물"))
    return has_two_stage and has_food_purchase


def _is_goods_narrow_control_price_competitive(normalized_text: str) -> bool:
    """Detect low-rate control-equipment goods without catching high-rate control panels."""
    if "(계측제어)" not in normalized_text:
        return False
    high_rate_control_terms = (
        "관급자재",
        "프로세스",
        "계장",
    )
    return not any(keyword in normalized_text for keyword in high_rate_control_terms)


# Goods rule table weaving the two named predicates (defined above) into their
# leading order slots ahead of the verbatim price-competitive keyword rule.
GOODS_BAND_RULES = build_goods_band_rules(
    deep_discount_predicate=_is_goods_deep_discount_notice,
    narrow_control_predicate=_is_goods_narrow_control_price_competitive,
)
