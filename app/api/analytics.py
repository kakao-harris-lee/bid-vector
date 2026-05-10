"""Analytics routes"""
from datetime import timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import Analytics, Bid, Project
from app.schemas.schemas import (
    AnalyticsEventRequest,
    AnalyticsSummaryResponse,
    OperatorStatsResponse,
    PredictionFeedbackResponse,
)
from app.services.prediction_feedback import PredictionFeedbackService

router = APIRouter()


@router.post("/event")
def log_event(event: AnalyticsEventRequest, db: Session = Depends(get_db)):
    """Log an analytics event for the singleton operator."""
    operator = ensure_operator_account(db)
    analytics = Analytics(
        user_id=operator.id,
        event_type=event.event_type,
        event_data=str(event.event_data),
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
    total_events = db.query(Analytics).filter(Analytics.user_id == operator.id, Analytics.timestamp >= date_from).count()

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
        (Analytics.user_id == operator_id) & (Analytics.timestamp >= date_from)
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
    db: Session = Depends(get_db),
):
    """Compare stored prediction and recommendation amounts against actual tender results."""
    return PredictionFeedbackService().build_feedback(db, days=days, limit=limit)


@router.get("/user-stats/{user_id}", response_model=OperatorStatsResponse, deprecated=True)
def get_user_stats(user_id: int, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Legacy compatibility alias for the new single-operator stats view."""
    operator = ensure_operator_account(db)
    payload = _build_operator_stats(operator.id, days, db)
    payload["requested_user_id"] = user_id
    return payload
