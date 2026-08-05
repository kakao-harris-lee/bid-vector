"""Pydantic schemas for the 투찰서 초안(bid-form draft) export (PR8 / item 4-B).

Maps an already-persisted ``BidDecisionRecord`` aggregation onto the 나라장터
(KONEPS) 투찰서 입력 항목 so the operator can read the draft and **type the
values into KONEPS by hand**.

⚠ 자동 제출은 명시적으로 범위 밖입니다. 이 산출물은 KONEPS API를 호출하지 않으며,
어떤 형태의 자동 투찰서 제출도 수행하지 않습니다. 운영자가 직접 나라장터에 입력·
제출합니다.

표시 정직화 (PR7 와 동일 원칙):
- ``bid_base_amount`` 는 투찰율이 곱해지는 기초금액/사업금액(과세 공고면 부가세 포함)
  이고, ``budget_estimate`` 는 추정가격(부가세 별도 표기)이다. **서로 다른 금액**이라
  한 라벨로 묶지 않는다 — 두 금액을 동의어로 표기한 초안이 실투찰 혼동의 원인이었다.
- ``recommended_bid_rate`` 는 추천 투찰가 / 추정가격(budget_estimate) 비율이고,
  ``recommended_bid_rate_on_base`` 는 기초금액 기준 투찰율이다. 카테고리 **참고**
  하한율이 기초금액 기준이므로 적격여부(추정) 판정은 후자를 쓴다. 다만 실제 낙찰하한가는
  개찰 시 추첨된 **예정가격** 기준이라 이 비교로는 실격 여부가 결정되지 않으며, 그
  괴리 위험은 ``floor_shortfall``(하한 미달 빈도)이 따로 표시한다.
- ``eligibility_estimate`` 는 카테고리 낙찰하한율(참고) 대비 추천가 위치에서 도출한
  **추정 라벨**이다. 실제 적격/낙찰 여부가 아니다. 라이브 공고는 예정가/실하한가를
  개찰 전에 모르므로 하한율은 참고값일 뿐이다.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bid_summary import BID_BASE_NOTE
from app.schemas.prediction import FloorShortfallEstimate

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

# 복수예비가격 추첨번호 — 무작위 편의 픽. 정직 표기(분석/유리 아님).
# 예정가격은 전체 투찰자의 추첨을 합산해 공동으로 정해지므로, 개별 번호 선택은
# 자기 낙찰 결과에 영향이 없다. 공고번호 seed 로 재현 가능(감사 가능).
LOTTERY_NUMBERS_NOTE = (
    "무작위 편의 픽입니다(공고번호 기준 재현 가능). 복수예비가격 15개 중 2개를 "
    "고르는 절차로, 예정가격은 전체 투찰자의 선택을 합산해 정해지므로 개별 번호 "
    "선택은 낙찰 결과에 영향이 없습니다. 분석·최적화 결과가 아니며, 공고에 따라 "
    "복수예비가격 절차가 적용되지 않을 수 있습니다."
)


# 하한 미달 빈도 — 정직 표기. 실격 확률이 아니라 과거 개찰 표본에서 같은 투찰율이
# 하한 아래로 떨어졌을 비율이며, 이번 공고의 추첨 결과를 예측하지 않는다.
SHORTFALL_NOTE = (
    "과거 개찰 표본에서 같은 투찰율이 낙찰하한 미달(실격)로 떨어졌을 **표본 비율**"
    "입니다. 이번 공고의 실격 확률이 아닙니다 — 예정가격은 개찰 시 추첨으로 정해집니다."
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
        description=(
            "추정가격(부가세 별도 표기). **기초금액과 다른 금액이며 투찰 기준금액이 "
            "아니다** — 투찰율은 bid_base_amount 에 곱해진다. 예정가/실하한가는 개찰 전 "
            "미공개."
        ),
    )
    bid_base_amount: float = Field(
        default=0.0,
        description=(
            "공고 투찰 기준금액(기초금액/사업금액, 과세 공고는 부가세 포함). 추천 "
            "투찰금액이 실제로 곱해진 금액이다."
        ),
    )
    bid_base_source: Optional[str] = Field(
        default=None,
        description=(
            "기초금액 출처(clean-base / reserve-estimate / base-fallback / "
            "budget-estimate-fallback)."
        ),
    )
    bid_base_to_estimate_ratio: Optional[float] = Field(
        default=None,
        description=(
            "기초금액 ÷ 추정가격(관측값). 1.1 부근은 전형적으로 부가세 관계가 관측되는 "
            "값이나, 이 비율만으로 과세 여부를 단정할 수는 없다. 추정가격이 0 이면 null."
        ),
    )
    bid_base_note: str = Field(default=BID_BASE_NOTE)
    recommended_amount: float = Field(
        description=(
            "추천 투찰금액(원) — 나라장터에 그대로 입력하는 제출값. 기초금액 기준으로 "
            "산정되므로 과세 공고에서는 부가세가 포함된 금액이다."
        )
    )
    recommended_bid_rate: Optional[float] = Field(
        default=None,
        description=(
            "추천 투찰가 / 추정가격 — **참고 지표**. 적격심사가 보는 율이 아니다"
            "(그 율은 recommended_bid_rate_on_base). budget>0 일 때만."
        ),
    )
    recommended_bid_rate_on_base: Optional[float] = Field(
        default=None,
        description=(
            "추천 투찰가 / 기초금액 — 카테고리 **참고** 하한율(기초금액 기준)과 같은 "
            "basis 라 적격여부(추정) 판정과 투찰서 표기는 이 율을 쓴다. 실제 낙찰하한가는 "
            "개찰 시 추첨된 **예정가격** 기준이라 이 비교가 실격 여부를 결정하지는 "
            "않으며, 그 괴리 위험은 floor_shortfall 이 표시한다. bid_base_amount>0 일 때만."
        ),
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

    lottery_numbers: List[int] = Field(
        default_factory=list,
        description=(
            "복수예비가격 추첨번호(2개) 무작위 편의 픽. 공고번호 seed 로 재현 가능. "
            "분석/유리가 아니며 개별 선택은 낙찰 결과에 영향 없음. 공고번호 부재 시 빈 리스트."
        ),
    )
    lottery_numbers_note: str = Field(default=LOTTERY_NUMBERS_NOTE)

    floor_shortfall: Optional[FloorShortfallEstimate] = Field(
        default=None,
        description=(
            "추천 투찰가가 낙찰하한 미달이 됐을 과거 표본 빈도(실격 확률 아님). 내부 "
            "shortfall_frequency 가 null 이면 '위험 없음'이 아니라 '판정 불가'이며 사유는 "
            "unmeasurable_reason 에 담긴다."
        ),
    )

    fields: List[BidFormDraftField] = Field(
        description="나라장터 입력 항목 매핑(라벨+값) 리스트. 운영자가 그대로 입력."
    )

    direct_submission_notice: str = Field(
        default=BID_FORM_DRAFT_NOTICE,
        description="자동 제출이 아니며 운영자가 직접 제출한다는 정직 안내 문구.",
    )
