"""투찰 보고서 메일 전달 서비스 — 기본 DRY-RUN, 외부 송신 없음.

기존 산출물(``BidSummaryService`` + ``BidFormDraftService``)을 **재사용**해 하나의
메일로 렌더링한다. 보고서 생성 로직을 재구현하지 않는다.

정직/안전 원칙:
- **DRY-RUN 기본**: ``EMAIL_DRY_RUN`` 이 True(기본)이면 제목/본문을 렌더링하고 로깅만
  하며 **SMTP 연결을 절대 열지 않는다**. 라이브 송신은
  ``EMAIL_DELIVERY_ENABLED and not EMAIL_DRY_RUN`` 일 때만 실행되는 향후 opt-in 이고,
  ``ENVIRONMENT=test`` 에서는 어떤 경우에도 실제 송신을 하지 않는다.
- **마스킹(§2)**: 수신자 이메일은 응답/로그/텔레메트리 어디에도 마스킹된 형태로만
  노출한다(``mask_email`` — telegram ``mask_chat_id`` 미러).
- **베스트에포트**: 전달 실패가 보고서 흐름으로 예외를 전파하지 않는다(telegram
  try/except 패턴 미러). 렌더/조회 실패는 raise 없이 실패 결과를 돌려준다.
- **정직 고지**: 본문은 자동 제출이 아님(``DIRECT_SUBMISSION_NOTICE`` /
  ``BID_FORM_DRAFT_NOTICE``)과 추천가가 보장 낙찰가가 아님을 명시한다.

렌더링(순수 함수)은 I/O(SMTP·DB)와 분리한다(§4.7-4) — 값 테이블로 단위 검증 가능.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import html
import logging
import smtplib
import ssl
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE, NON_DELIVERING_ENVIRONMENTS
from app.core.time import utc_now
from app.models.models import Analytics, User
from app.schemas.analytics_events import BidReportEmailDeliveryEvent
from app.schemas.bid_form_draft import BID_FORM_DRAFT_NOTICE
from app.schemas.bid_summary import DIRECT_SUBMISSION_NOTICE
from app.services.analytics_event_payload import dump_analytics_event
from app.services.bid_form_draft import BidFormDraftService
from app.services.bid_summary import BidSummaryService

logger = logging.getLogger(__name__)

# 판단(action) → 한국어 라벨(telegram build_bid_decision_message 미러, 선언적 룩업).
_ACTION_LABELS = {
    "bid_now": "즉시 투찰",
    "review": "추가 검토",
    "skip": "보류",
}

# 전달 상태 상수(정직 표기 — 라이브 송신은 기본 비활성).
STATUS_DRY_RUN = "dry_run_rendered"
STATUS_NO_RECIPIENT = "skipped_no_recipient"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

# event_type 문자열 단일 출처는 app/core/constants.py (payload 계약 레지스트리와 공유).
_ANALYTICS_EVENT_TYPE = BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE


@dataclass(frozen=True)
class RenderedEmail:
    """렌더링된 메일 산출물 — SMTP I/O 이전의 순수 데이터."""

    subject: str
    html_body: str
    text_body: str
    attachment_filename: str
    attachment_content: str
    attachment_mime: str = "text/csv"

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachment_content)


@dataclass(frozen=True)
class EmailDeliveryResult:
    """전달 결과 — 원문 수신자/본문 시크릿을 담지 않는다(마스킹된 수신자만)."""

    dry_run: bool
    sent: bool
    delivery_status: str
    masked_recipient: str
    subject: str
    has_draft_attachment: bool
    detail: str = ""
    project_id: Optional[int] = None
    decision_record_id: Optional[int] = None


class EmailNotificationService:
    """투찰 보고서를 메일로 전달(기본 DRY-RUN)하는 얇은 배달 서비스."""

    def __init__(
        self,
        summary_service: Optional[BidSummaryService] = None,
        draft_service: Optional[BidFormDraftService] = None,
    ) -> None:
        self._summary_service = summary_service or BidSummaryService()
        self._draft_service = draft_service or BidFormDraftService(
            summary_service=self._summary_service
        )

    # --- masking --------------------------------------------------------------

    @staticmethod
    def mask_email(addr: str | None) -> str:
        """수신자 이메일을 로그/응답/텔레메트리용으로 마스킹한다.

        telegram ``mask_chat_id`` 미러 — 원문을 노출하지 않되 운영자가 어느 주소인지
        가늠할 수 있게 로컬 파트 첫 글자와 도메인만 남긴다(예: ``c***@gmail.com``).
        """
        normalized = str(addr or "").strip()
        if not normalized:
            return "(not configured)"
        if "@" not in normalized:
            # @ 가 없는 비정상/불투명 값 — 첫 글자만 노출.
            return f"{normalized[0]}***"
        local, _, domain = normalized.partition("@")
        if not local:
            masked_local = "***"
        elif len(local) == 1:
            masked_local = f"{local}***"
        else:
            masked_local = f"{local[0]}***"
        return f"{masked_local}@{domain}" if domain else f"{masked_local}@***"

    # --- pure rendering -------------------------------------------------------

    def build_bid_report_email(self, summary: dict, draft: dict) -> RenderedEmail:
        """요약+초안을 하나의 메일(subject/HTML/text/CSV 첨부)로 렌더링한다(순수).

        외부(KONEPS) 문자열(공고명/수요기관 등)은 HTML escape 로 중화한다. CSV 첨부는
        ``BidFormDraftService.render_csv`` 를 재사용해 field_label/value/note 를 담고,
        수식 인젝션도 그쪽에서 중화된다.
        """
        notice = summary.get("notice", {}) or {}
        recommendation = summary.get("recommendation", {}) or {}

        title = str(notice.get("title") or "(제목 없음)")
        subject = f"[투찰 보고서] {title}"

        action = str(recommendation.get("action") or "skip")
        action_label = _ACTION_LABELS.get(action, action)
        decision_status = str(recommendation.get("decision_status") or "planned")
        recommended_amount = float(recommendation.get("recommended_amount") or 0.0)
        recommended_bid_rate = recommendation.get("recommended_bid_rate")

        rows = [
            ("공고명", title),
            ("공고번호", str(notice.get("notice_number") or "")),
            ("수요기관", str(notice.get("demand_agency") or "")),
            ("분류(카테고리)", str(notice.get("category") or "")),
            ("업종", str(notice.get("business_type_label") or "")),
            ("추천 투찰가", self._format_currency(recommended_amount)),
            ("투찰률(%)", self._format_percent(recommended_bid_rate)),
            ("판단", action_label),
            ("결정 상태", decision_status),
            ("투찰 마감일시", self._format_datetime(notice.get("deadline"))),
        ]

        text_body = self._render_text_body(rows)
        html_body = self._render_html_body(rows)

        draft_csv = self._draft_service.render_csv(draft)
        record_id = summary.get("decision_record_id")
        attachment_filename = (
            f"bid-form-draft-{record_id}.csv" if record_id is not None else "bid-form-draft.csv"
        )
        # 초안에 매핑 항목이 없으면 첨부를 생략(빈 문자열 → has_attachment False).
        attachment_content = draft_csv if (draft.get("fields") or []) else ""

        return RenderedEmail(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachment_filename=attachment_filename,
            attachment_content=attachment_content,
        )

    def _render_text_body(self, rows: list[tuple[str, str]]) -> str:
        lines = ["[bid-vector 투찰 보고서 — 참고용, 자동 제출 아님]", ""]
        for label, value in rows:
            lines.append(f"{label}: {value}")
        lines.append("")
        lines.append(DIRECT_SUBMISSION_NOTICE)
        lines.append(BID_FORM_DRAFT_NOTICE)
        return "\n".join(lines)

    def _render_html_body(self, rows: list[tuple[str, str]]) -> str:
        row_html = "".join(
            "<tr>"
            f"<th style=\"text-align:left;padding:4px 12px 4px 0\">{html.escape(label)}</th>"
            f"<td style=\"padding:4px 0\">{html.escape(value)}</td>"
            "</tr>"
            for label, value in rows
        )
        return (
            "<html><body>"
            "<h2>bid-vector 투찰 보고서</h2>"
            "<p>참고용 요약입니다. 자동 제출이 아니며 운영자가 직접 나라장터에 입력·제출합니다.</p>"
            f"<table>{row_html}</table>"
            f"<hr><p style=\"color:#a15b00\">{html.escape(DIRECT_SUBMISSION_NOTICE)}</p>"
            f"<p style=\"color:#a15b00\">{html.escape(BID_FORM_DRAFT_NOTICE)}</p>"
            "</body></html>"
        )

    # --- send (dry-run by default) --------------------------------------------

    def send_bid_report(
        self,
        *,
        summary: dict,
        draft: dict,
        recipient: str | None,
    ) -> EmailDeliveryResult:
        """수신자를 마스킹하고 메일을 렌더링해 결과를 돌려준다(기본 DRY-RUN).

        DRY-RUN(기본)에서는 **SMTP 연결을 열지 않고** 렌더링/로깅만 한다. 라이브 송신은
        ``_should_send_live`` 가 True 일 때만 실행되는 향후 opt-in 이다. 렌더/송신 실패는
        raise 하지 않고 실패 결과를 돌려준다(베스트에포트).
        """
        masked_recipient = self.mask_email(recipient)
        project_id, record_id = self._safe_ids(summary)

        if not str(recipient or "").strip():
            logger.info(
                "투찰 보고서 메일 전달 스킵 — 수신자 없음 (record %s, recipient %s)",
                record_id,
                masked_recipient,
            )
            return EmailDeliveryResult(
                dry_run=True,
                sent=False,
                delivery_status=STATUS_NO_RECIPIENT,
                masked_recipient=masked_recipient,
                subject="",
                has_draft_attachment=False,
                detail="수신자 이메일이 없어 전달을 건너뜁니다.",
                project_id=project_id,
                decision_record_id=record_id,
            )

        try:
            rendered = self.build_bid_report_email(summary, draft)
        except Exception as exc:  # 베스트에포트: 렌더 실패가 흐름을 막지 않는다.
            logger.warning(
                "투찰 보고서 메일 렌더 실패 (record %s, recipient %s): %s",
                record_id,
                masked_recipient,
                exc,
                exc_info=True,
            )
            return EmailDeliveryResult(
                dry_run=not self._should_send_live(),
                sent=False,
                delivery_status=STATUS_FAILED,
                masked_recipient=masked_recipient,
                subject="",
                has_draft_attachment=False,
                detail="메일 렌더링에 실패했습니다.",
                project_id=project_id,
                decision_record_id=record_id,
            )

        if not self._should_send_live():
            logger.info(
                "투찰 보고서 메일 DRY-RUN 렌더 완료 (record %s, recipient %s, subject '%s')",
                record_id,
                masked_recipient,
                rendered.subject,
            )
            return EmailDeliveryResult(
                dry_run=True,
                sent=False,
                delivery_status=STATUS_DRY_RUN,
                masked_recipient=masked_recipient,
                subject=rendered.subject,
                has_draft_attachment=rendered.has_attachment,
                detail="DRY-RUN — 렌더링만 수행하고 SMTP 연결/송신을 하지 않았습니다.",
                project_id=project_id,
                decision_record_id=record_id,
            )

        # --- 라이브 송신(향후 opt-in) — 기본 비활성, 이 저장소에서 미검증 ----------
        return self._deliver_live(
            rendered=rendered,
            recipient=str(recipient).strip(),
            masked_recipient=masked_recipient,
            project_id=project_id,
            record_id=record_id,
        )

    def _deliver_live(
        self,
        *,
        rendered: RenderedEmail,
        recipient: str,
        masked_recipient: str,
        project_id: Optional[int],
        record_id: Optional[int],
    ) -> EmailDeliveryResult:
        """SMTP 라이브 송신(STARTTLS 587 / 암시적 SSL 465) — 설정으로만 켜지는 향후 opt-in(베스트에포트)."""
        if not settings.SMTP_HOST or not settings.SMTP_FROM:
            logger.warning(
                "투찰 보고서 메일 라이브 송신 미구성 (record %s) — SMTP_HOST/SMTP_FROM 필요",
                record_id,
            )
            return EmailDeliveryResult(
                dry_run=False,
                sent=False,
                delivery_status=STATUS_FAILED,
                masked_recipient=masked_recipient,
                subject=rendered.subject,
                has_draft_attachment=rendered.has_attachment,
                detail="SMTP 구성이 없어 송신하지 못했습니다.",
                project_id=project_id,
                decision_record_id=record_id,
            )
        try:
            message = self._build_mime(rendered, recipient=recipient)
            # 서버 인증서·호스트명 검증(보안 기본값) — 검증을 끄지 않는다.
            context = ssl.create_default_context()
            if settings.SMTP_USE_SSL:
                # 암시적 SSL(465) — 첫 바이트부터 TLS. starttls() 를 호출하지 않는다.
                with smtplib.SMTP_SSL(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.EMAIL_SEND_TIMEOUT_SECONDS,
                    context=context,
                ) as client:
                    self._authenticate_and_send(client, message)
            else:
                # STARTTLS(587) 또는 평문 — 평문 연결 후 조건부 TLS 승격.
                with smtplib.SMTP(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.EMAIL_SEND_TIMEOUT_SECONDS,
                ) as client:
                    if settings.SMTP_USE_TLS:
                        client.starttls(context=context)
                    self._authenticate_and_send(client, message)
        except Exception as exc:  # 베스트에포트: 송신 실패가 흐름을 막지 않는다.
            logger.warning(
                "투찰 보고서 메일 라이브 송신 실패 (record %s, recipient %s): %s",
                record_id,
                masked_recipient,
                exc,
                exc_info=True,
            )
            return EmailDeliveryResult(
                dry_run=False,
                sent=False,
                delivery_status=STATUS_FAILED,
                masked_recipient=masked_recipient,
                subject=rendered.subject,
                has_draft_attachment=rendered.has_attachment,
                detail="SMTP 송신에 실패했습니다.",
                project_id=project_id,
                decision_record_id=record_id,
            )
        logger.info(
            "투찰 보고서 메일 송신 완료 (record %s, recipient %s)",
            record_id,
            masked_recipient,
        )
        return EmailDeliveryResult(
            dry_run=False,
            sent=True,
            delivery_status=STATUS_SENT,
            masked_recipient=masked_recipient,
            subject=rendered.subject,
            has_draft_attachment=rendered.has_attachment,
            detail="SMTP 송신에 성공했습니다.",
            project_id=project_id,
            decision_record_id=record_id,
        )

    @staticmethod
    def _authenticate_and_send(client: smtplib.SMTP, message: EmailMessage) -> None:
        """인증(사용자명 설정 시) 후 메시지를 송신한다 — SSL/STARTTLS 공통 경로.

        ``smtplib.SMTP_SSL`` 은 ``smtplib.SMTP`` 의 서브클래스라 두 분기가 이 헬퍼를
        공유한다(login+send_message 중복 제거).
        """
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)

    def _build_mime(self, rendered: RenderedEmail, *, recipient: str) -> EmailMessage:
        """RenderedEmail 을 MIME 메시지로 조립한다(라이브 송신 경로 전용)."""
        message = EmailMessage()
        message["Subject"] = rendered.subject
        message["From"] = settings.SMTP_FROM
        message["To"] = recipient
        message.set_content(rendered.text_body)
        message.add_alternative(rendered.html_body, subtype="html")
        if rendered.has_attachment:
            message.add_attachment(
                rendered.attachment_content.encode("utf-8-sig"),
                maintype="text",
                subtype="csv",
                filename=rendered.attachment_filename,
            )
        return message

    def _should_send_live(self) -> bool:
        """라이브 송신 조건 — 기본 False(DRY-RUN). 미배달 환경에서는 항상 False.

        미배달 환경은 선언 데이터(``NON_DELIVERING_ENVIRONMENTS``)가 정한다 — Telegram
        경계와 같은 집합이라 채널마다 판정이 갈라지지 않는다(§4.5-1/3).
        """
        if settings.ENVIRONMENT in NON_DELIVERING_ENVIRONMENTS:
            return False
        return bool(settings.EMAIL_DELIVERY_ENABLED and not settings.EMAIL_DRY_RUN)

    # --- orchestration + telemetry --------------------------------------------

    def deliver_for_decision(
        self,
        db: Session,
        *,
        decision_record_id: int,
        operator: User,
        recipient_override: str | None = None,
    ) -> EmailDeliveryResult:
        """결정 레코드로 요약+초안을 만들어 메일 전달(기본 DRY-RUN) + 텔레메트리 기록.

        요약/초안 집계는 ``BidSummaryService``/``BidFormDraftService`` 를 재사용한다.
        알 수 없는(또는 교차 운영자) 레코드는 ``ValueError`` 를 전파해 라우터가 404 로
        매핑한다. 전달/기록 실패는 베스트에포트로 흐름을 막지 않는다.
        """
        summary = self._summary_service.build_summary(
            db, decision_record_id=decision_record_id, operator=operator
        )
        draft = self._draft_service.build_draft(
            db, decision_record_id=decision_record_id, operator=operator
        )
        recipient = recipient_override or getattr(operator, "email", None)
        result = self.send_bid_report(summary=summary, draft=draft, recipient=recipient)
        self.record_delivery_event(
            db, operator_id=getattr(operator, "id", None), result=result
        )
        return result

    def record_delivery_event(
        self,
        db: Session,
        *,
        operator_id: Optional[int],
        result: EmailDeliveryResult,
    ) -> None:
        """전달 시도를 Analytics 에 기록한다(마스킹된 수신자만, 베스트에포트)."""
        try:
            event = Analytics(
                user_id=operator_id,
                event_type=_ANALYTICS_EVENT_TYPE,
                event_data=dump_analytics_event(
                    BidReportEmailDeliveryEvent(
                        operator_id=operator_id,
                        project_id=result.project_id,
                        decision_record_id=result.decision_record_id,
                        dry_run=result.dry_run,
                        sent=result.sent,
                        delivery_status=result.delivery_status,
                        masked_recipient=result.masked_recipient,
                        has_draft_attachment=result.has_draft_attachment,
                        recorded_at=utc_now().isoformat(),
                    )
                ),
            )
            db.add(event)
            db.commit()
        except Exception as exc:  # 베스트에포트: 텔레메트리 실패가 흐름을 막지 않는다.
            db.rollback()
            logger.warning(
                "투찰 보고서 메일 전달 텔레메트리 기록 실패 (record %s): %s",
                result.decision_record_id,
                exc,
                exc_info=True,
            )

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def _safe_ids(summary: Any) -> tuple[Optional[int], Optional[int]]:
        """요약에서 project_id/decision_record_id 를 방어적으로 추출한다."""
        project_id: Optional[int] = None
        record_id: Optional[int] = None
        try:
            record_raw = summary.get("decision_record_id")
            record_id = int(record_raw) if record_raw is not None else None
        except (AttributeError, TypeError, ValueError):
            record_id = None
        try:
            project_raw = (summary.get("notice") or {}).get("project_id")
            project_id = int(project_raw) if project_raw is not None else None
        except (AttributeError, TypeError, ValueError):
            project_id = None
        return project_id, record_id

    @staticmethod
    def _format_currency(amount: Optional[float]) -> str:
        if not amount:
            return ""
        return f"{int(round(amount)):,}원"

    @staticmethod
    def _format_percent(rate: Optional[float]) -> str:
        if rate is None:
            return ""
        return f"{rate * 100:.3f}%"

    @staticmethod
    def _format_datetime(value: Any) -> str:
        if value is None:
            return ""
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except (AttributeError, ValueError):
            return str(value)
