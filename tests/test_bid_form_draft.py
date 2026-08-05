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
from app.models.models import BidDecisionRecord, HistoricalData, Project, User
from app.schemas.bid_form_draft import BID_FORM_DRAFT_NOTICE

DRAFT_PATH = "/api/v1/operations/bid-decisions/{record_id}/bid-form-draft"


def _seed_project_and_decision(
    test_db,
    *,
    category: str = "construction",
    budget: float = 100_000_000.0,
    recommended_amount: float = 90_000_000.0,
    probability: float = 0.72,
    title: str = "초안 테스트 공고",
) -> tuple[Project, BidDecisionRecord]:
    operator = ensure_operator_account(test_db)
    project = Project(
        title=title,
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
    for expected in (
        "공고번호",
        "공고명",
        "수요기관",
        "투찰금액",
        "투찰률(%) — 기초금액 기준",
        "적격여부(추정)",
    ):
        assert expected in field_labels

    # 투찰금액 항목의 표시값/원시값 매핑.
    amount_field = next(f for f in payload["fields"] if f["field_label"] == "투찰금액")
    assert amount_field["value"] == "90,000,000원"
    assert amount_field["raw_value"] == 90_000_000.0


def test_bid_form_draft_separates_bid_base_from_budget_estimate(client, test_db):
    """과세 공고: 기초금액(부가세 포함)과 추정가격이 서로 다른 항목으로 나가야 한다.

    실투찰 사고의 형태를 그대로 재현한다 — 추정가격 1억, 기초금액 1.1억(부가세 포함)인
    공고에 기초금액의 88%(=96,800,000원)를 투찰하는 상황. 추정가격 기준 율(0.968)로
    하한(0.87)을 재면 여유가 크게 남는 것처럼 보이지만, 실제로 적격심사가 보는 기초금액
    기준 율은 0.88 이라 하한 바로 위다.
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

    payload = client.get(DRAFT_PATH.format(record_id=decision.id)).json()

    # 두 금액이 각각 제 이름으로 나간다.
    assert payload["budget_estimate"] == 100_000_000.0
    assert payload["bid_base_amount"] == 110_000_000.0
    assert payload["bid_base_source"] == "base-fallback"
    assert payload["bid_base_to_estimate_ratio"] == 1.1

    # 두 basis 의 투찰율이 각각 산출되고, 하한 판정은 기초금액 기준 율을 쓴다.
    assert payload["recommended_bid_rate"] == 0.968
    assert payload["recommended_bid_rate_on_base"] == 0.88
    # 0.88 <= 0.87 * 1.02 → "하한 근접"(추정가격 기준 0.968 로 쟀다면 "적격 추정"이었다).
    assert payload["eligibility_estimate"] == "하한 근접"

    fields = {f["key"]: f for f in payload["fields"]}
    assert fields["bid_base_amount"]["field_label"] == "기초금액(사업금액)"
    assert fields["bid_base_amount"]["value"] == "110,000,000원"
    assert fields["bid_base_amount"]["raw_value"] == 110_000_000.0
    assert fields["budget_estimate"]["field_label"] == "추정가격(부가세 별도)"
    assert fields["budget_estimate"]["raw_value"] == 100_000_000.0
    # 투찰서에 적히는 율도 기초금액 기준 — key 가 그 basis 를 그대로 말해야 한다
    # (raw_value 는 on_base 값인데 key 는 추정가격 기준 이름이던 혼동 제거).
    assert "recommended_bid_rate" not in fields
    assert fields["recommended_bid_rate_on_base"]["raw_value"] == 0.88
    assert "기초금액" in fields["recommended_bid_rate_on_base"]["field_label"]
    # 참고 하한과 실제(예정가 기준) 하한의 괴리를 note 가 분명히 말한다.
    assert "예정가격" in fields["recommended_bid_rate_on_base"]["note"]


def test_bid_form_draft_shortfall_unmeasurable_is_not_zero(client, test_db):
    """표본/하한을 확보하지 못하면 0%가 아니라 '판정 불가'로 표기된다(정직 명세 §2)."""
    _, decision = _seed_project_and_decision(test_db)

    payload = client.get(DRAFT_PATH.format(record_id=decision.id)).json()

    shortfall = payload["floor_shortfall"]
    assert shortfall is not None
    assert shortfall["shortfall_frequency"] is None
    assert shortfall["unmeasurable_reason"]

    field = next(
        f for f in payload["fields"] if f["key"] == "floor_shortfall_frequency"
    )
    assert field["value"] == "판정 불가"
    assert field["raw_value"] is None
    assert "0%" not in field["value"]
    # note 는 판정 불가 사유를 그대로 전달해, 인쇄본만 봐도 '위험 없음'으로 읽히지 않는다.
    assert "위험이 없다는 뜻이 아닙니다" in field["note"]


def test_bid_form_draft_lottery_numbers_present_and_valid(client, test_db):
    """복수예비가격 추첨번호: 1~15 중 서로 다른 2개, 정렬, 정직 note 포함."""
    _, decision = _seed_project_and_decision(test_db)

    payload = client.get(DRAFT_PATH.format(record_id=decision.id)).json()

    nums = payload["lottery_numbers"]
    assert isinstance(nums, list) and len(nums) == 2
    assert len(set(nums)) == 2  # 서로 다른 2개
    assert all(1 <= n <= 15 for n in nums)
    assert nums == sorted(nums)

    # 정직 note — 개별 선택이 낙찰에 영향 없음 + 분석 아님을 분명히.
    note = payload["lottery_numbers_note"]
    assert "무작위" in note
    assert "영향" in note  # 개별 번호 선택은 낙찰 결과에 영향 없음
    assert "분석" in note

    # fields 리스트에도 추첨번호 항목이 라벨+note 와 함께 노출.
    lottery_field = next(
        f for f in payload["fields"] if f["key"] == "lottery_numbers"
    )
    # 라벨 자체에 "무작위"가 있어야 함(빠른 스캔 시 분석 픽 오해 방지).
    assert lottery_field["field_label"] == "복수예비가격 추첨번호(무작위 2개)"
    assert "무작위" in lottery_field["field_label"]
    assert lottery_field["value"] == ", ".join(str(n) for n in nums)
    assert "영향" in (lottery_field["note"] or "")


def test_lottery_note_matches_pool_and_pick_constants():
    """정직 note 의 '15개 중 2개' 문구가 실제 상수와 일치(드리프트 가드)."""
    from app.schemas.bid_form_draft import LOTTERY_NUMBERS_NOTE
    from app.services.bid_form_draft import (
        _LOTTERY_PICK_COUNT,
        _LOTTERY_POOL_SIZE,
    )

    assert f"{_LOTTERY_POOL_SIZE}개 중 {_LOTTERY_PICK_COUNT}개" in LOTTERY_NUMBERS_NOTE
    # 절차 미적용 가능성 고지도 유지(공고별 복수예비가격 미적용 대비).
    assert "적용되지 않을 수 있습니다" in LOTTERY_NUMBERS_NOTE


def test_bid_form_draft_lottery_numbers_are_reproducible(client, test_db):
    """같은 공고번호 → 같은 추첨번호(공고번호 seed, 결정적)."""
    _, decision = _seed_project_and_decision(test_db)
    path = DRAFT_PATH.format(record_id=decision.id)

    first = client.get(path).json()["lottery_numbers"]
    second = client.get(path).json()["lottery_numbers"]
    assert first == second


def test_suggest_lottery_numbers_empty_without_notice_number():
    """공고번호가 없으면 추천을 생성하지 않는다(빈 리스트)."""
    from app.services.bid_form_draft import BidFormDraftService

    service = BidFormDraftService()
    assert service._suggest_lottery_numbers(None) == []
    assert service._suggest_lottery_numbers("") == []
    assert service._suggest_lottery_numbers("   ") == []


def test_suggest_lottery_numbers_deterministic():
    """공고번호별 결정적(재현 가능)이며 항상 1~15 중 서로 다른 2개."""
    from app.services.bid_form_draft import BidFormDraftService

    service = BidFormDraftService()
    for notice in ("20260101-777", "20260101-888", "R26BK01599047"):
        first = service._suggest_lottery_numbers(notice)
        second = service._suggest_lottery_numbers(notice)
        assert first == second  # 재현성
        assert len(set(first)) == 2
        assert all(1 <= n <= 15 for n in first)
        assert first == sorted(first)


def test_bid_form_draft_eligibility_below_floor(client, test_db):
    # recommended_amount 85M / budget 100M = 0.85 < floor 0.87 → 하한 미만(주의).
    _, decision = _seed_project_and_decision(test_db, recommended_amount=85_000_000.0)

    response = client.get(DRAFT_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    assert response.json()["eligibility_estimate"] == "하한 미만(주의)"


def test_bid_form_draft_eligibility_near_floor(client, test_db):
    # recommended_amount 88.2M / budget 100M = 0.882. construction floor 0.87,
    # 근접 상한 0.87 * 1.02 = 0.8874 → 0.87 <= 0.882 <= 0.8874 이므로 "하한 근접".
    _, decision = _seed_project_and_decision(
        test_db, recommended_amount=88_200_000.0
    )

    response = client.get(DRAFT_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recommended_bid_rate"] == 0.882
    assert payload["eligibility_estimate"] == "하한 근접"


def test_bid_form_draft_eligibility_unknown_when_rate_missing(client, test_db):
    # budget_estimate 0 → recommended_bid_rate 산출 불가(None) → "판단 불가".
    _, decision = _seed_project_and_decision(
        test_db, budget=0.0, recommended_amount=90_000_000.0
    )

    response = client.get(DRAFT_PATH.format(record_id=decision.id))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recommended_bid_rate"] is None
    assert payload["eligibility_estimate"] == "판단 불가"


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


def test_bid_form_draft_csv_neutralizes_formula_injection(client, test_db):
    # 외부 나라장터 공고명이 "="로 시작 → CSV injection 벡터. 셀이 작은따옴표로
    # 중화되어 수식으로 실행되지 않아야 한다(OWASP). 공고명에 콤마/따옴표가 섞여
    # 있어도 (CSV quoting을 통과해도) parse된 셀 값이 작은따옴표로 시작해야 한다.
    import csv as _csv
    import io as _io

    dangerous_title = '=HYPERLINK("http://evil","공고")'
    _, decision = _seed_project_and_decision(test_db, title=dangerous_title)

    response = client.get(
        DRAFT_PATH.format(record_id=decision.id), params={"format": "csv"}
    )
    assert response.status_code == 200, response.text
    body = response.text

    rows = list(_csv.reader(_io.StringIO(body)))

    # 공고명 셀: 원문 보존 + 선행 작은따옴표로 중화(수식 시작 아님).
    title_cells = [row[1] for row in rows if row and row[0] == "공고명"]
    assert title_cells, "공고명 행이 CSV 에 존재해야 한다"
    neutralized = "'" + dangerous_title
    assert title_cells[0] == neutralized
    assert title_cells[0][0] not in ("=", "+", "-", "@")

    # 어떤 CSV 셀도 bare 수식 트리거 문자로 시작하지 않아야 한다.
    for row in rows:
        for cell in row:
            if cell:
                assert cell[0] not in ("=", "+", "@", "\t", "\r"), cell


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


def test_bid_form_draft_returns_404_for_missing_project(client, test_db):
    """A record whose linked project is absent must 404 (honest 부재).

    The decision record exists and is owned by the canonical operator, but its
    project row is gone (orphan). The summary aggregation raises ``ValueError``
    so the endpoint maps it to 404 rather than rendering a half-empty draft.
    SQLite test engine does not enforce FKs, so the orphan is seedable directly.
    """
    project, decision = _seed_project_and_decision(test_db)

    # Drop the linked project, leaving the decision record orphaned.
    test_db.delete(project)
    test_db.commit()
    test_db.expire(decision)

    response = client.get(DRAFT_PATH.format(record_id=decision.id))

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found for bid decision record"
