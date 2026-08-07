"""Tests for the basis-aware 기초금액 selector (``app/domain/reliable_base.py``).

This is the first production consumer of the #199 ``base_amount_basis`` provenance
tag. The regression that matters most: a ``clean`` (or unclassified NULL) basis must
return its ``base_amount`` UNCHANGED, so live pricing stays byte-identical. The only
value change allowed is an explicitly non-clean basis with a reserve-recovered
estimate, which substitutes that estimate.
"""

from __future__ import annotations

import pytest

from app.domain.money import Basis
from app.domain.reliable_base import (
    ReliableBaseSource,
    get_reliable_base,
)
from app.services.base_amount_basis import (
    ALL_BASES,
    BASIS_CLEAN,
    BASIS_DERIVED_VAT,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
)

_CLEAN_BASE = 55_000_000.0
_RESERVE_ESTIMATE = 50_000_000.0

# --------------------------------------------------------------------------- #
# clean / unclassified → base_amount UNCHANGED (the regression guard)
# --------------------------------------------------------------------------- #


def test_clean_basis_returns_base_amount_unchanged():
    """basis == clean → 저장된 base_amount 그대로(신뢰). 예측 base 불변."""
    result = get_reliable_base(
        base_amount=_CLEAN_BASE,
        basis=BASIS_CLEAN,
        base_amount_estimated=_RESERVE_ESTIMATE,  # present, but ignored for clean
    )
    assert result.value == pytest.approx(_CLEAN_BASE)
    assert result.source is ReliableBaseSource.CLEAN_BASE


def test_null_basis_falls_back_to_base_amount():
    """basis 미분류(None) → base_amount 폴백(기존 동작). open 공고의 흔한 경우."""
    result = get_reliable_base(
        base_amount=_CLEAN_BASE,
        basis=None,
        base_amount_estimated=_RESERVE_ESTIMATE,  # ignored — basis not explicitly non-clean
    )
    assert result.value == pytest.approx(_CLEAN_BASE)
    assert result.source is ReliableBaseSource.BASE_FALLBACK


# --------------------------------------------------------------------------- #
# non-clean + estimate → substitute the reserve-recovered estimate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "basis",
    [
        BASIS_DERIVED_YEGA,
        BASIS_DERIVED_VAT,
        BASIS_SUSPECT_FRACTIONAL,
        BASIS_SUSPECT_RATIO,
    ],
)
def test_non_clean_basis_with_estimate_prefers_estimate(basis):
    """non-clean basis(예정가-basis 등 오염) + 양수 추정치 → 추정치로 대체."""
    result = get_reliable_base(
        base_amount=_CLEAN_BASE,  # polluted raw base
        basis=basis,
        base_amount_estimated=_RESERVE_ESTIMATE,
    )
    assert result.value == pytest.approx(_RESERVE_ESTIMATE)
    assert result.source is ReliableBaseSource.RESERVE_ESTIMATE


@pytest.mark.parametrize(
    "basis",
    [
        BASIS_DERIVED_YEGA,
        BASIS_DERIVED_VAT,
        BASIS_SUSPECT_FRACTIONAL,
        BASIS_SUSPECT_RATIO,
    ],
)
def test_non_clean_basis_without_estimate_falls_back_to_base(basis):
    """non-clean basis인데 복구 추정치가 없으면 base_amount 폴백(기존 동작 보존)."""
    for missing in (None, 0.0, -1.0):
        result = get_reliable_base(
            base_amount=_CLEAN_BASE,
            basis=basis,
            base_amount_estimated=missing,
        )
        assert result.value == pytest.approx(_CLEAN_BASE)
        assert result.source is ReliableBaseSource.BASE_FALLBACK


# --------------------------------------------------------------------------- #
# no usable base → UNAVAILABLE (caller falls back to 추정가격)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("base_amount", [None, 0.0, -5.0])
def test_no_positive_base_is_unavailable(base_amount):
    """양수 base가 없으면 값 None + UNAVAILABLE → 호출부가 상위 추정가격 폴백."""
    result = get_reliable_base(
        base_amount=base_amount,
        basis=None,
        base_amount_estimated=None,
    )
    assert result.value is None
    assert result.source is ReliableBaseSource.UNAVAILABLE


def test_non_clean_zero_base_with_estimate_uses_estimate():
    """base_amount가 0이어도 non-clean basis + 양수 추정치면 추정치를 쓴다."""
    result = get_reliable_base(
        base_amount=0.0,
        basis=BASIS_DERIVED_YEGA,
        base_amount_estimated=_RESERVE_ESTIMATE,
    )
    assert result.value == pytest.approx(_RESERVE_ESTIMATE)
    assert result.source is ReliableBaseSource.RESERVE_ESTIMATE


# --------------------------------------------------------------------------- #
# money.Basis: the selector always yields a 기초금액-basis value, never 예정가
# --------------------------------------------------------------------------- #


def test_every_non_clean_label_is_rejected_as_a_trusted_base():
    """clean 이 아닌 **모든** 라벨이 추정치로 대체된다 — 새 라벨이 조용히 새지 않는다.

    ``_NON_CLEAN_BASES`` 는 ``ALL_BASES`` 에서 clean 만 뺀 파생이라, 어휘에 라벨을 추가하면
    이 접근자가 자동으로 non-clean 으로 취급한다. 그 파생 관계를 값표로 못박아, 새 라벨을
    ``ALL_BASES`` 에 등록하지 않고 도입하면(=clean 처럼 취급되면) 여기서 실패하게 한다.
    """
    for basis in ALL_BASES:
        result = get_reliable_base(
            base_amount=_CLEAN_BASE,
            basis=basis,
            base_amount_estimated=_RESERVE_ESTIMATE,
        )
        expected = (
            ReliableBaseSource.CLEAN_BASE
            if basis == BASIS_CLEAN
            else ReliableBaseSource.RESERVE_ESTIMATE
        )
        assert result.source is expected, basis


def test_result_basis_is_always_base_amount():
    """어느 경로든 결과는 기초금액(BASE_AMOUNT) basis다(예정가 아님, #162)."""
    for basis in (BASIS_CLEAN, None, BASIS_DERIVED_YEGA):
        result = get_reliable_base(
            base_amount=_CLEAN_BASE,
            basis=basis,
            base_amount_estimated=_RESERVE_ESTIMATE,
        )
        assert result.basis is Basis.BASE_AMOUNT
