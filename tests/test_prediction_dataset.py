"""Tests for prediction dataset helpers."""

from datetime import timedelta

from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import BASIS_CLEAN, BASIS_DERIVED_YEGA
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

    test_db.add_all(
        [
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
        ]
    )
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


def test_training_dataset_uses_tender_result_when_historical_bid_rate_missing(test_db):
    """개찰 결과가 있으면 HistoricalData.bid_rate=0이어도 가격학습 라벨로 사용한다."""
    now = utc_now()
    project = Project(
        title="Tender fallback",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=project.id,
                notice_number="TENDER-FALLBACK",
                agency_name="조달청",
                category="service",
                base_amount=100_000_000.0,
                predicted_price=90_000_000.0,
                bid_rate=0.0,
                opened_at=now - timedelta(days=1),
            ),
            TenderResult(
                project_id=project.id,
                winning_company="낙찰사",
                winning_amount=88_035_000.0,
                winning_rate=88.035,
                result_status="낙찰",
                announced_at=now - timedelta(hours=12),
            ),
            TenderResult(
                project_id=project.id,
                winning_company="",
                winning_amount=0.0,
                winning_rate=0.0,
                result_status="등록공고",
                announced_at=now - timedelta(hours=1),
            ),
        ]
    )
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="service",
        limit=10,
        explicit_bid_rate_only=True,
    )

    assert dataset["summary"]["sample_count"] == 1
    point = dataset["series"][0]
    assert point["notice_number"] == "TENDER-FALLBACK"
    assert point["bid_rate"] == 0.88035
    assert point["bid_rate_source"] == "tender_result_winning_rate"
    assert point["winning_amount"] == 88_035_000.0
    assert point["tender_result_status"] == "낙찰"


def test_training_dataset_excludes_rows_opened_after_as_of(test_db):
    """학습 데이터셋은 as_of 이후 opened_at 샘플을 제외해 미래 데이터 누수를 막는다."""
    now = utc_now()
    past_project = Project(
        title="Past award",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
    )
    future_project = Project(
        title="Future award",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
    )
    test_db.add_all([past_project, future_project])
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=past_project.id,
                notice_number="PAST-AWARD",
                agency_name="조달청",
                category="construction",
                base_amount=100_000_000.0,
                predicted_price=90_000_000.0,
                bid_rate=0.9,
                opened_at=now - timedelta(hours=1),
            ),
            HistoricalData(
                project_id=future_project.id,
                notice_number="FUTURE-AWARD",
                agency_name="조달청",
                category="construction",
                base_amount=100_000_000.0,
                predicted_price=90_000_000.0,
                bid_rate=0.91,
                opened_at=now + timedelta(hours=1),
            ),
        ]
    )
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="construction",
        limit=10,
        explicit_bid_rate_only=True,
        as_of=now,
    )

    assert [item["notice_number"] for item in dataset["series"]] == ["PAST-AWARD"]


def test_training_dataset_business_group_prefers_project_business_type_code(test_db):
    """카테고리와 업종코드가 다르면 업종코드 기반 business_group을 우선한다."""
    now = utc_now()
    project = Project(
        title="공사 업종코드가 있는 용역 카테고리",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="service",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add(
        HistoricalData(
            project_id=project.id,
            notice_number="GROUP-CODE-FIRST",
            agency_name="조달청",
            category="service",
            base_amount=100_000_000.0,
            predicted_price=90_000_000.0,
            bid_rate=0.9,
            opened_at=now - timedelta(days=1),
        )
    )
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="service",
        limit=10,
        explicit_bid_rate_only=True,
    )

    point = dataset["series"][0]
    assert point["business_group"] == "construction"
    assert point["business_group_source"] == "business_type_code"
    assert point["business_type_code"] == "0411"


