"""Value-table tests for the national construction qualification-floor spec.

Pins the effective-date × 추정가격-tier lookup declared in
``app.ai.predictors.legal_floor_spec`` — the 2026-01-30 (+2%p) revision, the
[하한, 상한) bracket boundaries, the 100억+ 종심제 gap, and the leakage guards
(unknown amount / unknown reference_date → None).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.ai.predictors.legal_floor_spec import (
    CONSTRUCTION_FLOOR_REVISION_DATE,
    resolve_construction_qualification_floor as resolve_floor,
)

_EOK = 100_000_000.0
_OLD_DATE = date(2026, 1, 29)  # 개정 시행 하루 전
_NEW_DATE = date(2026, 1, 30)  # 개정 시행일(포함)


# ---------------------------------------------------------------------------
# 신율 (2026-01-30 개정, +2%p) — 구간별 값
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "amount, expected",
    [
        (5 * _EOK, 0.89745),  # 10억 미만
        (9.99 * _EOK, 0.89745),  # 10억 직전
        (10 * _EOK, 0.88745),  # 정확히 10억 → 10억~50억 구간
        (30 * _EOK, 0.88745),  # 10억~50억
        (49.99 * _EOK, 0.88745),  # 50억 직전
        (50 * _EOK, 0.87495),  # 정확히 50억 → 50억~100억 구간
        (80 * _EOK, 0.87495),  # 50억~100억
        (99.99 * _EOK, 0.87495),  # 100억 직전
    ],
)
def test_new_rate_brackets(amount, expected):
    assert resolve_floor(amount, _NEW_DATE) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 구율 (개정 전) — 구간별 값
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "amount, expected",
    [
        (5 * _EOK, 0.87745),  # 10억 미만
        (10 * _EOK, 0.86745),  # 정확히 10억 → 10억~50억
        (30 * _EOK, 0.86745),  # 10억~50억
        (50 * _EOK, 0.85495),  # 정확히 50억 → 50억~100억
        (80 * _EOK, 0.85495),  # 50억~100억
    ],
)
def test_old_rate_brackets(amount, expected):
    assert resolve_floor(amount, _OLD_DATE) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 각 구간 신·구율 차이는 정확히 +2%p
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount", [5 * _EOK, 30 * _EOK, 80 * _EOK])
def test_revision_is_plus_two_pp(amount):
    old = resolve_floor(amount, _OLD_DATE)
    new = resolve_floor(amount, _NEW_DATE)
    assert new is not None and old is not None
    assert new - old == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# 시행일 경계: 2026-01-29 = 구율, 2026-01-30 = 신율
# ---------------------------------------------------------------------------
def test_effective_date_boundary():
    amount = 30 * _EOK  # 10억~50억
    assert resolve_floor(amount, date(2026, 1, 29)) == pytest.approx(0.86745)  # 구
    assert resolve_floor(amount, date(2026, 1, 30)) == pytest.approx(0.88745)  # 신
    assert CONSTRUCTION_FLOOR_REVISION_DATE == date(2026, 1, 30)


def test_datetime_reference_is_narrowed_to_day():
    amount = 30 * _EOK
    # 2026-01-30 00:00 (신율 시행 당일 자정) — datetime 도 date 로 정규화돼 신율.
    assert resolve_floor(amount, datetime(2026, 1, 30, 0, 0)) == pytest.approx(0.88745)
    assert resolve_floor(amount, datetime(2026, 1, 29, 23, 59)) == pytest.approx(0.86745)


# ---------------------------------------------------------------------------
# None 케이스: 100억+ 종심제, 추정가격 불명/0/음수, 기준일 불명
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reference_date", [_OLD_DATE, _NEW_DATE])
@pytest.mark.parametrize("amount", [100 * _EOK, 150 * _EOK, 500 * _EOK])
def test_hundred_eok_and_above_is_none(amount, reference_date):
    # 종심제 — 하한율 표 대상 아님(신·구 무관).
    assert resolve_floor(amount, reference_date) is None


@pytest.mark.parametrize("amount", [None, 0, 0.0, -1, -5 * _EOK])
def test_absent_or_nonpositive_amount_is_none(amount):
    assert resolve_floor(amount, _NEW_DATE) is None


def test_unknown_reference_date_is_none():
    # 기준일 불명이면 소급 적용 방지를 위해 표 미적용(호출부는 기존 flat 유지).
    assert resolve_floor(30 * _EOK, None) is None


def test_string_amount_is_coerced():
    assert resolve_floor("3000000000", _NEW_DATE) == pytest.approx(0.88745)


def test_garbage_amount_is_none():
    assert resolve_floor("not-a-number", _NEW_DATE) is None
