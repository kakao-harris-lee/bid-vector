"""Pydantic schemas for request/response"""

import json
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union

try:  # pragma: no cover - optional dependency fallback
    import email_validator  # noqa: F401
    from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    EmailStr = str


# 표시 정직화: 이 점수는 "가격 적합도(추정)" 신호이지 실제 낙찰 확률 P(낙찰)이 아니다.
# 적격성 게이트(would_have_won_final)는 별도로 판정된다. 학습/보정 시에는
# summary.probability_calibration 으로 정산 결과에 보정될 수 있으나 라벨은 별개다.
_PROBABILITY_SCORE_DESCRIPTION = (
    "가격 적합도(추정) — P(낙찰) 아님(would_have_won_final 게이트 별도)"
)


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

    def _as_str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str) and item]

    return _as_str_list(parsed.get("strengths")), _as_str_list(parsed.get("risk_flags"))


# User Schemas
class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: str
    company: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    company: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OperatorLoginRequest(BaseModel):
    username: str
    password: str


class OperatorPasswordResetRequest(BaseModel):
    username: Optional[str] = None
    reset_token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class OperatorProfileUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    company: Optional[str] = None
    business_type: Optional[str] = None
    license_codes: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None
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
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
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
    action: Literal["bid_now", "review", "skip"]
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


class OperatorStrategyUpdate(BaseModel):
    focus_categories: Optional[List[str]] = None
    focus_regions: Optional[List[str]] = None
    exclude_regions: Optional[List[str]] = None
    required_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    min_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    max_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    minimum_match_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    minimum_probability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    auto_workload_penalty_multiplier: Optional[float] = Field(
        default=None, ge=0.0, le=2.0
    )
    category_priority_overrides: Optional[Dict[str, float]] = None
    notify_only_high_priority: Optional[bool] = None
    max_recommended_candidates: Optional[int] = Field(default=None, ge=1, le=100)


class OperatorStrategyResponse(BaseModel):
    operator_id: int
    focus_categories: List[str] = Field(default_factory=list)
    focus_regions: List[str] = Field(default_factory=list)
    exclude_regions: List[str] = Field(default_factory=list)
    required_keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)
    min_budget_estimate: float
    max_budget_estimate: float
    minimum_match_score: float = Field(ge=0.0, le=1.0)
    minimum_probability_score: float = Field(ge=0.0, le=1.0)
    bid_now_threshold: float = Field(ge=0.0, le=1.0)
    review_threshold: float = Field(ge=0.0, le=1.0)
    auto_workload_penalty_multiplier: float = Field(ge=0.0, le=2.0)
    category_priority_overrides: Dict[str, float] = Field(default_factory=dict)
    notify_only_high_priority: bool
    max_recommended_candidates: int = Field(ge=1, le=100)
    strategy_configured: bool
    current_operator_id: int
    current_operator_username: str


class OperatorStrategyCandidateItem(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    budget_estimate: float
    deadline: Optional[datetime] = None
    matched_score: float = Field(ge=0.0, le=1.0)
    probability_score: float = Field(
        ge=0.0, le=1.0, description=_PROBABILITY_SCORE_DESCRIPTION
    )
    priority_score: float = Field(ge=0.0, le=1.0)
    action: Literal["bid_now", "review", "skip"]
    recommended_amount: float
    analysis_summary: str
    strategy_reasons: List[str] = Field(default_factory=list)


class OperatorStrategyCandidatesResponse(BaseModel):
    operator_id: int
    evaluated_project_count: int = Field(ge=0)
    returned_candidate_count: int = Field(ge=0)
    high_priority_only: bool
    candidates: List[OperatorStrategyCandidateItem] = Field(default_factory=list)
    current_operator_id: int
    current_operator_username: str


class OperatorStrategyMonitorRequest(BaseModel):
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    high_priority_only: Optional[bool] = None
    max_active_bids: int = Field(default=3, ge=1)
    current_workload_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    same_category_only: bool = True
    similar_limit: int = Field(default=3, ge=1, le=10)
    min_similarity: float = Field(default=0.15, ge=0.0, le=1.0)


class OperatorStrategyMonitorResultItem(BaseModel):
    project_id: int
    title: str
    decision_record_id: int
    notification_id: Optional[int] = None
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    probability_score: float = Field(
        ge=0.0, le=1.0, description=_PROBABILITY_SCORE_DESCRIPTION
    )
    matched_score: float = Field(ge=0.0, le=1.0)
    recommended_amount: float
    analysis_summary: str
    is_new_candidate: bool = False
    notification_created: bool = False
    strategy_reasons: List[str] = Field(default_factory=list)


class OperatorStrategyMonitorResponse(BaseModel):
    monitor_run_id: Optional[int] = None
    task_id: Optional[str] = None
    trigger_source: Optional[str] = None
    previous_run_id: Optional[int] = None
    operator_id: int
    evaluated_project_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    persisted_candidate_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)
    new_candidate_count: int = Field(ge=0)
    continuing_candidate_count: int = Field(ge=0)
    dropped_candidate_count: int = Field(ge=0)
    high_priority_only: bool
    limit_applied: int = Field(ge=1, le=100)
    new_candidate_project_ids: List[int] = Field(default_factory=list)
    continuing_candidate_project_ids: List[int] = Field(default_factory=list)
    dropped_candidate_project_ids: List[int] = Field(default_factory=list)
    results: List[OperatorStrategyMonitorResultItem] = Field(default_factory=list)


class OperatorStrategyMonitorTaskResponse(BaseModel):
    task_id: str
    monitor_run_id: int
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class OperatorStrategyMonitorTaskStatusResponse(BaseModel):
    task_id: str
    monitor_run_id: Optional[int] = None
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[OperatorStrategyMonitorResponse] = None


class OperatorStrategyRunResponse(BaseModel):
    id: int
    operator_id: int
    task_id: Optional[str] = None
    trigger_source: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    high_priority_only: bool
    limit_applied: int = Field(ge=1, le=100)
    evaluated_project_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    persisted_candidate_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class OperatorStrategyRunListResponse(BaseModel):
    operator_id: int
    result_count: int = Field(ge=0)
    runs: List[OperatorStrategyRunResponse] = Field(default_factory=list)


class OperatorStrategyRunDetailResponse(BaseModel):
    id: int
    operator_id: int
    task_id: Optional[str] = None
    trigger_source: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    high_priority_only: bool
    limit_applied: int = Field(ge=1, le=100)
    evaluated_project_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    persisted_candidate_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    previous_run_id: Optional[int] = None
    new_candidate_count: int = Field(ge=0)
    continuing_candidate_count: int = Field(ge=0)
    dropped_candidate_count: int = Field(ge=0)
    request_payload: dict = Field(default_factory=dict)
    result: Optional[OperatorStrategyMonitorResponse] = None
    new_candidates: List[OperatorStrategyMonitorResultItem] = Field(
        default_factory=list
    )
    continuing_candidates: List[OperatorStrategyMonitorResultItem] = Field(
        default_factory=list
    )
    dropped_candidates: List[OperatorStrategyMonitorResultItem] = Field(
        default_factory=list
    )


# Authentication Schemas
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    operator_id: Optional[int] = None
    username: Optional[str] = None


# Project Schemas
class ProjectBase(BaseModel):
    title: str
    description: str
    requirements: str
    budget_estimate: float
    category: str
    notice_number: Optional[str] = None
    source_url: Optional[str] = None
    issuing_agency: Optional[str] = None
    demand_agency: Optional[str] = None


class ProjectCreate(ProjectBase):
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    deadline: Optional[datetime] = None


