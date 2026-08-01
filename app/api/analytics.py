"""Analytics routes"""
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.constants import INTERNAL_TELEMETRY_EVENT_TYPES as SHARED_INTERNAL_TELEMETRY_EVENT_TYPES
from app.core.database import get_db
from app.core.security import get_current_operator_from_bearer, get_current_operator_optional
from app.core.single_user import ensure_operator_account, resolve_read_operator
from app.core.time import utc_now
from app.models.models import Analytics, Bid, BidDecisionRecord, Project, User
from app.schemas.analytics_events import AnalyticsEventEnvelope
from app.schemas.schemas import (
    AccuracyReportResponse,
    AnalyticsEventRequest,
    DecisionRecommendationResponse,
    DecisionExperimentRunCreateRequest,
    DecisionExperimentRunDetailResponse,
    DecisionExperimentRunListResponse,
    DecisionExperimentStrategyApplyRequest,
    DecisionExperimentStrategyApplyResponse,
    DecisionExperimentRunUpdateRequest,
    DecisionExperimentThresholdApplyRequest,
    DecisionExperimentThresholdApplyResponse,
    DecisionFunnelResponse,
    G2EvidenceSummaryResponse,
    AnalyticsSummaryResponse,
    DecisionInsightsResponse,
    MLTaskResponse,
    OperatorStatsResponse,
    OperationsDashboardResponse,
    OperationsKpiResponse,
    PredictionFeedbackResponse,
    PredictionObservabilityResponse,
    RecommendationFeedbackLabelsResponse,
)
from app.services.accuracy_integration import AccuracyIntegrationService
from app.services.analytics_event_payload import dump_analytics_event
from app.services.analytics_reporting import AnalyticsReportingService
from app.services.decision_analytics import DecisionAnalyticsService
from app.services.decision_experiments import DecisionExperimentService
from app.services.prediction_feedback import PredictionFeedbackService
from app.services.prediction_reporting import PredictionReportingService
from app.tasks.jobs import (
    enqueue_decision_experiment_reevaluation,
    get_decision_experiment_reevaluation_task_status,
)

router = APIRouter()

# Single source in app/core/constants.py; kept as a module-level alias so the
# existing filter call sites and their name remain stable.
INTERNAL_TELEMETRY_EVENT_TYPES = SHARED_INTERNAL_TELEMETRY_EVENT_TYPES


# Read-scoped operator resolution is shared across analytics/operator/decision
# endpoints; see app.core.single_user.resolve_read_operator for the security
# policy (canonical fallback / 403 / 404). Kept as a thin alias so existing
# call sites and their names remain stable.
_resolve_analytics_operator = resolve_read_operator


def _with_current_operator(payload: dict, operator) -> dict:
    """Inject ``current_operator_id`` / ``current_operator_username`` into the response.

    Analytics service payloads carry ``operator_id`` already; this helper just
    makes the actor explicit so the frontend can render which company is
    currently shown in the dropdown.
    """
    payload["current_operator_id"] = int(operator.id)
    payload["current_operator_username"] = str(operator.username or "")
    return payload


def _raise_decision_experiment_http_error(exc: ValueError) -> None:
    detail = str(exc)
    status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/event")
