"""Authenticated mobile dashboard API routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_operator_from_bearer
from app.core.time import ensure_utc, utc_now
from app.models.models import (
    Bid,
    BidDecisionRecord,
    OperatorStrategyRun,
    PaperBid,
    PaperBidRun,
    PricePrediction,
    Project,
    TenderResult,
    User,
)
from app.schemas.schemas import (
    DashboardBidListResponse,
    DashboardListMeta,
    DashboardOpportunityListResponse,
    DashboardResultListResponse,
    DashboardSummaryResponse,
)

router = APIRouter()

_OPPORTUNITY_STATUSES = {"planned", "reviewing", "submitted", "skipped"}
_ACTIVE_OPPORTUNITY_STATUSES = {"planned", "reviewing"}
_BID_STATUSES = {"submitted", "reviewed", "accepted", "rejected"}
_PAPER_ACTION_STATUS = {"bid_now": "planned", "review": "reviewing", "skip": "skipped"}
_DEFAULT_PAPER_OPPORTUNITY_ACTIONS = {"bid_now", "review"}


def _project_brief(project: Project | None) -> dict:
    """Serialize the shared compact project shape used by all dashboard tabs."""
    return {
        "project_id": (
            int(project.id) if project is not None and project.id is not None else 0
        ),
        "title": (
            project.title
            if project is not None and project.title
            else "Untitled project"
        ),
        "category": project.category if project is not None else None,
        "notice_number": project.notice_number if project is not None else None,
        "issuing_agency": project.issuing_agency if project is not None else None,
        "demand_agency": project.demand_agency if project is not None else None,
        "budget_estimate": (
            float(project.budget_estimate or 0.0) if project is not None else 0.0
        ),
        "deadline": project.deadline if project is not None else None,
        "status": str(project.status or "open") if project is not None else "unknown",
    }


def _normalize_opportunity_status(status_value: str | None) -> str:
    normalized = str(status_value or "planned")
    if normalized in _OPPORTUNITY_STATUSES:
        return normalized
    return "reviewing"


def _normalize_action(
    action_value: str | None, *, pursue_bid: bool | None = None
) -> str:
    normalized = str(action_value or "")
    if normalized in {"bid_now", "review", "skip"}:
        return normalized
    return "review" if pursue_bid else "skip"


def _normalize_bid_status(status_value: str | None) -> str:
    normalized = str(status_value or "submitted")
    if normalized in _BID_STATUSES:
        return normalized
    return "submitted"


def _paper_status_from_action(action_value: str | None) -> str:
    return _PAPER_ACTION_STATUS.get(str(action_value or ""), "reviewing")


def _hours_until(deadline, *, now) -> int | None:
    if deadline is None:
        return None
    return int((ensure_utc(deadline) - now).total_seconds() // 3600)


def _round_optional(value: float | None, *, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _compute_delta(
    candidate_amount: float | None, winning_amount: float
) -> float | None:
    if candidate_amount is None or winning_amount <= 0:
        return None
    return candidate_amount - winning_amount


def _compute_error_rate(
    candidate_amount: float | None, winning_amount: float
) -> float | None:
    if candidate_amount is None or winning_amount <= 0:
        return None
    return abs(candidate_amount - winning_amount) / winning_amount


def _load_latest_decision_map(
    db: Session, *, operator_id: int, project_ids: Iterable[int]
) -> dict[int, BidDecisionRecord]:
    project_id_list = sorted(
        {int(project_id) for project_id in project_ids if project_id is not None}
    )
    if not project_id_list:
        return {}

    records = (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.project_id.in_(project_id_list),
        )
        .order_by(
            BidDecisionRecord.project_id.asc(),
            BidDecisionRecord.updated_at.desc(),
            BidDecisionRecord.id.desc(),
        )
        .all()
    )
    latest_by_project: dict[int, BidDecisionRecord] = {}
    for record in records:
        latest_by_project.setdefault(int(record.project_id), record)
    return latest_by_project


def _load_latest_prediction_map(
    db: Session, *, operator_id: int, project_ids: Iterable[int]
) -> dict[int, PricePrediction]:
    project_id_list = sorted(
        {int(project_id) for project_id in project_ids if project_id is not None}
    )
    if not project_id_list:
        return {}

    predictions = (
        db.query(PricePrediction)
        .filter(
            PricePrediction.user_id == operator_id,
            PricePrediction.project_id.in_(project_id_list),
        )
        .order_by(
            PricePrediction.project_id.asc(),
            PricePrediction.created_at.desc(),
            PricePrediction.id.desc(),
        )
        .all()
    )
    latest_by_project: dict[int, PricePrediction] = {}
    for prediction in predictions:
        latest_by_project.setdefault(int(prediction.project_id), prediction)
    return latest_by_project


def _load_latest_result_map(
    db: Session, *, project_ids: Iterable[int]
) -> dict[int, TenderResult]:
    project_id_list = sorted(
        {int(project_id) for project_id in project_ids if project_id is not None}
    )
    if not project_id_list:
        return {}

    results = (
        db.query(TenderResult)
        .filter(
            TenderResult.project_id.in_(project_id_list),
            TenderResult.winning_amount.isnot(None),
            TenderResult.winning_amount > 0,
        )
        .order_by(
            TenderResult.project_id.asc(),
            TenderResult.announced_at.desc().nullslast(),
            TenderResult.created_at.desc(),
            TenderResult.id.desc(),
        )
        .all()
    )
    latest_by_project: dict[int, TenderResult] = {}
    for result in results:
        latest_by_project.setdefault(int(result.project_id), result)
    return latest_by_project


def _load_latest_bid_map(
    db: Session, *, operator_id: int, project_ids: Iterable[int]
) -> dict[int, Bid]:
    project_id_list = sorted(
        {int(project_id) for project_id in project_ids if project_id is not None}
    )
    if not project_id_list:
        return {}

    bids = (
        db.query(Bid)
        .filter(Bid.user_id == operator_id, Bid.project_id.in_(project_id_list))
        .order_by(Bid.project_id.asc(), Bid.updated_at.desc(), Bid.id.desc())
        .all()
    )
    latest_by_project: dict[int, Bid] = {}
    for bid in bids:
        latest_by_project.setdefault(int(bid.project_id), bid)
    return latest_by_project


def _serialize_opportunity(record: BidDecisionRecord, *, now) -> dict:
    project = record.project
    deadline_hours = (
        int(record.deadline_hours_remaining)
        if record.deadline_hours_remaining is not None
        else _hours_until(project.deadline if project is not None else None, now=now)
    )
    return {
        "source": "decision",
        "source_label": "입찰 판단",
        "decision_record_id": int(record.id),
        "paper_bid_id": None,
        "project": _project_brief(project),
        "action": _normalize_action(record.action, pursue_bid=record.pursue_bid),
        "decision_status": _normalize_opportunity_status(record.decision_status),
        "recommended_amount": float(record.recommended_amount or 0.0),
        "probability_score": float(record.probability_score or 0.0),
        "matched_score": float(record.matched_score or 0.0),
        "priority_score": float(record.priority_score or 0.0),
        "urgency_score": float(record.urgency_score or 0.0),
        "deadline_hours_remaining": deadline_hours,
        "reasoning": record.reasoning or "",
        "updated_at": record.updated_at,
        "detail_href": f"/api/v1/operations/bid-decisions/{record.id}",
    }


def _serialize_paper_opportunity(paper_bid: PaperBid, *, now) -> dict:
    project = paper_bid.project
    action = _normalize_action(paper_bid.action)
    return {
        "source": "paper_bid",
        "source_label": "페이퍼 후보",
        "decision_record_id": None,
        "paper_bid_id": int(paper_bid.id),
        "project": _project_brief(project),
        "action": action,
        "decision_status": _paper_status_from_action(action),
        "recommended_amount": float(paper_bid.paper_bid_amount or 0.0),
        "probability_score": float(paper_bid.probability_score or 0.0),
        "matched_score": float(paper_bid.matched_score or 0.0),
        "priority_score": float(paper_bid.priority_score or 0.0),
        "urgency_score": 0.0,
        "deadline_hours_remaining": _hours_until(
            project.deadline if project is not None else None, now=now
        ),
        "reasoning": paper_bid.reasoning or "",
        "updated_at": paper_bid.created_at,
        "detail_href": f"/api/v1/backtests/paper-bidding/runs/{paper_bid.run_id}",
    }


def _serialize_bid(bid: Bid, *, decision: BidDecisionRecord | None) -> dict:
    return {
        "bid_id": int(bid.id),
        "project": _project_brief(bid.project),
        "decision_record_id": int(decision.id) if decision is not None else None,
        "decision_status": (
            _normalize_opportunity_status(decision.decision_status)
            if decision is not None
            else None
        ),
        "bid_amount": float(bid.bid_amount or 0.0),
        "recommended_amount": (
            float(decision.recommended_amount)
            if decision is not None and decision.recommended_amount is not None
            else None
        ),
        "proposed_timeline": int(bid.proposed_timeline or 0),
        "status": _normalize_bid_status(bid.status),
        "score": float(bid.score) if bid.score is not None else None,
        "submitted_at": bid.created_at,
        "updated_at": bid.updated_at,
        "detail_href": f"/api/v1/bids/{bid.id}",
    }


def _resolve_award_outcome(
    result: TenderResult, *, operator: User, bid: Bid | None
) -> str:
    if bid is not None and _normalize_bid_status(bid.status) == "accepted":
        return "won"

    winning_company = (result.winning_company or "").strip().lower()
    operator_company = (operator.company or "").strip().lower()
    if (
        winning_company
        and operator_company
        and (winning_company in operator_company or operator_company in winning_company)
    ):
        return "won"

    if bid is not None and str(result.result_status or "").lower() in {
        "awarded",
        "closed",
    }:
        return "lost"
    return "unknown"


def _serialize_result(
    result: TenderResult,
    *,
    operator: User,
    prediction: PricePrediction | None,
    decision: BidDecisionRecord | None,
    bid: Bid | None,
) -> dict:
    winning_amount = float(result.winning_amount or 0.0)
    predicted_price = (
        float(prediction.predicted_price)
        if prediction is not None and prediction.predicted_price is not None
        else None
    )
    recommended_amount = (
        float(decision.recommended_amount)
        if decision is not None and decision.recommended_amount is not None
        else None
    )
    prediction_delta = _compute_delta(predicted_price, winning_amount)
    recommendation_delta = _compute_delta(recommended_amount, winning_amount)

    return {
        "tender_result_id": int(result.id),
        "project": _project_brief(result.project),
        "winning_company": result.winning_company,
        "winning_amount": winning_amount,
        "winning_rate": float(result.winning_rate or 0.0),
        "result_status": str(result.result_status or "pending"),
        "award_outcome": _resolve_award_outcome(result, operator=operator, bid=bid),
        "announced_at": result.announced_at,
        "latest_prediction_id": int(prediction.id) if prediction is not None else None,
        "predicted_price": _round_optional(predicted_price),
        "prediction_delta_amount": _round_optional(prediction_delta),
        "prediction_error_rate": _round_optional(
            _compute_error_rate(predicted_price, winning_amount), digits=4
        ),
        "latest_decision_record_id": int(decision.id) if decision is not None else None,
        "recommended_amount": _round_optional(recommended_amount),
        "recommendation_delta_amount": _round_optional(recommendation_delta),
        "recommendation_error_rate": _round_optional(
            _compute_error_rate(recommended_amount, winning_amount), digits=4
        ),
        "detail_href": f"/api/v1/analytics/prediction-feedback?project_id={result.project_id}",
    }


def _load_opportunity_records(
    db: Session,
    *,
    operator_id: int,
    limit: int,
    statuses: set[str] | None = None,
) -> list[BidDecisionRecord]:
    query = db.query(BidDecisionRecord).filter(
        BidDecisionRecord.operator_id == operator_id
    )
    if statuses:
        query = query.filter(BidDecisionRecord.decision_status.in_(sorted(statuses)))
    return (
        query.order_by(
            BidDecisionRecord.priority_score.desc(),
            BidDecisionRecord.updated_at.desc(),
            BidDecisionRecord.id.desc(),
        )
        .limit(limit)
        .all()
    )


def _latest_paper_run(db: Session, *, operator_id: int) -> PaperBidRun | None:
    return (
        db.query(PaperBidRun)
        .filter(
            PaperBidRun.operator_id == operator_id, PaperBidRun.mode == "forward_paper"
        )
        .order_by(PaperBidRun.started_at.desc(), PaperBidRun.id.desc())
        .first()
    )


def _paper_actions_for_statuses(statuses: set[str] | None) -> set[str]:
    if not statuses:
        return set(_DEFAULT_PAPER_OPPORTUNITY_ACTIONS)
    return {
        action
        for action, mapped_status in _PAPER_ACTION_STATUS.items()
        if mapped_status in statuses
    }


def _paper_opportunity_query(
    db: Session,
    *,
    operator_id: int,
    statuses: set[str] | None = None,
    exclude_project_ids: set[int] | None = None,
):
    latest_run = _latest_paper_run(db, operator_id=operator_id)
    actions = _paper_actions_for_statuses(statuses)
    if latest_run is None or not actions:
        return None

    query = db.query(PaperBid).filter(
        PaperBid.run_id == latest_run.id,
        PaperBid.operator_id == operator_id,
        PaperBid.action.in_(sorted(actions)),
    )
    if exclude_project_ids:
        query = query.filter(PaperBid.project_id.notin_(sorted(exclude_project_ids)))
    return query


def _load_paper_opportunities(
    db: Session,
    *,
    operator_id: int,
    limit: int,
    statuses: set[str] | None = None,
    exclude_project_ids: set[int] | None = None,
) -> list[PaperBid]:
    query = _paper_opportunity_query(
        db,
        operator_id=operator_id,
        statuses=statuses,
        exclude_project_ids=exclude_project_ids,
    )
    if query is None:
        return []
    return (
        query.order_by(
            PaperBid.priority_score.desc(),
            PaperBid.created_at.desc(),
            PaperBid.id.desc(),
        )
        .limit(limit)
        .all()
    )


def _count_paper_opportunities(
    db: Session,
    *,
    operator_id: int,
    statuses: set[str] | None = None,
    exclude_project_ids: set[int] | None = None,
) -> int:
    query = _paper_opportunity_query(
        db,
        operator_id=operator_id,
        statuses=statuses,
        exclude_project_ids=exclude_project_ids,
    )
    return int(query.count()) if query is not None else 0


def _build_opportunity_items(
    db: Session,
    *,
    operator_id: int,
    limit: int,
    statuses: set[str] | None,
    now,
) -> list[dict]:
    records = _load_opportunity_records(
        db, operator_id=operator_id, limit=limit, statuses=statuses
    )
    items = [_serialize_opportunity(record, now=now) for record in records]
    excluded_project_ids = {
        int(record.project_id) for record in records if record.project_id is not None
    }
    remaining_limit = max(0, limit - len(items))
    if remaining_limit:
        paper_bids = _load_paper_opportunities(
            db,
            operator_id=operator_id,
            limit=remaining_limit,
            statuses=statuses,
            exclude_project_ids=excluded_project_ids,
        )
        items.extend(
            _serialize_paper_opportunity(paper_bid, now=now) for paper_bid in paper_bids
        )
    return items


def _load_latest_result_rows(db: Session, *, limit: int) -> list[TenderResult]:
    rows = (
        db.query(TenderResult)
        .filter(
            TenderResult.project_id.isnot(None),
            TenderResult.winning_amount.isnot(None),
            TenderResult.winning_amount > 0,
        )
        .order_by(
            TenderResult.announced_at.desc().nullslast(),
            TenderResult.created_at.desc(),
            TenderResult.id.desc(),
        )
        .limit(limit * 4)
        .all()
    )
    latest_rows: list[TenderResult] = []
    seen_project_ids: set[int] = set()
    for row in rows:
        project_id = int(row.project_id)
        if project_id in seen_project_ids:
            continue
        seen_project_ids.add(project_id)
        latest_rows.append(row)
        if len(latest_rows) >= limit:
            break
    return latest_rows


def _list_meta(
    *, operator: User, generated_at, limit: int, returned_count: int
) -> DashboardListMeta:
    return DashboardListMeta(
        operator_id=int(operator.id),
        generated_at=generated_at,
        limit=limit,
        returned_count=returned_count,
    )


@router.get("/opportunities", response_model=DashboardOpportunityListResponse)
def list_dashboard_opportunities(
    status: str | None = Query(
        default=None, pattern="^(planned|reviewing|submitted|skipped)$"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return bid-decision candidates for the mobile dashboard opportunity tab."""
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
    status: str | None = Query(
        default=None, pattern="^(submitted|reviewed|accepted|rejected)$"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return submitted bids with their latest linked decision record."""
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
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return tender results with linked prediction, recommendation, and bid outcome context."""
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
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return the mobile-first dashboard home payload centered on today's work."""
    generated_at = utc_now()
    due_until = generated_at + timedelta(hours=24)

    active_opportunity_count = (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.operator_id == operator.id,
            BidDecisionRecord.decision_status.in_(sorted(_ACTIVE_OPPORTUNITY_STATUSES)),
        )
        .count()
    )
    active_decision_project_ids = {
        int(project_id)
        for (project_id,) in db.query(BidDecisionRecord.project_id)
        .filter(
            BidDecisionRecord.operator_id == operator.id,
            BidDecisionRecord.decision_status.in_(sorted(_ACTIVE_OPPORTUNITY_STATUSES)),
            BidDecisionRecord.project_id.isnot(None),
        )
        .all()
    }
    active_paper_opportunity_count = _count_paper_opportunities(
        db,
        operator_id=operator.id,
        statuses=_ACTIVE_OPPORTUNITY_STATUSES,
        exclude_project_ids=active_decision_project_ids,
    )
    total_active_opportunity_count = (
        active_opportunity_count + active_paper_opportunity_count
    )
    due_records = (
        db.query(BidDecisionRecord)
        .join(Project)
        .filter(
            BidDecisionRecord.operator_id == operator.id,
            BidDecisionRecord.decision_status.in_(sorted(_ACTIVE_OPPORTUNITY_STATUSES)),
            Project.deadline.isnot(None),
            Project.deadline <= due_until,
        )
        .order_by(Project.deadline.asc(), BidDecisionRecord.priority_score.desc())
        .limit(limit)
        .all()
    )
    due_paper_query = _paper_opportunity_query(
        db,
        operator_id=operator.id,
        statuses=_ACTIVE_OPPORTUNITY_STATUSES,
        exclude_project_ids=active_decision_project_ids,
    )
    due_paper_opportunity_count = 0
    due_paper_opportunities: list[PaperBid] = []
    if due_paper_query is not None:
        due_paper_query = due_paper_query.join(
            Project, PaperBid.project_id == Project.id
        ).filter(
            Project.deadline.isnot(None),
            Project.deadline <= due_until,
        )
        due_paper_opportunity_count = due_paper_query.count()
        due_paper_opportunities = (
            due_paper_query.order_by(
                Project.deadline.asc(), PaperBid.priority_score.desc()
            )
            .limit(max(0, limit - len(due_records)))
            .all()
        )
    total_due_opportunity_count = len(due_records) + due_paper_opportunity_count

    recent_opportunity_records = _load_opportunity_records(
        db,
        operator_id=operator.id,
        limit=limit,
        statuses=_ACTIVE_OPPORTUNITY_STATUSES,
    )
    recent_opportunities = [
        _serialize_opportunity(record, now=generated_at)
        for record in recent_opportunity_records
    ]
    recent_opportunity_project_ids = {
        int(record.project_id)
        for record in recent_opportunity_records
        if record.project_id is not None
    }
    if len(recent_opportunities) < limit:
        recent_paper_opportunities = _load_paper_opportunities(
            db,
            operator_id=operator.id,
            limit=limit - len(recent_opportunities),
            statuses=_ACTIVE_OPPORTUNITY_STATUSES,
            exclude_project_ids=recent_opportunity_project_ids,
        )
        recent_opportunities.extend(
            _serialize_paper_opportunity(paper_bid, now=generated_at)
            for paper_bid in recent_paper_opportunities
        )

    active_bids_query = db.query(Bid).filter(
        Bid.user_id == operator.id, Bid.status.in_(["submitted", "reviewed"])
    )
    active_bid_count = active_bids_query.count()
    recent_bids = (
        active_bids_query.order_by(Bid.updated_at.desc(), Bid.id.desc())
        .limit(limit)
        .all()
    )
    bid_decision_map = _load_latest_decision_map(
        db, operator_id=operator.id, project_ids=[bid.project_id for bid in recent_bids]
    )
    latest_results_for_bids = _load_latest_result_map(
        db, project_ids=[bid.project_id for bid in recent_bids]
    )
    recent_bid_items = [
        _serialize_bid(bid, decision=bid_decision_map.get(int(bid.project_id)))
        for bid in recent_bids
    ]

    result_rows = _load_latest_result_rows(db, limit=limit)
    result_project_ids = [int(result.project_id) for result in result_rows]
    prediction_map = _load_latest_prediction_map(
        db, operator_id=operator.id, project_ids=result_project_ids
    )
    decision_map = _load_latest_decision_map(
        db, operator_id=operator.id, project_ids=result_project_ids
    )
    result_bid_map = _load_latest_bid_map(
        db, operator_id=operator.id, project_ids=result_project_ids
    )
    recent_result_items = [
        _serialize_result(
            result,
            operator=operator,
            prediction=prediction_map.get(int(result.project_id)),
            decision=decision_map.get(int(result.project_id)),
            bid=result_bid_map.get(int(result.project_id)),
        )
        for result in result_rows
    ]
    result_review_count = len(recent_result_items)

    latest_run = (
        db.query(OperatorStrategyRun)
        .filter(OperatorStrategyRun.operator_id == operator.id)
        .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
        .first()
    )
    operational_status = _serialize_operational_status(latest_run)
    latest_paper_run = (
        db.query(PaperBidRun)
        .filter(PaperBidRun.operator_id == operator.id)
        .order_by(PaperBidRun.started_at.desc(), PaperBidRun.id.desc())
        .first()
    )
    paper_backtest_metric = _serialize_paper_backtest_metric(latest_paper_run)

    work_items = []
    for record in due_records:
        opportunity = _serialize_opportunity(record, now=generated_at)
        deadline_hours = opportunity["deadline_hours_remaining"]
        severity = (
            "critical"
            if deadline_hours is not None and deadline_hours <= 6
            else "watch"
        )
        work_items.append(
            {
                "key": f"opportunity:{record.id}",
                "item_type": "opportunity_due",
                "severity": severity,
                "title": opportunity["project"]["title"],
                "subtitle": "24시간 내 마감 입찰 판단",
                "project_id": opportunity["project"]["project_id"],
                "due_at": opportunity["project"]["deadline"],
                "status": opportunity["decision_status"],
                "href": "/dashboard/opportunities",
            }
        )

    for paper_bid in due_paper_opportunities:
        if len(work_items) >= limit:
            break
        opportunity = _serialize_paper_opportunity(paper_bid, now=generated_at)
        deadline = opportunity["project"]["deadline"]
        deadline_hours = opportunity["deadline_hours_remaining"]
        severity = (
            "critical"
            if deadline_hours is not None and deadline_hours <= 6
            else "watch"
        )
        work_items.append(
            {
                "key": f"paper-bid:{opportunity['paper_bid_id']}",
                "item_type": "opportunity_due",
                "severity": severity,
                "title": opportunity["project"]["title"],
                "subtitle": "페이퍼 후보 마감 확인",
                "project_id": opportunity["project"]["project_id"],
                "due_at": deadline,
                "status": opportunity["decision_status"],
                "href": "/dashboard/opportunities",
            }
        )

    for bid in recent_bids:
        if len(work_items) >= limit:
            break
        if int(bid.project_id) in latest_results_for_bids:
            continue
        work_items.append(
            {
                "key": f"bid:{bid.id}",
                "item_type": "bid_pending_result",
                "severity": "info",
                "title": (
                    bid.project.title
                    if bid.project is not None and bid.project.title
                    else "제출 투찰"
                ),
                "subtitle": "제출 후 결과 확인 대기",
                "project_id": int(bid.project_id),
                "due_at": None,
                "status": _normalize_bid_status(bid.status),
                "href": "/dashboard/bids",
            }
        )

    for result_item in recent_result_items:
        if len(work_items) >= limit:
            break
        if (
            result_item["prediction_error_rate"] is None
            and result_item["recommendation_error_rate"] is None
        ):
            continue
        work_items.append(
            {
                "key": f"result:{result_item['tender_result_id']}",
                "item_type": "result_review",
                "severity": "watch",
                "title": result_item["project"]["title"],
                "subtitle": "낙찰 결과 예측 오차 확인",
                "project_id": result_item["project"]["project_id"],
                "due_at": result_item["announced_at"],
                "status": result_item["result_status"],
                "href": "/dashboard/results",
            }
        )

    return {
        "operator_id": int(operator.id),
        "generated_at": generated_at,
        "today": generated_at.date(),
        "operational_status": operational_status,
        "metrics": [
            {
                "key": "due_opportunities",
                "label": "오늘 마감",
                "value": total_due_opportunity_count,
                "unit": "count",
                "status": "critical" if total_due_opportunity_count else "healthy",
                "detail": "24시간 이내 저장된 판단과 페이퍼 후보입니다.",
            },
            {
                "key": "active_opportunities",
                "label": "판단 대기",
                "value": total_active_opportunity_count,
                "unit": "count",
                "status": "watch" if total_active_opportunity_count else "healthy",
                "detail": "저장된 판단과 최신 페이퍼 후보를 합친 입찰 후보입니다.",
            },
            {
                "key": "active_bids",
                "label": "결과 대기",
                "value": active_bid_count,
                "unit": "count",
                "status": "info" if active_bid_count else "healthy",
                "detail": "submitted/reviewed 상태의 투찰입니다.",
            },
            {
                "key": "recent_results",
                "label": "결과 확인",
                "value": result_review_count,
                "unit": "count",
                "status": "watch" if result_review_count else "healthy",
                "detail": "최근 낙찰 결과와 예측 오차입니다.",
            },
            paper_backtest_metric,
        ],
        "work_items": work_items,
        "sections": [
            {
                "key": "opportunities",
                "label": "입찰",
                "count": total_active_opportunity_count,
                "status": "watch" if total_active_opportunity_count else "healthy",
                "href": "/dashboard/opportunities",
            },
            {
                "key": "bids",
                "label": "투찰",
                "count": active_bid_count,
                "status": "info" if active_bid_count else "healthy",
                "href": "/dashboard/bids",
            },
            {
                "key": "results",
                "label": "결과",
                "count": result_review_count,
                "status": "watch" if result_review_count else "healthy",
                "href": "/dashboard/results",
            },
        ],
        "recent_opportunities": recent_opportunities,
        "recent_bids": recent_bid_items,
        "recent_results": recent_result_items,
        "realtime_href": "/api/v1/realtime/events",
    }


