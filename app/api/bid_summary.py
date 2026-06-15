"""Bid-decision summary route (투찰 의사결정 요약).

Thin router — aggregation lives in ``BidSummaryService``. Exposes a single
read-only endpoint the operator consults while writing the 나라장터 투찰서 by hand.
Automated submission is out of scope (운영자가 직접 제출).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.bid_summary import BidSummaryResponse
from app.services.bid_summary import BidSummaryService

router = APIRouter()


@router.get(
    "/bid-decisions/{decision_record_id}/summary",
    response_model=BidSummaryResponse,
)
def get_bid_decision_summary(
    decision_record_id: int,
    db: Session = Depends(get_db),
):
    """Aggregate one persisted bid decision into a decision-support summary.

    Returns recommended bid amount/rate, price range, 가격 적합도(추정), reasoning,
    the reference 카테고리 낙찰하한율, optional 분야 통계, notice metadata, and the
    direct-submission notice. 404 when the record id is unknown (or belongs to a
    different operator) / the linked project is missing.
    """
    try:
        return BidSummaryService().build_summary(
            db, decision_record_id=decision_record_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