def test_dataset_base_amount_preserves_clean_and_unclassified_rows(test_db):
    """clean·basis 미분류(NULL) 행은 emitted base_amount가 raw 그대로여야 한다(회귀 0).

    basis-aware 접근(get_reliable_base, #225)이 학습·데이터셋 조립에 배선돼도, clean
    태그와 미분류(NULL) 태그 행은 저장된 base_amount를 바이트 동일하게 낸다. clean 행은
    ``base_amount_estimated`` 가 존재해도 이를 무시하고 base_amount를 신뢰한다.
    """
    now = utc_now()
    project = Project(
        title="Basis clean/unclassified",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=project.id,
                notice_number="CLEAN-ROW",
                agency_name="조달청",
                category="service",
                base_amount=100_000_000.0,
                base_amount_basis=BASIS_CLEAN,
                # clean 행이면 복구 추정치가 있어도 base_amount를 신뢰 → 무시돼야 한다.
                base_amount_estimated=90_000_000.0,
                predicted_price=90_000_000.0,
                bid_rate=0.9,
                opened_at=now - timedelta(days=2),
            ),
            HistoricalData(
                project_id=project.id,
                notice_number="UNCLASSIFIED-ROW",
                agency_name="조달청",
                category="service",
                base_amount=120_000_000.0,
                base_amount_basis=None,
                base_amount_estimated=None,
                predicted_price=110_000_000.0,
                bid_rate=0.91,
                opened_at=now - timedelta(days=1),
            ),
        ]
    )
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="service",
        limit=10,
        explicit_bid_rate_only=True,
    )

    by_notice = {point["notice_number"]: point for point in dataset["series"]}
    assert by_notice["CLEAN-ROW"]["base_amount"] == 100_000_000.0
    assert by_notice["UNCLASSIFIED-ROW"]["base_amount"] == 120_000_000.0


def test_dataset_base_amount_recovers_reliable_base_for_derived_yega_row(test_db):
    """non-clean(derived-yega) 행은 emitted base_amount·파생 bid_rate가 복구 추정치를 쓴다.

    #199 오염(``win ÷ winning_rate`` 역산 = 예정가-basis)이 태깅된 행은 저장된
    base_amount(예정가)가 아니라 복수예비가격 midpoint 복구 추정치
    (``base_amount_estimated``)를 학습 base로 써야 한다. winning_rate가 없어 파생 rate가
    winning_amount ÷ base로 계산되는 경로도 복구 base를 분모로 삼는지 함께 고정한다.
    """
    now = utc_now()
    project = Project(
        title="Basis derived-yega recovery",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=project.id,
                notice_number="POLLUTED-ROW",
                agency_name="조달청",
                category="service",
                # 예정가-basis 오염값(신뢰 불가) — 절대 학습 base로 쓰이면 안 된다.
                base_amount=100_000_000.0,
                base_amount_basis=BASIS_DERIVED_YEGA,
                # 복수예비가격 midpoint로 복구한 실제 기초금액.
                base_amount_estimated=88_000_000.0,
                predicted_price=0.0,
                bid_rate=0.0,
                opened_at=now - timedelta(days=1),
            ),
            TenderResult(
                project_id=project.id,
                winning_company="낙찰사",
                winning_amount=80_000_000.0,
                # winning_rate 미상 → winning_amount ÷ base 파생 경로 강제.
                winning_rate=0.0,
                result_status="낙찰",
                announced_at=now - timedelta(hours=12),
            ),
        ]
    )
    test_db.commit()

    dataset = PredictionDatasetService().build_training_dataset(
        test_db,
        category="service",
        limit=10,
    )

    assert dataset["summary"]["sample_count"] == 1
    point = dataset["series"][0]
    assert point["notice_number"] == "POLLUTED-ROW"
    # emitted base = 복구 추정치(88M), 오염 예정가(100M)가 아니다.
    assert point["base_amount"] == 88_000_000.0
    # 파생 bid_rate 분모도 복구 base(80M ÷ 88M), 오염 base(80M ÷ 100M=0.8)가 아니다.
    assert point["bid_rate_source"] == "tender_result_winning_amount"
    assert point["bid_rate"] == round(80_000_000.0 / 88_000_000.0, 6)
