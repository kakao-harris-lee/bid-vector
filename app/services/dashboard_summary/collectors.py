"""Per-section summary collectors for the dashboard home payload."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import (
    Bid,
    BidDecisionRecord,
    OperatorStrategyRun,
    PaperBid,
    PaperBidRun,
    Project,
    User,
)

from .constants import _ACTIVE_OPPORTUNITY_STATUSES
from .lookups import (
    _load_latest_bid_map,
    _load_latest_decision_map,
    _load_latest_prediction_map,
    _load_latest_result_map,
)
from .metric_cards import (
    _serialize_operational_status,
    _serialize_paper_backtest_metric,
)
from .queries import (
    _count_paper_opportunities,
    _load_latest_result_rows,
    _load_opportunity_records,
    _load_paper_opportunities,
    _paper_opportunity_query,
)
from .serializers import (
    _serialize_bid,
    _serialize_opportunity,
    _serialize_paper_opportunity,
    _serialize_result,
)


def _summarize_active_opportunity_counts(db: Session, *, operator: User) -> dict:
    """Active decision + active paper opportunity counts and the decision project set."""
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
    return {
        "active_decision_project_ids": active_decision_project_ids,
        "total_active_opportunity_count": total_active_opportunity_count,
    }


def _summarize_due_opportunities(
    db: Session,
    *,
    operator: User,
    limit: int,
    due_until,
    active_decision_project_ids: set[int],
) -> dict:
    """Decision records and paper candidates due within the 24h window."""
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
    return {
        "due_records": due_records,
        "due_paper_opportunities": due_paper_opportunities,
        "total_due_opportunity_count": total_due_opportunity_count,
    }


def _summarize_recent_opportunities(
    db: Session, *, operator: User, limit: int, now
) -> list[dict]:
    """Recent active decision opportunities backfilled with paper candidates."""
    recent_opportunity_records = _load_opportunity_records(
        db,
        operator_id=operator.id,
        limit=limit,
        statuses=_ACTIVE_OPPORTUNITY_STATUSES,
    )
    recent_opportunities = [
        _serialize_opportunity(record, now=now) for record in recent_opportunity_records
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
            _serialize_paper_opportunity(paper_bid, now=now)
            for paper_bid in recent_paper_opportunities
        )
    return recent_opportunities


def _summarize_active_bids(db: Session, *, operator: User, limit: int) -> dict:
    """Active submitted/reviewed bids plus their serialized rows and lookup maps."""
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
    return {
        "active_bid_count": active_bid_count,
        "recent_bids": recent_bids,
        "latest_results_for_bids": latest_results_for_bids,
        "recent_bid_items": recent_bid_items,
    }


def _summarize_recent_results(db: Session, *, operator: User, limit: int) -> dict:
    """Recent tender results enriched with prediction/decision/bid context."""
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
    return {
        "recent_result_items": recent_result_items,
        "result_review_count": len(recent_result_items),
    }


def _summarize_operational_metrics(db: Session, *, operator: User) -> dict:
    """Latest operator strategy run + paper backtest run metric cards."""
    latest_run = (
        db.query(OperatorStrategyRun)
        .filter(OperatorStrategyRun.operator_id == operator.id)
        .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
        .first()
    )
    latest_paper_run = (
        db.query(PaperBidRun)
        .filter(PaperBidRun.operator_id == operator.id)
        .order_by(PaperBidRun.started_at.desc(), PaperBidRun.id.desc())
        .first()
    )
    return {
        "operational_status": _serialize_operational_status(latest_run),
        "paper_backtest_metric": _serialize_paper_backtest_metric(latest_paper_run),
    }
