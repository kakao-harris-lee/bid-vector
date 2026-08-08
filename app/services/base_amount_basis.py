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


def exceeds_base_trust_ratio(
    base_amount: float | None, budget_estimate: float | None
) -> bool:
    """기초금액 ÷ 추정가격 이 부가세로 설명할 수 없는 배수인가 (순수, 공유 술어).

    같은 질문을 두 곳이 각자 답하던 것을 한 벌로 합친다(§4.5-8): 저장 행의 provenance
    분류(:func:`classify_base_basis`)와 라이브 예산 상한 게이트
    (``app/services/opportunity_analysis/market.py::enforceable_floor_price``)가 이 술어를
    함께 쓴다. 종전에는 게이트가 곱셈형(``base > cap × 임계``), 분류기가 나눗셈형이라 경계
    근처에서 판정이 갈릴 수 있었다 — 합치면서 **나눗셈형으로 통일**했다.

    두 금액 중 하나라도 양수로 확보되지 않으면 ``False`` 다. 이는 "신뢰 범위 안"이라는
    주장이 아니라 **이 술어가 할 말이 없다**는 뜻이므로, 확보 실패를 어떻게 다룰지는 호출부가
    정한다: 게이트는 base 를 못 받으면 보수적으로 닫고, 분류기는 규칙을 적용하지 않는다.

    입력은 이미 수치로 강제된 값이다(분류기는 ``_safe_float`` 로, 게이트는
    ``optional_float`` 로 통과시킨 뒤 넘긴다). ``_safe_float`` 를 한 번 더 거치는 것은
    NaN/inf 방어이며, 원시 문자열을 여기서 받는 계약이 아니다.
    """
    base = _safe_float(base_amount)
    estimate = _safe_float(budget_estimate)
    if base is None or estimate is None or base <= 0 or estimate <= 0:
        return False
    return base / estimate > BID_BASE_TRUST_RATIO_MAX


