"""Pydantic schemas for the bid-decision summary (투찰 의사결정 요약).

PR7 / item 4-A. Aggregates already-persisted data for one ``BidDecisionRecord``
into a single decision-support payload an operator consults while writing the
바라 나라장터 투찰서 **by hand** (자동 제출은 범위 밖 — 운영자가 직접 제출).

표시 정직화: 가격 적합도(추정)는 P(낙찰)이 아니다. 카테고리 낙찰하한율은 라이브
공고에서는 예정가/실하한가를 개찰 전에 모르므로 **참고용 하한율**이며, 분야 통계는
백테스트 기반 추정(표본 수 동반)이다.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.prediction import FloorShortfallEstimate

# 가격 적합도(추정) 라벨 — schemas.py 의 _PROBABILITY_SCORE_DESCRIPTION 와 정직 일관.
_PROBABILITY_SCORE_DESCRIPTION = (
    "가격 적합도(추정) — P(낙찰) 아님(would_have_won_final 게이트 별도)"
)

# 두 금액이 왜 다른지 운영자에게 항상 같은 문장으로 설명한다(단일 출처). 화면·CSV·
# 메일 어디서 읽어도 "예산"이 투찰 기준금액이 아니라는 사실이 함께 따라가야 한다.
BID_BASE_NOTE = (
    "투찰율이 곱해지는 기준금액은 기초금액/사업금액(과세 공고면 부가세 포함)이며, "
    "추정가격(부가세 별도 표기)과 다른 금액입니다. 투찰금액은 기초금액 기준으로 "
    "산정되므로 추정가격에 투찰율을 곱해 검산하면 값이 어긋납니다."
)

# 운영자에게 항상 노출되는 정직 안내 문구. 자동 제출이 아님을 분명히 한다.
DIRECT_SUBMISSION_NOTICE = (
    "이 요약은 투찰 판단 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 "
    "운영자가 직접 진행해야 하며, 추천 투찰가는 보장된 낙찰가가 아닙니다."
)


class BidSummaryNoticeMeta(BaseModel):
    """공고 메타 — 투찰서 헤더 작성에 필요한 식별 정보."""

    project_id: int
    title: str
    notice_number: Optional[str] = None
    category: Optional[str] = None
    business_type_label: Optional[str] = None
    budget_estimate: float = Field(
        default=0.0,
        description=(
            "추정가격(부가세 별도 표기). **투찰 기준금액이 아니다** — 투찰율은 "
            "bid_base_amount 에 곱해진다. 예정가/실하한가는 개찰 전 미공개."
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
            "budget-estimate-fallback). budget-estimate-fallback 은 기초금액을 확보하지 "
            "못해 추정가격을 그대로 기준금액으로 쓴 상태."
        ),
    )
    bid_base_to_estimate_ratio: Optional[float] = Field(
        default=None,
        description=(
            "기초금액 ÷ 추정가격. 1.0 이면 두 금액이 같고, 1.1 부근이면 기초금액이 "
            "추정가격에 없는 부가세를 포함한다는 뜻이다. 추정가격이 0 이면 null."
        ),
    )
    bid_base_note: str = Field(default=BID_BASE_NOTE)
    demand_agency: Optional[str] = None
    issuing_agency: Optional[str] = None
    deadline: Optional[datetime] = None
    source_url: Optional[str] = None
    status: Optional[str] = None


class BidSummaryRecommendation(BaseModel):
    """추천 투찰가/투찰률/가격대 + 가격 적합도(추정) + 결정 상태."""

    recommended_amount: float = Field(
        description=(
            "추천 투찰가(원) — 나라장터에 그대로 입력하는 제출값이다. 기초금액 기준으로 "
            "산정되므로 과세 공고에서는 부가세가 포함된 금액이다(추정가격 기준 아님)."
        )
    )
    recommended_bid_rate: Optional[float] = Field(
        default=None,
        description=(
            "추천 투찰가 / 추정가격 — **참고 지표**. 적격심사가 보는 율이 아니다"
            "(그 율은 recommended_bid_rate_on_base). budget_estimate>0 일 때만 산출."
        ),
    )
    recommended_bid_rate_on_base: Optional[float] = Field(
        default=None,
        description=(
            "추천 투찰가 / 기초금액 — 낙찰하한율과 **같은 basis** 의 투찰율이라 하한 "
            "비교는 이 값으로 한다. bid_base_amount>0 일 때만 산출."
        ),
    )
    probability_score: float = Field(description=_PROBABILITY_SCORE_DESCRIPTION)
    action: str = Field(description="bid_now / review / skip 중 현재 판단.")
    decision_status: str = Field(
        description="planned / reviewing / submitted / skipped 워크플로 상태."
    )
    priority_score: float = Field(default=0.0, description="종합 우선순위 점수(0-1).")
    matched_score: float = Field(default=0.0, description="공고 적합도 점수(0-1).")
    competitiveness_score: float = Field(
        default=0.0, description="시장 경쟁력 점수(0-1)."
    )
    reasoning: str = Field(default="", description="결정 근거 서술(감사 가능).")
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class BidSummaryPrediction(BaseModel):
    """결정에 연결된 가격 예측 메타(없으면 null)."""

    predicted_price: Optional[float] = None
    predicted_bid_rate: Optional[float] = None
    price_range_min: Optional[float] = None
    price_range_max: Optional[float] = None
    confidence_score: Optional[float] = None
    pricing_mode: Optional[str] = None
    predictor_name: Optional[str] = None
    guardrail_applied: bool = False
    floor_bid_rate: Optional[float] = Field(
        default=None,
        description="예측이 적용한 낙찰하한 가드레일 투찰률(있으면).",
    )
    created_at: Optional[datetime] = None


class BidSummaryCategoryFloor(BaseModel):
    """카테고리 낙찰하한율(참고) — 가드레일 floor 와 동일 출처.

    라이브 공고는 예정가/실하한가를 개찰 전에 모른다. 따라서 이 값은 절대 하한가가
    아니라 **설정된 카테고리 최소 투찰률(참고)**이다.
    """

    category: Optional[str] = None
    business_group: Optional[str] = None
    floor_bid_rate: Optional[float] = Field(
        default=None, description="카테고리/그룹 최소 투찰률(참고). 미설정 시 null."
    )
    floor_price: Optional[float] = Field(
        default=None,
        description=(
            "bid_base_amount * floor_bid_rate 참고 하한가. 이 율은 예측이 기초금액에 "
            "곱하는 값이므로 하한가도 기초금액 기준으로 환산한다(추정가격 기준 아님, "
            "예정가 기준도 아님)."
        ),
    )
    note: str = Field(
        default=(
            "카테고리 낙찰하한율(참고)입니다. 실제 낙찰하한가는 개찰 시 예정가 기준으로 "
            "결정되며, 이 값은 가드레일이 사용하는 설정값일 뿐입니다."
        )
    )


class BidSummaryFieldStat(BaseModel):
    """분야 통계(체크리스트) — 최신 백테스트 분야 지표(있으면)."""

    category: Optional[str] = None
    settled_count: int = Field(default=0, description="해당 분야 백테스트 표본 수.")
    est_price_close_rate: Optional[float] = Field(
        default=None,
        description="가격 근접 추정율(would_have_won_price_only / settled). 실제 낙찰 아님.",
    )
    eligible_favorable_rate: Optional[float] = Field(
        default=None,
        description="적격성 게이트 추정 적격율(unknown 제외 분모).",
    )
    source_run_id: Optional[int] = None
    source_operator_slug: Optional[str] = None
    note: str = Field(
        default=(
            "최신 백테스트 분야 추정 지표입니다(표본 수 동반). 실제 낙찰률이 아닌 "
            "가격/적격성 기반 추정이며, 표본이 적으면 신뢰도가 낮습니다."
        )
    )


class BidSummaryResponse(BaseModel):
    """투찰 의사결정 요약 — 운영자가 직접 투찰서를 작성할 때 참고하는 집계 산출물."""

    model_config = ConfigDict(from_attributes=False)

    decision_record_id: int
    operator_id: int
    generated_at: datetime
    notice: BidSummaryNoticeMeta
    recommendation: BidSummaryRecommendation
    prediction: Optional[BidSummaryPrediction] = None
    category_floor: BidSummaryCategoryFloor
    floor_shortfall: Optional[FloorShortfallEstimate] = Field(
        default=None,
        description=(
            "추천 투찰가가 낙찰하한 미달이 됐을 과거 표본 빈도(실격 확률 아님). 내부 "
            "shortfall_frequency 가 null 이면 '위험 없음'이 아니라 '판정 불가'이며 "
            "unmeasurable_reason 에 사유가 담긴다. 이 필드 자체가 null 이면 산출 경로가 "
            "동작하지 않은 것."
        ),
    )
    field_stat: Optional[BidSummaryFieldStat] = Field(
        default=None,
        description="분야 통계(없거나 미산출 시 null — graceful).",
    )
    direct_submission_notice: str = Field(
        default=DIRECT_SUBMISSION_NOTICE,
        description="실제 나라장터 투찰서 제출은 운영자가 직접 진행한다는 안내 문구.",
    )
