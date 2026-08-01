"""Opportunity analysis and bid-decision schemas."""

import json
from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.core.constants import DecisionStatus, PaperBidAction
from app.schemas._shared import _PROBABILITY_SCORE_DESCRIPTION
from app.schemas.crawl import ClassificationResponse
from app.schemas.prediction import PricePredictionResponse
from app.schemas.project import ProjectSimilaritySearchResponse
from app.utils.sequence_coercion import as_str_list


def _extract_decision_reasons(score_breakdown) -> tuple[list[str], list[str]]:
    """Parse persisted strengths/risk_flags out of a score_breakdown blob.

    The score_breakdown column stores decision signals as JSON text. Decision
    reasons (strengths/risk_flags) are merged into the same blob so they can be
    persisted without a schema migration. Older records that predate this merge
    simply lack the keys and resolve to empty lists.
    """
    if score_breakdown is None or score_breakdown == "":
        return [], []
    if isinstance(score_breakdown, str):
        try:
            parsed = json.loads(score_breakdown)
        except json.JSONDecodeError:
            return [], []
    else:
        parsed = score_breakdown
    if not isinstance(parsed, dict):
        return [], []

    return as_str_list(parsed.get("strengths")), as_str_list(parsed.get("risk_flags"))


class OpportunityAnalysisRequest(BaseModel):
    project_id: int
    agency_name: Optional[str] = None
    legal_floor_bid_rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="공고별 법정 낙찰하한율. 메일/공고 분석값을 가격 예측 guardrail에 전달합니다.",
    )
    current_active_bids: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optional what-if override. When omitted, the service counts active bid decisions from the DB.",
    )
    max_active_bids: int = Field(default=3, ge=1)
    current_workload_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    same_category_only: bool = True
    similar_limit: int = Field(default=3, ge=1, le=10)
    min_similarity: float = Field(default=0.15, ge=0.0, le=1.0)
    user_historical_data: Optional[dict] = None


class BidDecisionRequest(BaseModel):
    project_id: int
    recommended_amount: float
    probability_score: float = Field(description=_PROBABILITY_SCORE_DESCRIPTION)
    matched_score: float = Field(default=0.0, ge=0.0, le=1.0)
    deadline_hours_remaining: Optional[int] = Field(default=None, ge=0)
    current_active_bids: int = Field(default=0, ge=0)
    max_active_bids: int = Field(default=3, ge=1)
    current_workload_score: float = Field(default=0.0, ge=0.0, le=1.0)
    budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    competitiveness_score: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_margin_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    execution_complexity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workload_source: Literal["provided", "auto"] = "provided"


class BidDecisionScoreBreakdown(BaseModel):
    probability_signal: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_signal: float = Field(default=0.0, ge=0.0, le=1.0)
    urgency_signal: float = Field(default=0.0, ge=0.0, le=1.0)
    competitiveness_signal: float = Field(default=0.5, ge=0.0, le=1.0)
    budget_capture_signal: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_margin_signal: float = Field(default=0.5, ge=0.0, le=1.0)
    execution_complexity_signal: float = Field(default=0.35, ge=0.0, le=1.0)
    active_load_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    workload_score_used: float = Field(default=0.0, ge=0.0, le=1.0)
    opportunity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    auto_workload_penalty_multiplier: float = Field(default=1.0, ge=0.0, le=2.0)
    load_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_complexity_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    total_penalty: float = Field(default=0.0, ge=0.0, le=1.0)


class BidDecisionResponse(BaseModel):
    project_id: int
    pursue_bid: bool
    action: PaperBidAction
    priority_score: float
    recommended_amount: float
    probability_score: float = Field(description=_PROBABILITY_SCORE_DESCRIPTION)
    urgency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    competitiveness_score: float = Field(default=0.5, ge=0.0, le=1.0)
    budget_capture_score: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_margin_score: float = Field(default=0.5, ge=0.0, le=1.0)
    execution_complexity_score: float = Field(default=0.35, ge=0.0, le=1.0)
    workload_source: Literal["provided", "auto"] = "provided"
    score_breakdown: BidDecisionScoreBreakdown = Field(
        default_factory=BidDecisionScoreBreakdown
    )
    reasoning: str


class OpportunityMarketInsights(BaseModel):
    average_bid: float
    median_bid: float
    std_dev: float
    min_bid: float
    max_bid: float
    competitiveness_score: float = Field(ge=0.0, le=1.0)


class OpportunityAnalysisResponse(BaseModel):
    project_id: int
    project_title: str
    operator_id: int
    matched: bool
    matched_score: float = Field(ge=0.0, le=1.0)
    probability_score: float = Field(
        ge=0.0, le=1.0, description=_PROBABILITY_SCORE_DESCRIPTION
    )
    recommended_amount: float
    deadline_hours_remaining: Optional[int] = None
    current_active_bids: int = Field(ge=0)
    max_active_bids: int = Field(ge=1)
    current_workload_score: float = Field(ge=0.0, le=1.0)
    workload_source: Literal["provided", "auto"] = "provided"
    strategy_adjustments: Dict[str, float] = Field(default_factory=dict)
    analysis_summary: str
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    market_insights: OpportunityMarketInsights
    classification: ClassificationResponse
    price_prediction: PricePredictionResponse
    similar_projects: ProjectSimilaritySearchResponse
    decision: BidDecisionResponse


