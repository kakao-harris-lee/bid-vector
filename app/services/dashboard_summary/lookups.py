"""Latest-per-project lookup map loaders (decision/prediction/result/bid)."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.models.models import Bid, BidDecisionRecord, PricePrediction, TenderResult
from app.services.query_predicates import settled_with_amount


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
            settled_with_amount(),
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
