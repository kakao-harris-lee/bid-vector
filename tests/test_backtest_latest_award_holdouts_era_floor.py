"""Era-correct 공사 법정 낙찰하한 wiring in the latest-award holdout harness (#197 후속).

``scripts/backtest_latest_award_holdouts.py`` is the natural harness for MEASURING the
#197 construction 낙찰하한 tier (2026-01-30 +2%p, 추정가격 구간·시행일 인지), but its
``predict_price`` call did not pass estimation_amount/reference_date, so <10억 공사
holdout targets never received the tier (floor stayed the flat 0.87). This module pins
the holdout path now feeding the era-correct tier inputs, keyed on each notice's OWN
date via the same helper the live path uses (``resolve_notice_legal_floor_inputs``).

Coverage:
  (spy value table)  era × 추정가격 구간 → the (estimation_amount, reference_date) the
        holdout harness feeds predict_price, incl. the UTC→KST boundary that flips era.
        estimation_amount is the notice 추정가격 (budget_estimate), NOT the pricing base.
  (end-to-end)  through the REAL predictor, the holdout ``recommended`` rate lands on the
        era-correct floor for a <10억 공사 — 0.87745 (구율) vs 0.89745 (신율).

The tier value math (max()-fold red line, brackets) is pinned in
tests/test_guardrail_legal_floor.py; here we pin only the *holdout wiring*.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.models import HistoricalData, Project, TenderResult
from app.services.prediction_dataset import PredictionDatasetService
from scripts import backtest_latest_award_holdouts as holdouts
from scripts.backtest_latest_award_holdouts import HoldoutTarget, evaluate_target

# created_at chosen so the KST calendar day is unambiguously in the intended era.
_OLD_ERA_UTC = datetime(2026, 1, 29, 0, 0, tzinfo=UTC)  # KST 01-29 → 구율
_NEW_ERA_UTC = datetime(2026, 1, 30, 0, 0, tzinfo=UTC)  # KST 01-30 → 신율
# 2026-01-29 20:00 UTC == 2026-01-30 05:00 KST → KST day is 신율 though UTC day is 01-29.
_KST_BOUNDARY_UTC = datetime(2026, 1, 29, 20, 0, tzinfo=UTC)


def _build_target(
    test_db, *, budget_estimate: float, created_at: datetime, base_amount: float
) -> HoldoutTarget:
    """Persist a construction award and wrap it as a HoldoutTarget.

    ``base_amount`` (기초금액) is the pricing budget; ``budget_estimate`` (추정가격) is the
    tier-bracket input — kept distinct so the test proves the harness reads the RIGHT one.
    """
    project = Project(
        title="항만 정비 공사",
        description="방파제 보강 및 준설",
        requirements="토목공사업 면허",
        budget_estimate=budget_estimate,
        category="construction",
        status="awarded",
        issuing_agency="부산지방해양수산청",
        business_type_code=None,
        created_at=created_at,
    )
    test_db.add(project)
    test_db.flush()
    historical = HistoricalData(
        project_id=project.id,
        notice_number=f"HOLDOUT-{project.id}",
        agency_name="부산지방해양수산청",
        category="construction",
        base_amount=base_amount,
        predicted_price=base_amount * 0.88,
        bid_rate=0.88,
        opened_at=created_at + timedelta(days=5),
    )
    result = TenderResult(
        project_id=project.id,
        winning_company="Winner",
        winning_amount=base_amount * 0.88,
        winning_rate=0.88,
        result_status="awarded",
        announced_at=created_at + timedelta(days=20),
    )
    test_db.add_all([historical, result])
    test_db.flush()
    event_at = created_at + timedelta(days=20)
    return HoldoutTarget(
        group="construction",
        group_source="test",
        result=result,
        project=project,
        historical=historical,
        event_at=event_at,
        available_at=event_at,
    )


class _SpyPredictPrice:
    """Captures predict_price kwargs; returns a minimal usable payload."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "predicted_price": float(kwargs.get("budget") or 0.0) * 0.88,
            "predicted_bid_rate": 0.88,
            "bid_rate_candidates": [],
        }


