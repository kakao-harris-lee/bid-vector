"""Declarative procurement rate-band spec table + thin interpreters.

Single source of truth for the five procurement bidding bands. The same 5-way
switch previously lived verbatim in five places (base clamp, candidate clamp,
the inline group-branch clamp inside ``select_competitive_base_rate``, and both
explanation builders). Consolidating the numeric bounds and the Korean band
clause here keeps them from drifting apart.

stdlib-only (no ``app`` imports) so both ``historical`` and ``ensemble`` can
import it without introducing an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class RateBandSpec:
    """One procurement band's clamp bounds and shared explanation clause.

    Each band contributes exactly one base bound — ``base_floor`` (``max``,
    negotiated bands) XOR ``base_ceiling`` (``min``, competitive/discount bands);
    the other is ``None``. The candidate tuples hold per-scenario
    (conservative, base, aggressive) bounds with the same floor-XOR-ceiling
    property per position, so clamp order is unambiguous.
    """

    base_floor: Optional[float]
    base_ceiling: Optional[float]
    candidate_floors: tuple[Optional[float], Optional[float], Optional[float]]
    candidate_ceilings: tuple[Optional[float], Optional[float], Optional[float]]
    explanation_clause: str


RATE_BAND_SPECS: Mapping[str, RateBandSpec] = {
    "service_high_negotiated": RateBandSpec(
        base_floor=1.0,
        base_ceiling=None,
        candidate_floors=(0.98, 1.0, 1.0),
        candidate_ceilings=(None, None, None),
        explanation_clause="협상/위탁형 용역으로 보고 100% 근접 목표율을 적용했습니다",
    ),
    "service_direct_negotiated": RateBandSpec(
        base_floor=0.95,
        base_ceiling=None,
        candidate_floors=(0.93, 0.95, 0.97),
        candidate_ceilings=(None, None, None),
        explanation_clause="수의시담/수의계약형 용역으로 보고 95% 이상 목표율을 적용했습니다",
    ),
    "service_price_competitive": RateBandSpec(
        base_floor=None,
        base_ceiling=0.90,
        candidate_floors=(None, None, None),
        candidate_ceilings=(0.90, 0.90, 0.90),
        explanation_clause="가격경쟁형 용역으로 보고 90% 상한 목표율을 적용했습니다",
    ),
    "goods_deep_discount": RateBandSpec(
        base_floor=None,
        base_ceiling=0.78,
        candidate_floors=(None, None, None),
        candidate_ceilings=(0.76, 0.78, 0.84),
        explanation_clause="2단계 급식/농산물형 물품으로 보고 78% 기준 저가 목표율을 적용했습니다",
    ),
    "goods_price_competitive": RateBandSpec(
        base_floor=None,
        base_ceiling=0.90,
        candidate_floors=(None, None, None),
        candidate_ceilings=(0.88, 0.90, 0.91),
        explanation_clause="견적/2단계/구매설치형 물품으로 보고 90% 기준, 91% 상한 목표율을 적용했습니다",
    ),
}

# The construction/service group branches (sample_size>=10) inside
# ``select_competitive_base_rate`` apply ONLY these two bands inline — a
# deliberate PARTIAL application (service_direct_negotiated / goods_* bands are
# skipped there, unlike the full ``apply_procurement_rate_band``). Preserved
# verbatim; guarded by ``test_group_branch_applies_only_two_of_five_bands``.
GROUP_BRANCH_BASE_BANDS: frozenset[str] = frozenset(
    {"service_high_negotiated", "service_price_competitive"}
)


def apply_band_to_base(
    base_rate: float,
    rate_band: Optional[str],
    *,
    only_bands: Optional[frozenset[str]] = None,
) -> float:
    """Clamp a single base rate to its band floor/ceiling.

    ``only_bands`` restricts application to a subset of band keys (used by the
    group-branch partial application); unlisted bands pass through unchanged.
    """
    spec = RATE_BAND_SPECS.get(rate_band) if rate_band is not None else None
    if spec is None:
        return base_rate
    if only_bands is not None and rate_band not in only_bands:
        return base_rate
    if spec.base_floor is not None:
        return max(base_rate, spec.base_floor)
    if spec.base_ceiling is not None:
        return min(base_rate, spec.base_ceiling)
    return base_rate


def apply_band_to_candidates(
    *,
    conservative_rate: float,
    base_rate: float,
    aggressive_rate: float,
    rate_band: Optional[str],
) -> tuple[float, float, float]:
    """Clamp (conservative, base, aggressive) scenarios to the band bounds."""
    spec = RATE_BAND_SPECS.get(rate_band) if rate_band is not None else None
    if spec is None:
        return conservative_rate, base_rate, aggressive_rate
    clamped: list[float] = []
    triple = (conservative_rate, base_rate, aggressive_rate)
    for value, floor, ceiling in zip(triple, spec.candidate_floors, spec.candidate_ceilings):
        if floor is not None:
            value = max(value, floor)
        if ceiling is not None:
            value = min(value, ceiling)
        clamped.append(value)
    return clamped[0], clamped[1], clamped[2]


def band_explanation_clause(rate_band: Optional[str]) -> Optional[str]:
    """Return the shared Korean band clause (no leading space / trailing period)."""
    spec = RATE_BAND_SPECS.get(rate_band) if rate_band is not None else None
    return spec.explanation_clause if spec is not None else None
