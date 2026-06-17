"""Pydantic schemas for the 의사결정 증적 샘플(decision evidence samples) export.

Roadmap 0단계 L87 "예측/추천 API 응답 샘플 캡처(증적 보관)".

이 산출물은 시스템이 이미 영속화한 추천(``BidDecisionRecord``)과 예측
(``PricePrediction``)을 **최근 순으로 통합 조회**해 감사 증적으로 노출한다.
정확도 리포트가 정산(settled) 항목만 보여주는 것과 달리, 이 목록은 정산 여부와
무관하게 시스템이 **산출·기록한 추천/예측 그 자체**를 보여준다.

표시 정직화 (§1.5 와 동일 원칙):
- ``probability_score`` 는 **가격 적합도(추정)** 이지 P(낙찰) 이 아니다.
- 이 목록은 **감사 증적**이며 정산·실낙찰 결과가 아니다.
- 단일 운영자(canonical) 기준. ``operator_id`` 는 정보용으로만 노출한다.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# 운영자/감사자에게 항상 노출되는 정직 안내 문구.
DECISION_SAMPLES_NOTES = (
    "이 목록은 시스템이 산출·기록한 추천/예측의 감사 증적이며, 정산·실낙찰 결과가 "
    "아닙니다. probability_score 는 가격 적합도(추정)이지 P(낙찰)이 아닙니다. "
    "현재 선택된 운영자 기준이며, truncated 가 true 이면 limit 상한에 도달해 "
    "더 오래된 기록이 잘렸음을 의미합니다."
)


class DecisionSamplePrediction(BaseModel):
    """샘플에 연결된 최신 ``PricePrediction`` 증적(서빙된 예측)."""

    model_config = ConfigDict(from_attributes=False)

    predicted_price: Optional[float] = Field(
        default=None, description="서빙된 예측 투찰가(원)."
    )
    price_range_min: Optional[float] = Field(
        default=None, description="예측 가격 범위 하한(원)."
    )
    price_range_max: Optional[float] = Field(
        default=None, description="예측 가격 범위 상한(원)."
    )
    confidence_score: Optional[float] = Field(
        default=None, description="예측 신뢰도(0-1)."
    )
    predictor_name: Optional[str] = Field(
        default=None, description="예측을 산출한 predictor 이름."
    )
    predictor_family: Optional[str] = Field(
        default=None, description="predictor 계열(statistical/ml 등)."
    )
    pricing_mode: Optional[str] = Field(
        default=None, description="가격 산출 모드(heuristic/model 등)."
    )
    created_at: Optional[datetime] = Field(
        default=None, description="이 예측이 기록된 시각(UTC)."
    )


class DecisionSampleItem(BaseModel):
    """추천 1건의 증적 — ``BidDecisionRecord`` + 공고 메타 + 최신 예측(있으면)."""

    model_config = ConfigDict(from_attributes=False)

    decision_record_id: int = Field(description="BidDecisionRecord id.")
    project_id: int = Field(description="연결된 공고 id.")
    notice_number: Optional[str] = Field(default=None, description="공고번호.")
    title: str = Field(default="", description="공고명.")
    demand_agency: Optional[str] = Field(default=None, description="수요기관.")
    category: Optional[str] = Field(default=None, description="카테고리(분류).")
    budget_estimate: Optional[float] = Field(
        default=None, description="기초금액(추정가격, 원)."
    )
    deadline: Optional[datetime] = Field(default=None, description="투찰 마감일시.")

    recommended_amount: float = Field(
        default=0.0, description="추천 투찰금액(원)."
    )
    recommended_bid_rate: Optional[float] = Field(
        default=None,
        description="추천 투찰률 = 추천 투찰가 / 추정가격. budget>0 일 때만, 아니면 null.",
    )
    action: Optional[str] = Field(default=None, description="추천 액션(bid_now/skip 등).")
    decision_status: Optional[str] = Field(
        default=None, description="결정 상태(planned/reviewing/submitted/skipped)."
    )

    probability_score: Optional[float] = Field(
        default=None,
        description="가격 적합도(추정) 점수. P(낙찰) 아님(§1.5).",
    )
    priority_score: Optional[float] = Field(
        default=None, description="우선순위 점수."
    )
    matched_score: Optional[float] = Field(
        default=None, description="프로필 적합도(매칭) 점수."
    )
    competitiveness_score: Optional[float] = Field(
        default=None, description="경쟁력 점수."
    )
    reasoning: Optional[str] = Field(
        default=None, description="추천 근거(감사용 기록)."
    )

    decided_at: Optional[datetime] = Field(
        default=None, description="최초 결정 시각(first_decided_at)."
    )
    created_at: Optional[datetime] = Field(
        default=None, description="이 추천 기록이 생성된 시각(UTC)."
    )
    updated_at: Optional[datetime] = Field(
        default=None, description="이 추천 기록이 마지막으로 갱신된 시각(UTC)."
    )

    prediction: Optional[DecisionSamplePrediction] = Field(
        default=None,
        description="이 공고에 대한 최신 서빙 예측 증적. 저장된 예측이 없으면 null.",
    )


class DecisionSamplesResponse(BaseModel):
    """최근 추천/예측 증적 샘플 목록(감사 목적, 정산 결과 아님)."""

    model_config = ConfigDict(from_attributes=False)

    operator_id: int = Field(description="현재 응답 범위로 해소된 운영자 id.")
    current_operator_id: int = Field(description="현재 응답 범위로 해소된 운영자 id.")
    current_operator_username: str = Field(description="현재 응답 범위로 해소된 운영자 username.")
    period_days: int = Field(description="조회 기간(일). created_at >= now-days.")
    limit: int = Field(description="요청한 최대 샘플 수.")
    sample_count: int = Field(description="반환된 샘플 수.")
    generated_at: datetime = Field(description="이 목록을 생성한 시각(UTC).")
    truncated: bool = Field(
        description="sample_count 가 limit 에 도달했는지. true 면 더 오래된 기록이 잘림.",
    )
    notes: str = Field(
        default=DECISION_SAMPLES_NOTES,
        description="감사 증적/추정 라벨/단일 운영자 기준에 대한 정직 안내 문구.",
    )
    samples: List[DecisionSampleItem] = Field(
        default_factory=list,
        description="최근 순(created_at desc) 추천 증적 샘플 리스트.",
    )