def log_event(
    event: AnalyticsEventRequest,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Log one client telemetry event, attributed to the authenticated operator."""
    # 유일한 *쓰기* 경계라 bearer 필수(읽기의 익명 → canonical 폴백과 다르다) · 귀속은 토큰
    # 소유자. 그 분리의 범위/한계와 payload 계약은 ``AnalyticsEventRequest`` 주석이 소유한다.
    analytics = Analytics(
        user_id=operator.id,
        event_type=event.event_type,
        event_data=dump_analytics_event(
            AnalyticsEventEnvelope.model_validate(event.event_data)
        ),
    )
    db.add(analytics)
    db.commit()

    return {"status": "logged"}


@router.get("/summary", response_model=AnalyticsSummaryResponse)
def get_analytics_summary(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db)
):
    """Get repository-wide analytics summary for the singleton workflow."""
    operator = ensure_operator_account(db)
    date_from = utc_now() - timedelta(days=days)

    total_bids = db.query(Bid).filter(Bid.user_id == operator.id, Bid.created_at >= date_from).count()
    total_projects = db.query(Project).filter(Project.created_at >= date_from).count()
    total_events = (
        db.query(Analytics)
        .filter(
            Analytics.user_id == operator.id,
            Analytics.timestamp >= date_from,
            ~Analytics.event_type.in_(INTERNAL_TELEMETRY_EVENT_TYPES),
        )
        .count()
    )

    return {
        "operator_id": operator.id,
        "period_days": days,
        "total_bids": total_bids,
        "total_projects": total_projects,
        "total_events": total_events,
        "mode": "single_operator",
    }


def _build_operator_stats(operator_id: int, days: int, db: Session) -> dict:
    date_from = utc_now() - timedelta(days=days)

    total_bids = db.query(Bid).filter(
        (Bid.user_id == operator_id) & (Bid.created_at >= date_from)
    ).count()

    total_events = db.query(Analytics).filter(
        (Analytics.user_id == operator_id)
        & (Analytics.timestamp >= date_from)
        & (~Analytics.event_type.in_(INTERNAL_TELEMETRY_EVENT_TYPES))
    ).count()

    return {
        "operator_id": operator_id,
        "period_days": days,
        "total_bids": total_bids,
        "total_events": total_events,
        "bids_count": db.query(Bid).filter(Bid.user_id == operator_id).count(),
        "mode": "single_operator",
    }


@router.get("/operator-stats", response_model=OperatorStatsResponse)
def get_operator_stats(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Get singleton operator statistics."""
    operator = ensure_operator_account(db)
    return _build_operator_stats(operator.id, days, db)


@router.get("/prediction-feedback", response_model=PredictionFeedbackResponse)
def get_prediction_feedback(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Compare stored prediction and recommendation amounts against actual tender results."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = PredictionFeedbackService().build_feedback(
        db,
        days=days,
        limit=limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/prediction-observability", response_model=PredictionObservabilityResponse)
def get_prediction_observability(
    days: int = Query(90, ge=1, le=365),
    trend_bucket_days: int = Query(14, ge=1, le=90),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Summarize predictor selection, fallback, guardrails, and result accuracy."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = PredictionReportingService().build_observability(
        db,
        days=days,
        trend_bucket_days=trend_bucket_days,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/accuracy-report", response_model=AccuracyReportResponse)
def get_accuracy_report(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(500, ge=1, le=2000),
    trend_bucket_days: int = Query(7, ge=1, le=30),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Consolidate 추천가 vs 실제 낙찰가 정확도 (정산 완료 건만, 단일 운영자 기준)."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = AccuracyIntegrationService().build_accuracy_report(
        db,
        days=days,
        limit=limit,
        trend_bucket_days=trend_bucket_days,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/operations-dashboard", response_model=OperationsDashboardResponse)
def get_operations_dashboard(
    days: int = Query(30, ge=1, le=365),
    recent_limit: int = Query(5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return dashboard cards for crawl health and strategy monitoring performance."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = AnalyticsReportingService().build_operations_dashboard(
        db,
        days=days,
        recent_limit=recent_limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/g2-evidence", response_model=G2EvidenceSummaryResponse)
def get_g2_evidence_summary(
    days: int = Query(30, ge=1, le=365),
    recent_limit: int = Query(5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return one operator-scoped evidence ledger for G-2 exit review."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = AnalyticsReportingService().build_g2_evidence_summary(
        db,
        window_days=days,
        recent_limit=recent_limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/decision-insights", response_model=DecisionInsightsResponse)
def get_decision_insights(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Summarize persisted bid-decision signals for tuning and operator review."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = DecisionAnalyticsService().build_insights(
        db,
        days=days,
        limit=limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/decision-funnel", response_model=DecisionFunnelResponse)
def get_decision_funnel(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    breakdown_limit: int = Query(5, ge=1, le=20),
    trend_bucket_days: int = Query(7, ge=1, le=30),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Summarize how persisted decision records progress through the operator workflow."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = DecisionAnalyticsService().build_funnel(
        db,
        days=days,
        limit=limit,
        breakdown_limit=breakdown_limit,
        trend_bucket_days=trend_bucket_days,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/operations-kpi", response_model=OperationsKpiResponse)
def get_operations_kpi(
    days: int = Query(30, ge=1, le=365),
    missed_limit: int = Query(10, ge=1, le=50),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Aggregate roadmap operating KPIs (manual override, conversion, accuracy, missed) in one call."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = DecisionAnalyticsService().build_operations_kpi(
        db,
        days=days,
        missed_limit=missed_limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get(
    "/recommendation-feedback-labels",
    response_model=RecommendationFeedbackLabelsResponse,
)
def get_recommendation_feedback_labels(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(100, ge=1, le=500),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Export deduped recommendation-feedback labels joined with decision/project context."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = DecisionAnalyticsService().build_recommendation_feedback_labels(
        db,
        days=days,
        limit=limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.get("/decision-recommendations", response_model=DecisionRecommendationResponse)
def get_decision_recommendations(
    days: int = Query(30, ge=1, le=365),
    breakdown_limit: int = Query(5, ge=1, le=20),
    trend_bucket_days: int = Query(7, ge=1, le=30),
    recommendation_limit: int = Query(5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return actionable tuning recommendations derived from the decision funnel analytics."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    payload = DecisionAnalyticsService().build_recommendations(
        db,
        days=days,
        breakdown_limit=breakdown_limit,
        trend_bucket_days=trend_bucket_days,
        recommendation_limit=recommendation_limit,
        operator=target_operator,
    )
    return _with_current_operator(payload, target_operator)


@router.post("/decision-experiments", response_model=DecisionExperimentRunDetailResponse)
def create_decision_experiment_run(
    request: DecisionExperimentRunCreateRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Persist one experiment plan so the operator can track execution and later evaluate outcomes."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    return DecisionExperimentService().create_run(db, request=request, operator=target_operator)


@router.get("/decision-experiments", response_model=DecisionExperimentRunListResponse)
def list_decision_experiment_runs(
    limit: int = Query(20, ge=1, le=100),
    run_status: str | None = Query(default=None, alias="status"),
    outcome: str | None = Query(default=None),
    application_status: str | None = Query(default=None),
    sort: str = Query(default="needs_attention"),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return recent decision experiment runs for dashboard status tracking."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    return DecisionExperimentService().list_runs(
        db,
        limit=limit,
        run_status=run_status,
        outcome=outcome,
        application_status=application_status,
        sort=sort,
        operator=target_operator,
    )


@router.get("/decision-experiments/{experiment_run_id}", response_model=DecisionExperimentRunDetailResponse)
def get_decision_experiment_run_detail(
    experiment_run_id: int,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return one persisted experiment run with its baseline snapshot and latest evaluation."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    try:
        return DecisionExperimentService().get_run_detail(db, run_id=experiment_run_id, operator=target_operator)
    except ValueError as exc:
        _raise_decision_experiment_http_error(exc)


@router.post(
    "/decision-experiments/{experiment_run_id}/evaluate",
    response_model=MLTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def evaluate_decision_experiment_run(
    experiment_run_id: int,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Queue experiment re-evaluation instead of running it inside the API request."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    try:
        DecisionExperimentService().get_run_detail(db, run_id=experiment_run_id, operator=target_operator)
    except ValueError as exc:
        _raise_decision_experiment_http_error(exc)
    async_result = enqueue_decision_experiment_reevaluation(
        experiment_run_id=experiment_run_id,
        operator_id=int(target_operator.id),
    )
    status_payload = get_decision_experiment_reevaluation_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "queue": status_payload["queue"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/ml/reevaluations/decision-experiments/tasks/{async_result.id}",
    }


@router.patch("/decision-experiments/{experiment_run_id}", response_model=DecisionExperimentRunDetailResponse)
def update_decision_experiment_run(
    experiment_run_id: int,
    request: DecisionExperimentRunUpdateRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Manually update a persisted experiment run's notes or lifecycle state."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    try:
        return DecisionExperimentService().update_run(
            db,
            run_id=experiment_run_id,
            request=request,
            operator=target_operator,
        )
    except ValueError as exc:
        _raise_decision_experiment_http_error(exc)


@router.post(
    "/decision-experiments/{experiment_run_id}/apply-thresholds",
    response_model=DecisionExperimentThresholdApplyResponse,
)
def apply_decision_experiment_thresholds(
    experiment_run_id: int,
    request: DecisionExperimentThresholdApplyRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Apply one successful experiment's threshold recommendation to the operator strategy."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    try:
        return DecisionExperimentService().apply_threshold_adjustments(
            db,
            run_id=experiment_run_id,
            request=request,
            operator=target_operator,
        )
    except ValueError as exc:
        _raise_decision_experiment_http_error(exc)


@router.post(
    "/decision-experiments/{experiment_run_id}/apply-strategy",
    response_model=DecisionExperimentStrategyApplyResponse,
)
def apply_decision_experiment_strategy(
    experiment_run_id: int,
    request: DecisionExperimentStrategyApplyRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Apply one successful experiment's workload/category tuning to the operator strategy."""
    target_operator = _resolve_analytics_operator(db, current_operator, operator_id)
    try:
        return DecisionExperimentService().apply_strategy_adjustments(
            db,
            run_id=experiment_run_id,
            request=request,
            operator=target_operator,
        )
    except ValueError as exc:
        _raise_decision_experiment_http_error(exc)


@router.get("/user-stats/{user_id}", response_model=OperatorStatsResponse, deprecated=True)
def get_user_stats(user_id: int, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Legacy compatibility alias for the new single-operator stats view."""
    operator = ensure_operator_account(db)
    payload = _build_operator_stats(operator.id, days, db)
    payload["requested_user_id"] = user_id
    return payload