class ProjectResponse(ProjectBase):
    id: int
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SimilarProjectItem(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    status: str
    budget_estimate: float
    deadline: Optional[datetime] = None
    created_at: datetime
    similarity_score: float = Field(ge=0.0, le=1.0)
    embedding_model: Optional[str] = None


class ProjectSimilaritySearchResponse(BaseModel):
    target_project_id: int
    target_project_title: str
    target_embedding_model: Optional[str] = None
    search_mode: Literal["postgres_vector", "python_fallback"]
    same_category_only: bool
    min_similarity: float = Field(ge=0.0, le=1.0)
    result_count: int
    results: List[SimilarProjectItem] = Field(default_factory=list)


class ProjectEmbeddingRefreshResponse(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    embedding_model: Optional[str] = None
    semantic_text_length: int = Field(ge=0)
    embedding_dimensions: int = Field(ge=0)
    embedding_updated_at: Optional[datetime] = None
    vector_storage_enabled: bool
    vector_persisted: bool


class ProjectEmbeddingBatchRefreshResponse(BaseModel):
    processed_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    category: Optional[str] = None
    project_status: Optional[str] = None
    force: bool
    vector_storage_enabled: bool
    project_ids: List[int] = Field(default_factory=list)
    results: List[ProjectEmbeddingRefreshResponse] = Field(default_factory=list)


class ProjectEmbeddingBatchRefreshTaskResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class ProjectEmbeddingBatchRefreshTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[ProjectEmbeddingBatchRefreshResponse] = None


class MLTaskResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class MLTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[dict] = None


class PricePredictionTrainingRequest(BaseModel):
    release_tag: Optional[str] = None
    category: Optional[str] = None
    agency_name: Optional[str] = None
    limit: int = Field(default=500, ge=1, le=5000)
    notes: Optional[str] = None
    create_manifest: bool = True
    publish_remote: bool = True


# Bid Schemas
class BidBase(BaseModel):
    bid_amount: float
    proposed_timeline: int


class BidCreate(BidBase):
    project_id: int
    description: str


class BidUpdate(BaseModel):
    bid_amount: Optional[float] = None
    description: Optional[str] = None


class BidResponse(BidBase):
    id: int
    project_id: int
    operator_id: int
    user_id: int
    status: str
    decision_record_id: Optional[int] = None
    decision_status: Optional[
        Literal["planned", "reviewing", "submitted", "skipped"]
    ] = None
    score: Optional[float] = None
    created_at: datetime


# Price Prediction Schemas
class PricePredictionRequest(BaseModel):
    project_id: int
    budget_estimate: float
    category: str
    description: str
    agency_name: Optional[str] = None


class PricePredictionScenario(BaseModel):
    label: Literal["conservative", "base", "aggressive"]
    bid_rate: float
    predicted_price: float
    confidence_weight: float = Field(ge=0.0, le=1.0)


class PricePredictionReservePattern(BaseModel):
    sample_count: int = Field(ge=0)
    average_reserve_span_rate: float = Field(ge=0.0)
    estimated_price_sample_count: int = Field(default=0, ge=0)
    average_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    median_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    median_bid_to_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    average_selected_number: float = Field(ge=0.0)
    frequent_selected_numbers: List[int] = Field(default_factory=list)


class PricePredictionFeedbackCalibration(BaseModel):
    sample_count: int = Field(ge=0)
    agency_match_sample_count: int = Field(ge=0)
    average_signed_error_rate: float
    average_absolute_error_rate: float = Field(ge=0.0)
    applied_adjustment_rate: float


class PricePredictionResponse(BaseModel):
    predicted_price: float
    price_range_min: float
    price_range_max: float
    confidence_score: float
    model_version: str
    predictor_name: str = "historical_statistical"
    predictor_family: str = "statistical"
    fallback_reason: Optional[str] = None
    selector_name: str = "configured_preference"
    selection_reason: Optional[str] = None
    backtest_sample_count: int = Field(default=0, ge=0)
    backtest_average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    backtest_report: Optional[dict] = None
    training_window_size: int = Field(default=0, ge=0)
    pricing_mode: Literal["historical_blend", "heuristic"] = "heuristic"
    historical_sample_size: int = Field(default=0, ge=0)
    agency_match_sample_size: int = Field(default=0, ge=0)
    predicted_bid_rate: float = 0.0
    competitive_target_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    procurement_rate_band: Optional[str] = None
    bid_rate_candidates: List[PricePredictionScenario] = Field(default_factory=list)
    reserve_price_context: Optional[PricePredictionReservePattern] = None
    feedback_calibration: Optional[PricePredictionFeedbackCalibration] = None
    guardrail_applied: bool = False
    guardrail_reason: Optional[str] = None
    floor_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    floor_price: Optional[float] = Field(default=None, ge=0.0)
    ceiling_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    ceiling_price: Optional[float] = Field(default=None, ge=0.0)
    explanation: str = ""


# Bid Recommendation Schemas
class BidRecommendationRequest(BaseModel):
    project_id: int
    user_historical_data: Optional[dict] = None


class BidRecommendationResponse(BaseModel):
    recommended_bid: float
    confidence_score: float
    reasoning: str
    market_analysis: Optional[dict] = None


# Document Analysis Schemas
class DocumentAnalysisRequest(BaseModel):
    project_id: int
    document_content: str
    document_type: str


class DocumentAnalysisResponse(BaseModel):
    key_requirements: List[str]
    complexity_score: float
    estimated_effort: float
    risks: List[str]


# Notification Schemas
class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Analytics Schemas
class AnalyticsEventRequest(BaseModel):
    event_type: str
    event_data: dict


class AnalyticsSummaryResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_projects: int
    total_events: int
    mode: Literal["single_operator"] = "single_operator"


class OperatorStatsResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_events: int
    bids_count: int
    requested_user_id: Optional[int] = None
    mode: Literal["single_operator"] = "single_operator"


class DecisionInsightsRecentItem(BaseModel):
    decision_record_id: int
    project_id: int
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    expected_margin_score: float = Field(ge=0.0, le=1.0)
    execution_complexity_score: float = Field(ge=0.0, le=1.0)
    competitiveness_score: float = Field(ge=0.0, le=1.0)
    budget_capture_score: float = Field(ge=0.0, le=1.0)
    updated_at: datetime


class DecisionInsightsResponse(BaseModel):
    operator_id: int
    period_days: int
    result_count: int = Field(ge=0)
    high_priority_count: int = Field(ge=0)
    bid_now_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    skip_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    auto_workload_count: int = Field(ge=0)
    provided_workload_count: int = Field(ge=0)
    average_priority_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_expected_margin_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_execution_complexity_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    average_competitiveness_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_budget_capture_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    action_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_decisions: List[DecisionInsightsRecentItem] = Field(default_factory=list)


class DecisionFunnelRecentSubmissionItem(BaseModel):
    decision_record_id: int
    project_id: int
    project_title: str
    initial_action: Literal["bid_now", "review", "skip"]
    initial_decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    current_action: Literal["bid_now", "review", "skip"]
    current_decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    recommended_amount: float
    first_decided_at: Optional[datetime] = None
    submitted_at: datetime
    hours_to_submit: Optional[float] = Field(default=None, ge=0.0)
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class DecisionFunnelTrendItem(BaseModel):
    bucket_start: date
    bucket_end: date
    decision_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    entry_bid_now_count: int = Field(ge=0)
    entry_review_count: int = Field(ge=0)
    entry_skip_count: int = Field(ge=0)
    submitted_after_bid_now_count: int = Field(ge=0)
    submitted_after_review_count: int = Field(ge=0)
    submitted_after_skip_count: int = Field(ge=0)
    submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_priority_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_expected_margin_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)


class DecisionFunnelBreakdownItem(BaseModel):
    segment: str
    decision_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    entry_bid_now_count: int = Field(ge=0)
    entry_review_count: int = Field(ge=0)
    entry_skip_count: int = Field(ge=0)
    submitted_after_bid_now_count: int = Field(ge=0)
    submitted_after_review_count: int = Field(ge=0)
    submitted_after_skip_count: int = Field(ge=0)
    submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_priority_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_expected_margin_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)


class DecisionFunnelPeriodSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    decision_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    entry_bid_now_count: int = Field(ge=0)
    entry_review_count: int = Field(ge=0)
    entry_skip_count: int = Field(ge=0)
    direct_submitted_count: int = Field(ge=0)
    submitted_after_bid_now_count: int = Field(ge=0)
    submitted_after_review_count: int = Field(ge=0)
    submitted_after_skip_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workflow_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)


class DecisionFunnelComparisonSummary(BaseModel):
    current_period_start: datetime
    current_period_end: datetime
    previous_period_start: datetime
    previous_period_end: datetime
    decision_count_delta: int
    project_count_delta: int
    submitted_count_delta: int
    active_pending_count_delta: int
    skipped_count_delta: int
    overall_submission_rate_delta: Optional[float] = None
    workflow_submission_rate_delta: Optional[float] = None
    bid_now_submission_rate_delta: Optional[float] = None
    review_submission_rate_delta: Optional[float] = None
    average_hours_to_submit_delta: Optional[float] = None


class DecisionRecommendationExperiment(BaseModel):
    experiment_key: str
    recommendation_key: str
    priority_rank: int = Field(ge=1, le=20)
    title: str
    hypothesis: str
    suggested_change: str
    target_metric: str
    expected_direction: Literal["increase", "decrease", "stabilize"]
    success_criteria: str
    guardrail_metric: str
    minimum_decision_sample: int = Field(ge=1)
    duration_days: int = Field(ge=1, le=30)
    rollback_trigger: str
    parameter_recommendation: dict = Field(default_factory=dict)


class DecisionRecommendationHistoryAdjustment(BaseModel):
    status: Literal["neutral", "promoted", "deprioritized"]
    priority_delta: float
    reason: str
    recent_run_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    rollback_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    applied_count: int = Field(ge=0)


class DecisionRecommendationItem(BaseModel):
    key: str
    severity: Literal["info", "watch", "action"]
    title: str
    summary: str
    suggested_adjustment: Optional[str] = None
    supporting_metrics: dict = Field(default_factory=dict)
    priority_score: float = Field(default=0.0, ge=0.0)
    history_adjustment: DecisionRecommendationHistoryAdjustment
    parameter_recommendation: dict = Field(default_factory=dict)
    experiment_plan: Optional[DecisionRecommendationExperiment] = None


class DecisionRecommendationResponse(BaseModel):
    operator_id: int
    period_days: int
    decision_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workflow_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    recommendation_count: int = Field(ge=0)
    recommendation_limit_applied: int = Field(default=5, ge=1, le=20)
    experiment_count: int = Field(ge=0)
    headline: str
    comparison: DecisionFunnelComparisonSummary
    experiment_history: dict = Field(default_factory=dict)
    recommended_next_experiment: Optional[DecisionRecommendationExperiment] = None
    experiments: List[DecisionRecommendationExperiment] = Field(default_factory=list)
    recommendations: List[DecisionRecommendationItem] = Field(default_factory=list)


class DecisionExperimentMetricSnapshot(BaseModel):
    window_start: datetime
    window_end: datetime
    decision_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workflow_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    auto_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    provided_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    best_category: Optional[str] = None
    best_category_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    worst_category: Optional[str] = None
    worst_category_submission_rate: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )


class DecisionExperimentEvaluation(BaseModel):
    evaluated_at: datetime
    sample_size: int = Field(ge=0)
    minimum_sample_reached: bool
    target_metric: str
    baseline_target_value: Optional[float] = None
    current_target_value: Optional[float] = None
    target_delta: Optional[float] = None
    guardrail_metric: str
    baseline_guardrail_value: Optional[float] = None
    current_guardrail_value: Optional[float] = None
    guardrail_delta: Optional[float] = None
    outcome: Literal[
        "insufficient_data", "watch", "success", "rollback", "inconclusive"
    ]
    recommended_action: Literal["collect_more_data", "continue", "complete", "rollback"]
    summary: str
    current_summary: DecisionExperimentMetricSnapshot


class DecisionExperimentRunCreateRequest(DecisionRecommendationExperiment):
    baseline_days: int = Field(default=14, ge=1, le=90)
    started_at: Optional[datetime] = None
    notes: Optional[str] = None


class DecisionExperimentRunUpdateRequest(BaseModel):
    status: Optional[Literal["planned", "running", "completed", "rolled_back"]] = None
    outcome: Optional[
        Literal["insufficient_data", "watch", "success", "rollback", "inconclusive"]
    ] = None
    replace_notes: Optional[str] = None
    append_note: Optional[str] = None
    ended_at: Optional[datetime] = None


class DecisionStrategyThresholdSnapshot(BaseModel):
    bid_now_threshold: float = Field(ge=0.0, le=1.0)
    review_threshold: float = Field(ge=0.0, le=1.0)


class DecisionThresholdAdjustmentItem(BaseModel):
    parameter: Literal["bid_now_threshold", "review_threshold"]
    label: str
    direction: Literal["increase", "decrease"]
    previous_value: float = Field(ge=0.0, le=1.0)
    suggested_value: float = Field(ge=0.0, le=1.0)
    delta: float
    rationale: str


class DecisionExperimentThresholdApplyRequest(BaseModel):
    dry_run: bool = False
    force: bool = False
    append_note: Optional[str] = None


class DecisionExperimentThresholdApplyResponse(BaseModel):
    operator_id: int
    run_id: int
    experiment_key: str
    recommendation_key: str
    applied: bool
    dry_run: bool
    latest_outcome: Optional[
        Literal["insufficient_data", "watch", "success", "rollback", "inconclusive"]
    ] = None
    threshold_updates: List[DecisionThresholdAdjustmentItem] = Field(
        default_factory=list
    )
    strategy_thresholds: DecisionStrategyThresholdSnapshot
    detail: str


class DecisionStrategyTuningSnapshot(BaseModel):
    auto_workload_penalty_multiplier: float = Field(ge=0.0, le=2.0)
    category_priority_overrides: Dict[str, float] = Field(default_factory=dict)


class DecisionStrategyAdjustmentItem(BaseModel):
    parameter: Literal[
        "auto_workload_penalty_multiplier", "category_priority_overrides"
    ]
    label: str
    direction: Literal["increase", "decrease", "replace"]
    previous_value: Union[float, Dict[str, float]]
    suggested_value: Union[float, Dict[str, float]]
    delta: Optional[Union[float, Dict[str, float]]] = None
    rationale: str


class DecisionExperimentStrategyApplyRequest(BaseModel):
    dry_run: bool = False
    force: bool = False
    append_note: Optional[str] = None


class DecisionExperimentStrategyApplyResponse(BaseModel):
    operator_id: int
    run_id: int
    experiment_key: str
    recommendation_key: str
    applied: bool
    dry_run: bool
    latest_outcome: Optional[
        Literal["insufficient_data", "watch", "success", "rollback", "inconclusive"]
    ] = None
    strategy_updates: List[DecisionStrategyAdjustmentItem] = Field(default_factory=list)
    strategy_tuning: DecisionStrategyTuningSnapshot
    detail: str


