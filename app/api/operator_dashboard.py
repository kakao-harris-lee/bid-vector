"""Dashboard + overview domain impls for the single-operator API.

Pure move (#295 pattern) from ``app/api/operator.py``: the ``@router`` entries
stay in operator.py (thin), forwarding the resolved operator plus the raw
request/query values here. No behaviour change.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.api.operator_common import (
    _append_operator_query,
    _feedback_status,
    _is_profile_configured,
    _operator_context_fields,
)
from app.core.constants import (
    ACTIVE_DECISION_STATUSES,
    INTERNAL_TELEMETRY_EVENT_TYPES as SHARED_INTERNAL_TELEMETRY_EVENT_TYPES,
)
from app.core.single_user import (
    ensure_operator_profile_for,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    Bid,
    BidDecisionRecord,
    Notification,
    PricePrediction,
    Project,
    User,
)
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.prediction_feedback import PredictionFeedbackService

# Single source in app/core/constants.py; kept as a module-level alias so the
# existing filter call site and its name remain stable.
INTERNAL_OPERATOR_EVENT_TYPES = SHARED_INTERNAL_TELEMETRY_EVENT_TYPES


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


def get_operator_dashboard_impl(
    operator: User,
    operator_id: int | None,
    days: int,
    limit: int,
    db: Session,
) -> dict:
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


def get_operator_overview_impl(target: User, days: int, db: Session) -> dict:
    return _build_operator_overview_payload(target, days=days, db=db)
