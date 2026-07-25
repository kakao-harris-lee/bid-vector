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
    prepare_prediction_inputs,
    resolve_notice_bid_base,
    resolve_notice_legal_floor_bid_rate,
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


def _add_base_row(
    db,
    project: Project,
    base_amount: float,
    *,
    base_amount_basis: str | None = None,
    base_amount_estimated: float | None = None,
) -> HistoricalData:
    """Attach the collected 기초금액 for a project.

    ``bid_rate`` is left at 0 so this row is NOT loaded as a training sample
    (``explicit_bid_rate_only``); it exists purely to carry the base amount.
    ``base_amount_basis`` / ``base_amount_estimated`` default to unset (NULL), which
    is the common state for open notices before #199 backfill classifies them.
    """
    record = HistoricalData(
        project_id=project.id,
        category=project.category,
        base_amount=base_amount,
        bid_rate=0.0,
        base_amount_basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
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
# basis-aware wiring (#199 base_amount_basis consumer). clean/unclassified rows
# MUST resolve byte-identically to the pre-wiring behavior; only an explicitly
# non-clean basis with a reserve-recovered estimate substitutes that estimate.
# --------------------------------------------------------------------------- #

_RESERVE_ESTIMATE = 48_000_000.0  # distinct from _VAT_BASE_AMOUNT / _BUDGET_ESTIMATE


def test_resolve_bid_base_clean_basis_unchanged(test_db):
    """clean basis → base_amount 그대로. estimate가 있어도 무시(회귀 가드)."""
    project = _make_project(test_db)
    _add_base_row(
        test_db,
        project,
        _VAT_BASE_AMOUNT,
        base_amount_basis="clean",
        base_amount_estimated=_RESERVE_ESTIMATE,
    )
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)
    assert resolved != pytest.approx(_RESERVE_ESTIMATE)


def test_resolve_bid_base_null_basis_unchanged(test_db):
    """basis 미분류(NULL) → base_amount 폴백. open 공고 흔한 경우, 라이브 불변."""
    project = _make_project(test_db)
    _add_base_row(
        test_db,
        project,
        _VAT_BASE_AMOUNT,
        base_amount_basis=None,
        base_amount_estimated=_RESERVE_ESTIMATE,
    )
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)


def test_resolve_bid_base_derived_yega_prefers_estimate(test_db):
    """derived-yega(예정가-basis 오염) + reserve 추정치 → 추정치로 방어 대체."""
    project = _make_project(test_db)
    _add_base_row(
        test_db,
        project,
        _VAT_BASE_AMOUNT,  # polluted 예정가-basis value
        base_amount_basis="derived-yega",
        base_amount_estimated=_RESERVE_ESTIMATE,
    )
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_RESERVE_ESTIMATE)
    assert resolved != pytest.approx(_VAT_BASE_AMOUNT)


def test_resolve_bid_base_derived_yega_without_estimate_keeps_base(test_db):
    """derived-yega인데 복구 추정치가 없으면 base_amount 폴백(기존 동작 보존)."""
    project = _make_project(test_db)
    _add_base_row(
        test_db,
        project,
        _VAT_BASE_AMOUNT,
        base_amount_basis="derived-yega",
        base_amount_estimated=None,
    )
    test_db.commit()

    resolved = resolve_notice_bid_base(test_db, project)

    assert resolved == pytest.approx(_VAT_BASE_AMOUNT)


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


# --------------------------------------------------------------------------- #
# resolve_notice_legal_floor_bid_rate — wire the notice's OWN published 낙찰하한율
# (award_floor_rate, #201) into the prediction guardrail floor. This closes the
# safety gap where a published 하한 of 0.88 could still let a 0.876 recommendation
# pass because only the category floor (0.87) was consulted (P3a).
#
# RED LINE: guardrail_core folds the value with max() only, so a published 하한 can
# ONLY RAISE the recommendation floor, never lower the category/legal floor.
# --------------------------------------------------------------------------- #

