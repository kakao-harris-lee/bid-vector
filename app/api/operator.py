"""Single-operator profile and overview routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.constants import (
    ACTIVE_DECISION_STATUSES,
    INTERNAL_TELEMETRY_EVENT_TYPES as SHARED_INTERNAL_TELEMETRY_EVENT_TYPES,
)
from app.core.database import get_db
from app.core.security import (
    CANONICAL_OPERATOR_USERNAME,
    get_current_operator_optional,
    is_privileged_operator,
)
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ensure_operator_account,
    ensure_operator_profile_for,
    ensure_operator_strategy_for,
    join_multi_value_text,
    resolve_read_operator,
    resolve_write_operator,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    Bid,
    BidDecisionRecord,
    CompanyProfile,
    Notification,
    OperatorNotificationChannel,
    OperatorStrategyRun,
    PricePrediction,
    Project,
    User,
)
from app.schemas.schemas import (
    NotificationResponse,
    OperatorAccountItem,
    OperatorAccountListResponse,
    OperatorDashboardResponse,
    OperatorNotificationChannelItem,
    OperatorNotificationChannelListResponse,
    OperatorOverviewResponse,
    OperatorProfileResponse,
    OperatorProfileUpdate,
    OperatorStrategyCandidatesResponse,
    OperatorStrategyMonitorRequest,
    OperatorStrategyMonitorResponse,
    OperatorStrategyMonitorTaskResponse,
    OperatorStrategyMonitorTaskStatusResponse,
    OperatorStrategyRunDetailResponse,
    OperatorStrategyRunListResponse,
    OperatorStrategyResponse,
    OperatorStrategyUpdate,
)
from app.schemas.eligibility_feedback import (
    EligibilityFeedbackRequest,
    EligibilityFeedbackResponse,
)
from app.services.eligibility_feedback import record_operator_label
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.notifications.manager import (
    OperatorNotificationService,
    mask_notification_route_key,
    mask_notification_target,
)
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
    get_strategy_auto_workload_penalty_multiplier,
    get_strategy_category_priority_overrides,
)
from app.services.prediction_feedback import PredictionFeedbackService
from app.tasks.jobs import enqueue_operator_strategy_monitor, get_operator_strategy_monitor_task_status

router = APIRouter()
# Single source in app/core/constants.py; kept as a module-level alias so the
# existing filter call site and its name remain stable.
INTERNAL_OPERATOR_EVENT_TYPES = SHARED_INTERNAL_TELEMETRY_EVENT_TYPES


# Read-scoped operator resolution is shared across analytics/operator/decision
# endpoints; see app.core.single_user.resolve_read_operator for the security
# policy (canonical fallback / 403 / 404). The returned operator also populates
# the ``current_operator_*`` envelope fields (convention from PR #70). Kept as a
# thin alias so existing call sites and their names remain stable.
_resolve_operator_for_read = resolve_read_operator


# Write-scoped operator resolution (self-only; 403 on cross-operator writes) is
# shared by PUT /operator/profile, PUT /operator/strategy, the eligibility
# feedback route, and the onboarding apply endpoint. See
# app.core.single_user.resolve_write_operator for the canonical/synthetic write
# isolation policy. Kept as a thin alias so existing call sites and their names
# remain stable.
_resolve_operator_for_write = resolve_write_operator


def _append_operator_query(path: str, operator_id: int) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}operator_id={int(operator_id)}"


def _operator_context_fields(target: User) -> dict:
    """Return the standard ``current_operator_*`` envelope fields.

    Convention (from PR #70): ``current_operator_*`` reflects the **target**
    operator the response is scoped to — i.e. the company the frontend
    switcher should highlight as active — not the bearer-token owner.
    """
    return {
        "current_operator_id": int(target.id),
        "current_operator_username": str(target.username or ""),
    }


def _is_profile_configured(
    license_codes: list[str],
    region_codes: list[str],
    annual_revenue: float,
    capacity_score: float,
    total_awards: int,
    construction_capacity_amount: float = 0.0,
    awarded_contract_limit: float = 0.0,
) -> bool:
    return any([
        bool(license_codes),
        bool(region_codes),
        annual_revenue > 0,
        capacity_score > 0,
        total_awards > 0,
        construction_capacity_amount > 0,
        awarded_contract_limit > 0,
    ])


def _build_operator_profile_response(
    operator: User,
    license_codes: list[str],
    region_codes: list[str],
    business_type: str,
    annual_revenue: float,
    capacity_score: float,
    total_awards: int,
    construction_capacity_amount: float = 0.0,
    awarded_contract_limit: float = 0.0,
    association_memberships: list[str] | None = None,
    tech_fields: list[str] | None = None,
) -> OperatorProfileResponse:
    return OperatorProfileResponse(
        operator_id=operator.id,
        username=operator.username,
        email=operator.email,
        full_name=operator.full_name,
        company=operator.company,
        is_active=operator.is_active,
        created_at=operator.created_at,
        business_type=business_type,
        license_codes=license_codes,
        region_codes=region_codes,
        association_memberships=association_memberships or [],
        tech_fields=tech_fields or [],
        annual_revenue=annual_revenue,
        capacity_score=capacity_score,
        construction_capacity_amount=construction_capacity_amount,
        awarded_contract_limit=awarded_contract_limit,
        total_awards=total_awards,
        profile_configured=_is_profile_configured(
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=annual_revenue,
            capacity_score=capacity_score,
            total_awards=total_awards,
            construction_capacity_amount=construction_capacity_amount,
            awarded_contract_limit=awarded_contract_limit,
        ),
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
    )


def _is_strategy_configured(
    focus_categories: list[str],
    focus_regions: list[str],
    exclude_regions: list[str],
    required_keywords: list[str],
    exclude_keywords: list[str],
    min_budget_estimate: float,
    max_budget_estimate: float,
    minimum_match_score: float,
    minimum_probability_score: float,
    bid_now_threshold: float,
    review_threshold: float,
    auto_workload_penalty_multiplier: float,
    category_priority_overrides: dict[str, float],
    notify_only_high_priority: bool,
    max_recommended_candidates: int,
) -> bool:
    return any([
        bool(focus_categories),
        bool(focus_regions),
        bool(exclude_regions),
        bool(required_keywords),
        bool(exclude_keywords),
        min_budget_estimate > 0,
        max_budget_estimate > 0,
        round(minimum_match_score, 4) != 0.6,
        round(minimum_probability_score, 4) != 0.55,
        round(bid_now_threshold, 4) != DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
        round(review_threshold, 4) != DEFAULT_OPERATOR_REVIEW_THRESHOLD,
        round(auto_workload_penalty_multiplier, 4) != 1.0,
        bool(category_priority_overrides),
        notify_only_high_priority is False,
        max_recommended_candidates != 10,
    ])


def _validate_strategy_thresholds(*, bid_now_threshold: float, review_threshold: float) -> None:
    if review_threshold > bid_now_threshold:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="review_threshold cannot be greater than bid_now_threshold",
        )


def _build_operator_strategy_response(
    operator: User,
    focus_categories: list[str],
    focus_regions: list[str],
    exclude_regions: list[str],
    required_keywords: list[str],
    exclude_keywords: list[str],
    min_budget_estimate: float,
    max_budget_estimate: float,
    minimum_match_score: float,
    minimum_probability_score: float,
    bid_now_threshold: float,
    review_threshold: float,
    auto_workload_penalty_multiplier: float,
    category_priority_overrides: dict[str, float],
    notify_only_high_priority: bool,
    max_recommended_candidates: int,
) -> OperatorStrategyResponse:
    _validate_strategy_thresholds(
        bid_now_threshold=bid_now_threshold,
        review_threshold=review_threshold,
    )
    return OperatorStrategyResponse(
        operator_id=operator.id,
        focus_categories=focus_categories,
        focus_regions=focus_regions,
        exclude_regions=exclude_regions,
        required_keywords=required_keywords,
        exclude_keywords=exclude_keywords,
        min_budget_estimate=min_budget_estimate,
        max_budget_estimate=max_budget_estimate,
        minimum_match_score=minimum_match_score,
        minimum_probability_score=minimum_probability_score,
        bid_now_threshold=bid_now_threshold,
        review_threshold=review_threshold,
        auto_workload_penalty_multiplier=auto_workload_penalty_multiplier,
        category_priority_overrides=category_priority_overrides,
        notify_only_high_priority=notify_only_high_priority,
        max_recommended_candidates=max_recommended_candidates,
        strategy_configured=_is_strategy_configured(
            focus_categories=focus_categories,
            focus_regions=focus_regions,
            exclude_regions=exclude_regions,
            required_keywords=required_keywords,
            exclude_keywords=exclude_keywords,
            min_budget_estimate=min_budget_estimate,
            max_budget_estimate=max_budget_estimate,
            minimum_match_score=minimum_match_score,
            minimum_probability_score=minimum_probability_score,
            bid_now_threshold=bid_now_threshold,
            review_threshold=review_threshold,
            auto_workload_penalty_multiplier=auto_workload_penalty_multiplier,
            category_priority_overrides=category_priority_overrides,
            notify_only_high_priority=notify_only_high_priority,
            max_recommended_candidates=max_recommended_candidates,
        ),
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
    )


def _build_operator_overview_payload(
    operator: User,
    *,
    days: int,
    db: Session,
) -> dict:
    """Build the compact dashboard overview shared by overview and dashboard endpoints.

    Scopes counts/profile lookups by ``operator`` (the target operator) so the
    payload reflects the company that the request is currently viewing. The
    ``current_operator_*`` envelope is set to the same target operator to
    match the convention from PR #70 (dashboard / analytics endpoints).
    """
    profile = ensure_operator_profile_for(db, operator)
    date_from = utc_now() - timedelta(days=days)
    license_codes = split_multi_value_text(profile.license_codes)
    region_codes = split_multi_value_text(profile.region_codes)

    return {
        "operator_id": operator.id,
        "project_count": db.query(Project).count(),
        "bid_count": db.query(Bid).filter(Bid.user_id == operator.id).count(),
        "active_bid_count": db.query(Bid).filter(Bid.user_id == operator.id, Bid.status.in_(["submitted", "reviewed"])).count(),
        "prediction_count": db.query(PricePrediction).filter(PricePrediction.user_id == operator.id).count(),
        "unread_notification_count": db.query(Notification).filter(Notification.user_id == operator.id, Notification.is_read.is_(False)).count(),
        "recent_event_count": (
            db.query(Analytics)
            .filter(
                Analytics.user_id == operator.id,
                Analytics.timestamp >= date_from,
                ~Analytics.event_type.in_(INTERNAL_OPERATOR_EVENT_TYPES),
            )
            .count()
        ),
        "profile_configured": _is_profile_configured(
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=profile.annual_revenue,
            capacity_score=profile.capacity_score,
            total_awards=profile.total_awards,
            construction_capacity_amount=float(
                profile.construction_capacity_amount or 0.0
            ),
            awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        ),
        "current_operator_id": int(operator.id),
        "current_operator_username": str(operator.username or ""),
    }


def _feedback_status(error_rate: float | None) -> str:
    """Map feedback error rate to a dashboard card status."""
    if error_rate is None:
        return "info"
    if error_rate <= 0.03:
        return "healthy"
    if error_rate <= 0.08:
        return "watch"
    return "critical"


@router.get("/profile", response_model=OperatorProfileResponse)
def get_operator_profile_endpoint(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return the operator account and company profile for the active context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    profile = ensure_operator_profile_for(db, target)
    license_codes = split_multi_value_text(profile.license_codes)
    region_codes = split_multi_value_text(profile.region_codes)
    return _build_operator_profile_response(
        operator=target,
        license_codes=license_codes,
        region_codes=region_codes,
        business_type=profile.business_type,
        annual_revenue=profile.annual_revenue,
        capacity_score=profile.capacity_score,
        total_awards=profile.total_awards,
        construction_capacity_amount=float(
            profile.construction_capacity_amount or 0.0
        ),
        awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        association_memberships=split_multi_value_text(
            profile.association_memberships
        ),
        tech_fields=split_multi_value_text(profile.tech_fields),
    )


@router.put("/profile", response_model=OperatorProfileResponse)
def update_operator_profile(
    request: OperatorProfileUpdate,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Update the active operator's account and fit profile settings.

    Edits are restricted to the bearer-token owner. Supplying ``?operator_id``
    that differs from the actor's id returns 403; synthetic-company profile
    edits must go through ``/synthetic/custom-operators/{slug}`` instead.
    """
    actor = _resolve_operator_for_write(db, current_operator, operator_id)
    operator = actor
    profile = ensure_operator_profile_for(db, actor)

    if request.username is not None and request.username != operator.username:
        existing_username = db.query(User).filter(User.username == request.username, User.id != operator.id).first()
        if existing_username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")
        operator.username = request.username

    if request.email is not None and request.email != operator.email:
        existing_email = db.query(User).filter(User.email == request.email, User.id != operator.id).first()
        if existing_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
        operator.email = request.email

    if request.full_name is not None:
        operator.full_name = request.full_name
    if request.company is not None:
        operator.company = request.company
    if request.business_type is not None:
        profile.business_type = request.business_type
    if request.license_codes is not None:
        profile.license_codes = join_multi_value_text(request.license_codes)
    if request.region_codes is not None:
        profile.region_codes = join_multi_value_text(request.region_codes)
    if request.association_memberships is not None:
        profile.association_memberships = join_multi_value_text(
            request.association_memberships
        )
    if request.tech_fields is not None:
        profile.tech_fields = join_multi_value_text(request.tech_fields)
    if request.annual_revenue is not None:
        profile.annual_revenue = request.annual_revenue
    if request.capacity_score is not None:
        profile.capacity_score = request.capacity_score
    if request.construction_capacity_amount is not None:
        profile.construction_capacity_amount = request.construction_capacity_amount
    if request.awarded_contract_limit is not None:
        profile.awarded_contract_limit = request.awarded_contract_limit
    if request.total_awards is not None:
        profile.total_awards = request.total_awards

    db.commit()
    db.refresh(operator)
    db.refresh(profile)

    license_codes = split_multi_value_text(profile.license_codes)
    region_codes = split_multi_value_text(profile.region_codes)
    return _build_operator_profile_response(
        operator=operator,
        license_codes=license_codes,
        region_codes=region_codes,
        business_type=profile.business_type,
        annual_revenue=profile.annual_revenue,
        capacity_score=profile.capacity_score,
        total_awards=profile.total_awards,
        construction_capacity_amount=float(
            profile.construction_capacity_amount or 0.0
        ),
        awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        association_memberships=split_multi_value_text(
            profile.association_memberships
        ),
        tech_fields=split_multi_value_text(profile.tech_fields),
    )


@router.get("/strategy", response_model=OperatorStrategyResponse)
def get_operator_strategy_endpoint(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return the watch strategy for the active operator context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    strategy = ensure_operator_strategy_for(db, target)
    return _build_operator_strategy_response(
        operator=target,
        focus_categories=split_multi_value_text(strategy.focus_categories),
        focus_regions=split_multi_value_text(strategy.focus_regions),
        exclude_regions=split_multi_value_text(strategy.exclude_regions),
        required_keywords=split_multi_value_text(strategy.required_keywords),
        exclude_keywords=split_multi_value_text(strategy.exclude_keywords),
        min_budget_estimate=float(strategy.min_budget_estimate or 0.0),
        max_budget_estimate=float(strategy.max_budget_estimate or 0.0),
        minimum_match_score=float(strategy.minimum_match_score or 0.0),
        minimum_probability_score=float(strategy.minimum_probability_score or 0.0),
        bid_now_threshold=float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD),
        review_threshold=float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD),
        auto_workload_penalty_multiplier=get_strategy_auto_workload_penalty_multiplier(strategy),
        category_priority_overrides=get_strategy_category_priority_overrides(strategy),
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


@router.put("/strategy", response_model=OperatorStrategyResponse)
def update_operator_strategy(
    request: OperatorStrategyUpdate,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Update the active operator's watch rules used for monitoring and prioritization.

    Like :func:`update_operator_profile`, edits are restricted to the
    bearer-token owner — ``?operator_id`` mismatched against the actor returns
    403. Cross-operator strategy edits are not supported.
    """
    actor = _resolve_operator_for_write(db, current_operator, operator_id)
    operator = actor
    strategy = ensure_operator_strategy_for(db, actor)

    next_bid_now_threshold = float(
        request.bid_now_threshold
        if request.bid_now_threshold is not None
        else strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD
    )
    next_review_threshold = float(
        request.review_threshold
        if request.review_threshold is not None
        else strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD
    )
    _validate_strategy_thresholds(
        bid_now_threshold=next_bid_now_threshold,
        review_threshold=next_review_threshold,
    )

    if request.focus_categories is not None:
        strategy.focus_categories = join_multi_value_text(request.focus_categories)
    if request.focus_regions is not None:
        strategy.focus_regions = join_multi_value_text(request.focus_regions)
    if request.exclude_regions is not None:
        strategy.exclude_regions = join_multi_value_text(request.exclude_regions)
    if request.required_keywords is not None:
        strategy.required_keywords = join_multi_value_text(request.required_keywords)
    if request.exclude_keywords is not None:
        strategy.exclude_keywords = join_multi_value_text(request.exclude_keywords)
    if request.min_budget_estimate is not None:
        strategy.min_budget_estimate = request.min_budget_estimate
    if request.max_budget_estimate is not None:
        strategy.max_budget_estimate = request.max_budget_estimate
    if request.minimum_match_score is not None:
        strategy.minimum_match_score = request.minimum_match_score
    if request.minimum_probability_score is not None:
        strategy.minimum_probability_score = request.minimum_probability_score
    if request.bid_now_threshold is not None:
        strategy.bid_now_threshold = request.bid_now_threshold
    if request.review_threshold is not None:
        strategy.review_threshold = request.review_threshold
    if request.auto_workload_penalty_multiplier is not None:
        strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(
            request.auto_workload_penalty_multiplier
        )
    if request.category_priority_overrides is not None:
        strategy.category_priority_overrides = dump_category_priority_overrides(
            request.category_priority_overrides
        )
    if request.notify_only_high_priority is not None:
        strategy.notify_only_high_priority = request.notify_only_high_priority
    if request.max_recommended_candidates is not None:
        strategy.max_recommended_candidates = request.max_recommended_candidates

    db.commit()
    db.refresh(strategy)

    return _build_operator_strategy_response(
        operator=operator,
        focus_categories=split_multi_value_text(strategy.focus_categories),
        focus_regions=split_multi_value_text(strategy.focus_regions),
        exclude_regions=split_multi_value_text(strategy.exclude_regions),
        required_keywords=split_multi_value_text(strategy.required_keywords),
        exclude_keywords=split_multi_value_text(strategy.exclude_keywords),
        min_budget_estimate=float(strategy.min_budget_estimate or 0.0),
        max_budget_estimate=float(strategy.max_budget_estimate or 0.0),
        minimum_match_score=float(strategy.minimum_match_score or 0.0),
        minimum_probability_score=float(strategy.minimum_probability_score or 0.0),
        bid_now_threshold=float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD),
        review_threshold=float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD),
        auto_workload_penalty_multiplier=get_strategy_auto_workload_penalty_multiplier(strategy),
        category_priority_overrides=get_strategy_category_priority_overrides(strategy),
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


@router.get("/strategy/candidates", response_model=OperatorStrategyCandidatesResponse)
def list_operator_strategy_candidates(
    limit: int | None = Query(default=None, ge=1, le=100),
    high_priority_only: bool | None = Query(default=None),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Preview currently open projects that match the active operator's watch strategy."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    payload = StrategyMonitoringService().preview_candidates(
        db,
        limit=limit,
        high_priority_only=high_priority_only,
        operator=target,
    )
    payload.update(_operator_context_fields(target))
    return payload


@router.post("/strategy/monitor", response_model=OperatorStrategyMonitorResponse)
def run_operator_strategy_monitor(
    request: OperatorStrategyMonitorRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Execute the stored strategy, persist bid decisions, and create operator notifications."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return StrategyMonitoringService().execute_monitoring(
        db,
        request=request,
        trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
        operator=target,
    )


@router.get("/strategy/monitor/runs", response_model=OperatorStrategyRunListResponse)
def list_operator_strategy_monitor_runs(
    limit: int = Query(default=20, ge=1, le=100),
    run_status: str | None = Query(default=None, alias="status"),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return recent strategy monitoring execution history for the resolved operator."""
    service = StrategyMonitoringService()
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    runs = service.list_recent_runs(db, limit=limit, run_status=run_status, operator=operator)
    return {
        "operator_id": operator.id,
        **_operator_context_fields(operator),
        "result_count": len(runs),
        "runs": [service.serialize_run(run) for run in runs],
    }


@router.get("/strategy/monitor/runs/{run_id}", response_model=OperatorStrategyRunDetailResponse)
def get_operator_strategy_monitor_run_detail(
    run_id: int,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return one strategy monitor run with full payloads and candidate diff details."""
    service = StrategyMonitoringService()
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    try:
        return service.get_run_detail(db, run_id=run_id, operator=operator)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/strategy/monitor/async", response_model=OperatorStrategyMonitorTaskResponse)
def run_operator_strategy_monitor_async(
    request: OperatorStrategyMonitorRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Queue operator strategy monitoring work and return a pollable task id."""
    service = StrategyMonitoringService()
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    monitor_run = service.create_monitor_run(
        db,
        request=request,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        status="queued",
        operator=operator,
    )
    async_result = enqueue_operator_strategy_monitor(
        request=request,
        monitor_run_id=monitor_run.id,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        operator_id=operator.id,
    )
    monitor_run = service.update_monitor_run_task_id(db, run_id=monitor_run.id, task_id=async_result.id) or monitor_run
    status_payload = get_operator_strategy_monitor_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "monitor_run_id": monitor_run.id,
        "operator_id": operator.id,
        **_operator_context_fields(operator),
        "task_name": status_payload["task_name"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/operator/strategy/monitor/tasks/{async_result.id}",
    }


@router.get("/strategy/monitor/tasks/{task_id}", response_model=OperatorStrategyMonitorTaskStatusResponse)
def get_operator_strategy_monitor_status(
    task_id: str,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Inspect the current status and final result of a queued strategy monitoring task."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    payload = get_operator_strategy_monitor_task_status(task_id)

    monitor_run = (
        db.query(OperatorStrategyRun)
        .filter(OperatorStrategyRun.task_id == task_id)
        .order_by(OperatorStrategyRun.id.desc())
        .first()
    )
    if monitor_run is None and payload.get("monitor_run_id") is not None:
        monitor_run = (
            db.query(OperatorStrategyRun)
            .filter(OperatorStrategyRun.id == int(payload["monitor_run_id"]))
            .first()
        )

    result = payload.get("result")
    result_operator_id = None
    if isinstance(result, dict) and result.get("operator_id") is not None:
        result_operator_id = int(result["operator_id"])

    resolved_owner_id = int(monitor_run.operator_id) if monitor_run is not None else result_operator_id
    if resolved_owner_id is not None and int(resolved_owner_id) != int(operator.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitoring task not found")

    payload["operator_id"] = int(operator.id)
    payload.update(_operator_context_fields(operator))
    return payload


@router.get("/dashboard", response_model=OperatorDashboardResponse)
def get_operator_dashboard(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return a card-ready web dashboard payload for analysis, decisions, monitoring, and feedback."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    def operator_scoped_href(path: str) -> str:
        if operator_id is None:
            return path
        return _append_operator_query(path, int(operator.id))

    overview = _build_operator_overview_payload(operator, days=days, db=db)
    date_from = utc_now() - timedelta(days=days)

    active_decision_count = (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.operator_id == operator.id,
            BidDecisionRecord.decision_status.in_(ACTIVE_DECISION_STATUSES),
        )
        .count()
    )
    failed_monitor_run_count = sum(
        1
        for run in StrategyMonitoringService().list_recent_runs(
            db, limit=100, run_status="failed", operator=operator
        )
        if run.created_at is not None and run.created_at >= date_from
    )
    recent_decisions = (
        db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.operator_id == operator.id)
        .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
        .limit(limit)
        .all()
    )
    recent_monitor_runs = StrategyMonitoringService().list_recent_runs(
        db, limit=limit, operator=operator
    )
    feedback = PredictionFeedbackService().build_feedback(
        db, days=days, limit=limit, operator=operator
    )
    recommendation_error = feedback.get("average_recommendation_error_rate")

    return {
        "operator_id": operator.id,
        "generated_at": utc_now(),
        "period_days": days,
        "overview": overview,
        "cards": _operator_dashboard_cards(
            overview=overview,
            days=days,
            active_decision_count=active_decision_count,
            failed_monitor_run_count=failed_monitor_run_count,
            recommendation_error=recommendation_error,
            operator_scoped_href=operator_scoped_href,
        ),
        "recent_decisions": _operator_dashboard_decisions(recent_decisions),
        "recent_monitor_runs": _operator_dashboard_monitor_runs(
            recent_monitor_runs,
            operator_scoped_href=operator_scoped_href,
        ),
        "feedback_summary": _operator_dashboard_feedback(
            feedback,
            recommendation_error=recommendation_error,
        ),
        "action_hrefs": _operator_dashboard_action_hrefs(operator_scoped_href),
        **_operator_context_fields(operator),
    }


def _operator_dashboard_cards(
    *,
    overview: dict,
    days: int,
    active_decision_count: int,
    failed_monitor_run_count: int,
    recommendation_error,
    operator_scoped_href,
) -> list[dict]:
    return [
        {
            "key": "profile_configured",
            "label": "전략 프로필",
            "value": 1 if overview["profile_configured"] else 0,
            "unit": "state",
            "status": "healthy" if overview["profile_configured"] else "watch",
            "detail": "운영자 프로필이 설정되었습니다." if overview["profile_configured"] else "운영자 프로필 설정이 필요합니다.",
            "href": "/api/v1/operator/profile",
        },
        {
            "key": "active_bid_decisions",
            "label": "진행 중 판단",
            "value": active_decision_count,
            "unit": "count",
            "status": "info" if active_decision_count else "healthy",
            "detail": "planned/reviewing 상태의 입찰 판단 수입니다.",
            "href": "/api/v1/operations/bid-decisions",
        },
        {
            "key": "unread_notifications",
            "label": "미확인 알림",
            "value": overview["unread_notification_count"],
            "unit": "count",
            "status": "watch" if overview["unread_notification_count"] else "healthy",
            "detail": "웹 대시보드에서 확인할 알림 수입니다.",
            "href": "/api/v1/operator/notifications",
        },
        {
            "key": "monitor_failures",
            "label": "모니터링 실패",
            "value": failed_monitor_run_count,
            "unit": "count",
            "status": "critical" if failed_monitor_run_count else "healthy",
            "detail": f"최근 {days}일 내 실패한 전략 모니터링 실행 수입니다.",
            "href": operator_scoped_href("/api/v1/operator/strategy/monitor/runs?status=failed"),
        },
        {
            "key": "recommendation_error_rate",
            "label": "추천 오차율",
            "value": recommendation_error,
            "unit": "ratio",
            "status": _feedback_status(recommendation_error),
            "detail": "낙찰 결과가 연결된 추천 금액의 평균 절대 오차율입니다.",
            "href": "/api/v1/analytics/prediction-feedback",
        },
    ]


def _operator_dashboard_decisions(records) -> list[dict]:
    return [
        {
            "decision_record_id": int(record.id),
            "project_id": int(record.project_id),
            "project_title": record.project.title if record.project is not None else "",
            "action": str(record.action),
            "decision_status": str(record.decision_status),
            "priority_score": float(record.priority_score or 0.0),
            "probability_score": float(record.probability_score or 0.0),
            "recommended_amount": float(record.recommended_amount or 0.0),
            "updated_at": record.updated_at,
            "detail_href": f"/api/v1/operations/bid-decisions/{record.id}",
            "analysis_href": "/api/v1/operations/opportunity-analysis",
        }
        for record in records
    ]


def _operator_dashboard_monitor_runs(records, *, operator_scoped_href) -> list[dict]:
    return [
        {
            "monitor_run_id": int(run.id),
            "status": str(run.status),
            "trigger_source": str(run.trigger_source),
            "persisted_candidate_count": int(run.persisted_candidate_count or 0),
            "notification_count": int(run.notification_count or 0),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "detail_href": operator_scoped_href(f"/api/v1/operator/strategy/monitor/runs/{run.id}"),
        }
        for run in records
    ]


def _operator_dashboard_feedback(feedback: dict, *, recommendation_error) -> dict:
    return {
        "result_count": int(feedback.get("result_count") or 0),
        "prediction_sample_count": int(feedback.get("prediction_sample_count") or 0),
        "recommendation_sample_count": int(feedback.get("recommendation_sample_count") or 0),
        "average_prediction_error_rate": feedback.get("average_prediction_error_rate"),
        "average_recommendation_error_rate": recommendation_error,
        "recommendation_better_than_prediction_count": int(
            feedback.get("recommendation_better_than_prediction_count") or 0
        ),
        "href": "/api/v1/analytics/prediction-feedback",
    }


def _operator_dashboard_action_hrefs(operator_scoped_href) -> dict:
    return {
        "opportunity_analysis": "/api/v1/operations/opportunity-analysis",
        "decision_list": "/api/v1/operations/bid-decisions",
        "strategy_candidates": operator_scoped_href("/api/v1/operator/strategy/candidates"),
        "strategy_monitor": operator_scoped_href("/api/v1/operator/strategy/monitor"),
        "strategy_monitor_runs": operator_scoped_href("/api/v1/operator/strategy/monitor/runs"),
        "prediction_feedback": "/api/v1/analytics/prediction-feedback",
        "operations_dashboard": operator_scoped_href("/api/v1/analytics/operations-dashboard"),
    }


@router.get("/overview", response_model=OperatorOverviewResponse)
def get_operator_overview(
    days: int = Query(7, ge=1, le=90),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return a compact dashboard summary for the active operator context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return _build_operator_overview_payload(target, days=days, db=db)


_SYNTHETIC_USERNAME_PREFIX = "synthetic-"


def _serialize_operator_account(
    user: User,
    *,
    profile: CompanyProfile | None,
) -> OperatorAccountItem:
    """Convert a ``User`` into the compact account row used by the switcher."""
    business_type = profile.business_type if profile is not None else None
    return OperatorAccountItem(
        operator_id=int(user.id),
        username=str(user.username or ""),
        full_name=user.full_name,
        company=user.company,
        business_type=business_type,
        is_canonical=user.username == CANONICAL_OPERATOR_USERNAME,
        is_synthetic=bool(
            user.username and user.username.startswith(_SYNTHETIC_USERNAME_PREFIX)
        ),
        is_active=bool(user.is_active),
        profile_configured=profile is not None
        and _is_profile_configured(
            license_codes=split_multi_value_text(profile.license_codes),
            region_codes=split_multi_value_text(profile.region_codes),
            annual_revenue=float(profile.annual_revenue or 0.0),
            capacity_score=float(profile.capacity_score or 0.0),
            total_awards=int(profile.total_awards or 0),
            construction_capacity_amount=float(
                profile.construction_capacity_amount or 0.0
            ),
            awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        ),
    )


def _serialize_notification_channel(
    channel: OperatorNotificationChannel,
) -> OperatorNotificationChannelItem:
    return OperatorNotificationChannelItem(
        channel_id=int(channel.id),
        operator_id=int(channel.operator_id),
        channel_type=str(channel.channel_type),
        route_key=mask_notification_route_key(channel.route_key),
        target_label=mask_notification_target(channel.target_label),
        is_active=bool(channel.is_active),
        dry_run_only=bool(channel.dry_run_only),
        source="operator_notification_channels",
        verified_at=channel.verified_at,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _notification_channel_item_from_plan(
    plan: dict[str, object],
) -> OperatorNotificationChannelItem:
    channel_id = plan.get("channel_id")
    return OperatorNotificationChannelItem(
        channel_id=int(channel_id) if channel_id is not None else None,
        operator_id=int(plan["operator_id"]),
        channel_type=str(plan.get("channel_type") or ""),
        route_key=str(plan.get("route_key") or ""),
        target_label=(
            str(plan["target_label"]) if plan.get("target_label") is not None else None
        ),
        is_active=bool(plan.get("channel_active")),
        dry_run_only=bool(plan.get("dry_run_only")),
        source=str(plan.get("channel_source") or ""),
    )


@router.get("/accounts", response_model=OperatorAccountListResponse)
def list_operator_accounts(
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """List operator accounts visible to the current bearer-token owner.

    Privileged callers (canonical ``operator`` or ``is_admin``) receive the full
    catalogue (canonical + ``synthetic-*``). Non-privileged callers receive only
    their own row so the dropdown collapses to a single self-pick.
    """
    actor = current_operator or ensure_operator_account(db)
    privileged = is_privileged_operator(actor)

    if privileged:
        users = (
            db.query(User)
            .filter(
                (User.username == CANONICAL_OPERATOR_USERNAME)
                | (User.username.like(f"{_SYNTHETIC_USERNAME_PREFIX}%"))
                | (User.id == actor.id)
            )
            .order_by(User.id.asc())
            .all()
        )
    else:
        users = [actor]

    if not users:
        users = [actor]

    user_ids = [int(user.id) for user in users]
    profile_rows = (
        db.query(CompanyProfile).filter(CompanyProfile.user_id.in_(user_ids)).all()
        if user_ids
        else []
    )
    profile_by_user = {int(row.user_id): row for row in profile_rows}

    operators: list[OperatorAccountItem] = []
    seen_ids: set[int] = set()
    for user in users:
        if int(user.id) in seen_ids:
            continue
        seen_ids.add(int(user.id))
        operators.append(
            _serialize_operator_account(user, profile=profile_by_user.get(int(user.id)))
        )

    return OperatorAccountListResponse(
        current_operator_id=int(actor.id),
        current_operator_username=str(actor.username or ""),
        is_privileged=privileged,
        operator_count=len(operators),
        operators=operators,
    )


@router.get("/notification-channels", response_model=OperatorNotificationChannelListResponse)
def list_operator_notification_channels(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return masked notification route metadata for the resolved operator."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    rows = (
        db.query(OperatorNotificationChannel)
        .filter(OperatorNotificationChannel.operator_id == int(target.id))
        .order_by(
            OperatorNotificationChannel.channel_type.asc(),
            OperatorNotificationChannel.is_active.desc(),
            OperatorNotificationChannel.id.asc(),
        )
        .all()
    )
    channels = [_serialize_notification_channel(row) for row in rows]

    has_telegram_row = any(row.channel_type == "telegram" for row in rows)
    if not has_telegram_row:
        plan = OperatorNotificationService().build_telegram_delivery_plan(
            db, operator_id=int(target.id)
        )
        if plan.get("channel_source") == "legacy_settings":
            channels.append(_notification_channel_item_from_plan(plan))

    return OperatorNotificationChannelListResponse(
        operator_id=int(target.id),
        **_operator_context_fields(target),
        channel_count=len(channels),
        channels=channels,
    )


@router.get("/notifications", response_model=list[NotificationResponse])
def list_operator_notifications(
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    notification_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return recent notifications for the singleton operator's web dashboard."""
    operator = ensure_operator_account(db)
    query = db.query(Notification).filter(Notification.user_id == operator.id)

    if unread_only:
        query = query.filter(Notification.is_read.is_(False))

    if notification_type:
        query = query.filter(Notification.type == notification_type)

    return query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()


@router.put("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_operator_notification_read(notification_id: int, db: Session = Depends(get_db)):
    """Mark a notification as read from the web dashboard."""
    operator = ensure_operator_account(db)
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == operator.id)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    return OperatorNotificationService().mark_as_read(db, notification)


def _reject_synthetic_labeler(operator: User) -> None:
    """synthetic-* 검증 계정의 식별 정답 라벨 write 를 거부한다(403).

    operator 라벨 유니크가 ``(project_id, source="operator")`` 로 operator 차원이
    없어, synthetic operator 제출이 canonical 운영자 피드백과 같은 행을 덮어써
    precision/recall 정답 세트를 오염시킨다. 핵심 불변식(synthetic 데이터를 canonical
    에 섞지 않음, CLAUDE.md §8)을 지키려 정답 세트는 실제 운영자 피드백만 받는다.
    판별은 파일 내 단일 출처 ``_SYNTHETIC_USERNAME_PREFIX`` 를 재사용한다.
    """
    username = operator.username or ""
    if username.startswith(_SYNTHETIC_USERNAME_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="synthetic operator는 식별 정답 라벨을 남길 수 없습니다.",
        )


@router.post("/eligibility-feedback", response_model=EligibilityFeedbackResponse)
def submit_eligibility_feedback(
    request: EligibilityFeedbackRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """운영자 식별 피드백(적합/부적합/보류)을 operator 소스 라벨로 upsert 한다.

    식별 피드백은 투찰 결정(BidDecisionRecord)과 별개 축으로, 어느 공고든
    (eligibility_raw 유무 무관) 저장한다. precision/recall 은 리포트가 rule∩operator
    교집합에서만 산출하므로 rule 라벨이 뒤늦게 backfill 되면 지표에 자동 편입된다.
    정답 세트 오염 방지를 위해 synthetic operator 는 거부한다(canonical 만 허용).
    """
    operator = _resolve_operator_for_write(db, current_operator, operator_id)
    _reject_synthetic_labeler(operator)
    project = (
        db.query(Project).filter(Project.id == request.project_id).one_or_none()
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    label = record_operator_label(
        db, project=project, verdict=request.verdict, operator=operator
    )
    db.commit()
    db.refresh(label)
    return EligibilityFeedbackResponse(
        project_id=int(label.project_id),
        verdict=request.verdict,
        label=str(label.label),
        source=str(label.source),
        rationale=label.rationale,
        labeler_version=label.labeler_version,
        operator_id=int(operator.id),
    )
