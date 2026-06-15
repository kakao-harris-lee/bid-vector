"""Tests for the 투찰서 초안(bid-form draft) export endpoint (PR8 / item 4-B).

Covers the happy path (known BidDecisionRecord -> KONEPS-field-mapped draft with
the core fields present + honest 적격여부(추정) + direct-submission notice), the CSV
and plain-text formats, the 404 path (unknown record id), and the cross-operator
404 (single-operator scope regression). Automated KONEPS submission is out of
scope and is asserted absent via the notice text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord, Project, User
from app.schemas.bid_form_draft import BID_FORM_DRAFT_NOTICE

DRAFT_PATH = "/api/v1/operations/bid-decisions/{record_id}/bid-form-draft"


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
        title="초안 테스트 공고",
        description="draft test",
        requirements="",
        budget_estimate=budget,
        category=category,
        notice_number="20260101-777",
        demand_agency="수요기관 D",
        issuing_agency="발주기관 E",
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
            {"strengths": ["면허 적합"], "risk_flags": []},
            ensure_ascii=False,
        ),
        reasoning="가격 적합도(추정) 점수 0.72를 반영했습니다.",
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)
    return project, decision


def test_bid_form_draft_maps_known_decision(client, test_db):
    project, decision = _seed_project_and_decision(test_db)

    response = client.get(DRAFT_PATH.format(record_id=decision.id))

    assert response.status_code == 200, response.text
    payload = response.json()

    # 핵심 식별/금액 필드 매핑 완전성.
    assert payload["decision_record_id"] == decision.id
    assert payload["notice_number"] == "20260101-777"
    assert payload["title"] == "초안 테스트 공고"
    assert payload["demand_agency"] == "수요기관 D"
    assert payload["budget_estimate"] == 100_000_000.0
    assert payload["recommended_amount"] == 90_000_000.0
    # 90,000,000 / 100,000,000 = 0.9
    assert payload["recommended_bid_rate"] == 0.9
    assert payload["category"] == "construction"
    assert payload["business_type_label"] == "건축공사"

    # 적격여부(추정): 추천율 0.9 >= floor 0.87 → 하한 미달은 아님. 0.87*1.02=0.8874
    # 보다 큰 0.9 이므로 "적격 추정".
    assert payload["eligibility_estimate"] == "적격 추정"

    # 자동 제출 없음 / 직접 제출 고지 단언.
    assert payload["direct_submission_notice"] == BID_FORM_DRAFT_NOTICE
    assert "직접" in payload["direct_submission_notice"]
    assert "자동" in payload["direct_submission_notice"]

    # 나라장터 입력 항목 매핑 리스트에 핵심 라벨이 모두 존재.
    field_labels = {f["field_label"] for f in payload["fields"]}
    for expected in ("공고번호", "공고명", "수요기관", "투찰금액", "투찰률(%)", "적격여부(추정)"):
        assert expected in field_labels

    # 투찰금액 항목의 표시값/원시값 매핑.
    amount_field = next(f for f in payload["fields"] if f["field_label"] == "투찰금액")
    assert amount_field["value"] == "90,000,000원"
    assert amount_field["raw_value"] == 90_000_000.0


def test_bid_form_draft_eligibility_below_floor(client, test_db):
    # recommended_amount 85M / budget 100M = 0.85 < floor 0.87 → 하한 미만(주의).
    _, decision = _seed_project_and_decision(test_db, recommended_amount=85_000_000.0)

    response = client.get(DRAFT_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    assert response.json()["eligibility_estimate"] == "하한 미만(주의)"


def test_bid_form_draft_csv_format(client, test_db):
    _, decision = _seed_project_and_decision(test_db)

    response = client.get(
        DRAFT_PATH.format(record_id=decision.id), params={"format": "csv"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    body = response.text
    # 헤더 행 + 핵심 라벨 행 + 고지 행 존재.
    assert "field_label,value,note" in body
    assert "공고번호" in body
    assert "투찰금액" in body
    assert "90,000,000원" in body
    assert "직접" in body  # 고지 문구가 CSV 에도 포함.


def test_bid_form_draft_text_format(client, test_db):
    _, decision = _seed_project_and_decision(test_db)

    response = client.get(
        DRAFT_PATH.format(record_id=decision.id), params={"format": "text"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "투찰서 초안" in body
    assert "공고번호: 20260101-777" in body
    assert "투찰금액: 90,000,000원" in body
    assert BID_FORM_DRAFT_NOTICE in body


def test_bid_form_draft_returns_404_for_unknown_record(client, test_db):
    ensure_operator_account(test_db)

    response = client.get(DRAFT_PATH.format(record_id=999_999))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"


def test_bid_form_draft_returns_404_for_cross_operator_record(client, test_db):
    """A record owned by a synthetic operator must 404 on the default path.

    Pins the single-operator read scope as a regression (same intent as the PR7
    summary cross-operator test).
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

    response = client.get(DRAFT_PATH.format(record_id=foreign_record.id))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"