def _serialize_operational_status(latest_run: OperatorStrategyRun | None) -> dict:
    if latest_run is None:
        return {
            "key": "operator_strategy",
            "label": "운영 상태",
            "value": "no_run",
            "unit": "state",
            "status": "info",
            "detail": "아직 전략 모니터링 실행 기록이 없습니다.",
        }

    run_status = str(latest_run.status or "queued")
    if run_status == "completed":
        status_value = "healthy"
        detail = "최근 전략 모니터링이 정상 완료되었습니다."
    elif run_status in {"queued", "running"}:
        status_value = "watch"
        detail = "전략 모니터링 작업이 진행 중입니다."
    elif run_status == "failed":
        status_value = "critical"
        detail = latest_run.error_message or "최근 전략 모니터링이 실패했습니다."
    else:
        status_value = "info"
        detail = f"최근 전략 모니터링 상태: {run_status}"

    return {
        "key": "operator_strategy",
        "label": "운영 상태",
        "value": run_status,
        "unit": "state",
        "status": status_value,
        "detail": detail,
    }


def _serialize_paper_backtest_metric(latest_run: PaperBidRun | None) -> dict:
    if latest_run is None:
        return {
            "key": "paper_backtest",
            "label": "페이퍼 검증",
            "value": 0,
            "unit": "count",
            "status": "info",
            "detail": "아직 저장된 paper bidding 실행이 없습니다.",
        }

    run_status = str(latest_run.status or "unknown")
    if run_status == "completed":
        status_value = "healthy" if int(latest_run.paper_bid_count or 0) > 0 else "info"
    elif run_status == "failed":
        status_value = "critical"
    else:
        status_value = "watch"

    detail = (
        f"{latest_run.mode} 실행에서 후보 {int(latest_run.candidate_count or 0)}건, "
        f"가상 투찰 {int(latest_run.paper_bid_count or 0)}건, 정산 {int(latest_run.settled_count or 0)}건을 기록했습니다."
    )
    if latest_run.error_message:
        detail = latest_run.error_message
    return {
        "key": "paper_backtest",
        "label": "페이퍼 검증",
        "value": int(latest_run.paper_bid_count or 0),
        "unit": "count",
        "status": status_value,
        "detail": detail,
    }
