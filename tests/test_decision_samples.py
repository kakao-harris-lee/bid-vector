"""Tests for the 의사결정 증적 샘플(decision evidence samples) export endpoint.

Roadmap 0단계 L87 "예측/추천 API 응답 샘플 캡처(증적 보관)".

Covers: recent-first ordering, prediction null-tolerance, recommended_bid_rate
computation, the truncated flag, CSV formula-injection neutralization + header +
one-row-per-sample, single-operator filter correctness (a different operator's
decision is excluded), and the empty-window honest-zeros path.
"""

from __future__ import annotations

import csv as _csv
import io as _io
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, PricePrediction, Project, User
from app.schemas.decision_samples import DECISION_SAMPLES_NOTES

SAMPLES_PATH = "/api/v1/operations/decision-samples"


def _seed_project(
    test_db,
    *,
    title: str = "증적 샘플 공고",
    budget: float = 100_000_000.0,
    category: str = "construction",
    notice_number: str = "20260101-001",
) -> Project:
    project = Project(
        title=title,
        description="evidence sample",
        requirements="",
        budget_estimate=budget,
        category=category,
        notice_number=notice_number,
        demand_agency="수요기관 X",
        issuing_agency="발주기관 Y",
        business_type_code="0411",
        business_type_label="건축공사",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _seed_decision(
    test_db,
    *,
    operator: User,
    project: Project,
    recommended_amount: float = 90_000_000.0,
    probability: float = 0.71,
    created_at: datetime | None = None,
) -> BidDecisionRecord:
    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        first_decided_at=created_at or utc_now(),
        recommended_amount=recommended_amount,
        probability_score=probability,
        matched_score=0.8,
        priority_score=0.77,
        competitiveness_score=0.6,
        reasoning="가격 적합도(추정) 점수를 반영했습니다.",
    )
    if created_at is not None:
        decision.created_at = created_at
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)
    return decision


def _seed_prediction(
    test_db,
    *,
    operator: User,
    project: Project,
    predicted_price: float,
    created_at: datetime | None = None,
    predictor_name: str = "historical_statistical",
) -> PricePrediction:
    prediction = PricePrediction(
        user_id=operator.id,
        project_id=project.id,
        predicted_price=predicted_price,
        price_range_min=predicted_price * 0.95,
        price_range_max=predicted_price * 1.05,
        confidence_score=0.66,
        predictor_name=predictor_name,
        predictor_family="statistical",
        pricing_mode="heuristic",
    )
    if created_at is not None:
        prediction.created_at = created_at
    test_db.add(prediction)
    test_db.commit()
    test_db.refresh(prediction)
    return prediction


def test_decision_samples_recent_first_with_and_without_prediction(client, test_db):
    operator = ensure_operator_account(test_db)

    older_project = _seed_project(test_db, title="오래된 공고", notice_number="20260101-OLD")
    newer_project = _seed_project(test_db, title="최근 공고", notice_number="20260101-NEW")

    # Older decision WITHOUT a stored prediction.
    older = _seed_decision(
        test_db,
        operator=operator,
        project=older_project,
        created_at=utc_now() - timedelta(days=5),
    )
    # Newer decision WITH a stored prediction.
    newer = _seed_decision(
        test_db,
        operator=operator,
        project=newer_project,
        created_at=utc_now() - timedelta(days=1),
    )
    _seed_prediction(
        test_db,
        operator=operator,
        project=newer_project,
        predicted_price=88_000_000.0,
    )

    response = client.get(SAMPLES_PATH)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["operator_id"] == operator.id
    assert payload["period_days"] == 30
    assert payload["sample_count"] == 2
    assert payload["truncated"] is False
    assert payload["notes"] == DECISION_SAMPLES_NOTES

    samples = payload["samples"]
    # Most-recent-first ordering.
    assert [s["decision_record_id"] for s in samples] == [newer.id, older.id]

    newer_sample = samples[0]
    # recommended 90M / budget 100M = 0.9.
    assert newer_sample["recommended_bid_rate"] == 0.9
    assert newer_sample["title"] == "최근 공고"
    assert newer_sample["notice_number"] == "20260101-NEW"
    assert newer_sample["prediction"] is not None
    assert newer_sample["prediction"]["predicted_price"] == 88_000_000.0
    assert newer_sample["prediction"]["predictor_family"] == "statistical"

    older_sample = samples[1]
    # No stored prediction → null-tolerant.
    assert older_sample["prediction"] is None
    assert older_sample["probability_score"] == 0.71


