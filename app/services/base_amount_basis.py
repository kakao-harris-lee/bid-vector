"""Provenance classification for ``HistoricalData.base_amount``.

66% of stored ``base_amount`` values are NOT the real 기초금액 (integer 원화).
They are derived/polluted values — most commonly ``winning_amount ÷ winning_rate``
역산 (which equals 예정가-basis, e.g. ``43,996,200 ÷ 0.88035 = 49,975,805.0775``),
or ``VAT-inclusive ÷ 1.1`` divisions. This pollution silently invalidates 밴드
캘리브레이션 and 백테스트, so we tag each row's provenance instead of mutating the
original value (정직 명세 §2 — 원본 불변, 추정은 추정으로 표기).

This module is pure (no I/O): the backfill script (``scripts/backfill_base_amount_basis.py``)
does the DB reads/writes and calls these functions. Classification rules are a
declarative first-match table (§4.5) — add a case = add a row, not a branch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

# Basis verdicts. ``CLEAN`` is the ONLY value a feedback/calibration loop may
# trust as a real 기초금액 (see HistoricalData column notes + config warnings).
BASIS_CLEAN = "clean"
BASIS_DERIVED_YEGA = "derived-yega"  # win ÷ winning_rate 역산 (예정가-basis)
BASIS_DERIVED_VAT = "derived-vat"  # VAT-inclusive ÷ 1.1 파생
BASIS_SUSPECT_FRACTIONAL = "suspect-fractional"  # 소수/누락/미상 — 신뢰 불가 버킷

ALL_BASES = (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_DERIVED_VAT,
    BASIS_SUSPECT_FRACTIONAL,
)

# Match tolerances (declared, not inlined — §4.5).
_INTEGER_TOLERANCE = 1e-6  # a real 기초금액 (원화) is an exact float integer
_YEGA_TOLERANCE = 1.0  # |base × winning_rate − winning_amount| < 1원
_VAT_TOLERANCE = 0.01  # |base × 1.1 − round(base × 1.1)| < 0.01
_VAT_MULTIPLIER = 1.1

# 복수예비가격: KONEPS always publishes 15 reserve prices straddling 기초금액 by
# roughly ±2~3%, so their midpoint recovers the base for a polluted row.
RESERVE_PRICE_COUNT = 15


def _safe_float(value: Any) -> float | None:
    """Coerce to float, or None when not a finite number."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):  # NaN/inf
        return None
    return result


@dataclass(frozen=True)
class _BasisContext:
    """Normalized inputs for the classification rule table."""

    base: float
    winning_amount: float
    winning_rate: float


def _is_clean_integer(ctx: _BasisContext) -> bool:
    """A trustworthy 기초금액 is a strictly positive, exact integer 원화 value."""
    base = ctx.base
    if base <= 0:
        return False
    return abs(base - round(base)) < _INTEGER_TOLERANCE


def _is_derived_yega(ctx: _BasisContext) -> bool:
    """base == winning_amount ÷ winning_rate ⇒ base × winning_rate == winning_amount."""
    if ctx.winning_amount <= 0 or ctx.winning_rate <= 0:
        return False
    return abs(ctx.base * ctx.winning_rate - ctx.winning_amount) < _YEGA_TOLERANCE


def _is_derived_vat(ctx: _BasisContext) -> bool:
    """base == VAT-inclusive ÷ 1.1 ⇒ base × 1.1 lands (near) an integer 원화."""
    if ctx.base <= 0:
        return False
    scaled = ctx.base * _VAT_MULTIPLIER
    return abs(scaled - round(scaled)) < _VAT_TOLERANCE


# Declarative first-match rule table: the first predicate that matches wins,
# otherwise the value falls through to BASIS_SUSPECT_FRACTIONAL. Order matters —
# 'clean' integers are claimed before the derived checks, and 예정가 역산 is
# preferred over the VAT check when both could apply.
_CLASSIFICATION_RULES: tuple[tuple[str, Callable[[_BasisContext], bool]], ...] = (
    (BASIS_CLEAN, _is_clean_integer),
    (BASIS_DERIVED_YEGA, _is_derived_yega),
    (BASIS_DERIVED_VAT, _is_derived_vat),
)


def classify_base_basis(
    base_amount: Any,
    winning_amount: Any = None,
    winning_rate: Any = None,
) -> str:
    """Classify the provenance of a stored ``base_amount`` (pure).

    Returns one of ``ALL_BASES``. Missing / non-positive / non-numeric ``base``
    is NOT clean — it lands in ``suspect-fractional`` (the untrusted bucket) so a
    calibration loop filtered to ``clean`` never picks up 0/누락 rows.
    """
    base = _safe_float(base_amount)
    if base is None:
        return BASIS_SUSPECT_FRACTIONAL
    ctx = _BasisContext(
        base=base,
        winning_amount=_safe_float(winning_amount) or 0.0,
        winning_rate=_safe_float(winning_rate) or 0.0,
    )
    for label, predicate in _CLASSIFICATION_RULES:
        if predicate(ctx):
            return label
    return BASIS_SUSPECT_FRACTIONAL


def _coerce_reserve_prices(raw_value: Any) -> list[float]:
    """Parse the ``reserve_prices`` Text column (JSON list) into positive floats.

    Mirrors the coercion already used in paper_bidding_backtest: accept a JSON
    string or an already-decoded list/tuple, drop anything non-numeric or <= 0.
    """
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value or "[]")
        except (TypeError, ValueError):
            return []
    if not isinstance(raw_value, (list, tuple)):
        return []
    values: list[float] = []
    for item in raw_value:
        parsed = _safe_float(item)
        if parsed is not None and parsed > 0:
            values.append(parsed)
    return values


def estimate_base_amount_from_reserves(reserve_prices: Any) -> float | None:
    """Estimate the real 기초금액 from 복수예비가격, or None when unrecoverable.

    The 15 reserve prices straddle 기초금액 by ~±2~3%, so ``round((min+max)/2)``
    recovers it. Fewer than 15 valid positive reserves (empty / unparseable /
    partial) yields None — an estimate is only offered on full evidence. This is
    an ESTIMATE; callers must store it in ``base_amount_estimated``, never in
    ``base_amount``.
    """
    values = _coerce_reserve_prices(reserve_prices)
    if len(values) < RESERVE_PRICE_COUNT:
        return None
    return float(round((min(values) + max(values)) / 2))
