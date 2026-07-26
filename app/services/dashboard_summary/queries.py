"""Shared query helpers for summary + list endpoints (opportunity/paper/result feeds)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import BidDecisionRecord, PaperBid, PaperBidRun, TenderResult
from app.services.query_predicates import settled_with_amount

from .constants import _DEFAULT_PAPER_OPPORTUNITY_ACTIONS, _PAPER_ACTION_STATUS
from .serializers import _serialize_opportunity, _serialize_paper_opportunity


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
            settled_with_amount(),
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