class DecisionExperimentApplicationHistoryItem(BaseModel):
    apply_type: Literal["thresholds", "strategy"]
    note: str


class DecisionExperimentActionItem(BaseModel):
    action: Literal[
        "evaluate", "mark_success", "rollback", "apply_thresholds", "apply_strategy"
    ]
    label: str
    method: Literal["POST", "PATCH"]
    path: str
    enabled: bool
    reason: str
    payload: dict = Field(default_factory=dict)
    dry_run_supported: bool = False
    force_supported: bool = False


class DecisionExperimentRunResponse(BaseModel):
    id: int
    operator_id: int
    experiment_key: str
    recommendation_key: str
    status: Literal["planned", "running", "completed", "rolled_back", "failed"]
    outcome: Optional[
        Literal["insufficient_data", "watch", "success", "rollback", "inconclusive"]
    ] = None
    priority_rank: int = Field(ge=1, le=20)
    title: str
    hypothesis: str
    suggested_change: str
    target_metric: str
    expected_direction: Literal["increase", "decrease", "stabilize"]
    success_criteria: str
    guardrail_metric: str
    minimum_decision_sample: int = Field(ge=1)
    duration_days: int = Field(ge=1, le=30)
    baseline_days: int = Field(ge=1, le=90)
    rollback_trigger: str
    notes: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    last_evaluated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    latest_evaluation: Optional[DecisionExperimentEvaluation] = None
    supported_apply_types: List[Literal["thresholds", "strategy"]] = Field(
        default_factory=list
    )
    applied_apply_types: List[Literal["thresholds", "strategy"]] = Field(
        default_factory=list
    )
    application_status: Literal[
        "not_supported", "not_ready", "ready", "partially_applied", "applied", "blocked"
    ]
    application_detail: str
    application_history: List[DecisionExperimentApplicationHistoryItem] = Field(
        default_factory=list
    )
    review_bucket: Literal[
        "ready_to_apply",
        "blocked",
        "failed",
        "needs_evaluation",
        "collecting_data",
        "partially_applied",
        "scheduled",
        "applied",
        "unsupported",
    ]
    review_priority: int = Field(ge=0)
    review_reason: str
    next_actions: List[DecisionExperimentActionItem] = Field(default_factory=list)


class DecisionExperimentRunListResponse(BaseModel):
    operator_id: int
    result_count: int = Field(ge=0)
    total_match_count: int = Field(default=0, ge=0)
    sort: Literal[
        "needs_attention",
        "created_desc",
        "created_asc",
        "priority",
        "last_evaluated_desc",
        "application",
    ] = "needs_attention"
    active_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    rolled_back_count: int = Field(ge=0)
    failed_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    pending_count: int = Field(default=0, ge=0)
    inconclusive_count: int = Field(default=0, ge=0)
    rollback_count: int = Field(default=0, ge=0)
    applicable_count: int = Field(default=0, ge=0)
    ready_to_apply_count: int = Field(default=0, ge=0)
    applied_count: int = Field(default=0, ge=0)
    partially_applied_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    not_ready_count: int = Field(default=0, ge=0)
    not_supported_count: int = Field(default=0, ge=0)
    application_status_counts: dict[str, int] = Field(default_factory=dict)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    review_bucket_counts: dict[str, int] = Field(default_factory=dict)
    runs: List[DecisionExperimentRunResponse] = Field(default_factory=list)


class DecisionExperimentRunDetailResponse(BaseModel):
    run: DecisionExperimentRunResponse
    baseline_summary: DecisionExperimentMetricSnapshot


class DecisionFunnelResponse(BaseModel):
    operator_id: int
    period_days: int
    decision_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    active_pending_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    entry_bid_now_count: int = Field(ge=0)
    entry_review_count: int = Field(ge=0)
    entry_skip_count: int = Field(ge=0)
    direct_submitted_count: int = Field(ge=0)
    submitted_after_bid_now_count: int = Field(ge=0)
    submitted_after_review_count: int = Field(ge=0)
    submitted_after_skip_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workflow_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)
    current_period_start: datetime
    current_period_end: datetime
    previous_period: DecisionFunnelPeriodSummary
    comparison: DecisionFunnelComparisonSummary
    trend_bucket_days: int = Field(default=7, ge=1, le=30)
    breakdown_limit_applied: int = Field(default=5, ge=1, le=20)
    trend: List[DecisionFunnelTrendItem] = Field(default_factory=list)
    category_breakdown: List[DecisionFunnelBreakdownItem] = Field(default_factory=list)
    workload_source_breakdown: List[DecisionFunnelBreakdownItem] = Field(
        default_factory=list
    )
    agency_breakdown: List[DecisionFunnelBreakdownItem] = Field(default_factory=list)
    recent_submissions: List[DecisionFunnelRecentSubmissionItem] = Field(
        default_factory=list
    )


class PredictionFeedbackItem(BaseModel):
    project_id: int
    project_title: str
    tender_result_id: int
    result_status: str
    announced_at: Optional[datetime] = None
    winning_amount: float
    winning_rate: float
    latest_prediction_id: Optional[int] = None
    predicted_price: Optional[float] = None
    prediction_delta_amount: Optional[float] = None
    prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    latest_decision_record_id: Optional[int] = None
    recommended_amount: Optional[float] = None
    recommendation_delta_amount: Optional[float] = None
    recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    recommendation_improved_vs_prediction: Optional[bool] = None


class PredictionFeedbackResponse(BaseModel):
    operator_id: int
    period_days: int
    result_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    recommendation_sample_count: int = Field(ge=0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    prediction_within_1_percent_count: int = Field(ge=0)
    prediction_within_3_percent_count: int = Field(ge=0)
    recommendation_within_1_percent_count: int = Field(ge=0)
    recommendation_within_3_percent_count: int = Field(ge=0)
    recommendation_better_than_prediction_count: int = Field(ge=0)
    items: List[PredictionFeedbackItem] = Field(default_factory=list)


class OperationsKpiManualOverride(BaseModel):
    """Manual-override KPI (d): how often the operator changed a recommendation."""

    decision_count: int = Field(ge=0)
    modified_count: int = Field(ge=0)
    modification_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OperationsKpiConversion(BaseModel):
    """Conversion KPI (e): recommendation-to-submission rates reused from the funnel."""

    decision_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)


