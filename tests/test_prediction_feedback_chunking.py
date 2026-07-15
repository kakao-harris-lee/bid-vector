"""Regression tests for IN-clause chunking in prediction-feedback loaders.

These guard the fix for PostgreSQL's 65,535 bind-parameter limit: the
``project_id.in_(...)`` loaders in
``app.services.prediction_feedback.PredictionFeedbackService`` now issue the IN
filter in chunks of ``_IN_CHUNK_SIZE``. The tests force a tiny chunk size so a
``project_ids`` list spans multiple chunks, then assert that the per-project
"latest row" dedup stays correct across chunk boundaries and never raises.

Project ids are unique, so every project (and thus all of its rows) lands in
exactly one chunk. With the loaders' unchanged ``order_by`` applied per chunk,
the first row seen per project is still that project's latest.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, PricePrediction, Project
from app.services import prediction_feedback
from app.services.prediction_feedback import PredictionFeedbackService


def _make_project(test_db, index: int) -> Project:
    project = Project(
        title=f"Chunking Source {index}",
        description="chunking regression source",
        requirements="chunking window",
        budget_estimate=100_000_000.0,
        category="software",
    )
    test_db.add(project)
    test_db.flush()  # assign project.id
    return project


def _seed_prediction_projects(
    test_db,
    *,
    operator_id: int,
    project_count: int,
    rows_per_project: int = 3,
) -> tuple[list[int], dict[int, float]]:
    """Seed several projects, each with multiple predictions of differing
    ``created_at``. The row with the newest ``created_at`` carries a unique
    ``predicted_price`` so the expected latest can be asserted per project.
    """
    base = utc_now() - timedelta(days=30)
    project_ids: list[int] = []
    expected_latest_price: dict[int, float] = {}
    for index in range(project_count):
        project = _make_project(test_db, index)
        project_ids.append(project.id)
        for row in range(rows_per_project):
            # Inserted oldest -> newest, so the last row (largest created_at and
            # id) is the latest under the loader's order_by.
            predicted_price = 100_000_000.0 + index * 1000 + row
            if row == rows_per_project - 1:
                expected_latest_price[project.id] = predicted_price
            test_db.add(
                PricePrediction(
                    user_id=operator_id,
                    project_id=project.id,
                    predicted_price=predicted_price,
                    price_range_min=90_000_000.0,
                    price_range_max=110_000_000.0,
                    confidence_score=0.8,
                    model_version="v-test",
                    created_at=base + timedelta(hours=row),
                )
            )
    test_db.commit()
    return project_ids, expected_latest_price


def _seed_decision_projects(
    test_db,
    *,
    operator_id: int,
    project_count: int,
    rows_per_project: int = 3,
) -> tuple[list[int], dict[int, float]]:
    """Seed several projects, each with multiple bid decisions of differing
    ``updated_at``; the newest carries a unique ``recommended_amount``.
    """
    base = utc_now() - timedelta(days=30)
    project_ids: list[int] = []
    expected_latest_amount: dict[int, float] = {}
    for index in range(project_count):
        project = _make_project(test_db, index)
        project_ids.append(project.id)
        for row in range(rows_per_project):
            recommended_amount = 50_000_000.0 + index * 1000 + row
            if row == rows_per_project - 1:
                expected_latest_amount[project.id] = recommended_amount
            test_db.add(
                BidDecisionRecord(
                    project_id=project.id,
                    operator_id=operator_id,
                    recommended_amount=recommended_amount,
                    updated_at=base + timedelta(hours=row),
                )
            )
    test_db.commit()
    return project_ids, expected_latest_amount


def test_chunked_splits_and_covers_all_ids():
    """The helper partitions ids into slices covering every id exactly once."""
    ids = list(range(5))
    chunks = list(prediction_feedback._chunked(ids, size=2))

    assert chunks == [[0, 1], [2, 3], [4]]
    flat = [value for chunk in chunks for value in chunk]
    assert flat == ids  # no dropped or duplicated ids


def test_chunked_resolves_module_constant_at_call_time(monkeypatch):
    """Patching the module constant changes chunking without rebinding a default."""
    monkeypatch.setattr(prediction_feedback, "_IN_CHUNK_SIZE", 3)
    chunks = list(prediction_feedback._chunked(list(range(7))))

    assert [len(chunk) for chunk in chunks] == [3, 3, 1]


def test_load_latest_predictions_dedup_across_chunks(test_db, monkeypatch):
    """Latest-per-project dedup must survive project_ids spanning many chunks."""
    operator = ensure_operator_account(test_db)
    project_ids, expected_latest_price = _seed_prediction_projects(
        test_db, operator_id=operator.id, project_count=5, rows_per_project=3
    )

    # 5 ids in chunks of 2 => 3 chunks, so the IN filter runs per chunk.
    monkeypatch.setattr(prediction_feedback, "_IN_CHUNK_SIZE", 2)

    service = PredictionFeedbackService()
    latest = service._load_latest_predictions(
        test_db, operator_id=operator.id, project_ids=project_ids
    )

    assert set(latest.keys()) == set(project_ids)
    for project_id in project_ids:
        assert latest[project_id].predicted_price == expected_latest_price[project_id]


def test_load_latest_predictions_no_raise_with_oversized_id_list(test_db, monkeypatch):
    """A project_ids list far larger than the chunk size must not raise."""
    operator = ensure_operator_account(test_db)
    project_ids, expected_latest_price = _seed_prediction_projects(
        test_db, operator_id=operator.id, project_count=7, rows_per_project=2
    )

    monkeypatch.setattr(prediction_feedback, "_IN_CHUNK_SIZE", 2)

    service = PredictionFeedbackService()
    latest = service._load_latest_predictions(
        test_db, operator_id=operator.id, project_ids=project_ids
    )

    assert set(latest.keys()) == set(project_ids)
    for project_id in project_ids:
        assert latest[project_id].predicted_price == expected_latest_price[project_id]


def test_load_latest_decisions_dedup_across_chunks(test_db, monkeypatch):
    """Decisions loader dedup must also survive multi-chunk project_ids."""
    operator = ensure_operator_account(test_db)
    project_ids, expected_latest_amount = _seed_decision_projects(
        test_db, operator_id=operator.id, project_count=4, rows_per_project=3
    )

    monkeypatch.setattr(prediction_feedback, "_IN_CHUNK_SIZE", 2)

    service = PredictionFeedbackService()
    latest = service._load_latest_decisions(
        test_db, operator_id=operator.id, project_ids=project_ids
    )

    assert set(latest.keys()) == set(project_ids)
    for project_id in project_ids:
        assert latest[project_id].recommended_amount == expected_latest_amount[project_id]
