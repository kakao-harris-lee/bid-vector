"""Pydantic schemas for the 투찰서 초안(bid-form draft) export (PR8 / item 4-B).

Maps an already-persisted ``BidDecisionRecord`` aggregation onto the 나라장터
(KONEPS) 투찰서 입력 항목 so the operator can read the draft and **type the
values into KONEPS by hand**.

⚠ 자동 제출은 명시적으로 범위 밖입니다. 이 산출물은 KONEPS API를 호출하지 않으며,
어떤 형태의 자동 투찰서 제출도 수행하지 않습니다. 운영자가 직접 나라장터에 입력·
제출합니다.

표시 정직화 (PR7 와 동일 원칙):
- ``recommended_bid_rate`` 는 추천 투찰가 / 추정가격(budget_estimate) 비율이다.
- ``eligibility_estimate`` 는 카테고리 낙찰하한율(참고) 대비 추천가 위치에서 도출한
  **추정 라벨**이다. 실제 적격/낙찰 여부가 아니다. 라이브 공고는 예정가/실하한가를
  개찰 전에 모르므로 하한율은 참고값일 뿐이다.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# 운영자에게 항상 노출되는 정직 안내 문구. 자동 제출이 아님을 분명히 한다.
# bid_summary 의 DIRECT_SUBMISSION_NOTICE 와 동일 톤이되, 초안 export 맥락을 명시.
BID_FORM_DRAFT_NOTICE = (
    "이 투찰서 초안은 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 "
    "운영자가 직접 진행해야 합니다. 이 산출물은 KONEPS 를 호출하거나 자동으로 "
    "투찰서를 제출하지 않으며, 추천 투찰가는 보장된 낙찰가가 아닙니다."
)

# 적격여부(추정) 라벨 — 정직 표기. P(낙찰)/실제 적격 아님.
ELIGIBILITY_NOTE = (
    "카테고리 낙찰하한율(참고) 대비 추천 투찰가 위치에서 도출한 추정 라벨입니다. "
    "실제 낙찰하한가는 개찰 시 예정가 기준으로 결정되며, 적격/낙찰을 보장하지 않습니다."
)


class BidFormDraftField(BaseModel):
    """나라장터 투찰서 입력 항목 1개 — 라벨 + 값 + (선택) 보조 설명.

    운영자가 ``field_label`` 을 나라장터 화면의 입력 필드와 대조해 ``value`` 를 그대로
    입력할 수 있도록 구성한다. ``value`` 는 표시용 문자열(포맷 적용)이고, ``raw_value``
    는 원시 수치(있으면)로 프론트가 재포맷할 수 있게 보존한다.
    """

    key: str = Field(description="안정적 식별 키(프론트 매핑용, 영문 snake_case).")
    field_label: str = Field(description="나라장터 입력 필드 라벨(한국어).")
    value: str = Field(description="표시용 값(포맷 적용된 문자열). 미정 시 빈 문자열.")
    raw_value: Optional[float] = Field(
        default=None,
        description="원시 수치(금액/비율). 비수치 항목은 null.",
    )
    note: Optional[str] = Field(default=None, description="항목별 정직 caveat(있으면).")


class BidFormDraftResponse(BaseModel):
    """투찰서 초안 — 나라장터 입력 항목에 매핑된 구조화 산출물(자동 제출 아님)."""

    model_config = ConfigDict(from_attributes=False)

    decision_record_id: int
    operator_id: int
    generated_at: datetime = Field(description="이 초안을 작성(집계)한 시각(UTC).")

    # 핵심 식별/금액 필드 — 프론트/CSV 가 빠르게 참조할 수 있게 평탄화해 중복 노출.
    notice_number: Optional[str] = Field(default=None, description="공고번호.")
    title: str = Field(description="공고명.")
    demand_agency: Optional[str] = Field(default=None, description="수요기관.")
    budget_estimate: float = Field(
        default=0.0,
        description="기초금액(추정가격). 예정가/실하한가는 개찰 전 미공개.",
    )
    recommended_amount: float = Field(description="추천 투찰금액(원).")
    recommended_bid_rate: Optional[float] = Field(
        default=None,
        description="추천 투찰률 = 추천 투찰가 / 추정가격. budget>0 일 때만.",
    )
    category: Optional[str] = Field(default=None, description="카테고리(분류).")
    business_type_label: Optional[str] = Field(
        default=None, description="업종(세부 분류 라벨)."
    )
    deadline: Optional[datetime] = Field(default=None, description="투찰 마감일시.")

    eligibility_estimate: str = Field(
        description=(
            "적격여부(추정) 라벨 — '적격 추정' / '하한 근접' / '하한 미만(주의)' / "
            "'판단 불가' 중 하나. 실제 적격/낙찰 아님."
        )
    )
    eligibility_note: str = Field(default=ELIGIBILITY_NOTE)

    fields: List[BidFormDraftField] = Field(
        description="나라장터 입력 항목 매핑(라벨+값) 리스트. 운영자가 그대로 입력."
    )

    direct_submission_notice: str = Field(
        default=BID_FORM_DRAFT_NOTICE,
        description="자동 제출이 아니며 운영자가 직접 제출한다는 정직 안내 문구.",
    )
