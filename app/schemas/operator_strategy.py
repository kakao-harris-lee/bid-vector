"""Operator strategy configuration and monitor-run schemas."""

from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.schemas._shared import _PROBABILITY_SCORE_DESCRIPTION


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
    # 스냅샷 메타 (설계 2026-07-30 §6.2). 기존 필드는 전부 유지 — PR-C 전까지
    # 현행 프론트가 그대로 동작해야 하는 하위호환 superset (HARD 제약).
    computed_at: Optional[datetime] = None
    snapshot_status: Literal["idle", "running", "failed"] = "idle"
    stale: bool = False


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
    current_operator_id: Optional[int] = None
    current_operator_username: Optional[str] = None
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
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class OperatorStrategyMonitorTaskStatusResponse(BaseModel):
    task_id: str
    monitor_run_id: Optional[int] = None
    operator_id: Optional[int] = None
    current_operator_id: Optional[int] = None
    current_operator_username: Optional[str] = None
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
    current_operator_id: int
    current_operator_username: str
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
    current_operator_id: int
    current_operator_username: str
    result_count: int = Field(ge=0)
    runs: List[OperatorStrategyRunResponse] = Field(default_factory=list)


class OperatorStrategyRunDetailResponse(BaseModel):
    id: int
    operator_id: int
    current_operator_id: int
    current_operator_username: str
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