def test_decision_samples_picks_latest_prediction_per_project(client, test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    _seed_decision(test_db, operator=operator, project=project)

    _seed_prediction(
        test_db,
        operator=operator,
        project=project,
        predicted_price=80_000_000.0,
        created_at=utc_now() - timedelta(days=3),
        predictor_name="stale_predictor",
    )
    _seed_prediction(
        test_db,
        operator=operator,
        project=project,
        predicted_price=92_000_000.0,
        created_at=utc_now() - timedelta(hours=2),
        predictor_name="fresh_predictor",
    )

    response = client.get(SAMPLES_PATH)
    assert response.status_code == 200, response.text
    sample = response.json()["samples"][0]
    assert sample["prediction"]["predicted_price"] == 92_000_000.0
    assert sample["prediction"]["predictor_name"] == "fresh_predictor"


def test_decision_samples_prediction_is_operator_scoped_same_project(client, test_db):
    """A foreign operator's prediction for the SAME project must not leak.

    Both the canonical operator and a synthetic operator have a stored
    ``PricePrediction`` for the same project. The synthetic row is NEWER. The
    returned sample (for the canonical operator's decision) must reflect the
    CANONICAL prediction, not the foreign newer one. Synthetic data must not
    pollute canonical operator data.
    """
    canonical = ensure_operator_account(test_db)

    synthetic = User(
        username="synthetic-sw-small-busan",
        email="synthetic-sw-small-busan@example.com",
        full_name="Synthetic SW Small Busan",
        company="Synthetic Co",
        hashed_password=get_password_hash("password123"),
        is_active=True,
    )
    test_db.add(synthetic)
    test_db.commit()
    test_db.refresh(synthetic)
    assert synthetic.id != canonical.id

    project = _seed_project(test_db, notice_number="20260101-SHARED")
    _seed_decision(test_db, operator=canonical, project=project)

    # Canonical prediction (older).
    _seed_prediction(
        test_db,
        operator=canonical,
        project=project,
        predicted_price=80_000_000.0,
        created_at=utc_now() - timedelta(hours=6),
        predictor_name="canonical_predictor",
    )
    # Synthetic operator's prediction for the SAME project — NEWER + distinct.
    _seed_prediction(
        test_db,
        operator=synthetic,
        project=project,
        predicted_price=99_000_000.0,
        created_at=utc_now() - timedelta(minutes=5),
        predictor_name="synthetic_predictor",
    )

    response = client.get(SAMPLES_PATH)
    assert response.status_code == 200, response.text
    sample = response.json()["samples"][0]

    assert sample["prediction"] is not None
    assert sample["prediction"]["predictor_name"] == "canonical_predictor"
    assert sample["prediction"]["predicted_price"] == 80_000_000.0


def test_decision_samples_bid_rate_null_when_budget_zero(client, test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db, budget=0.0)
    _seed_decision(test_db, operator=operator, project=project)

    response = client.get(SAMPLES_PATH)
    assert response.status_code == 200, response.text
    sample = response.json()["samples"][0]
    assert sample["budget_estimate"] in (0.0, None)
    assert sample["recommended_bid_rate"] is None


def test_decision_samples_truncated_flag_when_limit_hit(client, test_db):
    operator = ensure_operator_account(test_db)
    for index in range(3):
        project = _seed_project(test_db, notice_number=f"20260101-{index}")
        _seed_decision(
            test_db,
            operator=operator,
            project=project,
            created_at=utc_now() - timedelta(hours=index + 1),
        )

    response = client.get(SAMPLES_PATH, params={"limit": 2})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["limit"] == 2
    assert payload["sample_count"] == 2
    assert payload["truncated"] is True


def test_decision_samples_csv_export_and_injection_neutralized(client, test_db):
    operator = ensure_operator_account(test_db)
    # 외부 나라장터 공고명이 "="로 시작 → CSV injection 벡터.
    dangerous_title = '=HYPERLINK("http://evil","공고")'
    project = _seed_project(test_db, title=dangerous_title)
    _seed_decision(test_db, operator=operator, project=project)

    response = client.get(SAMPLES_PATH, params={"format": "csv"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "decision-samples.csv" in response.headers["content-disposition"]

    body = response.text
    rows = list(_csv.reader(_io.StringIO(body)))

    # Header present.
    assert rows[0][0] == "decision_record_id"
    assert "title" in rows[0]
    assert "predicted_price" in rows[0]

    # One row per sample (header + 1 data row).
    assert len(rows) == 2

    title_index = rows[0].index("title")
    title_cell = rows[1][title_index]
    # 원문 보존 + 선행 작은따옴표로 중화(수식 시작 아님).
    assert title_cell == "'" + dangerous_title
    assert title_cell[0] not in ("=", "+", "-", "@")

    # No CSV cell starts with a bare formula trigger.
    for row in rows:
        for cell in row:
            if cell:
                assert cell[0] not in ("=", "+", "@", "\t", "\r"), cell


def test_decision_samples_excludes_other_operator(client, test_db):
    """A synthetic operator's decision must NOT appear (single-operator filter)."""
    canonical = ensure_operator_account(test_db)

    synthetic = User(
        username="synthetic-sw-small-busan",
        email="synthetic-sw-small-busan@example.com",
        full_name="Synthetic SW Small Busan",
        company="Synthetic Co",
        hashed_password=get_password_hash("password123"),
        is_active=True,
    )
    test_db.add(synthetic)
    test_db.commit()
    test_db.refresh(synthetic)
    assert synthetic.id != canonical.id

    canonical_project = _seed_project(test_db, notice_number="20260101-CANON")
    canonical_decision = _seed_decision(
        test_db, operator=canonical, project=canonical_project
    )

    synthetic_project = _seed_project(test_db, notice_number="20260101-SYNTH")
    _seed_decision(test_db, operator=synthetic, project=synthetic_project)

    response = client.get(SAMPLES_PATH)
    assert response.status_code == 200, response.text
    payload = response.json()

    record_ids = {s["decision_record_id"] for s in payload["samples"]}
    assert record_ids == {canonical_decision.id}
    assert payload["sample_count"] == 1


def test_decision_samples_empty_window_returns_honest_zeros(client, test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    # Decision older than the requested window → excluded.
    _seed_decision(
        test_db,
        operator=operator,
        project=project,
        created_at=utc_now() - timedelta(days=60),
    )

    response = client.get(SAMPLES_PATH, params={"days": 7})
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["period_days"] == 7
    assert payload["sample_count"] == 0
    assert payload["samples"] == []
    assert payload["truncated"] is False
    assert payload["notes"] == DECISION_SAMPLES_NOTES
