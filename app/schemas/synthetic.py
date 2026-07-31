"""Synthetic experiment lab (definitions / runs / gaps / compare) schemas."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import PaperBidAction


class SyntheticExperimentParams(BaseModel):
    """Execution parameters for a synthetic experiment (persisted as JSON).

    Field names mirror the existing synthetic backtest request (``start_at`` /
    ``end_at`` datetimes, ``scenario`` default ``base``). ``cutoff_hours`` /
    ``history_limit`` / ``settle_actions`` are persisted with the definition and
    consumed by experiment-scoped backtest runs. ``settle_actions`` accepts the
    legacy boolean form as well as the explicit action list.
    """

    model_config = ConfigDict(from_attributes=True)

    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=1000)
    scenario: str = "base"
    cutoff_hours: Optional[int] = Field(default=None, ge=0)
    history_limit: Optional[int] = Field(default=None, ge=1)
    settle_actions: Union[bool, List[PaperBidAction]] = Field(
        default_factory=lambda: ["bid_now"]
    )


class SyntheticExperimentCreate(BaseModel):
    """Request payload for creating (saving) a synthetic experiment."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    params: SyntheticExperimentParams
    operator_slugs: Optional[List[str]] = None


class SyntheticExperimentPreset(BaseModel):
    """Fixed G-1 experiment preset definition and persistence state."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str
    params: SyntheticExperimentParams
    operator_slugs: List[str] = Field(default_factory=list)
    experiment_id: Optional[int] = None
    latest_run_id: Optional[int] = None
    latest_run_status: Optional[str] = None


class SyntheticExperimentPresetListResponse(BaseModel):
    presets: List[SyntheticExperimentPreset] = Field(default_factory=list)


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
    sample_status: str = "insufficient_sample"
    sample_target: int = Field(default=30, ge=1)
    settled_count: int = Field(default=0, ge=0)
    missing_settled_count: int = Field(default=0, ge=0)


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


class SyntheticExperimentRunCreateRequest(BaseModel):
    """Optional metadata for a queued synthetic experiment run."""

    model_config = ConfigDict(from_attributes=True)

    source_sample_gap_candidate: Optional[Dict[str, Any]] = None


class SyntheticExperimentSampleGapWarning(BaseModel):
    """Warning emitted while building the read-only sample gap plan."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    run_ids: List[int] = Field(default_factory=list)
    operator_slugs: List[str] = Field(default_factory=list)


