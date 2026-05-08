"""Single-operator profile and overview routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    join_multi_value_text,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import Analytics, Bid, Notification, PricePrediction, Project, User
from app.schemas.schemas import NotificationResponse, OperatorOverviewResponse, OperatorProfileResponse, OperatorProfileUpdate
from app.services.notifications.manager import OperatorNotificationService

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