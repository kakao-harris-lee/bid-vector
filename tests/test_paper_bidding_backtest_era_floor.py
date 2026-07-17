"""Era-correct 공사 법정 낙찰하한 wiring in the paper-bidding backtest path (#197 후속).

#197 wired the construction legal 낙찰하한 tier (2026-01-30 +2%p — 추정가격 구간·시행일
인지 선언 테이블) into the LIVE path only (prediction_workflow / opportunity_analysis),
leaving the backtest predict call intentionally unwired so past evaluations kept their
historical numbers. This PR wires the backtest path so each past 공고 resolves the floor
that was in effect AT ITS OWN 날짜 (reference_date = 공고 KST day), never the 2026-01-30
신율 소급 적용 (leakage 차단).

Coverage:
  (spy)  estimation_amount/reference_date reach the backtest predict call, taken from the
         notice's OWN created_at (KST day) — incl. the UTC→KST boundary that flips era.
  (value table)  era × 추정가격 구간 → floor_bid_rate through the REAL predictor stack:
         - 구율 <10억 RISES (0.87745 > flat 0.87)
         - 구율 10~50억 / 50~100억 UNCHANGED (flat 0.87 dominates 0.86745 / 0.85495)
         - 신율 전 구간 RISES (0.89745 / 0.88745 / 0.87495)
         - 100억+ 종심제 → tier 미적용 (flat 0.87)

The tier value semantics themselves (max()-fold red line, E[사정률] conversion) are pinned
in tests/test_guardrail_legal_floor.py; here we pin only the *backtest wiring* — that the
notice date drives the era at the exact call site #197 left unwired.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.models import HistoricalData, Project
from app.services.paper_bidding_backtest import PaperBiddingBacktestService

# created_at chosen so the KST calendar day is unambiguously in the intended era.
# 2026-01-29 00:00 UTC → 2026-01-29 09:00 KST → 구율(< 2026-01-30).
# 2026-01-30 00:00 UTC → 2026-01-30 09:00 KST → 신율(≥ 2026-01-30).
_OLD_ERA_UTC = datetime(2026, 1, 29, 0, 0, tzinfo=UTC)
_NEW_ERA_UTC = datetime(2026, 1, 30, 0, 0, tzinfo=UTC)
# 2026-01-29 20:00 UTC == 2026-01-30 05:00 KST → the notice's KST day is 신율 even though
# its UTC day is still 01-29. Proves the wiring keys on the KST day, not the UTC day.
_KST_BOUNDARY_UTC = datetime(2026, 1, 29, 20, 0, tzinfo=UTC)

_CUTOFF = datetime(2026, 2, 20, tzinfo=UTC)


def _construction_project(*, budget: float, created_at: datetime) -> Project:
    return Project(
        title="항만 정비 공사",
        description="방파제 보강 및 준설",
        requirements="토목공사업 면허",
        budget_estimate=budget,
        category="construction",
        status="awarded",
        issuing_agency="부산지방해양수산청",
        created_at=created_at,
        deadline=created_at + timedelta(days=30),
    )


def _history_row(*, opened_at: datetime, bid_rate: float = 0.80) -> HistoricalData:
    """Unlinked construction history so the real predictor has a sample.

    A low ~0.80 base-relative rate keeps the predicted rate BELOW the floor, so the
    resolved floor binds — but ``floor_bid_rate`` is reported regardless of binding.
    """
    return HistoricalData(
        project_id=None,
        notice_number=f"ERA-{opened_at:%Y%m%d%H%M}",
        agency_name="부산지방해양수산청",
        category="construction",
        base_amount=100_000_000,
        predicted_price=100_000_000 * bid_rate,
        bid_rate=bid_rate,
        reserve_prices=json.dumps([100_000_000]),
        selected_numbers=json.dumps([1]),
        opened_at=opened_at,
    )


class _SpyPredictionPort:
    """Records the kwargs each predict call receives; returns a fixed payload."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def predict_price(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "predicted_bid_rate": 0.88,
            "predictor_name": "spy",
            "predicted_price": 88_000_000.0,
            "price_range_min": 87_000_000.0,
            "price_range_max": 89_000_000.0,
            "confidence_score": 0.7,
            "model_version": "spy",
            "predictor_family": "spy",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "spy",
        }


def _prediction_context_for(test_db, *, budget: float, created_at: datetime, port=None):
    service = PaperBiddingBacktestService(
        **({"price_prediction_port": port} if port is not None else {})
    )
    project = _construction_project(budget=budget, created_at=created_at)
    test_db.add(project)
    # flush (not commit) keeps the in-memory created_at tz-aware and unambiguous.
    test_db.flush()
    return service._build_candidate_prediction_context(
        test_db,
        project=project,
        tender_result=None,
        data_cutoff_at=_CUTOFF,
        cutoff_hours_before_deadline=0,
        history_limit=80,
    )