# ---------------------------------------------------------------------------
# (spy value table) era × 추정가격 구간 → (estimation_amount, reference_date) fed to predict
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "created_at, budget_estimate, expected_ref, label",
    [
        (_OLD_ERA_UTC, 500_000_000, date(2026, 1, 29), "구율 <10억"),
        (_OLD_ERA_UTC, 3_000_000_000, date(2026, 1, 29), "구율 10~50억"),
        (_OLD_ERA_UTC, 8_000_000_000, date(2026, 1, 29), "구율 50~100억"),
        (_NEW_ERA_UTC, 500_000_000, date(2026, 1, 30), "신율 <10억"),
        (_NEW_ERA_UTC, 15_000_000_000, date(2026, 1, 30), "100억+ 종심제"),
        (_KST_BOUNDARY_UTC, 500_000_000, date(2026, 1, 30), "KST 경계 → 신율"),
    ],
)
def test_holdout_feeds_notice_estimation_and_reference_date(
    test_db, monkeypatch, created_at, budget_estimate, expected_ref, label
):
    spy = _SpyPredictPrice()
    monkeypatch.setattr(holdouts, "predict_price", spy)

    target = _build_target(
        test_db,
        budget_estimate=budget_estimate,
        created_at=created_at,
        base_amount=90_000_000,  # 기초금액 — distinct from 추정가격 above
    )
    evaluate_target(
        test_db,
        service=PredictionDatasetService(),
        target=target,
        history_limit=20,
        thresholds=(0.9,),
    )

    assert spy.calls, "predict_price was not invoked"
    call = spy.calls[0]
    assert call["category"] == "construction"
    # estimation_amount is the notice 추정가격, NOT the 90M pricing base.
    assert call["estimation_amount"] == budget_estimate, label
    # reference_date is the notice's OWN KST calendar day (era selector, leakage-safe).
    assert call["reference_date"] == expected_ref, label


# ---------------------------------------------------------------------------
# (end-to-end) the holdout recommended rate lands on the era-correct <10억 floor
# ---------------------------------------------------------------------------
def _seed_low_construction_history(test_db, *, as_of: datetime) -> None:
    """Unlinked construction history well below the floor so the floor binds."""
    for index in range(20):
        test_db.add(
            HistoricalData(
                project_id=None,
                notice_number=f"HIST-{index}",
                agency_name="부산지방해양수산청",
                category="construction",
                base_amount=100_000_000,
                predicted_price=75_000_000,
                bid_rate=0.75,
                opened_at=as_of - timedelta(days=10 + index),
            )
        )
    test_db.flush()


def _recommended_rate(test_db, *, created_at: datetime) -> float:
    target = _build_target(
        test_db,
        budget_estimate=500_000_000,  # <10억 → tier applies
        created_at=created_at,
        base_amount=500_000_000,
    )
    as_of = min(target.event_at, target.available_at) - timedelta(seconds=1)
    _seed_low_construction_history(test_db, as_of=as_of)
    payload = evaluate_target(
        test_db,
        service=PredictionDatasetService(),
        target=target,
        history_limit=20,
        thresholds=(0.9,),
    )
    recommended = next(
        s for s in payload["scenarios"] if s["scenario"] == "recommended"
    )
    return recommended["rate"]


def test_holdout_recommended_rate_is_era_correct_floor_for_small_construction(
    test_db, monkeypatch
):
    """A <10억 공사 holdout resolves the era-correct floor through the REAL predictor.

    History sits at 0.75 (below the floor), so the guardrail floor binds and the
    recommended rate sits AT the era floor plus the fixed 안전마진: 구율 0.87745 vs 신율
    0.89745. The margin is identical for both eras, so it cancels in the delta — the
    +2%p difference is the revision itself, and only the notice date changed. Asserting
    the floor lower bound + the exact delta avoids pinning the margin setting value.
    """
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "PREDICTION_CATEGORY_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(
        settings, "PREDICTION_GROUP_MINIMUM_BID_RATES", {"construction": 0.87}
    )
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {})

    old_rate = _recommended_rate(test_db, created_at=_OLD_ERA_UTC)
    new_rate = _recommended_rate(test_db, created_at=_NEW_ERA_UTC)

    # Each era respects its own <10억 floor (the recommended rate never dips below it).
    assert old_rate >= 0.87745 - 1e-9  # 구율 <10억
    assert new_rate >= 0.89745 - 1e-9  # 신율 <10억
    # Leakage guard: the OLD-era notice did NOT get the 2026-01-30 신율 retroactively.
    assert old_rate < 0.89745
    # The era delta is exactly the +2%p revision (safety margin cancels).
    assert new_rate - old_rate == pytest.approx(0.02, abs=1e-6)
