"""Bid routes"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.models.models import Bid, BidDecisionRecord, Project
from app.schemas.schemas import BidCreate, BidResponse, BidUpdate
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService

router = APIRouter()


def _serialize_bid_response(bid: Bid, decision_record: BidDecisionRecord | None) -> dict:
    """Build an operator-first bid response while keeping legacy fields available."""
    return {
        "id": bid.id,
        "project_id": bid.project_id,
        "operator_id": bid.user_id,
        "user_id": bid.user_id,
        "bid_amount": bid.bid_amount,
        "proposed_timeline": bid.proposed_timeline,
        "status": bid.status,
        "decision_record_id": decision_record.id if decision_record else None,
        "decision_status": decision_record.decision_status if decision_record else None,
        "score": bid.score,
        "created_at": bid.created_at,
    }


def _load_latest_decision_map(db: Session, project_ids: List[int], operator_id: int) -> dict[int, BidDecisionRecord]:
    """Load the newest decision record for each project in one query."""
    if not project_ids:
        return {}

    records = (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.project_id.in_(project_ids),
        )
        .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
        .all()
    )

    decision_map: dict[int, BidDecisionRecord] = {}
    for record in records:
        decision_map.setdefault(record.project_id, record)
    return decision_map


@router.post("/", response_model=BidResponse)
def submit_bid(bid: BidCreate, db: Session = Depends(get_db)):
    """Submit a bid for the singleton operator account."""
    project = db.query(Project).filter(Project.id == bid.project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    operator = ensure_operator_account(db)
    decision_service = BidDecisionService()
    db_bid = Bid(
        project_id=bid.project_id,
        user_id=operator.id,
        bid_amount=bid.bid_amount,
        proposed_timeline=bid.proposed_timeline,
        description=bid.description,
    )
    db.add(db_bid)
    decision_record = decision_service.sync_submitted_bid(
        db,
        project_id=bid.project_id,
        operator_id=operator.id,
        bid_amount=bid.bid_amount,
    )
    db.commit()
    db.refresh(db_bid)
    db.refresh(decision_record)
    OperatorNotificationService().create_bid_submission_notification(
        db,
        operator_id=operator.id,
        project=project,
        bid=db_bid,
        decision_record=decision_record,
    )

    return _serialize_bid_response(db_bid, decision_record)


@router.get("/", response_model=List[BidResponse])
def list_bids(
    skip: int = 0,
    limit: int = 100,
    project_id: int = None,
    status: str = None,
    db: Session = Depends(get_db)
):
    """List bids for the singleton operator with optional filters."""
    operator = ensure_operator_account(db)
    query = db.query(Bid).filter(Bid.user_id == operator.id)

    if project_id:
        query = query.filter(Bid.project_id == project_id)

    if status:
        query = query.filter(Bid.status == status)

    bids = query.offset(skip).limit(limit).all()
    decision_map = _load_latest_decision_map(db, [bid.project_id for bid in bids], operator.id)
    return [_serialize_bid_response(bid, decision_map.get(bid.project_id)) for bid in bids]


@router.get("/{bid_id}", response_model=BidResponse)
def get_bid(bid_id: int, db: Session = Depends(get_db)):
    """Get bid details for the singleton operator."""
    operator = ensure_operator_account(db)
    bid = db.query(Bid).filter(Bid.id == bid_id, Bid.user_id == operator.id).first()

    if not bid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bid not found"
        )

    decision_map = _load_latest_decision_map(db, [bid.project_id], operator.id)
    return _serialize_bid_response(bid, decision_map.get(bid.project_id))


@router.put("/{bid_id}", response_model=BidResponse)
def update_bid(
    bid_id: int,
    bid_update: BidUpdate,
    db: Session = Depends(get_db)
):
    """Update a bid owned by the singleton operator."""
    operator = ensure_operator_account(db)
    decision_service = BidDecisionService()
    bid = db.query(Bid).filter(Bid.id == bid_id, Bid.user_id == operator.id).first()

    if not bid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bid not found"
        )

    for key, value in bid_update.dict(exclude_unset=True).items():
        setattr(bid, key, value)

    decision_record = decision_service.sync_submitted_bid(
        db,
        project_id=bid.project_id,
        operator_id=operator.id,
        bid_amount=bid.bid_amount,
    )
    db.commit()
    db.refresh(bid)
    db.refresh(decision_record)
    project = db.query(Project).filter(Project.id == bid.project_id).first()
    if project:
        OperatorNotificationService().create_bid_submission_notification(
            db,
            operator_id=operator.id,
            project=project,
            bid=bid,
            decision_record=decision_record,
        )

    return _serialize_bid_response(bid, decision_record)
