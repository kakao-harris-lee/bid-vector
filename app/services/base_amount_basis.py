"""Provenance classification for ``HistoricalData.base_amount``.

66% of stored ``base_amount`` values are NOT the real 기초금액 (integer 원화).
They are derived/polluted values — most commonly ``winning_amount ÷ winning_rate``
역산 (which equals 예정가-basis, e.g. ``43,996,200 ÷ 0.88035 = 49,975,805.0775``),
or ``VAT-inclusive ÷ 1.1`` divisions. A second family carries an amount that is a
multiple of the notice's own 추정가격 too large to be VAT (다른 금액 필드 혼입) —
that one is only visible when the row is compared against ``Project.budget_estimate``,
so it needs an input the row itself does not carry. This pollution silently invalidates
밴드 캘리브레이션 and 백테스트, so we tag each row's provenance instead of mutating the
original value (정직 명세 §2 — 원본 불변, 추정은 추정으로 표기).

This module is pure (no I/O): the backfill script (``scripts/backfill_base_amount_basis.py``)
does the DB reads/writes and calls these functions. Classification rules are a
declarative first-match table (§4.5) — add a case = add a row, not a branch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from app.core.constants import BID_BASE_TRUST_RATIO_MAX
from app.domain.rate_normalization import to_bid_rate_fraction

# Basis verdicts. ``CLEAN`` is the ONLY value a feedback/calibration loop may
# trust as a real 기초금액 (see HistoricalData column notes + config warnings).
BASIS_CLEAN = "clean"
BASIS_DERIVED_YEGA = "derived-yega"  # win ÷ winning_rate 역산 (예정가-basis)
BASIS_DERIVED_VAT = "derived-vat"  # VAT-inclusive ÷ 1.1 파생
BASIS_SUSPECT_FRACTIONAL = "suspect-fractional"  # 소수/누락/미상 — 신뢰 불가 버킷
# 같은 공고의 추정가격과 부가세로 설명되지 않는 배수 — 다른 금액 필드 혼입 계열.
BASIS_SUSPECT_RATIO = "suspect-ratio"

ALL_BASES = (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_DERIVED_VAT,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
)

# Match tolerances (declared, not inlined — §4.5).
_INTEGER_TOLERANCE = 1e-6  # a real 기초금액 (원화) is an exact float integer
_YEGA_TOLERANCE = 1.0  # |base × winning_rate − winning_amount| < 1원
_VAT_TOLERANCE = 0.01  # |base × 1.1 − round(base × 1.1)| < 0.01
_VAT_MULTIPLIER = 1.1
# ``TenderResult.winning_rate`` is stored on mixed scales (scsbid persists a
# fraction e.g. 0.875; HTML parsing a percentage e.g. 87.5 — see
# app/services/bid_target_signals.py). The percentage-scale threshold and the
# >threshold → /100 rule are single-sourced in app.domain.rate_normalization.

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


def normalize_winning_rate(value: Any) -> float | None:
    """Normalize a mixed-scale ``TenderResult.winning_rate`` to a bid-rate fraction.

    scsbid persists a fraction (0.875) while HTML parsing persists a percentage
    (87.5); callers MUST normalize before passing the rate to
    ``classify_base_basis`` or a percentage-form 예정가-역산 row fails the
    derived-yega check (base × 87.5 ≠ winning_amount) and is mislabeled
    ``suspect-fractional``. No validity-range gate is applied, so a genuinely low
    award rate stays usable for the derived-yega match; non-positive / non-numeric
    returns None (the derived-yega check is then skipped).
    """
    rate = _safe_float(value)
    if rate is None or rate <= 0:
        return None
    return to_bid_rate_fraction(rate)


@dataclass(frozen=True)
class _BasisContext:
    """Normalized inputs for the classification rule table.

    ``budget_estimate`` (같은 공고의 추정가격, ``Project.budget_estimate``) 은 0.0 이면
    "확보 못 함"을 뜻하고, 그 경우 비율 규칙은 적용되지 않는다 — 이 값을 넘기지 않는
    호출부의 판정은 바이트 동일하게 유지된다.
    """

    base: float
    winning_amount: float
    winning_rate: float
    budget_estimate: float = 0.0


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


def _is_suspect_ratio(ctx: _BasisContext) -> bool:
    """base ÷ 추정가격 이 부가세로 설명할 수 없는 배수 ⇒ 저장된 base 는 기초금액이 아니다.

    한 공고의 두 금액(``HistoricalData.base_amount`` 와 ``Project.budget_estimate``)은
    부가세 관계 안에 있어야 한다: 면세 1.00, 과세 1.10. 임계
    :data:`~app.core.constants.BID_BASE_TRUST_RATIO_MAX` 를 넘는 배수는 세금이 아니라
    **다른 금액 필드가 혼입**된 것이다(운영 DB 실측 p50 1.408).

    어느 쪽 금액이 깨졌는지는 이 비율만으로 알 수 없다 — base 가 부풀었을 수도, 추정가격
    파싱이 깨졌을 수도 있다(실측에 est 3,636원 사례 혼재). 그래서 이 규칙은 base 를
    **교정하지 않고** clean 버킷에서만 뺀다: 두 금액이 서로 모순인 행을 캘리브레이션의
    ground truth 로 쓸 근거가 없다는 것까지가 이 증거가 말할 수 있는 전부다.

    추정가격을 확보하지 못하면(0.0) 비교 자체가 불가능하므로 규칙은 적용되지 않는다.
    저측(base < 추정가격)은 이 규칙의 대상이 아니다 — 기존 suspect-fractional 이 그 계열의
    98% 를 이미 잡는다(후속에서 별도 판정).
    """
    if ctx.base <= 0 or ctx.budget_estimate <= 0:
        return False
    return ctx.base / ctx.budget_estimate > BID_BASE_TRUST_RATIO_MAX


# Declarative first-match rule table: the first predicate that matches wins,
# otherwise the value falls through to BASIS_SUSPECT_FRACTIONAL.
#
# ORDER IS LOAD-BEARING. 비율 판정이 맨 앞에 서고, 그 뒤는 기존 순서를 그대로 둔다:
#
# 1. ``suspect-ratio`` — 같은 공고의 **독립적인 두 번째 금액**과의 모순이라 가장 강한
#    증거다. 뒤의 두 패턴 판정은 이 질문("이 값이 진짜 기초금액인가")에 답하지 않는다:
#    derived-yega 는 낙찰률이 ``winning ÷ base`` 로 정규화돼 저장되는 settled 행에서
#    자기충족이고, derived-vat 은 **모든 정수 base** 에서 참이다(그래서 clean 이 그 앞에
#    서 있다). 비율이 깨진 행이 그 패턴에도 걸리면 라벨은 suspect-ratio 로 가지만, 세
#    라벨 모두 non-clean 버킷이라 소비자 동작은 같다.
# 2. ``clean`` — 정수 원화. derived-vat 앞에 서야 정수 base 가 VAT 파생으로 오분류되지
#    않는다.
# 3. 예정가 역산 → VAT 파생 (둘 다 걸릴 수 있으면 역산이 더 구체적인 설명이다).
_CLASSIFICATION_RULES: tuple[tuple[str, Callable[[_BasisContext], bool]], ...] = (
    (BASIS_SUSPECT_RATIO, _is_suspect_ratio),
    (BASIS_CLEAN, _is_clean_integer),
    (BASIS_DERIVED_YEGA, _is_derived_yega),
    (BASIS_DERIVED_VAT, _is_derived_vat),
)


def classify_base_basis(
    base_amount: Any,
    winning_amount: Any = None,
    winning_rate: Any = None,
    budget_estimate: Any = None,
) -> str:
    """Classify the provenance of a stored ``base_amount`` (pure).

    Returns one of ``ALL_BASES``. Missing / non-positive / non-numeric ``base``
    is NOT clean — it lands in ``suspect-fractional`` (the untrusted bucket) so a
    calibration loop filtered to ``clean`` never picks up 0/누락 rows.

    ``budget_estimate`` 는 같은 공고의 추정가격(``Project.budget_estimate``)이다. 넘기지
    않으면(또는 결측/0/비수치면) 비율 규칙이 적용되지 않아 판정이 기존과 바이트 동일하다 —
    이 인자를 공급하는 것은 호출부의 선택이다(현재 백필만 공급한다).
    """
    base = _safe_float(base_amount)
    if base is None:
        return BASIS_SUSPECT_FRACTIONAL
    ctx = _BasisContext(
        base=base,
        winning_amount=_safe_float(winning_amount) or 0.0,
        winning_rate=_safe_float(winning_rate) or 0.0,
        budget_estimate=_safe_float(budget_estimate) or 0.0,
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
    # Range midpoint (min+max)/2, not the mean: 복수예비가격 straddle 기초금액
    # symmetrically by construction, so the range center is the robust base
    # estimate and is insensitive to how the draws cluster within the band.
    return float(round((min(values) + max(values)) / 2))
