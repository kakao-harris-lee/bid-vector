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
import ssl
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.single_user import ensure_operator_account
from app.models.models import Analytics, BidDecisionRecord, Project, User
from app.schemas.bid_form_draft import BID_FORM_DRAFT_NOTICE
from app.schemas.bid_summary import DIRECT_SUBMISSION_NOTICE
from app.schemas.prediction import FloorShortfallEstimate
from app.services.notifications.email import (
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_NO_RECIPIENT,
    STATUS_SENT,
    EmailNotificationService,
)

REPORT_EMAIL_PATH = "/api/v1/operations/bid-decisions/{record_id}/report-email"


# --- fixtures -----------------------------------------------------------------


def _sample_summary() -> dict:
    """이메일 렌더가 읽는 최소 요약 dict(값 테이블).

    ``BidSummaryResponse`` 검증을 통과해야 하므로 그 계약의 필수 필드를 모두 담는다.
    """
    return {
        "decision_record_id": 42,
        "operator_id": 1,
        "generated_at": datetime(2026, 1, 20, 9, 0, tzinfo=UTC),
        "category_floor": {"category": "construction", "floor_bid_rate": 0.87},
        "notice": {
            "project_id": 7,
            "title": "해양 준설 공사 공고",
            "notice_number": "20260101-777",
            "demand_agency": "수요기관 D",
            "category": "construction",
            "business_type_label": "건축공사",
            "budget_estimate": 100_000_000.0,
            "bid_base_amount": 110_000_000.0,
            "deadline": datetime(2026, 2, 1, 10, 30, tzinfo=UTC),
        },
        "recommendation": {
            "recommended_amount": 96_800_000.0,
            "recommended_bid_rate": 0.968,
            "recommended_bid_rate_on_base": 0.88,
            "probability_score": 0.72,
            "action": "bid_now",
            "decision_status": "planned",
        },
        "floor_shortfall": FloorShortfallEstimate(
            shortfall_frequency=0.3,
            shortfall_sample_count=60,
            sample_count=200,
            minimum_sample_count=150,
            critical_assessment_rate=1.0,
            scope="테스트 표본",
        ),
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
    assert "추천 투찰가: 96,800,000원" in rendered.text_body
    # 투찰률은 낙찰하한율과 basis 가 같은 기초금액 기준 율(0.88)이어야 한다 —
    # 추정가격 기준 율(0.968)을 실으면 하한 여유가 부풀어 보인다.
    assert "투찰률(%) — 기초금액 기준: 88.000%" in rendered.text_body
    assert "96.800%" not in rendered.text_body
    # 두 금액이 각각 제 이름으로 실린다(한 라벨로 묶지 않는다).
    assert "기초금액(사업금액): 110,000,000원" in rendered.text_body
    assert "추정가격(부가세 별도): 100,000,000원" in rendered.text_body
    # 하한 미달 빈도가 표본 수·임계 사정률과 함께 실린다(확률 아님).
    assert "하한 미달 빈도(과거 표본): 30.0% (표본 200건, 임계 사정률 1.0)" in (
        rendered.text_body
    )
    assert "판단: 즉시 투찰" in rendered.text_body  # bid_now → 한국어 라벨
    assert "결정 상태: planned" in rendered.text_body
    assert "공고번호: 20260101-777" in rendered.text_body

    # HTML 본문: escape 적용 + 핵심 값 포함.
    assert "<html>" in rendered.html_body
    assert "96,800,000원" in rendered.html_body

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


def test_send_bid_report_production_default_still_no_send(monkeypatch):
    """PRODUCTION + 기본 이메일 플래그에서도 절대 송신하지 않음을 핀한다.

    conftest 가 ENVIRONMENT=test 로 설정하면 ``_should_send_live`` 가 test 를
    바로 short-circuit 하므로, 위의 dry-run 테스트만으로는 "운영 환경에서 기본값이면
    송신 안 함"을 증명하지 못한다. 여기서는 ENVIRONMENT 를 production 으로 바꾸되
    EMAIL_DELIVERY_ENABLED=False / EMAIL_DRY_RUN=True 기본을 유지하고, smtplib.SMTP 를
    폭발 더블로 패치해 호출 0 + dry_run 결과를 단언한다(향후 게이팅 회귀 방지).
    monkeypatch 로 ENVIRONMENT 는 테스트 후 자동 복원된다.
    """
    import app.services.notifications.email as email_module
    from app.core.config import settings

    # 운영 환경으로 전환하되 이메일 플래그는 안전 기본값 유지.
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_ENABLED", False)
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", True)

    called = {"count": 0}

    class _ExplodingSMTP:  # noqa: D401 - test double
        def __init__(self, *args, **kwargs):
            called["count"] += 1
            raise AssertionError("운영 기본값에서는 SMTP 연결을 열면 안 된다")

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
    assert called["count"] == 0  # 운영 기본값에서도 smtplib.SMTP 미호출


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


# --- live send: implicit SSL (465) vs STARTTLS (587) --------------------------


def _arm_live_settings(monkeypatch, *, use_ssl: bool, use_tls: bool, port: int) -> None:
    """라이브 송신 경로를 열도록 settings 를 무장한다(운영 환경 + 게이트 ON).

    conftest 의 ``ENVIRONMENT=test`` 를 production 으로 바꾸고 전달 게이트를 켜야
    ``_should_send_live`` 가 True 가 되어 ``_deliver_live`` 에 도달한다. monkeypatch 라
    테스트 종료 시 모든 값이 자동 복원된다(전역 오염 없음).
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "SMTP_PORT", port)
    monkeypatch.setattr(settings, "SMTP_FROM", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "SMTP_USE_SSL", use_ssl)
    monkeypatch.setattr(settings, "SMTP_USE_TLS", use_tls)
    monkeypatch.setattr(settings, "EMAIL_SEND_TIMEOUT_SECONDS", 15)


def _cm_client(name: str):
    """with-문(컨텍스트 매니저)을 지원하는 SMTP 클라이언트 목."""
    from unittest.mock import MagicMock

    client = MagicMock(name=name)
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


def test_deliver_live_ssl_mode_uses_smtp_ssl_no_starttls(monkeypatch):
    """465 암시적 SSL: smtplib.SMTP_SSL(host, 465, timeout, context=SSLContext) 사용.

    구현 SSL(465)에서는 첫 바이트부터 TLS 라 starttls() 를 호출하면 안 되고, 평문
    smtplib.SMTP 를 열어도 안 된다. 인증(login)+송신(send_message)은 수행한다.
    """
    from unittest.mock import MagicMock

    import app.services.notifications.email as email_module

    _arm_live_settings(monkeypatch, use_ssl=True, use_tls=True, port=465)

    client = _cm_client("ssl_client")
    ssl_ctor = MagicMock(name="SMTP_SSL", return_value=client)
    plain_ctor = MagicMock(name="SMTP")
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", ssl_ctor)
    monkeypatch.setattr(email_module.smtplib, "SMTP", plain_ctor)

    service = EmailNotificationService()
    result = service.send_bid_report(
        summary=_sample_summary(),
        draft=_sample_draft(),
        recipient="chihunlee@gmail.com",
    )

    # SMTP_SSL 이 host/port 465/timeout/context(ssl.SSLContext) 로 1회 호출.
    ssl_ctor.assert_called_once()
    args, kwargs = ssl_ctor.call_args
    assert args[0] == "smtp.gmail.com"
    assert args[1] == 465
    assert kwargs["timeout"] == 15
    assert isinstance(kwargs["context"], ssl.SSLContext)

    # 평문 SMTP 미사용 + starttls 미호출(암시적 SSL 이라 STARTTLS 금지).
    plain_ctor.assert_not_called()
    client.starttls.assert_not_called()

    # 인증 + 송신 수행.
    client.login.assert_called_once_with("sender@example.com", "app-password")
    client.send_message.assert_called_once()

    # 결과: 라이브 송신 성공.
    assert result.sent is True
    assert result.dry_run is False
    assert result.delivery_status == STATUS_SENT
    assert result.masked_recipient == "c***@gmail.com"


def test_deliver_live_starttls_mode_uses_smtp_and_starttls(monkeypatch):
    """587 STARTTLS 회귀: smtplib.SMTP(host, 587, timeout) + starttls(context) 후 송신.

    SSL 이 아닌 경로에서는 평문 SMTP 를 열고 SMTP_USE_TLS 일 때 starttls 로 승격한다.
    SMTP_SSL 은 호출되지 않는다.
    """
    from unittest.mock import MagicMock

    import app.services.notifications.email as email_module

    _arm_live_settings(monkeypatch, use_ssl=False, use_tls=True, port=587)

    client = _cm_client("starttls_client")
    plain_ctor = MagicMock(name="SMTP", return_value=client)
    ssl_ctor = MagicMock(name="SMTP_SSL")
    monkeypatch.setattr(email_module.smtplib, "SMTP", plain_ctor)
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", ssl_ctor)

    service = EmailNotificationService()
    result = service.send_bid_report(
        summary=_sample_summary(),
        draft=_sample_draft(),
        recipient="chihunlee@gmail.com",
    )

    # 평문 SMTP 가 host/port 587/timeout 로 1회 호출, SMTP_SSL 미사용.
    plain_ctor.assert_called_once()
    args, kwargs = plain_ctor.call_args
    assert args[0] == "smtp.gmail.com"
    assert args[1] == 587
    assert kwargs["timeout"] == 15
    ssl_ctor.assert_not_called()

    # STARTTLS 승격 — context(ssl.SSLContext) 인자로 호출.
    client.starttls.assert_called_once()
    starttls_kwargs = client.starttls.call_args.kwargs
    assert isinstance(starttls_kwargs["context"], ssl.SSLContext)

    # 인증 + 송신 수행.
    client.login.assert_called_once_with("sender@example.com", "app-password")
    client.send_message.assert_called_once()

    assert result.sent is True
    assert result.dry_run is False
    assert result.delivery_status == STATUS_SENT


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


def test_report_email_endpoint_404_for_cross_operator_record(client, test_db):
    """A record owned by a synthetic operator must not be emailable cross-operator.

    Mirrors ``test_bid_summary_returns_404_for_cross_operator_record`` /
    the bid_form_draft equivalent: the default POST path resolves to the canonical
    singleton operator, and the summary/draft aggregation scopes its lookup with
    ``operator_id == target.id``. Pins the single-operator scope as a regression —
    a synthetic-owned record whose id EXISTS must still 404 on the default path.
    """
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
    # (canonical) POST path must not be able to email it.
    response = client.post(REPORT_EMAIL_PATH.format(record_id=foreign_record.id))

    assert response.status_code == 404
    assert response.json()["detail"] == "Bid decision record not found"
