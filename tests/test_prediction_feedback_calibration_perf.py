"""Performance-focused regression tests for prediction-feedback calibration.

These guard the data-loading optimizations in
``app.services.prediction_feedback.PredictionFeedbackService``:

1. ``build_calibration_context`` must not issue a per-row lazy SELECT for
   ``TenderResult.project`` (the previous N+1 over the full window).
2. The calibration numbers must stay byte-identical after the optimization.
3. Within one service instance (= one monitor run) the recent window must be
   loaded at most once even when called for multiple candidate categories.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import (
    HistoricalData,
    PricePrediction,
    Project,
    TenderResult,
)
from app.services.prediction_feedback import PredictionFeedbackService


@contextmanager
def count_select_statements(session):
    """Count SELECT statements emitted on the session's bound engine."""
    engine = session.get_bind()
    counter = {"select": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["select"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def _seed_calibration_window(test_db, *, operator_id: int, count: int = 12) -> None:
    """Seed many linked project/prediction/tender_result triples across categories.

    Half the rows are ``software`` and half are ``construction`` so the category
    filter exercises both the matched and unmatched lazy-load paths.
    """
    announced = utc_now() - timedelta(days=10)
    rows = []
    for index in range(count):
        category = "software" if index % 2 == 0 else "construction"
        project = Project(
            title=f"Calibration Source {index}",
            description="N+1 regression source",
            requirements="calibration window",
            budget_estimate=100_000_000.0,
            category=category,
        )
        test_db.add(project)
        test_db.flush()  # assign project.id

        rows.append(
            PricePrediction(
                user_id=operator_id,
                project_id=project.id,
                # +5% over the winning amount so the signed error is deterministic.
                predicted_price=105_000_000.0,
                price_range_min=100_000_000.0,
                price_range_max=110_000_000.0,
                confidence_score=0.8,
                model_version="v-test",
            )
        )
        rows.append(
            HistoricalData(
                project_id=project.id,
                notice_number=f"CAL-{index}",
                agency_name="서울특별시교육청",
                category=category,
                base_amount=100_000_000.0,
                predicted_price=100_000_000.0,
                bid_rate=0.95,
            )
        )
        rows.append(
            TenderResult(
                project_id=project.id,
                winning_company="낙찰사",
                winning_amount=100_000_000.0,
                winning_rate=95.0,
                result_status="awarded",
                announced_at=announced,
            )
        )

    test_db.add_all(rows)
    test_db.commit()


def test_build_calibration_context_avoids_project_n_plus_one(test_db):
    """A single calibration call must issue a small constant number of SELECTs."""
    operator = ensure_operator_account(test_db)
    _seed_calibration_window(test_db, operator_id=operator.id, count=12)

    service = PredictionFeedbackService()
    with count_select_statements(test_db) as counter:
        result = service.build_calibration_context(
            test_db,
            operator_id=operator.id,
            category="software",
            agency_name="서울특별시교육청",
        )

    assert result is not None
    assert result["sample_count"] == 6
    # tender_results (joinedload project) + predictions + histories => 3 core
    # SELECTs. We allow a little slack for operator resolution but reject the
    # per-row N+1 (which would scale with the number of tender results).
    assert counter["select"] <= 4, f"unexpected SELECT count {counter['select']} (N+1 regression?)"


def test_build_calibration_context_result_is_invariant(test_db):
    """The calibration numbers must match a deterministic hand-computed expectation."""
    operator = ensure_operator_account(test_db)
    _seed_calibration_window(test_db, operator_id=operator.id, count=12)

    service = PredictionFeedbackService()
    result = service.build_calibration_context(
        test_db,
        operator_id=operator.id,
        category="software",
        agency_name="서울특별시교육청",
    )

    assert result is not None
    # predicted 105M vs winning 100M => signed error +0.05 for every sample.
    assert result["sample_count"] == 6
    assert result["agency_match_sample_count"] == 6
    assert result["average_signed_error_rate"] == 0.05
    assert result["average_absolute_error_rate"] == 0.05
    # confidence_factor = min(6/8, 1.0) = 0.75
    # applied = -0.05 * (0.55 + 0.45*0.75) = -0.044375, *1.05 (agency match)
    #         = -0.04659375 -> rounded 4dp -> -0.0466, within +/-0.08 cap.
    assert result["applied_adjustment_rate"] == -0.0466


def test_recent_window_is_loaded_once_per_instance(test_db, monkeypatch):
    """Repeated candidates in one run must reuse the cached recent window."""
    operator = ensure_operator_account(test_db)
    _seed_calibration_window(test_db, operator_id=operator.id, count=12)

    service = PredictionFeedbackService()

    load_calls = {"count": 0}
    original_loader = service._load_recent_tender_results

    def _spy_loader(db, *, date_from):
        load_calls["count"] += 1
        return original_loader(db, date_from=date_from)

    monkeypatch.setattr(service, "_load_recent_tender_results", _spy_loader)

    first = service.build_calibration_context(
        test_db,
        operator_id=operator.id,
        category="software",
        agency_name="서울특별시교육청",
    )
    second = service.build_calibration_context(
        test_db,
        operator_id=operator.id,
        category="construction",
        agency_name="서울특별시교육청",
    )

    assert first is not None
    assert second is not None
    # The category filter is applied in memory on the cached window, so the
    # underlying loader runs exactly once for both candidate categories.
    assert load_calls["count"] == 1
    # Distinct categories still produce their own filtered sample sets.
    assert first["sample_count"] == 6
    assert second["sample_count"] == 6


def test_recent_window_cache_keys_separate_operators(test_db):
    """Different operator ids must not share a cached window."""
    operator = ensure_operator_account(test_db)
    _seed_calibration_window(test_db, operator_id=operator.id, count=4)

    service = PredictionFeedbackService()
    service.build_calibration_context(
        test_db,
        operator_id=operator.id,
        category="software",
    )
    # A second operator id (no predictions) keys a separate cache entry and
    # therefore returns None rather than reusing the first operator's window.
    other = service.build_calibration_context(
        test_db,
        operator_id=operator.id + 9999,
        category="software",
    )

    assert other is None
    assert (operator.id, 365) in service._recent_window_cache
    assert (operator.id + 9999, 365) in service._recent_window_cache
