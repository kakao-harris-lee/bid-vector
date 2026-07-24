"""Tests for the 투찰 보고서 메일 전달(bid-report email delivery) — DRY-RUN.

기존 산출물(BidSummaryService + BidFormDraftService)을 재사용해 메일로 전달하되,
기본 DRY-RUN 이라 SMTP 연결/외부 송신을 하지 않는다. 다음을 검증한다:

- ``build_bid_report_email`` 이 subject/HTML/text/CSV 를 값 테이블대로 렌더링하고
  정직 고지가 본문에 존재하며 CSV 에 초안 필드가 담긴다(순수 함수).
- ``mask_email`` 마스킹(엣지 케이스: 짧은 로컬 파트 / @ 없음 / 빈 값).
- DRY-RUN ``send_bid_report`` 이 dry_run=True + 마스킹된 수신자를 돌려주고 smtplib 를
  **호출하지 않는다**(smtplib.SMTP patch 후 not called 단언).
- 베스트에포트: 렌더/조회 실패가 raise 없이 실패 결과를 돌려준다.
- API 엔드포인트: POST 가 마스킹된 수신자의 dry_run 응답을 돌려주고, 원문 이메일이
  payload 에 없으며, Analytics 이벤트가 기록된다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.single_user import ensure_operator_account
from app.models.models import Analytics, BidDecisionRecord, Project
from app.schemas.bid_form_draft import BID_FORM_DRAFT_NOTICE
from app.schemas.bid_summary import DIRECT_SUBMISSION_NOTICE
from app.services.notifications.email import (
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_NO_RECIPIENT,
    EmailNotificationService,
)

REPORT_EMAIL_PATH = "/api/v1/operations/bid-decisions/{record_id}/report-email"


# --- fixtures -----------------------------------------------------------------


def _sample_summary() -> dict:
    """이메일 렌더가 읽는 최소 요약 dict(값 테이블)."""
    return {
        "decision_record_id": 42,
        "operator_id": 1,
        "notice": {
            "project_id": 7,
            "title": "해양 준설 공사 공고",
            "notice_number": "20260101-777",
            "demand_agency": "수요기관 D",
            "category": "construction",
            "business_type_label": "건축공사",
            "deadline": datetime(2026, 2, 1, 10, 30, tzinfo=UTC),
        },
        "recommendation": {
            "recommended_amount": 90_000_000.0,
            "recommended_bid_rate": 0.9,
            "action": "bid_now",
            "decision_status": "planned",
        },
    }


def _sample_draft() -> dict:
    """이메일 CSV 첨부가 읽는 최소 초안 dict."""
    return {
        "direct_submission_notice": BID_FORM_DRAFT_NOTICE,
        "fields": [
            {"field_label": "공고번호", "value": "20260101-777", "note": None},
            {"field_label": "투찰금액", "value": "90,000,000원", "note": "추천 투찰가"},
        ],
    }


def _seed_project_and_decision(
    test_db,
    *,
    budget: float = 100_000_000.0,
    recommended_amount: float = 90_000_000.0,
) -> tuple[Project, BidDecisionRecord]:
    operator = ensure_operator_account(test_db)
    project = Project(
        title="메일 전달 테스트 공고",
        description="email delivery test",
        requirements="",
        budget_estimate=budget,
        category="construction",
        notice_number="20260101-999",
        demand_agency="수요기관 M",
        issuing_agency="발주기관 N",
        business_type_code="0411",
        business_type_label="건축공사",
        deadline=datetime.now(UTC) + timedelta(hours=12),
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
        probability_score=0.72,
        matched_score=0.81,
        priority_score=0.77,
        competitiveness_score=0.6,
        score_breakdown=json.dumps({"strengths": [], "risk_flags": []}),
        reasoning="메일 전달 테스트 결정.",
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)
    return project, decision


# --- pure rendering -----------------------------------------------------------


def test_build_bid_report_email_renders_subject_html_text_csv():
    service = EmailNotificationService()
    rendered = service.build_bid_report_email(_sample_summary(), _sample_draft())

    # 제목은 공고 제목 기반.
    assert rendered.subject == "[투찰 보고서] 해양 준설 공사 공고"

    # 텍스트 본문: 추천 투찰가/투찰률/판단/결정 상태/공고 메타.
    assert "추천 투찰가: 90,000,000원" in rendered.text_body
    assert "투찰률(%): 90.000%" in rendered.text_body
    assert "판단: 즉시 투찰" in rendered.text_body  # bid_now → 한국어 라벨
    assert "결정 상태: planned" in rendered.text_body
    assert "공고번호: 20260101-777" in rendered.text_body

    # HTML 본문: escape 적용 + 핵심 값 포함.
    assert "<html>" in rendered.html_body
    assert "90,000,000원" in rendered.html_body

    # 정직 고지가 본문(HTML+텍스트)에 존재 — 자동 제출 아님 / 보장 낙찰가 아님.
    for body in (rendered.text_body, rendered.html_body):
        assert DIRECT_SUBMISSION_NOTICE in body or "직접" in body
        assert "자동" in body
    assert BID_FORM_DRAFT_NOTICE in rendered.text_body

    # CSV 첨부: 초안 필드(field_label,value) 포함 + 파일명 record 기반.
    assert rendered.has_attachment is True
    assert rendered.attachment_filename == "bid-form-draft-42.csv"
    assert "field_label,value,note" in rendered.attachment_content
    assert "공고번호" in rendered.attachment_content
    assert "90,000,000원" in rendered.attachment_content


def test_build_bid_report_email_escapes_external_title():
    """외부 공고명의 HTML 특수문자는 escape 되어 본문에 원시 태그로 새지 않는다."""
    summary = _sample_summary()
    summary["notice"]["title"] = "<script>alert(1)</script>"
    service = EmailNotificationService()
    rendered = service.build_bid_report_email(summary, _sample_draft())

    assert "<script>" not in rendered.html_body
    assert "&lt;script&gt;" in rendered.html_body


def test_build_bid_report_email_no_attachment_when_draft_empty():
    service = EmailNotificationService()
    rendered = service.build_bid_report_email(
        _sample_summary(), {"direct_submission_notice": BID_FORM_DRAFT_NOTICE, "fields": []}
    )
    assert rendered.has_attachment is False
    assert rendered.attachment_content == ""


# --- mask_email ---------------------------------------------------------------


def test_mask_email_edge_cases():
    mask = EmailNotificationService.mask_email

    # 일반 케이스: 로컬 첫 글자 + 도메인 유지.
    assert mask("chihunlee@gmail.com") == "c***@gmail.com"
    # 짧은 로컬 파트(1글자): 원문 그대로 노출하지 않고 마스킹.
    assert mask("a@x.com") == "a***@x.com"
    # 로컬 파트 없음.
    assert mask("@gmail.com") == "***@gmail.com"
    # @ 없음(불투명/비정상) — 첫 글자만 노출.
    assert mask("notanemail") == "n***"
    # 빈 값 / None.
    assert mask("") == "(not configured)"
    assert mask(None) == "(not configured)"
    assert mask("   ") == "(not configured)"

    # 어떤 케이스도 원문 전체를 그대로 노출하지 않는다.
    assert mask("chihunlee@gmail.com") != "chihunlee@gmail.com"


# --- send_bid_report (dry-run, no smtplib) ------------------------------------


def test_send_bid_report_dry_run_does_not_call_smtplib(monkeypatch):
    import app.services.notifications.email as email_module

    called = {"count": 0}

    class _ExplodingSMTP:  # noqa: D401 - test double
        def __init__(self, *args, **kwargs):
            called["count"] += 1
            raise AssertionError("DRY-RUN 에서는 SMTP 연결을 열면 안 된다")

    monkeypatch.setattr(email_module.smtplib, "SMTP", _ExplodingSMTP)

    service = EmailNotificationService()
    result = service.send_bid_report(
        summary=_sample_summary(),
        draft=_sample_draft(),
        recipient="chihunlee@gmail.com",
    )

    assert result.dry_run is True
    assert result.sent is False
    assert result.delivery_status == STATUS_DRY_RUN
    assert result.masked_recipient == "c***@gmail.com"
    assert result.subject == "[투찰 보고서] 해양 준설 공사 공고"
    assert result.has_draft_attachment is True
    assert called["count"] == 0  # smtplib.SMTP 미호출


def test_send_bid_report_skips_when_no_recipient():
    service = EmailNotificationService()
    result = service.send_bid_report(
        summary=_sample_summary(), draft=_sample_draft(), recipient=None
    )
    assert result.delivery_status == STATUS_NO_RECIPIENT
    assert result.sent is False
    assert result.masked_recipient == "(not configured)"


def test_send_bid_report_best_effort_on_render_failure(monkeypatch):
    """렌더/조회 실패는 raise 없이 실패 결과를 돌려준다(베스트에포트)."""
    import app.services.notifications.email as email_module

    def _explode(*args, **kwargs):
        raise AssertionError("DRY-RUN 실패 경로에서 SMTP 를 열면 안 된다")

    monkeypatch.setattr(email_module.smtplib, "SMTP", _explode)

    service = EmailNotificationService()
    # summary=None → 내부 렌더에서 조회 실패(AttributeError) → 잡혀서 실패 결과.
    result = service.send_bid_report(
        summary=None, draft={}, recipient="chihunlee@gmail.com"
    )

    assert result.delivery_status == STATUS_FAILED
    assert result.sent is False
    assert result.masked_recipient == "c***@gmail.com"  # 실패해도 마스킹 유지


# --- API endpoint -------------------------------------------------------------


def test_report_email_endpoint_returns_masked_dry_run(client, test_db):
    _, decision = _seed_project_and_decision(test_db)

    response = client.post(REPORT_EMAIL_PATH.format(record_id=decision.id))

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["decision_record_id"] == decision.id
    assert payload["dry_run"] is True
    assert payload["delivery_status"] == STATUS_DRY_RUN
    assert payload["has_draft_attachment"] is True
    assert payload["subject"] == "[투찰 보고서] 메일 전달 테스트 공고"
    # 정직 고지가 응답에 존재.
    assert payload["notice"] == DIRECT_SUBMISSION_NOTICE

    # 수신자는 마스킹만 노출 — 원문 이메일이 payload 어디에도 없어야 한다.
    assert payload["masked_recipient"] == "o***@local.bid-vector"
    raw_payload = json.dumps(payload, ensure_ascii=False)
    assert "operator@local.bid-vector" not in raw_payload

    # Analytics 이벤트가 마스킹된 수신자로 기록되고 원문은 없어야 한다.
    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == "email.bid_report.delivery")
        .all()
    )
    assert len(events) == 1
    event_data = json.loads(events[0].event_data)
    assert event_data["dry_run"] is True
    assert event_data["delivery_status"] == STATUS_DRY_RUN
    assert event_data["masked_recipient"] == "o***@local.bid-vector"
    assert "operator@local.bid-vector" not in events[0].event_data


def test_report_email_endpoint_recipient_override_is_masked(client, test_db):
    _, decision = _seed_project_and_decision(test_db)

    response = client.post(
        REPORT_EMAIL_PATH.format(record_id=decision.id),
        json={"recipient": "chihunlee@gmail.com"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["masked_recipient"] == "c***@gmail.com"
    # override 원문도 응답에 노출되지 않는다.
    assert "chihunlee@gmail.com" not in json.dumps(payload, ensure_ascii=False)


def test_report_email_endpoint_404_for_unknown_record(client, test_db):
    ensure_operator_account(test_db)

    response = client.post(REPORT_EMAIL_PATH.format(record_id=999_999))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"
