"""Operator profile, account, notification-channel and dashboard schemas."""

from datetime import datetime
from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field
from app.core.constants import DecisionStatus, PaperBidAction
from app.schemas._shared import EmailStr, _PROBABILITY_SCORE_DESCRIPTION


class OperatorProfileUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    company: Optional[str] = None
    business_type: Optional[str] = None
    license_codes: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None
    # cohort 정체성(협회 가입/기술부문). license_codes/region_codes 와 동일한 다중값
    # list[str] 캡처. None 이면 불변(부분 업데이트), 넘어오면 갱신.
    association_memberships: Optional[List[str]] = None
    tech_fields: Optional[List[str]] = None
    annual_revenue: Optional[float] = Field(default=None, ge=0.0)
    capacity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # 시공능력평가액(원). Construction-only manual annual input; 0 means
    # "not provided" and the matcher falls back to annual_revenue/capacity_score.
    construction_capacity_amount: Optional[float] = Field(default=None, ge=0.0)
    # 도급한도(원). Maximum simultaneously-held award amount. Persisted for
    # future capacity tracking; not yet used by the matcher.
    awarded_contract_limit: Optional[float] = Field(default=None, ge=0.0)
    total_awards: Optional[int] = Field(default=None, ge=0)


class OperatorProfileResponse(BaseModel):
    operator_id: int
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    company: Optional[str] = None
    is_active: bool
    created_at: datetime
    business_type: str
    license_codes: List[str] = Field(default_factory=list)
    region_codes: List[str] = Field(default_factory=list)
    association_memberships: List[str] = Field(default_factory=list)
    tech_fields: List[str] = Field(default_factory=list)
    annual_revenue: float
    capacity_score: float
    construction_capacity_amount: float = 0.0
    awarded_contract_limit: float = 0.0
    total_awards: int
    profile_configured: bool
    current_operator_id: int
    current_operator_username: str


class OperatorOverviewResponse(BaseModel):
    operator_id: int
    project_count: int
    bid_count: int
    active_bid_count: int
    prediction_count: int
    unread_notification_count: int
    recent_event_count: int
    profile_configured: bool
    current_operator_id: int
    current_operator_username: str


class OperatorAccountItem(BaseModel):
    """Compact operator-account row used by the company-switcher dropdown."""

    operator_id: int
    username: str
    full_name: Optional[str] = None
    company: Optional[str] = None
    business_type: Optional[str] = None
    is_canonical: bool = False
    is_synthetic: bool = False
    is_active: bool = True
    profile_configured: bool = False


class OperatorAccountListResponse(BaseModel):
    """Operator accounts visible to the current bearer-token owner."""

    current_operator_id: int
    current_operator_username: str
    is_privileged: bool = False
    operator_count: int = Field(ge=0)
    operators: List[OperatorAccountItem] = Field(default_factory=list)


class OperatorNotificationChannelItem(BaseModel):
    """Masked notification route metadata for one operator channel."""

    channel_id: Optional[int] = None
    operator_id: int
    channel_type: str
    route_key: str
    target_label: Optional[str] = None
    is_active: bool = False
    dry_run_only: bool = True
    source: str = "operator_notification_channels"
    verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OperatorNotificationChannelListResponse(BaseModel):
    """Notification channels visible for the resolved operator context."""

    operator_id: int
    current_operator_id: int
    current_operator_username: str
    channel_count: int = Field(ge=0)
    channels: List[OperatorNotificationChannelItem] = Field(default_factory=list)


class OperatorDashboardCard(BaseModel):
    key: str
    label: str
    value: Union[int, float, str, None] = None
    unit: str = "count"
    status: Literal["healthy", "watch", "critical", "info"] = "info"
    detail: str
    href: Optional[str] = None


class OperatorDashboardDecisionItem(BaseModel):
    decision_record_id: int
    project_id: int
    project_title: str
    action: PaperBidAction
    decision_status: DecisionStatus
    priority_score: float = Field(ge=0.0, le=1.0)
    probability_score: float = Field(
        ge=0.0, le=1.0, description=_PROBABILITY_SCORE_DESCRIPTION
    )
    recommended_amount: float
    updated_at: datetime
    detail_href: str
    analysis_href: str


class OperatorDashboardRunItem(BaseModel):
    monitor_run_id: int
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    trigger_source: str
    persisted_candidate_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)
    created_at: datetime
    completed_at: Optional[datetime] = None
    detail_href: str


class OperatorDashboardFeedbackSummary(BaseModel):
    result_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    recommendation_sample_count: int = Field(ge=0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    recommendation_better_than_prediction_count: int = Field(ge=0)
    href: str


class OperatorDashboardResponse(BaseModel):
    operator_id: int
    generated_at: datetime
    period_days: int
    overview: OperatorOverviewResponse
    cards: List[OperatorDashboardCard] = Field(default_factory=list)
    recent_decisions: List[OperatorDashboardDecisionItem] = Field(default_factory=list)
    recent_monitor_runs: List[OperatorDashboardRunItem] = Field(default_factory=list)
    feedback_summary: OperatorDashboardFeedbackSummary
    action_hrefs: Dict[str, str] = Field(default_factory=dict)
    current_operator_id: int
    current_operator_username: str
