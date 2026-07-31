"""Decision insights / funnel / recommendation / experiment schemas."""

from datetime import date, datetime
from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field
from app.core.constants import PaperBidAction

# 실험 판정 어휘 — 이 모듈 안에서 5번 중복 선언되어 있었다(§4.5-1). 값과 순서는 그대로다.
DecisionExperimentOutcome = Literal[
    "insufficient_data", "watch", "success", "rollback", "inconclusive"
]


class DecisionInsightsRecentItem(BaseModel):
    decision_record_id: int
    project_id: int
    action: PaperBidAction
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    expected_margin_score: float = Field(ge=0.0, le=1.0)
    execution_complexity_score: float = Field(ge=0.0, le=1.0)
    competitiveness_score: float = Field(ge=0.0, le=1.0)
    budget_capture_score: float = Field(ge=0.0, le=1.0)
    updated_at: datetime


class DecisionInsightsResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
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
    initial_action: PaperBidAction
    initial_decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    current_action: PaperBidAction
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
    current_operator_id: int
    current_operator_username: str
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
    outcome: DecisionExperimentOutcome
    recommended_action: Literal["collect_more_data", "continue", "complete", "rollback"]
    summary: str
    current_summary: DecisionExperimentMetricSnapshot


class DecisionExperimentRunCreateRequest(DecisionRecommendationExperiment):
    baseline_days: int = Field(default=14, ge=1, le=90)
    started_at: Optional[datetime] = None
    notes: Optional[str] = None


class DecisionExperimentRunUpdateRequest(BaseModel):
    status: Optional[Literal["planned", "running", "completed", "rolled_back"]] = None
    outcome: Optional[DecisionExperimentOutcome] = None
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
    current_operator_id: int
    current_operator_username: str
    run_id: int
    experiment_key: str
    recommendation_key: str
    applied: bool
    dry_run: bool
    latest_outcome: Optional[DecisionExperimentOutcome] = None
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
    current_operator_id: int
    current_operator_username: str
    run_id: int
    experiment_key: str
    recommendation_key: str
    applied: bool
    dry_run: bool
    latest_outcome: Optional[DecisionExperimentOutcome] = None
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
    outcome: Optional[DecisionExperimentOutcome] = None
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
    current_operator_id: int
    current_operator_username: str
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
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    run: DecisionExperimentRunResponse
    baseline_summary: DecisionExperimentMetricSnapshot


class DecisionFunnelResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
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
