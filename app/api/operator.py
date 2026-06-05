"""Single-operator profile and overview routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

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
    ensure_operator_profile,
    ensure_operator_strategy,
    join_multi_value_text,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    Bid,
    BidDecisionRecord,
    CompanyProfile,
    Notification,
    PricePrediction,
    Project,
    User,
)
from app.schemas.schemas import (
    NotificationResponse,
    OperatorAccountItem,
    OperatorAccountListResponse,
    OperatorDashboardResponse,
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
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
    get_strategy_auto_workload_penalty_multiplier,
    get_strategy_category_priority_overrides,
)
from app.services.prediction_feedback import PredictionFeedbackService
from app.tasks.jobs import enqueue_operator_strategy_monitor, get_operator_strategy_monitor_task_status

router = APIRouter()
INTERNAL_OPERATOR_EVENT_TYPES = {"telegram.delivery", "telegram.strategy.pending_edit"}


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
    )


def _build_operator_overview_payload(operator: User, *, days: int, db: Session) -> dict:
    """Build the compact dashboard overview shared by overview and dashboard endpoints."""
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
        construction_capacity_amount=float(
            profile.construction_capacity_amount or 0.0
        ),
        awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
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
        bid_now_threshold=float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD),
        review_threshold=float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD),
        auto_workload_penalty_multiplier=get_strategy_auto_workload_penalty_multiplier(strategy),
        category_priority_overrides=get_strategy_category_priority_overrides(strategy),
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


@router.put("/strategy", response_model=OperatorStrategyResponse)
def update_operator_strategy(request: OperatorStrategyUpdate, db: Session = Depends(get_db)):
    """Update the singleton operator's watch rules used for monitoring and prioritization."""
    operator = ensure_operator_account(db)
    strategy = ensure_operator_strategy(db)

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


@router.get("/dashboard", response_model=OperatorDashboardResponse)
def get_operator_dashboard(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Return a card-ready web dashboard payload for analysis, decisions, monitoring, and feedback."""
    operator = ensure_operator_account(db)
    overview = _build_operator_overview_payload(operator, days=days, db=db)
    date_from = utc_now() - timedelta(days=days)

    active_decision_count = (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.operator_id == operator.id,
            BidDecisionRecord.decision_status.in_(["planned", "reviewing"]),
        )
        .count()
    )
    failed_monitor_run_count = sum(
        1
        for run in StrategyMonitoringService().list_recent_runs(db, limit=100, run_status="failed")
        if run.created_at is not None and run.created_at >= date_from
    )
    recent_decisions = (
        db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.operator_id == operator.id)
        .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
        .limit(limit)
        .all()
    )
    recent_monitor_runs = StrategyMonitoringService().list_recent_runs(db, limit=limit)
    feedback = PredictionFeedbackService().build_feedback(
        db, days=days, limit=limit, operator=operator
    )
    recommendation_error = feedback.get("average_recommendation_error_rate")

    return {
        "operator_id": operator.id,
        "generated_at": utc_now(),
        "period_days": days,
        "overview": overview,
        "cards": [
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
                "href": "/api/v1/operator/strategy/monitor/runs?status=failed",
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
        ],
        "recent_decisions": [
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
            for record in recent_decisions
        ],
        "recent_monitor_runs": [
            {
                "monitor_run_id": int(run.id),
                "status": str(run.status),
                "trigger_source": str(run.trigger_source),
                "persisted_candidate_count": int(run.persisted_candidate_count or 0),
                "notification_count": int(run.notification_count or 0),
                "created_at": run.created_at,
                "completed_at": run.completed_at,
                "detail_href": f"/api/v1/operator/strategy/monitor/runs/{run.id}",
            }
            for run in recent_monitor_runs
        ],
        "feedback_summary": {
            "result_count": int(feedback.get("result_count") or 0),
            "prediction_sample_count": int(feedback.get("prediction_sample_count") or 0),
            "recommendation_sample_count": int(feedback.get("recommendation_sample_count") or 0),
            "average_prediction_error_rate": feedback.get("average_prediction_error_rate"),
            "average_recommendation_error_rate": recommendation_error,
            "recommendation_better_than_prediction_count": int(
                feedback.get("recommendation_better_than_prediction_count") or 0
            ),
            "href": "/api/v1/analytics/prediction-feedback",
        },
        "action_hrefs": {
            "opportunity_analysis": "/api/v1/operations/opportunity-analysis",
            "decision_list": "/api/v1/operations/bid-decisions",
            "strategy_candidates": "/api/v1/operator/strategy/candidates",
            "strategy_monitor": "/api/v1/operator/strategy/monitor",
            "strategy_monitor_runs": "/api/v1/operator/strategy/monitor/runs",
            "prediction_feedback": "/api/v1/analytics/prediction-feedback",
            "operations_dashboard": "/api/v1/analytics/operations-dashboard",
        },
    }


@router.get("/overview", response_model=OperatorOverviewResponse)
def get_operator_overview(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    """Return a compact single-user dashboard summary."""
    operator = ensure_operator_account(db)
    return _build_operator_overview_payload(operator, days=days, db=db)


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