class OperationsKpiPredictionAccuracy(BaseModel):
    """Prediction-accuracy KPI (f): error rates reused from prediction feedback."""

    result_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    recommendation_sample_count: int = Field(ge=0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    prediction_within_1_percent_count: int = Field(ge=0)
    prediction_within_3_percent_count: int = Field(ge=0)
    recommendation_within_1_percent_count: int = Field(ge=0)
    recommendation_within_3_percent_count: int = Field(ge=0)


class OperationsKpiMissedOpportunityItem(BaseModel):
    """One recommended-but-unactioned tender whose deadline has already passed."""

    decision_record_id: int
    project_id: int
    project_title: str
    deadline: Optional[datetime] = None
    initial_action: str
    decision_status: str
    priority_score: float


class OperationsKpiMissedOpportunities(BaseModel):
    """Missed-opportunity KPI (b): bid_now/review recommendations left past deadline."""

    missed_count: int = Field(ge=0)
    items: List[OperationsKpiMissedOpportunityItem] = Field(default_factory=list)


class OperationsKpiReviewTime(BaseModel):
    """Review-time KPI (a): minutes between first viewing a tender and deciding."""

    average_review_minutes: Optional[float] = Field(default=None, ge=0.0)
    sample_count: int = Field(ge=0)


class OperationsKpiRecommendationFeedback(BaseModel):
    """Recommendation-usefulness KPI (c): operator 👍/👎 votes on recommendations."""

    useful_count: int = Field(ge=0)
    not_useful_count: int = Field(ge=0)
    review_value_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    feedback_count: int = Field(ge=0)


class OperationsKpiSettlementCoverage(BaseModel):
    """Settlement-coverage KPI: how far paper-bid settlement has progressed.

    ``forward_*`` fields isolate the ``forward_paper`` run subset, which is the
    cohort the automated forward-settlement job is responsible for closing.
    Coverage rates are ``None`` when their denominator (paper-bid count) is zero.
    """

    total_paper_bids: int = Field(ge=0)
    settled_count: int = Field(ge=0)
    coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    forward_paper_bids: int = Field(ge=0)
    forward_settled_count: int = Field(ge=0)
    forward_coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OperationsKpiResponse(BaseModel):
    """Roadmap C-1 instrumentation: operating KPIs aggregated in one call."""

    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    manual_override: OperationsKpiManualOverride
    conversion: OperationsKpiConversion
    prediction_accuracy: OperationsKpiPredictionAccuracy
    missed_opportunities: OperationsKpiMissedOpportunities
    review_time: OperationsKpiReviewTime
    recommendation_feedback: OperationsKpiRecommendationFeedback
    settlement_coverage: OperationsKpiSettlementCoverage


class RecommendationFeedbackLabelBreakdown(BaseModel):
    """Useful/not_useful split for a single category or action bucket."""

    useful: int = Field(ge=0)
    not_useful: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RecommendationFeedbackLabelItem(BaseModel):
    """One operator-verdict label joined with its decision/project context."""

    decision_record_id: int
    project_id: int
    project_title: str
    project_category: Optional[str] = None
    project_business_type_code: Optional[str] = None
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    verdict: Literal["useful", "not_useful"]
    feedback_at: Optional[datetime] = None
    reasoning: str = ""
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class RecommendationFeedbackLabelsResponse(BaseModel):
    """Recommendation feedback labels exported for ML/QA review.

    Top-level counts mirror :class:`OperationsKpiRecommendationFeedback` so the
    KPI card and this label export stay numerically consistent. ``by_category``
    and ``by_action`` group verdicts by the joined project category and the
    decision's current action; ``items`` is the deduped (latest-per-decision)
    label rows, newest feedback first.
    """

    operator_id: int
    period_days: int
    label_count: int = Field(ge=0)
    useful_count: int = Field(ge=0)
    not_useful_count: int = Field(ge=0)
    review_value_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    by_category: Dict[str, RecommendationFeedbackLabelBreakdown] = Field(
        default_factory=dict
    )
    by_action: Dict[str, RecommendationFeedbackLabelBreakdown] = Field(
        default_factory=dict
    )
    items: List[RecommendationFeedbackLabelItem] = Field(default_factory=list)


class PredictionPredictorBreakdownItem(BaseModel):
    predictor_name: str
    predictor_family: str
    prediction_count: int = Field(ge=0)
    selection_rate: float = Field(ge=0.0, le=1.0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    within_1_percent_count: int = Field(ge=0)
    within_3_percent_count: int = Field(ge=0)
    average_confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_training_window_size: Optional[float] = Field(default=None, ge=0.0)
    average_predicted_bid_rate: Optional[float] = Field(default=None, ge=0.0)


class PredictionPricingModeBreakdownItem(BaseModel):
    pricing_mode: str
    prediction_count: int = Field(ge=0)
    selection_rate: float = Field(ge=0.0, le=1.0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)


class PredictionPerformanceTrendItem(BaseModel):
    bucket_start: datetime
    bucket_end: datetime
    prediction_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    backtest_sample_count: int = Field(ge=0)
    average_backtest_error_rate: Optional[float] = Field(default=None, ge=0.0)


class PredictionObservabilityResponse(BaseModel):
    operator_id: int
    period_days: int
    prediction_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    within_1_percent_count: int = Field(ge=0)
    within_3_percent_count: int = Field(ge=0)
    predictor_breakdown: List[PredictionPredictorBreakdownItem] = Field(
        default_factory=list
    )
    pricing_mode_breakdown: List[PredictionPricingModeBreakdownItem] = Field(
        default_factory=list
    )
    performance_trend: List[PredictionPerformanceTrendItem] = Field(
        default_factory=list
    )
    fallback_reason_breakdown: dict[str, int] = Field(default_factory=dict)
    guardrail_reason_breakdown: dict[str, int] = Field(default_factory=dict)


class OperationsDashboardCard(BaseModel):
    key: str
    label: str
    value: float
    unit: Literal["ratio", "count"]
    status: Literal["healthy", "watch", "critical", "info"]
    detail: str


class CrawlFailureItem(BaseModel):
    crawl_job_id: int
    source: str
    target_date: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class CrawlOperationsSummary(BaseModel):
    job_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    average_result_count: Optional[float] = Field(default=None, ge=0.0)
    total_result_count: int = Field(ge=0)
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    failure_reason_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_failures: List[CrawlFailureItem] = Field(default_factory=list)


class StrategyFailureItem(BaseModel):
    run_id: int
    trigger_source: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class StrategyOperationsSummary(BaseModel):
    run_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0.0, le=1.0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    evaluated_project_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    persisted_candidate_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)
    selection_rate: float = Field(ge=0.0, le=1.0)
    persistence_rate: float = Field(ge=0.0, le=1.0)
    notification_rate: float = Field(ge=0.0, le=1.0)
    average_selected_candidates: Optional[float] = Field(default=None, ge=0.0)
    last_completed_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    failure_reason_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_failures: List[StrategyFailureItem] = Field(default_factory=list)


class TaskBrokerHealth(BaseModel):
    url: str
    transport: str
    health_status: Literal["healthy", "watch", "critical", "info"]
    detail: str


class TaskResultBackendHealth(BaseModel):
    url: str
    transport: str
    health_status: Literal["healthy", "watch", "critical", "info"]
    detail: str


class TaskRuntimeHealth(BaseModel):
    eager_mode: bool
    inline_ml_tasks_allowed: bool
    worker_concurrency: int = Field(ge=0)
    worker_prefetch_multiplier: int = Field(ge=0)
    worker_max_tasks_per_child: int = Field(ge=0)
    task_time_limit_seconds: int = Field(ge=0)
    task_soft_time_limit_seconds: int = Field(ge=0)
    result_expires_seconds: int = Field(ge=0)
    task_track_started: bool
    worker_send_task_events: bool
    task_send_sent_event: bool
    broker_connection_retry_on_startup: bool
    broker_connection_max_retries: int = Field(ge=0)
    broker_publish_max_retries: int = Field(ge=0)
    health_status: Literal["healthy", "watch", "critical", "info"]
    detail: str


class TaskQueueDiagnostic(BaseModel):
    queue: str
    task_count: int = Field(ge=0)
    task_names: List[str] = Field(default_factory=list)


class TaskOperationsItem(BaseModel):
    source: str
    record_id: int
    task_id: Optional[str] = None
    task_name: str
    queue: str
    status: str
    detail: str
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    age_seconds: Optional[int] = Field(default=None, ge=0)


class TaskOperationsSummary(BaseModel):
    broker: TaskBrokerHealth
    result_backend: TaskResultBackendHealth
    runtime: TaskRuntimeHealth
    queues: List[TaskQueueDiagnostic] = Field(default_factory=list)
    tracked_task_count: int = Field(ge=0)
    queued_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    stale_task_threshold_seconds: int = Field(ge=0)
    stale_task_count: int = Field(ge=0)
    average_queue_wait_seconds: Optional[float] = Field(default=None, ge=0.0)
    average_runtime_seconds: Optional[float] = Field(default=None, ge=0.0)
    backlog_status: Literal["healthy", "watch", "critical", "info"]
    failure_status: Literal["healthy", "watch", "critical", "info"]
    risk_flags: List[str] = Field(default_factory=list)
    recent_delayed_tasks: List[TaskOperationsItem] = Field(default_factory=list)
    recent_failures: List[TaskOperationsItem] = Field(default_factory=list)
    recent_retries: List[TaskOperationsItem] = Field(default_factory=list)


class TelegramDeliveryFailureItem(BaseModel):
    event_id: int
    notification_id: Optional[int] = None
    source: str
    status: str
    detail: str
    timestamp: datetime


class NotificationOperationsSummary(BaseModel):
    notification_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)
    decision_notification_count: int = Field(ge=0)
    bid_submission_notification_count: int = Field(ge=0)
    telegram_configured: bool
    telegram_delivery_attempt_count: int = Field(ge=0)
    telegram_sent_count: int = Field(ge=0)
    telegram_failed_count: int = Field(ge=0)
    telegram_pending_configuration_count: int = Field(ge=0)
    telegram_skipped_count: int = Field(ge=0)
    telegram_success_rate: float = Field(ge=0.0, le=1.0)
    telegram_status: Literal["healthy", "watch", "critical", "info"]
    telegram_detail: str
    telegram_status_counts: dict[str, int] = Field(default_factory=dict)
    telegram_failure_reason_breakdown: dict[str, int] = Field(default_factory=dict)
    recent_telegram_failures: List[TelegramDeliveryFailureItem] = Field(
        default_factory=list
    )