# PREDICTION_CATEGORY_MINIMUM_BID_RATES["service"] — the configured floor a service
# notice falls back to when nothing raises it.
_SERVICE_CATEGORY_FLOOR = 0.87


@pytest.mark.parametrize(
    "award_floor_rate, request_legal, expected",
    [
        (0.88, None, 0.88),  # published fraction folded in
        (88.0, None, 0.88),  # published percent normalized (>1.5 → /100)
        (None, None, None),  # nothing published/requested → None (config floor kept)
        (0.0, None, None),  # non-positive published → None (ignored)
        (None, 0.90, 0.90),  # request-only (unchanged pre-existing behavior)
        (0.85, 0.90, 0.90),  # request wins over published
        (0.88, 0.86, 0.86),  # explicit client override respected even if lower
    ],
)
def test_resolve_legal_floor_bid_rate_precedence(award_floor_rate, request_legal, expected):
    """Pure precedence table: explicit request value wins; else the published
    award_floor_rate (normalized); else None."""
    project = Project(
        title="낙찰하한 wiring", category="service", award_floor_rate=award_floor_rate
    )
    resolved = resolve_notice_legal_floor_bid_rate(
        project, request_legal_floor_bid_rate=request_legal
    )
    if expected is None:
        assert resolved is None
    else:
        assert resolved == pytest.approx(expected)


def _predict_with_award(
    test_db,
    *,
    award_floor_rate=None,
    request_legal_floor_bid_rate=None,
    category: str = "service",
) -> dict:
    """Run the real prediction workflow for a notice carrying a published 낙찰하한율."""
    _seed_price_history(test_db, category=category)
    project = _make_project(test_db, category=category)
    project.award_floor_rate = award_floor_rate
    test_db.commit()

    service = PredictionWorkflowService()
    request = PricePredictionRequest(
        project_id=project.id,
        budget_estimate=_BUDGET_ESTIMATE,
        category=category,
        description="낙찰하한 wiring 검증",
        legal_floor_bid_rate=request_legal_floor_bid_rate,
    )
    return service.predict_project_price(test_db, request)


def test_predict_price_raises_floor_to_published_award_floor(test_db):
    """Published 하한 0.88 > category floor 0.87 → guardrail floor rises to 0.88, so a
    0.876 recommendation can no longer pass (the P3a gap is closed)."""
    prediction = _predict_with_award(test_db, award_floor_rate=0.88)

    assert prediction["legal_floor_bid_rate"] == pytest.approx(0.88)
    assert prediction["floor_bid_rate"] == pytest.approx(0.88)
    assert prediction["floor_bid_rate"] > _SERVICE_CATEGORY_FLOOR  # RAISED above category
    assert prediction["floor_guardrail_source"] == "legal"


def test_predict_price_normalizes_percent_award_floor(test_db):
    """Published 하한 stored as a percent (88) normalizes to the 0.88 fraction floor."""
    prediction = _predict_with_award(test_db, award_floor_rate=88.0)

    assert prediction["legal_floor_bid_rate"] == pytest.approx(0.88)
    assert prediction["floor_bid_rate"] == pytest.approx(0.88)


def test_predict_price_award_below_category_floor_does_not_lower_floor(test_db):
    """RED LINE: a published 하한 (0.85) BELOW the category floor (0.87) is ignored by
    max() — the floor is NEVER lowered below the configured category/legal floor."""
    prediction = _predict_with_award(test_db, award_floor_rate=0.85)

    assert prediction["floor_bid_rate"] == pytest.approx(_SERVICE_CATEGORY_FLOOR)
    assert prediction["floor_bid_rate"] > 0.85  # not lowered to the published value
    # The category floor remains the binding edge; the lower legal term does not bind.
    assert prediction["floor_guardrail_source"] == "category"


