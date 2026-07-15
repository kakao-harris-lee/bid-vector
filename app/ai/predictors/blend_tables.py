"""Declarative blend-weight / high-rate-tail rule tables for the historical predictor.

Single source of truth for the numeric constants that previously lived inline
inside ``select_competitive_base_rate`` (per group/category/sample-tier weighted
blends) and ``apply_high_rate_distribution_adjustment`` (per-group tail triggers
and lift blends). Lifting the literals here keeps them from drifting apart and
lets the routing code read as data.

⚠ FLOAT-EXACT CONTRACT: these tables carry ONLY the numeric weights/thresholds.
The call sites keep their original ``(a * w0) + (b * w1) + (c * w2)`` arithmetic
shape verbatim — operand identity and evaluation/association order are unchanged,
so the moved literals produce byte-identical floats (golden diff 0).

stdlib-only (no ``app`` imports) so ``historical`` can import it without an import
cycle, mirroring the ``rate_band_spec`` / ``procurement_band_rules`` siblings.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# select_competitive_base_rate — sample-size tiers + per-tier blend weights.
# The sample-size boundaries are a DESCENDING band: deep (>=10) > moderate (>=5)
# > sparse (>=2) > minimal (<2). The >=10 tier still sub-dispatches on
# group/category at the call site, so the boundaries are named constants rather
# than a collapsed lookup — the control flow stays identical.
# ---------------------------------------------------------------------------
COMPETITIVE_SAMPLE_TIER_DEEP = 10
COMPETITIVE_SAMPLE_TIER_MODERATE = 5
COMPETITIVE_SAMPLE_TIER_SPARSE = 2


@dataclass(frozen=True)
class CompetitiveBlend:
    """Three-term weighted-blend coefficients for one base-rate tier.

    The three weights apply, IN ORDER, to the three operands documented at the
    call site (they differ per tier — recent/median/heuristic, quantile/median/
    heuristic, median/recent/mean, ...). Only the literal weights live here; the
    call site keeps its ``(a * w0) + (b * w1) + (c * w2)`` form verbatim.
    """

    w0: float
    w1: float
    w2: float


@dataclass(frozen=True)
class TwoTermBlend:
    """Two-term weighted-blend coefficients (minimal-sample fallback)."""

    w0: float
    w1: float


# operands documented per row (call-site order is preserved verbatim):
CONSTRUCTION_GROUP_DEEP_BLEND = CompetitiveBlend(
    0.6, 0.3, 0.1
)  # recent, median, heuristic
SERVICE_GROUP_DEEP_BLEND = CompetitiveBlend(
    0.5, 0.35, 0.15
)  # quantile, median, heuristic
NOGROUP_DEEP_FALLBACK_BLEND = CompetitiveBlend(0.7, 0.2, 0.1)  # median, recent, mean
MODERATE_SAMPLE_BLEND = CompetitiveBlend(0.55, 0.35, 0.10)  # median, mean, heuristic
SPARSE_SAMPLE_BLEND = CompetitiveBlend(0.45, 0.35, 0.20)  # median, mean, heuristic
MINIMAL_SAMPLE_BLEND = TwoTermBlend(0.55, 0.45)  # mean, heuristic


# ---------------------------------------------------------------------------
# apply_high_rate_distribution_adjustment — enablement gates, excluded bands,
# and per-group tail-lift rules. Each group's OR-trigger sub-conditions are
# characterization-locked in isolation, so the call site keeps every sub-branch
# spelled out; only the thresholds/weights are lifted here.
# ---------------------------------------------------------------------------
HIGH_RATE_MIN_SAMPLE_SIZE = 20
HIGH_RATE_MIN_RECENT_SAMPLE_SIZE = 8
HIGH_RATE_MIN_LIFT_EPSILON = 1e-9

# Bands whose target is already governed by their own floor/ceiling: the tail
# lift is skipped for them entirely (verbatim membership set).
HIGH_RATE_EXCLUDED_BANDS = frozenset(
    {"service_price_competitive", "goods_deep_discount", "goods_price_competitive"}
)

# service_high_negotiated short-circuit: lift the base straight to 100%.
SERVICE_HIGH_NEGOTIATED_TARGET_RATE = 1.0
SERVICE_HIGH_NEGOTIATED_REASON = "service_high_negotiated"

CONSTRUCTION_SMALL_BUDGET_REASON = "construction_small_budget_high_rate_target"
PRESERVE_HISTORICAL_COMPONENT_REASON = "preserve_historical_component"


@dataclass(frozen=True)
class GoodsHighRateTailRule:
    """goods 그룹 tail lift — OR-triggered share/rate thresholds + blend weights.

    Trigger (OR): ``recent_ge_95_share >= recent_ge_95_share_min`` OR
    ``recent_ge_98_share >= recent_ge_98_share_min`` OR
    ``recent_upper_rate >= recent_upper_rate_min``.
    Target blend operands (call-site order): candidate, ``max(recent_median,
    upper)``, high_rate_anchor.
    """

    recent_ge_95_share_min: float
    recent_ge_98_share_min: float
    recent_upper_rate_min: float
    candidate_weight: float
    mid_weight: float
    anchor_weight: float
    reason: str


@dataclass(frozen=True)
class ServiceHighRateTailRule:
    """service 그룹 tail lift — median gate AND OR-triggered share thresholds.

    Trigger: ``recent_median >= recent_median_min`` AND
    (``recent_ge_93_share >= recent_ge_93_share_min`` OR
    ``recent_ge_95_share >= recent_ge_95_share_min``).
    ``tail_anchor = min(high_rate_anchor, recent_median + tail_anchor_offset)``.
    Target blend operands (call-site order): candidate, recent_median, tail_anchor.
    """

    recent_median_min: float
    recent_ge_93_share_min: float
    recent_ge_95_share_min: float
    tail_anchor_offset: float
    candidate_weight: float
    recent_median_weight: float
    tail_anchor_weight: float
    reason: str


GOODS_HIGH_RATE_TAIL_RULE = GoodsHighRateTailRule(
    recent_ge_95_share_min=0.35,
    recent_ge_98_share_min=0.20,
    recent_upper_rate_min=0.97,
    candidate_weight=0.20,
    mid_weight=0.25,
    anchor_weight=0.55,
    reason="goods_recent_high_rate_tail",
)

SERVICE_HIGH_RATE_TAIL_RULE = ServiceHighRateTailRule(
    recent_median_min=0.93,
    recent_ge_93_share_min=0.30,
    recent_ge_95_share_min=0.20,
    tail_anchor_offset=0.02,
    candidate_weight=0.25,
    recent_median_weight=0.65,
    tail_anchor_weight=0.10,
    reason="service_recent_high_rate_tail",
)