class BidDecisionSaveRequest(BidDecisionRequest):
    decision_status: Optional[DecisionStatus] = None
    strengths: List[str] = Field(
        default_factory=list,
        description="Why this notice is pursuable; persisted into score_breakdown.",
    )
    risk_flags: List[str] = Field(
        default_factory=list,
        description="Why this notice is risky; persisted into score_breakdown.",
    )


class BidDecisionRecordResponse(BaseModel):
    id: int
    project_id: int
    operator_id: int
    pursue_bid: bool
    action: PaperBidAction
    decision_status: DecisionStatus
    initial_action: PaperBidAction = "skip"
    initial_decision_status: DecisionStatus = "planned"
    first_decided_at: Optional[datetime] = None
    priority_score: float
    urgency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    competitiveness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    budget_capture_score: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_margin_score: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_complexity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_amount: float
    probability_score: float = Field(description=_PROBABILITY_SCORE_DESCRIPTION)
    matched_score: float
    deadline_hours_remaining: Optional[int] = None
    current_active_bids: int
    max_active_bids: int
    current_workload_score: float
    workload_source: Literal["provided", "auto"] = "provided"
    score_breakdown: BidDecisionScoreBreakdown = Field(
        default_factory=BidDecisionScoreBreakdown
    )
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    reasoning: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _hydrate_decision_reasons(cls, data):
        """Lift strengths/risk_flags out of the persisted score_breakdown blob.

        ORM records expose score_breakdown as JSON text and have no dedicated
        columns for decision reasons, so derive the list fields from that blob
        when they are not supplied explicitly.
        """
        raw_breakdown = None
        explicit_strengths = None
        explicit_risk_flags = None
        if isinstance(data, dict):
            raw_breakdown = data.get("score_breakdown")
            explicit_strengths = data.get("strengths")
            explicit_risk_flags = data.get("risk_flags")
        else:
            raw_breakdown = getattr(data, "score_breakdown", None)
            explicit_strengths = getattr(data, "strengths", None)
            explicit_risk_flags = getattr(data, "risk_flags", None)

        if explicit_strengths is not None and explicit_risk_flags is not None:
            return data

        derived_strengths, derived_risk_flags = _extract_decision_reasons(raw_breakdown)

        if isinstance(data, dict):
            payload = dict(data)
        else:
            payload = {
                field: getattr(data, field)
                for field in cls.model_fields
                if hasattr(data, field)
            }
        if explicit_strengths is None:
            payload["strengths"] = derived_strengths
        if explicit_risk_flags is None:
            payload["risk_flags"] = derived_risk_flags
        return payload

    @field_validator("score_breakdown", mode="before")
    @classmethod
    def _parse_score_breakdown(cls, value):
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value

    @field_validator("initial_action", mode="before")
    @classmethod
    def _normalize_initial_action(cls, value):
        return value or "skip"

    @field_validator("initial_decision_status", mode="before")
    @classmethod
    def _normalize_initial_decision_status(cls, value):
        return value or "planned"


class BidDecisionActionRequest(BaseModel):
    """Inline dashboard action request for an existing bid-decision record.

    Mirrors the action vocabulary accepted by
    :meth:`app.services.allocation.BidDecisionService.apply_telegram_action`
    so the dashboard reuses the same transition semantics as the Telegram
    inline buttons.
    """

    action: Literal["submit", "review", "skip"]


class BidDecisionProjectSnapshot(BaseModel):
    id: int
    title: str
    category: Optional[str] = None
    status: str
    budget_estimate: float
    deadline: Optional[datetime] = None
    notice_number: Optional[str] = None
    source_url: Optional[str] = None
    issuing_agency: Optional[str] = None
    demand_agency: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class BidDecisionDetailResponse(BaseModel):
    record: BidDecisionRecordResponse
    project: BidDecisionProjectSnapshot
    timeline_count: int = Field(ge=0)
    timeline_limit_applied: int = Field(ge=1, le=100)
    timeline: List[BidDecisionRecordResponse] = Field(default_factory=list)


class BidDecisionTimelineResponse(BaseModel):
    operator_id: int
    project: BidDecisionProjectSnapshot
    result_count: int = Field(ge=0)
    limit_applied: int = Field(ge=1, le=100)
    latest_decision_record_id: Optional[int] = None
    timeline: List[BidDecisionRecordResponse] = Field(default_factory=list)


AllocationRequest = BidDecisionRequest


AllocationResponse = BidDecisionResponse