class MLReleaseManifestSummaryItem(BaseModel):
    manifest_path: str
    release_tag: str
    validated_on: Optional[datetime] = None
    signature_status: Literal["verified", "missing", "invalid"]
    gate_status: str
    gate_passed: Optional[bool] = None
    gate_policy: Optional[str] = None
    backtest_sample_count: int = Field(ge=0)
    backtest_average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    dataset_quality_status: Optional[str] = None
    best_predictor_key: Optional[str] = None
    best_predictor_name: Optional[str] = None
    recommended_docker_target: Optional[str] = None
    remote_storage_enabled: bool = False
    detail: str = ""


class MLReleaseOperationsSummary(BaseModel):
    manifest_dir: str
    manifest_count: int = Field(ge=0)
    remote_storage_configured: bool
    remote_auto_publish: bool
    retention_limit: int = Field(ge=0)
    status: Literal["healthy", "watch", "critical", "info"]
    detail: str
    latest_release_tag: Optional[str] = None
    latest_manifest_path: Optional[str] = None
    latest_validated_on: Optional[datetime] = None
    latest_signature_status: Literal["verified", "missing", "invalid"]
    latest_gate_status: str
    latest_gate_passed: Optional[bool] = None
    latest_gate_policy: Optional[str] = None
    latest_best_predictor_key: Optional[str] = None
    latest_dataset_quality_status: Optional[str] = None
    latest_backtest_sample_count: int = Field(ge=0)
    latest_backtest_average_absolute_error_rate: Optional[float] = Field(
        default=None, ge=0.0
    )
    backtest_status: Literal["healthy", "watch", "critical", "info"]
    backtest_detail: str
    recent_manifests: List[MLReleaseManifestSummaryItem] = Field(default_factory=list)


class SmokeTestPhaseRate(BaseModel):
    name: str
    pass_rate: float = Field(
        ge=0.0, le=1.0, description="해당 단계가 기록된 사이클 중 통과한 비율"
    )
    evaluated_count: int = Field(
        ge=0, description="해당 단계가 기록된(존재한) 사이클 수"
    )


class SmokeTestLatestPhase(BaseModel):
    name: str
    passed: bool
    detail: str


class SmokeTestLatestRun(BaseModel):
    started_at: Optional[datetime] = None
    overall_passed: bool
    phases: List[SmokeTestLatestPhase] = Field(default_factory=list)


class SmokeTestRecentFailure(BaseModel):
    started_at: Optional[datetime] = None
    failed_phases: List[str] = Field(default_factory=list)


class SmokeTestOperationsSummary(BaseModel):
    cycle_count: int = Field(ge=0, description="기간 내 스모크 사이클 수")
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    pass_rate: float = Field(
        ge=0.0, le=1.0, description="스모크 사이클 통과율 (통과/전체, 데이터 없으면 0.0)"
    )
    current_streak: int = Field(
        ge=0, description="최근 연속 통과 사이클 수 (N일 연속 green 신호)"
    )
    schedule_enabled: bool = Field(
        description="스모크 스케줄(SMOKE_TEST_SCHEDULE_ENABLED) 활성 여부"
    )
    per_phase: List[SmokeTestPhaseRate] = Field(default_factory=list)
    latest: Optional[SmokeTestLatestRun] = None
    recent_failures: List[SmokeTestRecentFailure] = Field(default_factory=list)


class OperationsDashboardResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    crawl: CrawlOperationsSummary
    strategy: StrategyOperationsSummary
    tasks: TaskOperationsSummary
    notifications: NotificationOperationsSummary
    ml_release: MLReleaseOperationsSummary
    smoke_test: SmokeTestOperationsSummary
    cards: List[OperationsDashboardCard] = Field(default_factory=list)


class LegacyAdminStatsResponse(BaseModel):
    operator_id: int
    total_users: int
    active_users: int
    total_projects: int
    total_bids: int
    mode: Literal["single_operator"] = "single_operator"


class LegacyAdminActionResponse(BaseModel):
    status: str
    operator_id: int
    requested_user_id: int
    mode: Literal["single_operator"] = "single_operator"


class CrawlRequest(BaseModel):
    source: str = "koneps"
    category: Optional[str] = None
    target_date: Optional[str] = None
    keyword: Optional[str] = None
    execution_mode: Literal["mock", "live", "auto"] = "mock"
    max_items: int = Field(default=10, ge=1, le=100)
    # scsbid award coverage — all optional, fully backward compatible.
    categories: Optional[List[str]] = Field(
        default=None,
        description="scsbid multi-category sweep. None falls back to the single `category`.",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="scsbid award date-window start (YYYYMMDD or ISO).",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="scsbid award date-window end (YYYYMMDD or ISO).",
    )
    lookback_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="scsbid rolling window: end=today, start=today-lookback_days.",
    )
    page_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=999,
        description="scsbid numOfRows per page. Defaults to 100.",
    )
    max_pages: Optional[int] = Field(
        default=None,
        ge=1,
        description="scsbid per-category page ceiling. Defaults to 30.",
    )
    collect_reserve_detail: bool = Field(
        default=True,
        description="When False, skip per-item reserve-price detail fetches.",
    )


class CrawlNoticeItem(BaseModel):
    notice_number: str
    title: str
    base_amount: float
    estimated_amount: Optional[float] = None
    closing_at: Optional[datetime] = None
    business_type: Optional[str] = None
    business_type_code: Optional[str] = None
    business_type_label: Optional[str] = None
    region: Optional[str] = None
    license_codes: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class CrawlResponse(BaseModel):
    job_status: str
    source: str
    collected_count: int
    items: List[CrawlNoticeItem]
    metadata: dict = Field(default_factory=dict)


class CrawlTaskResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str
    crawl_job_id: int


class CrawlTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    crawl_job_id: Optional[int] = None
    error: Optional[str] = None
    result: Optional[CrawlResponse] = None


class ClassificationRequest(BaseModel):
    project_id: int
    user_id: Optional[int] = Field(
        default=None,
        description="Legacy compatibility field; omit in single-user mode.",
    )


class ClassificationResponse(BaseModel):
    matched: bool
    score: float
    reasons: List[str]
    criteria: dict = Field(default_factory=dict)
    score_breakdown: dict = Field(default_factory=dict)


class OpportunityAnalysisRequest(BaseModel):
    project_id: int
    agency_name: Optional[str] = None
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
    action: Literal["bid_now", "review", "skip"]
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
    bid_recommendation: BidRecommendationResponse
    similar_projects: ProjectSimilaritySearchResponse
    decision: BidDecisionResponse


class BidDecisionSaveRequest(BidDecisionRequest):
    decision_status: Optional[
        Literal["planned", "reviewing", "submitted", "skipped"]
    ] = None
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
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    initial_action: Literal["bid_now", "review", "skip"] = "skip"
    initial_decision_status: Literal[
        "planned", "reviewing", "submitted", "skipped"
    ] = "planned"
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


# Backward-compatible aliases while the domain language migrates away from multi-user allocation.
AllocationRequest = BidDecisionRequest
AllocationResponse = BidDecisionResponse


class TelegramNotificationRequest(BaseModel):
    title: str
    message: str
    url: Optional[str] = None


class TelegramCallbackChat(BaseModel):
    id: int


class TelegramCallbackMessage(BaseModel):
    message_id: int
    chat: TelegramCallbackChat


class TelegramCallbackQuery(BaseModel):
    id: str
    data: str
    message: Optional[TelegramCallbackMessage] = None


class TelegramCallbackUpdateRequest(BaseModel):
    update_id: Optional[int] = None
    callback_query: TelegramCallbackQuery


class TelegramActionResponse(BaseModel):
    status: str
    detail: str
    decision_record_id: Optional[int] = None
    action: Optional[Literal["bid_now", "review", "skip"]] = None
    decision_status: Optional[
        Literal["planned", "reviewing", "submitted", "skipped"]
    ] = None


class TelegramSyncResponse(BaseModel):
    status: str
    detail: str
    processed_count: int
    processed_update_ids: List[int] = Field(default_factory=list)
    known_chat_ids: List[int] = Field(default_factory=list)


class TelegramStatusResponse(BaseModel):
    configured: bool
    status: Literal["healthy", "watch", "error"] = "healthy"
    detail: str = ""
    delivery_chat_id: Optional[str] = None
    pending_update_count: int = 0
    webhook_url: str = ""
    has_custom_certificate: bool = False
    known_chat_ids: List[int] = Field(default_factory=list)


class BacktestDataAuditResponse(BaseModel):
    generated_at: str
    filters: dict = Field(default_factory=dict)
    table_counts: dict = Field(default_factory=dict)
    window_counts: dict = Field(default_factory=dict)
    date_range: dict = Field(default_factory=dict)
    category_breakdown: List[dict] = Field(default_factory=list)


class PaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=5000)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "local-backtest"
    model_version: str = "current"
    cutoff_hours_before_deadline: int = Field(default=2, ge=0, le=168)
    history_limit: int = Field(default=80, ge=1, le=500)
    settle_actions: List[Literal["bid_now", "review", "skip"]] = Field(
        default_factory=lambda: ["bid_now"]
    )
    persist: bool = False


class ForwardPaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "forward-paper"
    model_version: str = "current"
    history_limit: int = Field(default=80, ge=1, le=500)
    persist: bool = True


class PaperBiddingRunExecutionResponse(BaseModel):
    run_id: Optional[int] = None
    request: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    items: List[dict] = Field(default_factory=list)
    settlements: List[dict] = Field(default_factory=list)


class PaperBiddingRunListItem(BaseModel):
    id: int
    operator_id: int
    status: str
    mode: str
    scenario: str
    category_filter: Optional[str] = None
    strategy_version: str
    model_version: str
    target_start_at: Optional[datetime] = None
    target_end_at: Optional[datetime] = None
    data_cutoff_policy: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    candidate_count: int = Field(ge=0)
    paper_bid_count: int = Field(ge=0)
    settled_count: int = Field(ge=0)
    settlement_overview: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)


class PaperBiddingRunListResponse(BaseModel):
    operator_id: int
    returned_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: List[PaperBiddingRunListItem] = Field(default_factory=list)


class PaperBiddingRunDetailResponse(PaperBiddingRunListItem):
    request: dict = Field(default_factory=dict)
    paper_bids: List[dict] = Field(default_factory=list)
    settlements: List[dict] = Field(default_factory=list)


class PaperBiddingSummaryResponse(BaseModel):
    operator_id: int
    latest_run: Optional[PaperBiddingRunListItem] = None
    run_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    latest_summary: dict = Field(default_factory=dict)


class BackgroundJobResponse(BaseModel):
    task_name: str
    status: str
    detail: str


# --- Synthetic Experiment Lab (Phase 1) ---------------------------------------


class SyntheticExperimentParams(BaseModel):
    """Execution parameters for a synthetic experiment (persisted as JSON).

    Field names mirror the existing synthetic backtest request (``start_at`` /
    ``end_at`` datetimes, ``scenario`` default ``base``). ``cutoff_hours`` /
    ``history_limit`` / ``settle_actions`` are persisted with the definition for
    forward compatibility even though the current engine consumes only the core
    fields.
    """

    model_config = ConfigDict(from_attributes=True)

    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=1000)
    scenario: str = "base"
    cutoff_hours: Optional[int] = Field(default=None, ge=0)
    history_limit: Optional[int] = Field(default=None, ge=1)
    settle_actions: bool = False


