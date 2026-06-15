"""Tests for the bid-decision summary endpoint (투찰 의사결정 요약, PR7 / 4-A).

Covers the happy path (known BidDecisionRecord -> aggregated summary with honest
labels + direct-submission notice + reference category floor), the 404 path
(unknown record id), and graceful field-stat behaviour (absent backtest data ->
field_stat null; present matching category -> populated row).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.single_user import ensure_operator_account
from app.models.models import (
    BidDecisionRecord,
    PricePrediction,
    Project,
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.schemas.bid_summary import DIRECT_SUBMISSION_NOTICE

SUMMARY_PATH = "/api/v1/operations/bid-decisions/{record_id}/summary"


def _seed_project_and_decision(
    test_db,
    *,
    category: str = "construction",
    budget: float = 100_000_000.0,
    recommended_amount: float = 90_000_000.0,
    probability: float = 0.72,
) -> tuple[Project, BidDecisionRecord]:
    operator = ensure_operator_account(test_db)
    project = Project(
        title="요약 테스트 공고",
        description="summary test",
        requirements="",
        budget_estimate=budget,
        category=category,
        notice_number="20260101-001",
        demand_agency="수요기관 A",
        issuing_agency="발주기관 B",
        business_type_code="0411",
        business_type_label="건축공사",
        deadline=datetime.now(UTC) + timedelta(hours=10),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        recommended_amount=recommended_amount,
        probability_score=probability,
        matched_score=0.81,
        priority_score=0.77,
        competitiveness_score=0.6,
        score_breakdown=json.dumps(
            {
                "opportunity_score": 0.8,
                "strengths": ["면허 적합", "지역 적합"],
                "risk_flags": ["마감 임박"],
            },
            ensure_ascii=False,
        ),
        reasoning="가격 적합도(추정) 점수 0.72를 반영했습니다.",
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)
    return project, decision


def test_bid_summary_aggregates_known_decision(client, test_db):
    project, decision = _seed_project_and_decision(test_db)

    response = client.get(SUMMARY_PATH.format(record_id=decision.id))

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["decision_record_id"] == decision.id
    assert payload["direct_submission_notice"] == DIRECT_SUBMISSION_NOTICE

    notice = payload["notice"]
    assert notice["project_id"] == project.id
    assert notice["notice_number"] == "20260101-001"
    assert notice["category"] == "construction"
    assert notice["budget_estimate"] == 100_000_000.0

    rec = payload["recommendation"]
    assert rec["recommended_amount"] == 90_000_000.0
    # 90,000,000 / 100,000,000 = 0.9
    assert rec["recommended_bid_rate"] == 0.9
    assert rec["probability_score"] == 0.72
    assert rec["action"] == "bid_now"
    assert rec["decision_status"] == "planned"
    assert rec["strengths"] == ["면허 적합", "지역 적합"]
    assert rec["risk_flags"] == ["마감 임박"]

    # 가격 적합도(추정) 라벨 정직성: probability_score 필드 description 에 명시.
    schema = client.get("/openapi.json").json()
    prob_desc = schema["components"]["schemas"]["BidSummaryRecommendation"][
        "properties"
    ]["probability_score"]["description"]
    assert "추정" in prob_desc and "P(낙찰) 아님" in prob_desc

    floor = payload["category_floor"]
    assert floor["category"] == "construction"
    # construction 카테고리 floor = 0.87 (config), business_group=construction(0.87) → 0.87.
    assert floor["floor_bid_rate"] == 0.87
    # floor_price = 100,000,000 * 0.87.
    assert floor["floor_price"] == 87_000_000.0
    assert "낙찰하한가" in floor["note"]

    # 백테스트 데이터가 없으면 field_stat 은 graceful 하게 null.
    assert payload["field_stat"] is None
    # 연결된 예측이 없으면 prediction 은 null.
    assert payload["prediction"] is None


def test_bid_summary_includes_linked_prediction(client, test_db):
    _, decision = _seed_project_and_decision(test_db)
    operator = ensure_operator_account(test_db)

    test_db.add(
        PricePrediction(
            user_id=operator.id,
            project_id=decision.project_id,
            predicted_price=88_000_000.0,
            price_range_min=85_000_000.0,
            price_range_max=92_000_000.0,
            confidence_score=0.66,
            model_version="v-test",
            predicted_bid_rate=0.88,
            pricing_mode="heuristic",
            guardrail_applied=True,
            floor_bid_rate=0.87,
        )
    )
    test_db.commit()

    response = client.get(SUMMARY_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    prediction = response.json()["prediction"]
    assert prediction is not None
    assert prediction["predicted_price"] == 88_000_000.0
    assert prediction["price_range_min"] == 85_000_000.0
    assert prediction["price_range_max"] == 92_000_000.0
    assert prediction["predicted_bid_rate"] == 0.88
    assert prediction["guardrail_applied"] is True
    assert prediction["floor_bid_rate"] == 0.87


def test_bid_summary_includes_field_stat_from_latest_backtest(client, test_db):
    _, decision = _seed_project_and_decision(test_db, category="construction")

    experiment = SyntheticExperiment(name="요약 분야통계", params_json="{}")
    test_db.add(experiment)
    test_db.commit()
    test_db.refresh(experiment)

    run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status="completed",
        finished_at=datetime.now(UTC),
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)

    breakdown = {
        "by_category": [
            {
                "category": "construction",
                "settled_count": 40,
                "est_price_close_rate": 0.35,
                "win_rate": 0.35,
                "eligible_favorable_rate": 0.5,
            },
            {
                "category": "software",
                "settled_count": 12,
                "est_price_close_rate": 0.6,
                "eligible_favorable_rate": 0.7,
            },
        ],
        "by_budget_band": [],
    }
    test_db.add(
        SyntheticExperimentResult(
            run_id=run.id,
            operator_slug="synthetic-builder-a",
            metrics_json="{}",
            breakdown_json=json.dumps(breakdown, ensure_ascii=False),
        )
    )
    test_db.commit()

    response = client.get(SUMMARY_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    field_stat = response.json()["field_stat"]
    assert field_stat is not None
    assert field_stat["category"] == "construction"
    assert field_stat["settled_count"] == 40
    assert field_stat["est_price_close_rate"] == 0.35
    assert field_stat["eligible_favorable_rate"] == 0.5
    assert field_stat["source_run_id"] == run.id
    assert field_stat["source_operator_slug"] == "synthetic-builder-a"


def test_bid_summary_returns_404_for_unknown_record(client, test_db):
    # Ensure the singleton operator exists so the failure is a clean 404
    # (record not found), not an operator-bootstrap side effect.
    ensure_operator_account(test_db)

    response = client.get(SUMMARY_PATH.format(record_id=999_999))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"