def test_predict_price_no_award_floor_preserves_configured_floor(test_db):
    """No published 하한 and no request value → behavior unchanged: floor stays at the
    configured category floor and no legal floor is recorded."""
    prediction = _predict_with_award(test_db, award_floor_rate=None)

    assert prediction["legal_floor_bid_rate"] is None
    assert prediction["floor_bid_rate"] == pytest.approx(_SERVICE_CATEGORY_FLOOR)
    assert prediction["floor_guardrail_source"] == "category"


def test_predict_price_request_legal_floor_overrides_published_award(test_db):
    """An explicit client legal_floor_bid_rate wins over the published award_floor_rate."""
    prediction = _predict_with_award(
        test_db, award_floor_rate=0.88, request_legal_floor_bid_rate=0.90
    )

    assert prediction["legal_floor_bid_rate"] == pytest.approx(0.90)
    assert prediction["floor_bid_rate"] == pytest.approx(0.90)


# --------------------------------------------------------------------------- #
# prepare_prediction_inputs — the single combination helper every predict path
# routes through. It bundles the four notice-derived inputs so a caller cannot
# partially adopt the preprocessing (e.g. keep the base/text but drop the
# published floor, as the backtest/smoke/holdout paths previously did).
# --------------------------------------------------------------------------- #


def test_prepare_prediction_inputs_bundles_base_text_floor_tier(test_db):
    """The bundle carries 기초금액 base, title+desc+requirements text, the published
    낙찰하한, and the construction tier inputs — exactly the live-path preprocessing."""
    project = Project(
        title="한강 준설 2단계(규격·가격 동시)",
        description="본문 설명",
        requirements="자격 요건",
        budget_estimate=_BUDGET_ESTIMATE,
        category="construction",
        award_floor_rate=0.88,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    inputs = prepare_prediction_inputs(test_db, project)

    # base = 기초금액 (base_amount), not 추정가격
    assert inputs.bid_base == pytest.approx(_VAT_BASE_AMOUNT)
    # text = the shared assembler output (title + description + requirements)
    assert inputs.text == "한강 준설 2단계(규격·가격 동시) 본문 설명 자격 요건"
    # published floor folded in (normalized fraction)
    assert inputs.legal_floor_bid_rate == pytest.approx(0.88)
    # construction tier inputs: estimation = 추정가격, reference = 공고 KST day
    assert inputs.estimation_amount == pytest.approx(_BUDGET_ESTIMATE)
    assert inputs.reference_date == date(2026, 2, 1)


def test_prepare_prediction_inputs_matches_individual_helpers(test_db):
    """Composition contract: the bundle equals calling each helper directly, so the
    live paths that delegate to it stay byte-identical (diff 0)."""
    project = _make_project(test_db)
    project.award_floor_rate = 0.9
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    inputs = prepare_prediction_inputs(test_db, project)

    assert inputs.bid_base == resolve_notice_bid_base(test_db, project)
    est, ref = resolve_notice_legal_floor_inputs(project)
    assert inputs.estimation_amount == est
    assert inputs.reference_date == ref
    assert inputs.legal_floor_bid_rate == resolve_notice_legal_floor_bid_rate(project)


def test_prepare_prediction_inputs_respects_request_floor_override(test_db):
    """An explicit request override flows through the bundle (client wins over
    the notice's published 하한), matching resolve_notice_legal_floor_bid_rate."""
    project = _make_project(test_db)
    project.award_floor_rate = 0.88
    test_db.commit()

    inputs = prepare_prediction_inputs(
        test_db, project, request_legal_floor_bid_rate=0.91
    )

    assert inputs.legal_floor_bid_rate == pytest.approx(0.91)


def test_prepare_prediction_inputs_falls_back_to_budget_estimate(test_db):
    """No collected 기초금액 row → base falls back to 추정가격; no published 하한 →
    legal floor is None (config floor preserved downstream)."""
    project = _make_project(test_db)  # no HistoricalData base row, no award_floor_rate
    test_db.commit()

    inputs = prepare_prediction_inputs(test_db, project)

    assert inputs.bid_base == pytest.approx(_BUDGET_ESTIMATE)
    assert inputs.legal_floor_bid_rate is None
