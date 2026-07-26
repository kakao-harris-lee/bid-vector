"""Operations dashboard (crawl / strategy / task / smoke / release) schemas."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


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
        ge=0.0,
        le=1.0,
        description="시도분 대비 통과율 (스킵 제외, 시도 0이면 0.0)",
    )
    evaluated_count: int = Field(
        ge=0, description="해당 단계가 실제 시도된(스킵 제외) 사이클 수"
    )


class SmokeTestLatestPhase(BaseModel):
    name: str
    passed: bool
    detail: str
    failure_category: Optional[str] = None
    action_required: Optional[str] = None
    retry_method: Optional[str] = None
    skip_reason: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class SmokeTestLatestRun(BaseModel):
    started_at: Optional[datetime] = None
    overall_passed: bool
    phases: List[SmokeTestLatestPhase] = Field(default_factory=list)


class SmokeTestRecentFailure(BaseModel):
    started_at: Optional[datetime] = None
    failed_phases: List[str] = Field(default_factory=list)
    failure_categories: List[str] = Field(default_factory=list)
    failure_category_breakdown: dict[str, int] = Field(default_factory=dict)
    failure_actions: List[str] = Field(default_factory=list)
    retry_methods: List[str] = Field(default_factory=list)
    phase_details: List[SmokeTestLatestPhase] = Field(default_factory=list)


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
    healthy_streak_target: int = Field(
        default=7, ge=1, description="G-0에서 요구하는 연속 green 사이클 수"
    )
    current_streak_meets_target: bool = False
    schedule_enabled: bool = Field(
        description="스모크 스케줄(SMOKE_TEST_SCHEDULE_ENABLED) 활성 여부"
    )
    failure_category_breakdown: dict[str, int] = Field(default_factory=dict)
    per_phase: List[SmokeTestPhaseRate] = Field(default_factory=list)
    latest: Optional[SmokeTestLatestRun] = None
    recent_failures: List[SmokeTestRecentFailure] = Field(default_factory=list)


class SyntheticValidationLatestRun(BaseModel):
    experiment_id: int
    experiment_name: Optional[str] = None
    run_id: int
    status: str
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    sample_status: Optional[str] = None
    total_settled_count: int = Field(default=0, ge=0)
    missing_total_settled_count: int = Field(default=0, ge=0)


class SyntheticValidationPresetStatus(BaseModel):
    name: str
    experiment_id: Optional[int] = None
    latest_run_id: Optional[int] = None
    latest_run_status: Optional[str] = None
    latest_finished_at: Optional[datetime] = None
    sample_status: Optional[str] = None
    total_settled_count: int = Field(default=0, ge=0)
    missing_total_settled_count: int = Field(default=0, ge=0)
    insufficient_operator_count: int = Field(default=0, ge=0)


class SyntheticValidationOperationsSummary(BaseModel):
    """G-1 synthetic experiment health shown alongside smoke-test telemetry."""

    preset_count: int = Field(ge=0)
    saved_preset_count: int = Field(ge=0)
    completed_preset_count: int = Field(ge=0)
    failed_preset_count: int = Field(ge=0)
    sufficient_preset_count: int = Field(ge=0)
    sample_target: int = Field(default=100, ge=1)
    recent_run_count: int = Field(ge=0)
    recent_completed_count: int = Field(ge=0)
    recent_failed_count: int = Field(ge=0)
    status: Literal["healthy", "watch", "critical", "info"]
    detail: str
    latest: Optional[SyntheticValidationLatestRun] = None
    presets: List[SyntheticValidationPresetStatus] = Field(default_factory=list)


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
    synthetic_validation: SyntheticValidationOperationsSummary
    cards: List[OperationsDashboardCard] = Field(default_factory=list)


class G2EvidenceSummaryResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    window_days: int
    evidence_status: Literal["ready", "insufficient", "mixed_scope", "missing"]
    smoke: Dict[str, Any] = Field(default_factory=dict)
    strategy_monitor: Dict[str, Any] = Field(default_factory=dict)
    decision_experiments: Dict[str, Any] = Field(default_factory=dict)
    synthetic_experiments: Dict[str, Any] = Field(default_factory=dict)
    notifications: Dict[str, Any] = Field(default_factory=dict)
    blocking_gaps: List[str] = Field(default_factory=list)
    supporting_gaps: List[str] = Field(default_factory=list)


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
