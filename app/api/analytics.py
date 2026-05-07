"""Analytics routes"""
from typing import List
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Analytics, Bid, Project
from app.schemas.schemas import AnalyticsEventRequest

router = APIRouter()


@router.post("/event")
def log_event(event: AnalyticsEventRequest, user_id: int = None, db: Session = Depends(get_db)):
    """Log an analytics event"""
    analytics = Analytics(
        user_id=user_id,
        event_type=event.event_type,
        event_data=str(event.event_data),
    )
    db.add(analytics)
    db.commit()

    return {"status": "logged"}


@router.get("/summary")
def get_analytics_summary(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db)
):
    """Get analytics summary for specified period"""
    date_from = datetime.utcnow() - timedelta(days=days)

    # Count events
    total_bids = db.query(Bid).filter(Bid.created_at >= date_from).count()
    total_projects = db.query(Project).filter(Project.created_at >= date_from).count()
    total_events = db.query(Analytics).filter(Analytics.timestamp >= date_from).count()

    return {
        "period_days": days,
        "total_bids": total_bids,
        "total_projects": total_projects,
        "total_events": total_events,
    }


@router.get("/user-stats/{user_id}")
def get_user_stats(user_id: int, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Get user-specific statistics"""
    date_from = datetime.utcnow() - timedelta(days=days)

    # Count user activities
    user_bids = db.query(Bid).filter(
        (Bid.user_id == user_id) & (Bid.created_at >= date_from)
    ).count()

    user_events = db.query(Analytics).filter(
        (Analytics.user_id == user_id) & (Analytics.timestamp >= date_from)
    ).count()

    # Calculate average bid amount
    avg_bid = db.query(Bid).filter(Bid.user_id == user_id).count()

    return {
        "user_id": user_id,
        "period_days": days,
        "total_bids": user_bids,
        "total_events": user_events,
        "bids_count": avg_bid,
    }