class SyntheticExperimentCreate(BaseModel):
    """Request payload for creating (saving) a synthetic experiment."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    params: SyntheticExperimentParams
    operator_slugs: Optional[List[str]] = None


class SyntheticExperimentRunSummary(BaseModel):
    """Lightweight run summary embedded in an experiment detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    status: str
    task_id: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class SyntheticExperimentResponse(BaseModel):
    """Full synthetic experiment detail including run history."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    params: SyntheticExperimentParams
    operator_slugs: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    runs: List[SyntheticExperimentRunSummary] = Field(default_factory=list)


class SyntheticExperimentCategoryBreakdown(BaseModel):
    """Settlement aggregates grouped by project category (Phase 2 Lab).

    Two honest, separately-named estimates (both NOT actual awards):

    * ``win_rate`` / ``est_price_close_rate`` -- the SAME price-only estimate
      ``would_have_won_count / settled_count`` (``win_rate`` kept for frontend
      lockstep, ``est_price_close_rate`` is its honest alias). ``None`` when
      ``settled_count`` is 0.
    * ``eligible_favorable_rate`` -- PR3 eligibility-gate estimate
      ``eligible_favorable_count / eligibility_judged_count`` where the
      denominator EXCLUDES ``unknown`` (no 예가/낙찰하한 data) settlements.

    Health fields: ``settled_count`` (sample size) + ``latest_result_time``
    (freshness of the newest award in the group).
    """

    model_config = ConfigDict(from_attributes=True)

    category: str
    settled_count: int = 0
    would_have_won_count: int = 0
    win_rate: Optional[float] = None
    est_price_close_rate: Optional[float] = None
    eligible_favorable_count: int = 0
    eligibility_unknown_count: int = 0
    eligibility_judged_count: int = 0
    eligible_favorable_rate: Optional[float] = None
    avg_abs_bid_rate_error: Optional[float] = None
    latest_result_time: Optional[str] = None


class SyntheticExperimentBudgetBandBreakdown(BaseModel):
    """Settlement aggregates grouped by budget band (Phase 2 Lab).

    Band keys: ``lt_1eok`` / ``1eok_5eok`` / ``5eok_10eok`` / ``10eok_50eok`` /
    ``gte_50eok`` (KRW). Carries the same honest estimates + health fields as the
    category breakdown (``win_rate``/``est_price_close_rate``,
    ``eligible_favorable_rate`` with ``unknown`` excluded, ``settled_count`` +
    ``latest_result_time``).
    """

    model_config = ConfigDict(from_attributes=True)

    budget_band: str
    settled_count: int = 0
    would_have_won_count: int = 0
    win_rate: Optional[float] = None
    est_price_close_rate: Optional[float] = None
    eligible_favorable_count: int = 0
    eligibility_unknown_count: int = 0
    eligibility_judged_count: int = 0
    eligible_favorable_rate: Optional[float] = None
    avg_abs_bid_rate_error: Optional[float] = None
    latest_result_time: Optional[str] = None


class SyntheticExperimentBreakdown(BaseModel):
    """Per-operator settlement breakdown (category + budget band)."""

    model_config = ConfigDict(from_attributes=True)

    by_category: List[SyntheticExperimentCategoryBreakdown] = Field(
        default_factory=list
    )
    by_budget_band: List[SyntheticExperimentBudgetBandBreakdown] = Field(
        default_factory=list
    )


class SyntheticExperimentResultItem(BaseModel):
    """Per-operator result persisted for a synthetic experiment run."""

    model_config = ConfigDict(from_attributes=True)

    operator_slug: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    settlement_sample: Optional[Any] = None
    breakdown: SyntheticExperimentBreakdown = Field(
        default_factory=SyntheticExperimentBreakdown
    )


class SyntheticExperimentRunResponse(BaseModel):
    """Detailed status/result payload for a single experiment run (polling)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    status: str
    task_id: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    results: List[SyntheticExperimentResultItem] = Field(default_factory=list)


# --- Experiment Lab (Phase 4): A/B run comparison -----------------------------


class SyntheticExperimentCompareRunHeader(BaseModel):
    """Minimal run identity + summary embedded in a compare response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    summary: Optional[Dict[str, Any]] = None


class SyntheticExperimentCompareSide(BaseModel):
    """Per-operator metric slice for one side (run A or run B) of a comparison.

    ``win_rate_on_settled`` is a PRICE-ONLY estimate (NOT an actual award), passed
    through unchanged. Any field may be ``None`` (e.g. no settled rows).
    """

    model_config = ConfigDict(from_attributes=True)

    win_rate_on_settled: Optional[float] = None
    settled_count: Optional[int] = None
    bid_submission_rate: Optional[float] = None
    average_absolute_bid_rate_error: Optional[float] = None


class SyntheticExperimentCompareDelta(BaseModel):
    """Signed (B - A) deltas; positive means run B is higher. ``None`` when either
    side is missing/None (e.g. a one-sided ``win_rate`` for a group with no
    settled rows)."""

    model_config = ConfigDict(from_attributes=True)

    win_rate_on_settled: Optional[float] = None
    bid_submission_rate: Optional[float] = None
    average_absolute_bid_rate_error: Optional[float] = None


class SyntheticExperimentCompareOperator(BaseModel):
    """One operator present in BOTH runs, with its A/B metrics and their delta."""

    model_config = ConfigDict(from_attributes=True)

    operator_slug: str
    a: SyntheticExperimentCompareSide
    b: SyntheticExperimentCompareSide
    delta: SyntheticExperimentCompareDelta


class SyntheticExperimentCompareResponse(BaseModel):
    """A/B comparison of two completed runs, joined by ``operator_slug``.

    ``operators`` is the slug intersection (sorted); ``only_in_a`` / ``only_in_b``
    list slugs unique to one side. The two runs may belong to different
    experiments. All ``win_rate_*`` values remain price-only estimates."""

    model_config = ConfigDict(from_attributes=True)

    run_a: SyntheticExperimentCompareRunHeader
    run_b: SyntheticExperimentCompareRunHeader
    operators: List[SyntheticExperimentCompareOperator] = Field(default_factory=list)
    only_in_a: List[str] = Field(default_factory=list)
    only_in_b: List[str] = Field(default_factory=list)


# --- Synthetic Custom Operators (Phase 3) -------------------------------------


class CustomOperatorBase(BaseModel):
    """Shared strategy/profile fields for a custom synthetic company.

    All fields optional here; ``CustomOperatorCreate`` re-declares ``name`` as
    required. Multi-value text fields (categories/regions/keywords/licenses) are
    accepted as string lists and stored as the repo's comma-joined text format.
    """

    model_config = ConfigDict(from_attributes=True)

    company_name: Optional[str] = Field(default=None, max_length=100)
    business_type: Optional[str] = Field(default=None, max_length=50)
    license_codes: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None
    annual_revenue: Optional[float] = Field(default=None, ge=0.0)
    capacity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    focus_categories: Optional[List[str]] = None
    focus_regions: Optional[List[str]] = None
    exclude_regions: Optional[List[str]] = None
    required_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    min_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    max_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    minimum_match_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    minimum_probability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_recommended_candidates: Optional[int] = Field(default=None, ge=1, le=100)


class CustomOperatorCreate(CustomOperatorBase):
    """Create a new custom synthetic company (``synthetic-custom-<slug>``)."""

    name: str = Field(min_length=1, max_length=100)
    slug: Optional[str] = Field(
        default=None,
        max_length=80,
        description="Optional explicit slug; normalized to [a-z0-9-]. Derived from name when omitted.",
    )


class CustomOperatorUpdate(CustomOperatorBase):
    """Partial-update a custom synthetic company (all fields optional)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)


class CustomOperatorCloneRequest(CustomOperatorBase):
    """Clone a preset/custom company into a new custom one with overrides."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    slug: Optional[str] = Field(default=None, max_length=80)


class CustomOperatorDetail(BaseModel):
    """Detailed custom-company shape returned by create/update/clone.

    Superset of ``SyntheticOperatorItem`` (the list shape) plus the full strategy
    field set so the Phase 3 form can render without a second fetch.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    username: str
    slug: str
    is_custom: bool = True
    display_name: str
    company: Optional[str] = None
    business_type: Optional[str] = None
    annual_revenue: float = 0.0
    capacity_score: float = 0.0
    license_codes: List[str] = Field(default_factory=list)
    region_codes: List[str] = Field(default_factory=list)
    focus_categories: List[str] = Field(default_factory=list)
    focus_regions: List[str] = Field(default_factory=list)
    exclude_regions: List[str] = Field(default_factory=list)
    required_keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)
    min_budget_estimate: float = 0.0
    max_budget_estimate: float = 0.0
    minimum_match_score: float = 0.0
    minimum_probability_score: float = 0.0
    bid_now_threshold: float = 0.0
    review_threshold: float = 0.0
    max_recommended_candidates: int = 0


class CustomOperatorDeleteResponse(BaseModel):
    """Confirmation payload for a custom-company delete."""

    model_config = ConfigDict(from_attributes=True)

    deleted: bool = True
    slug: str
    username: str
