"""Tests for the notice bid-base (기초금액/사업금액) resolution used by price prediction.

KONEPS 적격심사 투찰가는 추정가격(ex-VAT, ``Project.budget_estimate``)이 아니라
기초금액/사업금액(배정예산, 과세 공고면 VAT 포함, ``HistoricalData.base_amount``)을
기준으로 산정된다. 과거 낙찰률이 base_amount 기준으로 정규화돼 있으므로 predictor
에 넘기는 ``budget`` 도 base_amount 여야 올바른(VAT 포함) 투찰 금액이 나온다.
"""

from datetime import UTC, date, datetime

import pytest

from app.models.models import HistoricalData, Project
from app.schemas.schemas import PricePredictionRequest
from app.services.bid_base import (
    resolve_notice_bid_base,
    resolve_notice_legal_floor_inputs,
)
from app.services.prediction_workflow import PredictionWorkflowService

# A VAT (과세) notice: 기초금액 = 추정가격 × 1.1.
_BUDGET_ESTIMATE = 50_000_000.0
_VAT_BASE_AMOUNT = _BUDGET_ESTIMATE * 1.1  # 55,000,000


def _make_project(
    db,
    *,
    budget_estimate: float = _BUDGET_ESTIMATE,
    category: str = "service",
) -> Project:
    project = Project(
        title="기초금액 검증 공고",
        description="적격심사 투찰 기준 검증",
        requirements="",
        budget_estimate=budget_estimate,
        category=category,
    )
    db.add(project)
    db.flush()
    return project


def _add_base_row(db, project: Project, base_amount: float) -> HistoricalData:
    """Attach the collected 기초금액 for a project.

    ``bid_rate`` is left at 0 so this row is NOT loaded as a training sample
    (``explicit_bid_rate_only``); it exists purely to carry the base amount.
    """
    record = HistoricalData(
        project_id=project.id,
        category=project.category,
        base_amount=base_amount,
        bid_rate=0.0,
    )
    db.add(record)
    db.flush()
    return record


def _seed_price_history(db, *, category: str = "service", bid_rate: float = 0.90) -> None:
    """Seed a few explicit-rate rows (unlinked to the target project) so the
    predictor runs in ``historical_blend`` mode with a deterministic base rate."""
    for _ in range(5):
        db.add(
            HistoricalData(
                project_id=None,
                category=category,
                base_amount=40_000_000.0,
                bid_rate=bid_rate,
            )
        )
    db.flush()


# --------------------------------------------------------------------------- #
# Direct unit tests of resolve_notice_bid_base
# --------------------------------------------------------------------------- #


def test_resolve_bid_base_prefers_historical_base_amount(test_db):
    """VAT notice: returns 기초금액 (base_amount = 추정가격 × 1.1), not 추정가격."""
    project = _make_project(test_db)
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)
    assert resolved != pytest.approx(_BUDGET_ESTIMATE)


def test_resolve_bid_base_tax_free_equals_budget_estimate(test_db):
    """면세 notice: base_amount == 추정가격 → resolution is a no-op."""
    project = _make_project(test_db)
    _add_base_row(test_db, project, _BUDGET_ESTIMATE)
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_BUDGET_ESTIMATE)


def test_resolve_bid_base_falls_back_when_no_history(test_db):
    """No HistoricalData row → falls back to project.budget_estimate."""
    project = _make_project(test_db)
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_BUDGET_ESTIMATE)


def test_resolve_bid_base_falls_back_when_base_amount_zero(test_db):
    """A HistoricalData row with a non-positive base_amount → falls back."""
    project = _make_project(test_db)
    _add_base_row(test_db, project, 0.0)
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_BUDGET_ESTIMATE)


def test_resolve_bid_base_uses_latest_row_with_positive_base(test_db):
    """When several rows carry a positive base, the latest (highest id) wins."""
    project = _make_project(test_db)
    _add_base_row(test_db, project, 12_345_678.0)  # stale
    latest = _add_base_row(test_db, project, _VAT_BASE_AMOUNT)  # newest
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)
    assert latest.base_amount == pytest.approx(_VAT_BASE_AMOUNT)


def test_resolve_bid_base_prefers_older_positive_over_latest_zero(test_db):
    """A later settlement snapshot may leave base_amount unset (0/NULL) even when an
    earlier collection captured it. The resolver must prefer the older POSITIVE base
    over falling back to 추정가격 — otherwise 과세 공고 regress to an under-bid."""
    project = _make_project(test_db)
    older = _add_base_row(test_db, project, _VAT_BASE_AMOUNT)  # older, positive
    _add_base_row(test_db, project, 0.0)  # newest, base unset
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)
    assert resolved != pytest.approx(_BUDGET_ESTIMATE)
    assert older.base_amount == pytest.approx(_VAT_BASE_AMOUNT)


# --------------------------------------------------------------------------- #
# End-to-end tests through the prediction workflow (the real-money fix)
# --------------------------------------------------------------------------- #


def _predict(test_db, project: Project) -> dict:
    service = PredictionWorkflowService()
    request = PricePredictionRequest(
        project_id=project.id,
        budget_estimate=_BUDGET_ESTIMATE,
        category=project.category,
        description="적격심사 투찰 기준 검증",
    )
    return service.predict_project_price(test_db, request)


def _fed_budget(prediction: dict) -> float:
    """Recover the base the bid rate was applied to: predicted_price ≈ rate × budget."""
    return float(prediction["predicted_price"]) / float(prediction["predicted_bid_rate"])


