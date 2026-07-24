"""투찰 보고서 메일 전달 route — 기본 DRY-RUN, 외부 송신 없음.

Thin router — 렌더링/전달/텔레메트리는 ``EmailNotificationService`` 가 담당한다.
기존 산출물(``BidSummaryService`` + ``BidFormDraftService``)을 재사용해 메일로 전달하되,
기본 DRY-RUN 이라 SMTP 연결/외부 송신을 하지 않는다.

⚠ 자동 제출은 범위 밖입니다. 이 엔드포인트는 KONEPS 를 호출하지 않으며, 수신자
이메일은 응답/로그에 항상 마스킹되어 노출됩니다(원문 노출 금지).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.schemas.bid_report_email import (
    BidReportEmailDeliveryResponse,
    BidReportEmailSendRequest,
)
from app.services.notifications.email import EmailNotificationService

router = APIRouter()


@router.post(
    "/bid-decisions/{decision_record_id}/report-email",
    response_model=BidReportEmailDeliveryResponse,
)
def send_bid_report_email(
    decision_record_id: int,
    payload: Optional[BidReportEmailSendRequest] = None,
    db: Session = Depends(get_db),
) -> BidReportEmailDeliveryResponse:
    """투찰 보고서를 운영자 이메일로 전달한다(기본 DRY-RUN — 렌더링/로깅만).

    기존 요약(``BidSummaryService``)+초안(``BidFormDraftService``)을 재사용해 메일을
    렌더링하고 전달 시도를 Analytics 에 기록한다. 실제 SMTP 송신은 하지 않으며(라이브
    송신은 설정으로만 켜지는 향후 opt-in), 수신자는 마스킹되어 노출된다. 404 when the
    record id is unknown(또는 다른 운영자 소유) / linked project is missing.
    """
    operator = ensure_operator_account(db)
    recipient_override = payload.recipient if payload else None

    service = EmailNotificationService()
    try:
        result = service.deliver_for_decision(
            db,
            decision_record_id=decision_record_id,
            operator=operator,
            recipient_override=recipient_override,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return BidReportEmailDeliveryResponse(
        project_id=result.project_id,
        decision_record_id=decision_record_id,
        dry_run=result.dry_run,
        delivery_status=result.delivery_status,
        masked_recipient=result.masked_recipient,
        subject=result.subject,
        has_draft_attachment=result.has_draft_attachment,
    )
