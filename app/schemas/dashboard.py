"""Operator-facing dashboard (opportunities / bids / results) schemas."""

from datetime import date, datetime
from typing import List, Literal, Optional, Union
from pydantic import BaseModel, Field
from app.core.constants import PaperBidAction
from app.schemas._shared import _PROBABILITY_SCORE_DESCRIPTION


class DashboardProjectBrief(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    notice_number: Optional[str] = None
    issuing_agency: Optional[str] = None
    demand_agency: Optional[str] = None
    budget_estimate: float
    deadline: Optional[datetime] = None
    status: str


class DashboardMetric(BaseModel):
    key: str
    label: str
    value: Union[int, float, str, None] = None
    unit: str = "count"
    status: Literal["healthy", "watch", "critical", "info"] = "info"
    detail: str


class DashboardWorkItem(BaseModel):
    key: str
    item_type: Literal["opportunity_due", "bid_pending_result", "result_review"]
    severity: Literal["info", "watch", "critical"]
    title: str
    subtitle: str
    project_id: Optional[int] = None
    due_at: Optional[datetime] = None
    status: str
    href: str


class DashboardOpportunityItem(BaseModel):
    source: Literal["decision", "paper_bid"] = "decision"
    source_label: str = "입찰 판단"
    decision_record_id: Optional[int] = None
    paper_bid_id: Optional[int] = None
    project: DashboardProjectBrief
    action: PaperBidAction
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    recommended_amount: float
    probability_score: float = Field(
        ge=0.0, le=1.0, description=_PROBABILITY_SCORE_DESCRIPTION
    )
    matched_score: float = Field(ge=0.0, le=1.0)
    priority_score: float = Field(ge=0.0, le=1.0)
    urgency_score: float = Field(ge=0.0, le=1.0)
    deadline_hours_remaining: Optional[int] = None
    reasoning: str = ""
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    updated_at: datetime
    detail_href: str


class DashboardBidItem(BaseModel):
    bid_id: int
    project: DashboardProjectBrief
    decision_record_id: Optional[int] = None
    decision_status: Optional[
        Literal["planned", "reviewing", "submitted", "skipped"]
    ] = None
    bid_amount: float
    recommended_amount: Optional[float] = None
    proposed_timeline: int
    status: Literal["submitted", "reviewed", "accepted", "rejected"]
    score: Optional[float] = None
    submitted_at: datetime
    updated_at: datetime
    detail_href: str


class DashboardResultItem(BaseModel):
    tender_result_id: int
    project: DashboardProjectBrief
    winning_company: Optional[str] = None
    winning_amount: float
    winning_rate: float
    result_status: str
    award_outcome: Literal["won", "lost", "unknown"] = "unknown"
    announced_at: Optional[datetime] = None
    latest_prediction_id: Optional[int] = None
    predicted_price: Optional[float] = None
    prediction_delta_amount: Optional[float] = None
    prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    latest_decision_record_id: Optional[int] = None
    recommended_amount: Optional[float] = None
    recommendation_delta_amount: Optional[float] = None
    recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    detail_href: str


class DashboardListMeta(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    generated_at: datetime
    returned_count: int = Field(ge=0)
    limit: int = Field(ge=1)


class DashboardOpportunityListResponse(DashboardListMeta):
    items: List[DashboardOpportunityItem] = Field(default_factory=list)


class DashboardBidListResponse(DashboardListMeta):
    items: List[DashboardBidItem] = Field(default_factory=list)


class DashboardResultListResponse(DashboardListMeta):
    items: List[DashboardResultItem] = Field(default_factory=list)


class DashboardSectionSummary(BaseModel):
    key: Literal["opportunities", "bids", "results"]
    label: str
    count: int = Field(ge=0)
    status: Literal["healthy", "watch", "critical", "info"] = "info"
    href: str


class DashboardSummaryResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    generated_at: datetime
    today: date
    operational_status: DashboardMetric
    metrics: List[DashboardMetric] = Field(default_factory=list)
    work_items: List[DashboardWorkItem] = Field(default_factory=list)
    sections: List[DashboardSectionSummary] = Field(default_factory=list)
    recent_opportunities: List[DashboardOpportunityItem] = Field(default_factory=list)
    recent_bids: List[DashboardBidItem] = Field(default_factory=list)
    recent_results: List[DashboardResultItem] = Field(default_factory=list)
    realtime_href: str = "/api/v1/realtime/events"
