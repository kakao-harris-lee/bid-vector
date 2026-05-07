"""Bid routes"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Bid, Project
from app.schemas.schemas import BidCreate, BidResponse, BidUpdate

router = APIRouter()


@router.post("/", response_model=BidResponse)
def submit_bid(bid: BidCreate, user_id: int, db: Session = Depends(get_db)):
    """Submit a bid for a project"""
    # Verify project exists
    project = db.query(Project).filter(Project.id == bid.project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Create bid
    db_bid = Bid(
        project_id=bid.project_id,
        user_id=user_id,
        bid_amount=bid.bid_amount,
        proposed_timeline=bid.proposed_timeline,
        description=bid.description,
    )
    db.add(db_bid)
    db.commit()
    db.refresh(db_bid)

    return db_bid


@router.get("/", response_model=List[BidResponse])
def list_bids(
    skip: int = 0,
    limit: int = 100,
    project_id: int = None,
    status: str = None,
    db: Session = Depends(get_db)
):
    """List bids with optional filters"""
    query = db.query(Bid)

    if project_id:
        query = query.filter(Bid.project_id == project_id)

    if status:
        query = query.filter(Bid.status == status)

    bids = query.offset(skip).limit(limit).all()
    return bids


@router.get("/{bid_id}", response_model=BidResponse)
def get_bid(bid_id: int, db: Session = Depends(get_db)):
    """Get bid details"""
    bid = db.query(Bid).filter(Bid.id == bid_id).first()

    if not bid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bid not found"
        )

    return bid


@router.put("/{bid_id}", response_model=BidResponse)
def update_bid(
    bid_id: int,
    bid_update: BidUpdate,
    user_id: int,
    db: Session = Depends(get_db)
):
    """Update a bid"""
    bid = db.query(Bid).filter(Bid.id == bid_id).first()

    if not bid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bid not found"
        )

    # Verify ownership
    if bid.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this bid"
        )

    # Update fields
    for key, value in bid_update.dict(exclude_unset=True).items():
        setattr(bid, key, value)

    db.commit()
    db.refresh(bid)

    return bid