def test_predict_price_uses_base_amount_for_vat_notice(test_db):
    """VAT notice → recommended bid is computed on 기초금액 (≈ rate × base_amount),
    i.e. ~10% higher than rate × 추정가격, avoiding an under-bid below the floor."""
    _seed_price_history(test_db)
    project = _make_project(test_db)
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    prediction = _predict(test_db, project)
    rate = float(prediction["predicted_bid_rate"])

    assert rate > 0
    # The bid amount is applied against the incl-VAT base, not the ex-VAT estimate.
    assert _fed_budget(prediction) == pytest.approx(_VAT_BASE_AMOUNT, rel=1e-3)
    assert prediction["predicted_price"] == pytest.approx(rate * _VAT_BASE_AMOUNT, rel=1e-3)
    # ...and it is materially (~10%) higher than the buggy ex-VAT computation.
    assert prediction["predicted_price"] > rate * _BUDGET_ESTIMATE * 1.05
    # Every candidate (conservative/base/aggressive) is anchored on the base amount.
    for candidate in prediction["bid_rate_candidates"]:
        candidate_rate = float(candidate["bid_rate"])
        assert candidate_rate > 0
        assert float(candidate["predicted_price"]) == pytest.approx(
            candidate_rate * _VAT_BASE_AMOUNT, rel=1e-3
        )


def test_predict_price_tax_free_matches_budget_estimate(test_db):
    """면세 notice (base_amount == 추정가격) → identical to using budget_estimate."""
    _seed_price_history(test_db)
    project = _make_project(test_db)
    _add_base_row(test_db, project, _BUDGET_ESTIMATE)
    test_db.commit()

    prediction = _predict(test_db, project)
    rate = float(prediction["predicted_bid_rate"])

    assert _fed_budget(prediction) == pytest.approx(_BUDGET_ESTIMATE, rel=1e-3)
    assert prediction["predicted_price"] == pytest.approx(rate * _BUDGET_ESTIMATE, rel=1e-3)


def test_predict_price_falls_back_to_budget_when_base_missing(test_db):
    """No collected 기초금액 → falls back to the request/project 추정가격 (no crash)."""
    _seed_price_history(test_db)
    project = _make_project(test_db)  # no HistoricalData base row
    test_db.commit()

    prediction = _predict(test_db, project)
    rate = float(prediction["predicted_bid_rate"])

    assert _fed_budget(prediction) == pytest.approx(_BUDGET_ESTIMATE, rel=1e-3)
    assert prediction["predicted_price"] == pytest.approx(rate * _BUDGET_ESTIMATE, rel=1e-3)


# --------------------------------------------------------------------------- #
# resolve_notice_legal_floor_inputs — construction legal 낙찰하한 tier inputs.
# Leakage-critical: the tier resolver keys on these, so pin the estimation
# coercion and the KST-day derivation (the ±1-day error right at the 2026-01-30
# 신율 시행일 boundary is what a UTC/KST slip would introduce).
# --------------------------------------------------------------------------- #


def _legal_floor_project(
    *, budget_estimate=3_000_000_000.0, created_at=None
) -> Project:
    """In-memory project (no flush) — the helper reads attributes directly and
    needs no DB. ``created_at`` is set explicitly because the column default only
    fires at INSERT time."""
    return Project(
        title="법정하한 입력 검증",
        budget_estimate=budget_estimate,
        category="construction",
        created_at=created_at,
    )


@pytest.mark.parametrize(
    "budget_estimate, expected",
    [
        (500_000_000.0, 500_000_000.0),  # 양수 → 그대로
        (0.0, None),  # 0 → None (구간 판정 불가)
        (-1.0, None),  # 음수 → None
        (None, None),  # 없음 → None
    ],
)
def test_legal_floor_inputs_estimation_amount(budget_estimate, expected):
    project = _legal_floor_project(
        budget_estimate=budget_estimate, created_at=datetime(2026, 2, 1, tzinfo=UTC)
    )
    estimation, _ = resolve_notice_legal_floor_inputs(project)
    if expected is None:
        assert estimation is None
    else:
        assert estimation == pytest.approx(expected)


def test_legal_floor_inputs_reference_date_at_kst_midnight_boundary():
    """KST = UTC+9, so KST 자정 = UTC 15:00. A notice created at UTC 2026-01-29
    15:00 is KST 2026-01-30 00:00 → the 신율 시행일 date, not 2026-01-29."""
    project = _legal_floor_project(created_at=datetime(2026, 1, 29, 15, 0, tzinfo=UTC))
    _, reference_date = resolve_notice_legal_floor_inputs(project)
    assert reference_date == date(2026, 1, 30)


def test_legal_floor_inputs_reference_date_just_before_kst_midnight():
    """UTC 2026-01-29 14:59 == KST 2026-01-29 23:59 → still the 구율 date."""
    project = _legal_floor_project(created_at=datetime(2026, 1, 29, 14, 59, tzinfo=UTC))
    _, reference_date = resolve_notice_legal_floor_inputs(project)
    assert reference_date == date(2026, 1, 29)


def test_legal_floor_inputs_naive_created_at_assumed_utc():
    """SQLite stores naive UTC datetimes; to_kst assumes naive == UTC, so the same
    15:00 straddle still lands on the KST-next-day date."""
    project = _legal_floor_project(created_at=datetime(2026, 1, 29, 15, 0))  # naive
    _, reference_date = resolve_notice_legal_floor_inputs(project)
    assert reference_date == date(2026, 1, 30)


def test_legal_floor_inputs_no_created_at_returns_none_reference():
    """created_at 없음 → reference_date None → 리졸버가 tier 미적용(소급 방지).
    추정가격은 있어도 날짜가 없으면 tier 를 걸지 않는다."""
    project = _legal_floor_project(created_at=None)
    estimation, reference_date = resolve_notice_legal_floor_inputs(project)
    assert estimation == pytest.approx(3_000_000_000.0)
    assert reference_date is None
