"""실투찰(real-bid) 트랙 route (얇게 유지 — 로직은 RealBidTrackService).

운영자가 KONEPS 에 실제로 제출한 투찰가를 등록/조회한다. 등록된 실투찰은
개찰 후 낙찰결과 자동 텔레그램(``jobs.notify_award_results``)이 추적한다.
단일 운영자(canonical operator) 스코프이며 기존 bids/decisions API 패턴을 따른다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.schemas.real_bid import (
    RealBidListResponse,
    RealBidRecord,
    RealBidRecordRequest,
)
from app.services.real_bid_track import RealBidNotFoundError, RealBidTrackService

router = APIRouter()


@router.post("/real-bids", response_model=RealBidRecord)
def register_real_bid(
    request: RealBidRecordRequest,
    db: Session = Depends(get_db),
):
    """Register an actual KONEPS submission for the singleton operator."""
    operator = ensure_operator_account(db)
    service = RealBidTrackService()
    try:
        project = service.resolve_project(
            db,
            project_id=request.project_id,
            notice_number=request.notice_number,
        )
    except RealBidNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    record = service.record_real_bid(
        db,
        operator=operator,
        project=project,
        bid_amount=request.bid_amount,
        floor_rate=request.floor_rate,
        submitted_at=request.submitted_at,
        note=request.note,
    )
    return service.serialize_record(db, record)


@router.get("/real-bids", response_model=RealBidListResponse)
def list_real_bids(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List the singleton operator's registered real bids (recent-first)."""
    operator = ensure_operator_account(db)
    service = RealBidTrackService()
    records = service.list_real_bids(db, operator=operator, limit=limit)
    return {
        "operator_id": int(operator.id),
        "count": len(records),
        "records": records,
    }
