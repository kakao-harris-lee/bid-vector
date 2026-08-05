"""Tests for the bid-decision summary endpoint (투찰 의사결정 요약, PR7 / 4-A).

Covers the happy path (known BidDecisionRecord -> aggregated summary with honest
labels + direct-submission notice + reference category floor), the 404 path
(unknown record id), and graceful field-stat behaviour (absent backtest data ->
field_stat null; present matching category -> populated row).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.single_user import ensure_operator_account
from app.models.models import (
    BidDecisionRecord,
    HistoricalData,
    PricePrediction,
    Project,
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
    User,
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


def test_bid_summary_exposes_bid_base_alongside_budget_estimate(client, test_db):
    """공고 메타가 기초금액(투찰 기준금액)과 추정가격을 분리해 낸다.

    기초금액이 실제로 적재된 과세 공고에서는 참고 하한가와 하한 비교용 투찰율도
    기초금액 기준으로 환산돼야 한다 — 추정가격 기준으로 재면 하한 여유가 부풀어 보인다.
    """
    project, decision = _seed_project_and_decision(
        test_db, recommended_amount=96_800_000.0
    )
    test_db.add(
        HistoricalData(
            project_id=project.id,
            notice_number=project.notice_number,
            category="construction",
            base_amount=110_000_000.0,
        )
    )
    test_db.commit()

    payload = client.get(SUMMARY_PATH.format(record_id=decision.id)).json()

    notice = payload["notice"]
    assert notice["budget_estimate"] == 100_000_000.0
    assert notice["bid_base_amount"] == 110_000_000.0
    assert notice["bid_base_source"] == "base-fallback"
    assert notice["bid_base_to_estimate_ratio"] == 1.1
    assert "기초금액" in notice["bid_base_note"]

    rec = payload["recommendation"]
    assert rec["recommended_bid_rate"] == 0.968  # 추정가격 기준(참고)
    assert rec["recommended_bid_rate_on_base"] == 0.88  # 기초금액 기준(하한과 동일 basis)

    # 참고 하한가는 기초금액 * 0.87 (추정가격 기준 87,000,000 이 아니다).
    assert payload["category_floor"]["floor_price"] == 95_700_000.0


def test_bid_summary_bid_base_falls_back_to_budget_estimate(client, test_db):
    """기초금액이 적재되지 않은 공고는 추정가격으로 폴백하고 그 사실을 출처로 알린다."""
    _, decision = _seed_project_and_decision(test_db)

    payload = client.get(SUMMARY_PATH.format(record_id=decision.id)).json()

    notice = payload["notice"]
    assert notice["bid_base_amount"] == 100_000_000.0
    assert notice["bid_base_source"] == "budget-estimate-fallback"
    assert notice["bid_base_to_estimate_ratio"] == 1.0
    # 폴백이면 두 basis 의 투찰율이 같은 값이라 기존 판정이 그대로 보존된다.
    rec = payload["recommendation"]
    assert rec["recommended_bid_rate"] == rec["recommended_bid_rate_on_base"] == 0.9


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


def test_bid_summary_returns_404_for_cross_operator_record(client, test_db):
    """A record owned by another (synthetic) operator must 404 on the default path.

    The default read path resolves to the canonical singleton operator, and the
    service scopes its lookup with ``operator_id == target.id``. This pins that
    single-operator scope as a regression: if the operator_id filter were ever
    dropped, the existing unknown-id 404 test would NOT catch it — but this one
    (where the id exists, just for a different operator) would.
    """
    # Default read path resolves to the canonical singleton operator.
    canonical = ensure_operator_account(test_db)

    synthetic = User(
        username="synthetic-sw-small-seoul",
        email="synthetic-sw-small-seoul@example.com",
        full_name="Synthetic SW Small Seoul",
        company="Synthetic Co",
        hashed_password=get_password_hash("password123"),
        is_active=True,
    )
    test_db.add(synthetic)
    test_db.commit()
    test_db.refresh(synthetic)
    assert synthetic.id != canonical.id

    project = Project(
        title="다른 운영자 소유 공고",
        description="cross-operator",
        requirements="",
        budget_estimate=80_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=8),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    foreign_record = BidDecisionRecord(
        project_id=project.id,
        operator_id=synthetic.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        recommended_amount=72_000_000.0,
        probability_score=0.6,
        matched_score=0.7,
        priority_score=0.7,
        reasoning="synthetic seed",
    )
    test_db.add(foreign_record)
    test_db.commit()
    test_db.refresh(foreign_record)

    # The record id EXISTS, but belongs to the synthetic operator — the default
    # (canonical) read path must not be able to read it.
    response = client.get(SUMMARY_PATH.format(record_id=foreign_record.id))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"
