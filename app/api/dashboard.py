"""Authenticated mobile dashboard API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import ACTIVE_DECISION_STATUSES
from app.core.database import get_db
from app.core.security import get_current_operator_from_bearer, resolve_target_operator
from app.core.time import utc_now
from app.models.models import Bid, User
from app.schemas.schemas import (
    DashboardBidListResponse,
    DashboardListMeta,
    DashboardOpportunityListResponse,
    DashboardResultListResponse,
    DashboardSummaryResponse,
)
from app.services.dashboard_summary import (
    _build_opportunity_items,
    _load_latest_bid_map,
    _load_latest_decision_map,
    _load_latest_prediction_map,
    _load_latest_result_rows,
    _serialize_bid,
    _serialize_result,
    build_dashboard_summary,
)

router = APIRouter()

# Re-exported for callers/tests that assert the active-status set is the shared
# canonical constant (see tests/test_active_decision_statuses_constant.py).
_ACTIVE_OPPORTUNITY_STATUSES = ACTIVE_DECISION_STATUSES


def _list_meta(
    *, operator: User, generated_at, limit: int, returned_count: int
) -> DashboardListMeta:
    return DashboardListMeta(
        operator_id=int(operator.id),
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
        generated_at=generated_at,
        limit=limit,
        returned_count=returned_count,
    )


@router.get("/opportunities", response_model=DashboardOpportunityListResponse)
def list_dashboard_opportunities(
    status: str
    | None = Query(default=None, pattern="^(planned|reviewing|submitted|skipped)$"),
    limit: int = Query(default=50, ge=1, le=100),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User = Depends(get_current_operator_from_bearer),
):
    """Return bid-decision candidates for the mobile dashboard opportunity tab."""
    operator = resolve_target_operator(db, current_operator, operator_id)
    generated_at = utc_now()
    statuses = {status} if status else None
    items = _build_opportunity_items(
        db,
        operator_id=operator.id,
        limit=limit,
        statuses=statuses,
        now=generated_at,
    )
    return {
        **_list_meta(
            operator=operator,
            generated_at=generated_at,
            limit=limit,
            returned_count=len(items),
        ).model_dump(),
        "items": items,
    }


@router.get("/bids", response_model=DashboardBidListResponse)
def list_dashboard_bids(
    status: str
    | None = Query(default=None, pattern="^(submitted|reviewed|accepted|rejected)$"),
    limit: int = Query(default=50, ge=1, le=100),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User = Depends(get_current_operator_from_bearer),
):
    """Return submitted bids with their latest linked decision record."""
    operator = resolve_target_operator(db, current_operator, operator_id)
    generated_at = utc_now()
    query = db.query(Bid).filter(Bid.user_id == operator.id)
    if status:
        query = query.filter(Bid.status == status)
    bids = query.order_by(Bid.updated_at.desc(), Bid.id.desc()).limit(limit).all()
    decision_map = _load_latest_decision_map(
        db, operator_id=operator.id, project_ids=[bid.project_id for bid in bids]
    )
    items = [
        _serialize_bid(bid, decision=decision_map.get(int(bid.project_id)))
        for bid in bids
    ]
    return {
        **_list_meta(
            operator=operator,
            generated_at=generated_at,
            limit=limit,
            returned_count=len(items),
        ).model_dump(),
        "items": items,
    }


@router.get("/results", response_model=DashboardResultListResponse)
def list_dashboard_results(
    limit: int = Query(default=50, ge=1, le=100),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User = Depends(get_current_operator_from_bearer),
):
    """Return tender results with linked prediction, recommendation, and bid outcome context."""
    operator = resolve_target_operator(db, current_operator, operator_id)
    generated_at = utc_now()
    results = _load_latest_result_rows(db, limit=limit)
    project_ids = [int(result.project_id) for result in results]
    prediction_map = _load_latest_prediction_map(
        db, operator_id=operator.id, project_ids=project_ids
    )
    decision_map = _load_latest_decision_map(
        db, operator_id=operator.id, project_ids=project_ids
    )
    bid_map = _load_latest_bid_map(db, operator_id=operator.id, project_ids=project_ids)
    items = [
        _serialize_result(
            result,
            operator=operator,
            prediction=prediction_map.get(int(result.project_id)),
            decision=decision_map.get(int(result.project_id)),
            bid=bid_map.get(int(result.project_id)),
        )
        for result in results
    ]
    return {
        **_list_meta(
            operator=operator,
            generated_at=generated_at,
            limit=limit,
            returned_count=len(items),
        ).model_dump(),
        "items": items,
    }


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    limit: int = Query(default=5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User = Depends(get_current_operator_from_bearer),
):
    """Return the mobile-first dashboard home payload centered on today's work."""
    operator = resolve_target_operator(db, current_operator, operator_id)
    generated_at = utc_now()
    return build_dashboard_summary(
        db, operator=operator, limit=limit, generated_at=generated_at
    )
