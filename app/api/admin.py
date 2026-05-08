"""Admin routes"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.models.models import User, Bid, Project
from app.schemas.schemas import LegacyAdminActionResponse, LegacyAdminStatsResponse, UserResponse

router = APIRouter()


@router.get("/users", response_model=List[UserResponse])
def list_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Legacy compatibility endpoint returning the singleton operator snapshot."""
    operator = ensure_operator_account(db)
    if skip > 0 or limit <= 0:
        return []
    return [operator]


@router.get("/stats", response_model=LegacyAdminStatsResponse)
def get_system_stats(db: Session = Depends(get_db)):
    """Legacy compatibility endpoint returning singleton operator system stats."""
    operator = ensure_operator_account(db)
    total_projects = db.query(Project).count()
    total_bids = db.query(Bid).filter(Bid.user_id == operator.id).count()
    active_users = 1 if operator.is_active else 0

    return {
        "operator_id": operator.id,
        "total_users": 1,
        "active_users": active_users,
        "total_projects": total_projects,
        "total_bids": total_bids,
        "mode": "single_operator",
    }


@router.put("/users/{user_id}/deactivate", response_model=LegacyAdminActionResponse)
def deactivate_user(user_id: int, db: Session = Depends(get_db)):
    """Legacy compatibility endpoint for deactivating the singleton operator."""
    operator = ensure_operator_account(db)

    if operator.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Only the singleton operator account is available in single-user mode",
        )

    operator.is_active = False
    db.commit()

    return {
        "status": "operator deactivated",
        "operator_id": operator.id,
        "requested_user_id": user_id,
        "mode": "single_operator",
    }
