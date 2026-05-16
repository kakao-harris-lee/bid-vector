"""Tests for prediction dataset helpers."""

from datetime import timedelta

from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.prediction_dataset import PredictionDatasetService


def test_prediction_dataset_service_builds_normalized_series_and_summary(test_db):
    """Dataset service should normalize historical rows and attach latest tender results."""
    now = utc_now()
    project_one = Project(
        title="Dataset Project One",
        description="Historical series project one",
        requirements="Need historical result linkage",
        budget_estimate=100000000.0,
        category="software",
    )
    project_two = Project(
        title="Dataset Project Two",
        description="Historical series project two",
        requirements="Need derived bid rate",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add_all([project_one, project_two])
    test_db.flush()

    test_db.add_all([
        HistoricalData(
            project_id=project_one.id,
            notice_number="DATASET-1",
            agency_name="서울특별시교육청",
            category="software",
            base_amount=100000000.0,
            predicted_price=91000000.0,
            bid_rate=0.91,
            reserve_prices="[100000000.0, 101000000.0, 102000000.0]",
            selected_numbers="[1, 4, 7, 12]",
            opened_at=now - timedelta(days=2),
        ),
        HistoricalData(
            project_id=project_two.id,
            notice_number="DATASET-2",
            agency_name="조달청",
            category="software",
            base_amount=120000000.0,
            predicted_price=111000000.0,
            bid_rate=0.0,
            reserve_prices="[119000000.0, 120500000.0, 121000000.0]",
            selected_numbers="[2, 5, 8, 12]",
            opened_at=now - timedelta(days=1),
        ),
        HistoricalData(
            project_id=project_two.id,
            notice_number="DATASET-INVALID",
            agency_name="조달청",
            category="software",
            base_amount=120000000.0,
            predicted_price=250000000.0,
            bid_rate=2.1,
            opened_at=now - timedelta(days=3),
        ),
        TenderResult(
            project_id=project_one.id,
            winning_company="이전 낙찰사",
            winning_amount=92000000.0,
            winning_rate=92.0,
            result_status="awarded",
            announced_at=now - timedelta(days=5),
        ),
        TenderResult(
            project_id=project_one.id,
            winning_company="최신 낙찰사",
            winning_amount=93000000.0,
            winning_rate=93.0,
            result_status="awarded",
            announced_at=now - timedelta(days=1, hours=12),
        ),
    ])
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="software",
        limit=10,
    )

    assert dataset["summary"]["sample_count"] == 2
    assert dataset["summary"]["project_count"] == 2
    assert dataset["summary"]["agency_count"] == 2
    assert dataset["summary"]["linked_result_count"] == 1
    assert dataset["summary"]["reserve_pattern_sample_count"] == 2
    assert len(dataset["series"]) == 2
    assert dataset["series"][0]["notice_number"] == "DATASET-2"
    assert dataset["series"][0]["bid_rate"] == 0.925
    assert dataset["series"][1]["winning_amount"] == 93000000.0
    assert dataset["series"][1]["tender_result_status"] == "awarded"

    explicit_only_dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="software",
        limit=10,
        explicit_bid_rate_only=True,
    )
    assert explicit_only_dataset["summary"]["sample_count"] == 1
    assert explicit_only_dataset["series"][0]["notice_number"] == "DATASET-1"