def _is_suspect_ratio(ctx: _BasisContext) -> bool:
    """base ÷ 추정가격 이 부가세로 설명할 수 없는 배수 ⇒ 저장된 base 는 기초금액이 아니다.

    한 공고의 두 금액(``HistoricalData.base_amount`` 와 ``Project.budget_estimate``)은
    부가세 관계 안에 있어야 한다: 면세 1.00, 과세 1.10. 임계
    :data:`~app.core.constants.BID_BASE_TRUST_RATIO_MAX` 를 넘는 배수는 세금이 아니라
    **다른 금액 필드가 혼입**된 것이다(운영 DB 실측 p50 1.408).

    이 규칙이 주장하지 **않는** 것 (주장 범위 — 리뷰 교정)
    ---------------------------------------------------
    - **"개찰 전 정보만 쓴다"고 주장하지 않는다.** ``Project.budget_estimate`` 자리에 개찰
      파생값이 들어올 수 있다: reserve detail 에 예정가가 없으면 수집이 ``winning_amount ÷
      sucsfbidRate`` 로 예정가를 역산해(``app/services/koneps/scsbid.py``)
      ``estimated_amount`` 로 배출한다. 출처 인지 write 가드
      (``app/services/koneps/budget_fields.py``) 이후 그 파생값은 **빈 자리(NULL/0)만**
      채우므로, 게시 추정가격이 이미 있는 행은 보존된다. 그래서 이 제외 기준이 실제로 남는
      범위는 둘이다: 게시 추정가격을 한 번도 얻지 못해 파생값이 빈 자리를 채운 행과, 가드
      이전에 이미 덮인 행(실측 ~3,982 — ``docs/operations/base-amount-basis-backfill.md``
      §6). 그 두 코호트에서는 비교 대상 금액 자체가 **개찰 결과에서 파생된 값**이다.
    - **"독립적인 두 번째 금액"이 항상 성립하지도 않는다.** 수집이 추정가격을 못 얻으면
      ``matching.resolve_budget_estimate`` 가 base_amount 를 그대로 추정가격으로 쓰므로 두
      금액이 같은 값의 두 사본이 되고, 비율이 항상 1.0 이라 이 규칙에 걸리지 않는다(실측
      clean 33,601 중 10,110 = 30%). 백필이 ``est_equals_base`` 로 그 사각 코호트 크기를
      함께 보고한다.

    그래서 이 규칙이 실제로 주장하는 것은 하나다: **두 금액이 서로 모순이면, 어느 쪽이
    파생값이든 그 행을 캘리브레이션의 ground truth 로 쓸 근거가 없다.** 어느 쪽이 깨졌는지는
    비율만으로 가릴 수 없으므로(실측에 est 3,636원 파싱 실패 사례 혼재) base 를 **교정하지
    않고** clean 버킷에서만 뺀다.

    부작용으로, settled 행에서 제외 기준은 사실상 결과 조건부가 된다(base ÷ (낙찰가 ÷
    사정률) > 임계). 남는 clean 버킷은 낙찰가 대비 base 가 낮은 쪽으로 치우치므로, 이
    버킷으로 밴드를 재캘리브레이션하면 앵커가 **위(안전측)** 로 편향된다 — 그 편향을 함께
    보고하지 않고 수치를 그대로 쓰지 말 것.

    이 라벨은 라이브 게이트와도 이어진다(코드리뷰 C2): ``#356`` budget_cap 게이트가 라벨을
    읽지는 않지만 게이트 입력 ``bid_base`` 가 ``get_reliable_base`` 를 거쳐 라벨에서
    파생되므로, 재태깅 + 복구 추정치를 가진 행에서는 게이트 입력이 바뀐다(자세한 방향과
    영향 코호트는 ``enforceable_floor_price`` docstring).

    추정가격을 확보하지 못하면(0.0) 비교 자체가 불가능하므로 규칙은 적용되지 않는다.
    저측(base < 추정가격)은 이 규칙이 보는 축이 아니다. 그 계열을 suspect-fractional 이
    구조적으로 잡는 것은 아니고(정수면 clean 으로 남는다) 운영 실측에서 98% 가 소수라 겹쳐
    잡혔을 뿐이라, 저측 판정은 별도 후속이다.
    """
    return exceeds_base_trust_ratio(ctx.base, ctx.budget_estimate)


# Declarative first-match rule table: the first predicate that matches wins,
# otherwise the value falls through to BASIS_SUSPECT_FRACTIONAL.
#
# ORDER IS LOAD-BEARING. 비율 판정이 맨 앞에 서고, 그 뒤는 기존 순서를 그대로 둔다:
#
# 1. ``suspect-ratio`` — 같은 공고의 **다른 금액 필드와의 모순**이라 가장 강한 증거다
#    (그 금액이 개찰 결과에서 파생됐을 수 있다는 한정은 위 predicate docstring 참조 —
#    "독립적"이라고 주장하지 않는다). 뒤의 두 패턴 판정은 이 질문("이 값이 진짜
#    기초금액인가")에 답하지 않는다: derived-yega 는 낙찰률이 ``winning ÷ base`` 로
#    정규화돼 저장되는 settled 행에서 자기충족이고, derived-vat 은 **모든 정수 base** 에서
#    참이다(그래서 clean 이 그 앞에 서 있다). 비율이 깨진 행이 그 패턴에도 걸리면 라벨은
#    suspect-ratio 로 가지만, 세 라벨 모두 non-clean 버킷이라 소비자 동작은 같다.
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
    이 인자를 공급하는 것은 호출부의 선택이다. 현재 공급자는 백필
    (``scripts/backfill_base_amount_basis.py``)과 수집 write 경로
    (``app/services/koneps/base_provenance.py``) 둘이고, 홀드아웃
    (``scripts/backtest_latest_award_holdouts.py``)은 의도적으로 공급하지 않는다.
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