class SyntheticExperimentSampleGapRunReference(BaseModel):
    """Run context attached to one sample gap."""

    model_config = ConfigDict(from_attributes=True)

    run_id: int
    experiment_id: int
    preset_name: Optional[str] = None
    status: str
    finished_at: Optional[datetime] = None
    start_at: Optional[Any] = None
    end_at: Optional[Any] = None
    category: Optional[str] = None
    limit: Optional[int] = None
    scenario: str = "base"
    settle_actions: Union[bool, List[PaperBidAction]] = Field(
        default_factory=lambda: ["bid_now"]
    )
    params: Dict[str, Any] = Field(default_factory=dict)
    operator_slugs: List[str] = Field(default_factory=list)
    synthetic_only: bool = True
    report_status: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class SyntheticExperimentSampleGapAction(BaseModel):
    """Operator-facing action hint for closing a sample gap."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    detail: str


class SyntheticExperimentSampleGapRecommendation(BaseModel):
    """Recommended backtest preset/params/actions for one gap."""

    model_config = ConfigDict(from_attributes=True)

    preset_name: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    actions: List[SyntheticExperimentSampleGapAction] = Field(default_factory=list)


class SyntheticExperimentSampleGapItem(BaseModel):
    """Aggregated lacking group from recent completed synthetic experiment runs."""

    model_config = ConfigDict(from_attributes=True)

    priority: int = Field(ge=1)
    dimension: Literal["preset", "category", "business_type", "budget_band"]
    key: str
    settled_count: int = Field(ge=0)
    sample_target: int = Field(ge=0)
    missing_settled_count: int = Field(ge=0)
    total_missing_settled_count: int = Field(ge=0)
    source_run_count: int = Field(ge=0)
    related_preset_names: List[str] = Field(default_factory=list)
    related_run_ids: List[int] = Field(default_factory=list)
    related_runs: List[SyntheticExperimentSampleGapRunReference] = Field(
        default_factory=list
    )
    recommendation: SyntheticExperimentSampleGapRecommendation
    warnings: List[str] = Field(default_factory=list)


class SyntheticExperimentSampleGapPlanResponse(BaseModel):
    """Read-only plan connecting sample_report gaps to operator follow-up work."""

    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    max_runs: int = Field(ge=1)
    scanned_completed_run_count: int = Field(ge=0)
    source_run_count: int = Field(ge=0)
    legacy_summary_run_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    warnings: List[SyntheticExperimentSampleGapWarning] = Field(default_factory=list)
    gaps: List[SyntheticExperimentSampleGapItem] = Field(default_factory=list)


class SyntheticExperimentSampleGapCandidateRequest(BaseModel):
    """Select one sample gap and ask for a read-only experiment/run candidate."""

    model_config = ConfigDict(from_attributes=True)

    dimension: Literal["preset", "category", "business_type", "budget_band"]
    key: str = Field(min_length=1, max_length=200)
    max_runs: int = Field(default=20, ge=1, le=100)
    action_code: Optional[str] = Field(default=None, max_length=100)


class SyntheticExperimentSampleGapHttpRequest(BaseModel):
    """Concrete follow-up API request for materializing a sample-gap candidate."""

    model_config = ConfigDict(from_attributes=True)

    method: Literal["POST"]
    path: str
    body: Dict[str, Any] = Field(default_factory=dict)


class SyntheticExperimentSampleGapExecutionPlan(BaseModel):
    """Repeatable, non-executing plan that bridges a gap candidate to a run."""

    model_config = ConfigDict(from_attributes=True)

    mode: Literal[
        "blocked",
        "run_existing_experiment",
        "save_preset_then_run",
        "create_experiment_then_run",
    ]
    approval_required: bool = True
    dry_run_default: bool = True
    source_context: Dict[str, Any] = Field(default_factory=dict)
    preset_request: Optional[SyntheticExperimentSampleGapHttpRequest] = None
    experiment_request: Optional[SyntheticExperimentSampleGapHttpRequest] = None
    run_request: Optional[SyntheticExperimentSampleGapHttpRequest] = None
    cli_command: str
    write_cli_command: Optional[str] = None
    instructions: List[str] = Field(default_factory=list)


class SyntheticExperimentSampleGapOperatorTarget(BaseModel):
    """Operator-id preflight resolution for a selected synthetic slug."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    username: str
    operator_id: Optional[int] = None
    user_id: Optional[int] = None
    resolved: bool = False
    operator_id_scope_ready: bool = False


class SyntheticExperimentSampleGapRunCandidateResponse(BaseModel):
    """Runnable follow-up candidate derived from one sample-gap recommendation."""

    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    gap: SyntheticExperimentSampleGapItem
    action_code: str
    action_label: str
    preset_name: Optional[str] = None
    params: SyntheticExperimentParams
    operator_slugs: List[str] = Field(default_factory=list)
    operator_targets: List[SyntheticExperimentSampleGapOperatorTarget] = Field(
        default_factory=list
    )
    operator_id_scope_ready: bool = False
    experiment_payload: SyntheticExperimentCreate
    experiment_id: Optional[int] = None
    latest_run_id: Optional[int] = None
    latest_run_status: Optional[str] = None
    next_step: Literal[
        "resolve_mixed_data",
        "run_existing_experiment",
        "save_preset",
        "create_experiment",
    ]
    execution_plan: SyntheticExperimentSampleGapExecutionPlan
    run_allowed: bool
    blocked_by_warnings: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    message: str


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
