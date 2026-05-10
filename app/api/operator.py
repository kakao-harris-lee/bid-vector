"""Single-operator profile and overview routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_strategy,
    join_multi_value_text,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import Analytics, Bid, Notification, PricePrediction, Project, User
from app.schemas.schemas import (
    NotificationResponse,
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
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.notifications.manager import OperatorNotificationService
from app.tasks.jobs import enqueue_operator_strategy_monitor, get_operator_strategy_monitor_task_status

router = APIRouter()


def _is_profile_configured(license_codes: list[str], region_codes: list[str], annual_revenue: float, capacity_score: float, total_awards: int) -> bool:
    return any([
        bool(license_codes),
        bool(region_codes),
        annual_revenue > 0,
        capacity_score > 0,
        total_awards > 0,
    ])


def _build_operator_profile_response(operator: User, license_codes: list[str], region_codes: list[str], business_type: str, annual_revenue: float, capacity_score: float, total_awards: int) -> OperatorProfileResponse:
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
        annual_revenue=annual_revenue,
        capacity_score=capacity_score,
        total_awards=total_awards,
        profile_configured=_is_profile_configured(
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=annual_revenue,
            capacity_score=capacity_score,
            total_awards=total_awards,
        ),
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
        notify_only_high_priority is False,
        max_recommended_candidates != 10,
    ])


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
    notify_only_high_priority: bool,
    max_recommended_candidates: int,
) -> OperatorStrategyResponse:
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
            notify_only_high_priority=notify_only_high_priority,
            max_recommended_candidates=max_recommended_candidates,
        ),
    )


@router.get("/profile", response_model=OperatorProfileResponse)
def get_operator_profile_endpoint(db: Session = Depends(get_db)):
    """Return the singleton operator account and company profile."""
    operator = ensure_operator_account(db)
    profile = ensure_operator_profile(db)
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
    )


@router.put("/profile", response_model=OperatorProfileResponse)
def update_operator_profile(request: OperatorProfileUpdate, db: Session = Depends(get_db)):
    """Update the singleton operator's account and fit profile settings."""
    operator = ensure_operator_account(db)
    profile = ensure_operator_profile(db)

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
    if request.annual_revenue is not None:
        profile.annual_revenue = request.annual_revenue
    if request.capacity_score is not None:
        profile.capacity_score = request.capacity_score
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
    )


@router.get("/strategy", response_model=OperatorStrategyResponse)
def get_operator_strategy_endpoint(db: Session = Depends(get_db)):
    """Return the singleton operator's watch strategy for monitoring and alerting."""
    operator = ensure_operator_account(db)
    strategy = ensure_operator_strategy(db)
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
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


@router.put("/strategy", response_model=OperatorStrategyResponse)
def update_operator_strategy(request: OperatorStrategyUpdate, db: Session = Depends(get_db)):
    """Update the singleton operator's watch rules used for monitoring and prioritization."""
    operator = ensure_operator_account(db)
    strategy = ensure_operator_strategy(db)

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
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


@router.get("/strategy/candidates", response_model=OperatorStrategyCandidatesResponse)
def list_operator_strategy_candidates(
    limit: int | None = Query(default=None, ge=1, le=100),
    high_priority_only: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Preview currently open projects that match the operator's stored watch strategy."""
    return StrategyMonitoringService().preview_candidates(db, limit=limit, high_priority_only=high_priority_only)


@router.post("/strategy/monitor", response_model=OperatorStrategyMonitorResponse)
def run_operator_strategy_monitor(request: OperatorStrategyMonitorRequest, db: Session = Depends(get_db)):
    """Execute the stored strategy, persist bid decisions, and create operator notifications."""
    return StrategyMonitoringService().execute_monitoring(
        db,
        request=request,
        trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
    )


@router.get("/strategy/monitor/runs", response_model=OperatorStrategyRunListResponse)
def list_operator_strategy_monitor_runs(
    limit: int = Query(default=20, ge=1, le=100),
    run_status: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    """Return recent strategy monitoring execution history for the singleton operator."""
    service = StrategyMonitoringService()
    operator = ensure_operator_account(db)
    runs = service.list_recent_runs(db, limit=limit, run_status=run_status)
    return {
        "operator_id": operator.id,
        "result_count": len(runs),
        "runs": [service.serialize_run(run) for run in runs],
    }


@router.get("/strategy/monitor/runs/{run_id}", response_model=OperatorStrategyRunDetailResponse)
def get_operator_strategy_monitor_run_detail(run_id: int, db: Session = Depends(get_db)):
    """Return one strategy monitor run with full payloads and candidate diff details."""
    service = StrategyMonitoringService()
    try:
        return service.get_run_detail(db, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/strategy/monitor/async", response_model=OperatorStrategyMonitorTaskResponse)
def run_operator_strategy_monitor_async(request: OperatorStrategyMonitorRequest, db: Session = Depends(get_db)):
    """Queue operator strategy monitoring work and return a pollable task id."""
    service = StrategyMonitoringService()
    monitor_run = service.create_monitor_run(
        db,
        request=request,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        status="queued",
    )
    async_result = enqueue_operator_strategy_monitor(
        request=request,
        monitor_run_id=monitor_run.id,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
    )
    monitor_run = service.update_monitor_run_task_id(db, run_id=monitor_run.id, task_id=async_result.id) or monitor_run
    status_payload = get_operator_strategy_monitor_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "monitor_run_id": monitor_run.id,
        "task_name": status_payload["task_name"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/operator/strategy/monitor/tasks/{async_result.id}",
    }


@router.get("/strategy/monitor/tasks/{task_id}", response_model=OperatorStrategyMonitorTaskStatusResponse)
def get_operator_strategy_monitor_status(task_id: str):
    """Inspect the current status and final result of a queued strategy monitoring task."""
    return get_operator_strategy_monitor_task_status(task_id)


@router.get("/overview", response_model=OperatorOverviewResponse)
def get_operator_overview(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    """Return a compact single-user dashboard summary."""
    operator = ensure_operator_account(db)
    profile = ensure_operator_profile(db)
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
        "recent_event_count": db.query(Analytics).filter(Analytics.user_id == operator.id, Analytics.timestamp >= date_from).count(),
        "profile_configured": _is_profile_configured(
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=profile.annual_revenue,
            capacity_score=profile.capacity_score,
            total_awards=profile.total_awards,
        ),
    }


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