# ---------------------------------------------------------------------------
# (spy) the tier inputs reach the backtest predict call, from the notice's own date
# ---------------------------------------------------------------------------
def test_backtest_predict_receives_notice_estimation_and_reference_date(test_db):
    port = _SpyPredictionPort()
    _prediction_context_for(
        test_db, budget=500_000_000, created_at=_OLD_ERA_UTC, port=port
    )

    assert port.calls, "predict_price was not invoked"
    call = port.calls[0]
    # estimation_amount is the notice 추정가격 (budget_estimate), NOT some future value.
    assert call["estimation_amount"] == 500_000_000
    # reference_date is the notice's OWN KST calendar day (구율 시대).
    assert call["reference_date"] == date(2026, 1, 29)


def test_backtest_reference_date_uses_kst_day_not_utc(test_db):
    # 2026-01-29 20:00 UTC == 2026-01-30 05:00 KST → the notice's KST day is 신율.
    port = _SpyPredictionPort()
    _prediction_context_for(
        test_db, budget=500_000_000, created_at=_KST_BOUNDARY_UTC, port=port
    )

    # The UTC day is 01-29 (구율) but the KST day is 01-30 (신율): the wiring must key
    # on the KST day so the notice resolves the rate in effect on its Korean 공고일.
    assert port.calls[0]["reference_date"] == date(2026, 1, 30)


# ---------------------------------------------------------------------------
# (value table) era × 추정가격 구간 → floor_bid_rate through the REAL predictor stack
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "created_at, budget, expected_floor, label",
    [
        (_OLD_ERA_UTC, 500_000_000, 0.87745, "구율 <10억 RISES (0.87745 > flat 0.87)"),
        (_OLD_ERA_UTC, 3_000_000_000, 0.87, "구율 10~50억 무변 (flat 0.87 지배 > 0.86745)"),
        (_OLD_ERA_UTC, 8_000_000_000, 0.87, "구율 50~100억 무변 (flat 0.87 지배 > 0.85495)"),
        (_NEW_ERA_UTC, 500_000_000, 0.89745, "신율 <10억 RISES"),
        (_NEW_ERA_UTC, 3_000_000_000, 0.88745, "신율 10~50억 RISES"),
        (_NEW_ERA_UTC, 8_000_000_000, 0.87495, "신율 50~100억 RISES"),
        (_NEW_ERA_UTC, 15_000_000_000, 0.87, "100억+ 종심제 → tier 미적용 (flat 0.87)"),
    ],
)
def test_backtest_floor_is_era_correct_through_real_predictor(
    test_db, monkeypatch, created_at, budget, expected_floor, label
):
    """The backtest floor resolves the era in effect on the notice's OWN date.

    Hermetic guardrail settings (flat construction floor 0.87, E[사정률] no-op) so the
    resolved ``floor_bid_rate`` == ``max(0.87, era-tier)`` deterministically. Same
    predictor/history for every row: only created_at (era) and budget (구간) change,
    so a divergence proves the era-correct wiring — not predictor variance.
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

    for index in range(20):
        test_db.add(_history_row(opened_at=_CUTOFF - timedelta(days=5 + index)))
    test_db.flush()

    context = _prediction_context_for(test_db, budget=budget, created_at=created_at)

    assert context.prediction["floor_bid_rate"] == pytest.approx(expected_floor), label


def test_backtest_new_era_floor_strictly_above_old_era_for_small_construction(
    test_db, monkeypatch
):
    """The SAME <10억 공고 resolves a higher floor post-revision than pre-revision.

    This is the core pre/post interpretation difference the wiring introduces: era is
    the only variable, so the floor delta is the +2%p revision, not any data change.
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

    for index in range(20):
        test_db.add(_history_row(opened_at=_CUTOFF - timedelta(days=5 + index)))
    test_db.flush()

    old = _prediction_context_for(test_db, budget=500_000_000, created_at=_OLD_ERA_UTC)
    new = _prediction_context_for(test_db, budget=500_000_000, created_at=_NEW_ERA_UTC)

    old_floor = old.prediction["floor_bid_rate"]
    new_floor = new.prediction["floor_bid_rate"]
    assert new_floor > old_floor
    assert old_floor == pytest.approx(0.87745)
    assert new_floor == pytest.approx(0.89745)
