"""Pydantic schemas for the 투찰 보고서 메일 전달(bid-report email delivery) action.

기존 산출물(``BidSummaryService`` + ``BidFormDraftService``)을 **재사용**해 메일로
전달하는 얇은 배달 채널의 입출력 계약이다. 이 채널은 기본 **DRY-RUN** 이다: 제목/본문을
렌더링하고 로깅만 하며, SMTP 연결이나 외부 송신을 하지 않는다.

정직 명세(§2):
- 수신자 이메일은 응답/로그/텔레메트리 어디에도 **마스킹된 형태로만** 노출한다
  (원문 절대 노출 금지).
- 본문은 자동 제출이 아니며 보장된 낙찰가가 아님을 알리는 정직 고지를 담는다.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bid_summary import DIRECT_SUBMISSION_NOTICE


class BidReportEmailSendRequest(BaseModel):
    """투찰 보고서 메일 전달 요청(모두 선택). 본문 없이 POST 해도 된다."""

    recipient: Optional[str] = Field(
        default=None,
        description=(
            "수신자 이메일 override(선택). 미지정 시 운영자 계정 이메일을 사용한다. "
            "지정하더라도 응답/로그에는 항상 마스킹되어 노출되며, DRY-RUN 이므로 실제 "
            "송신은 일어나지 않는다."
        ),
    )


class BidReportEmailDeliveryResponse(BaseModel):
    """투찰 보고서 메일 전달 결과 — 원문 수신자/본문 시크릿을 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=False)

    project_id: Optional[int] = Field(
        default=None, description="공고(프로젝트) ID(요약에서 도출)."
    )
    decision_record_id: int = Field(description="대상 BidDecisionRecord ID.")
    dry_run: bool = Field(
        default=True,
        description=(
            "DRY-RUN 여부. 기본 True(외부 송신 없음 — 렌더링/로깅만). 라이브 송신은 "
            "설정으로만 켤 수 있는 향후 opt-in."
        ),
    )
    delivery_status: str = Field(
        description=(
            "전달 상태 — 'dry_run_rendered'(렌더링 완료·미송신) / "
            "'skipped_no_recipient'(수신자 없음) / 'failed'(베스트에포트 실패) / "
            "'sent'(라이브 송신, 기본 비활성) 중 하나."
        ),
    )
    masked_recipient: str = Field(
        description="마스킹된 수신자 이메일. 원문은 어떤 응답/로그에도 노출하지 않는다."
    )
    subject: str = Field(description="렌더링된 메일 제목.")
    has_draft_attachment: bool = Field(
        description="투찰서 초안 CSV 첨부 포함 여부."
    )
    notice: str = Field(
        default=DIRECT_SUBMISSION_NOTICE,
        description="자동 제출이 아니며 추천가는 보장 낙찰가가 아니라는 정직 고지.",
    )
