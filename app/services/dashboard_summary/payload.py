"""Work item + metric/section card builders and the summary payload assembler."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.models import Bid, BidDecisionRecord, PaperBid, TenderResult, User

from .collectors import (
    _summarize_active_bids,
    _summarize_active_opportunity_counts,
    _summarize_due_opportunities,
    _summarize_operational_metrics,
    _summarize_recent_opportunities,
    _summarize_recent_results,
)
from .normalizers import _normalize_bid_status
from .serializers import _serialize_opportunity, _serialize_paper_opportunity


def build_work_items(
    *,
    due_records: list[BidDecisionRecord],
    due_paper_opportunities: list[PaperBid],
    recent_bids: list[Bid],
    latest_results_for_bids: dict[int, TenderResult],
    recent_result_items: list[dict],
    limit: int,
    now,
) -> list[dict]:
    """Build the prioritized work-item feed from the four candidate sources.

    Order and the ``len(work_items) >= limit`` cutoff after the first source
    match the original inline loops exactly.
    """
    work_items: list[dict] = []

    for record in due_records:
        opportunity = _serialize_opportunity(record, now=now)
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
        opportunity = _serialize_paper_opportunity(paper_bid, now=now)
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

    return work_items


def build_metric_cards(
    *,
    total_due_opportunity_count: int,
    total_active_opportunity_count: int,
    active_bid_count: int,
    result_review_count: int,
    paper_backtest_metric: dict,
) -> list[dict]:
    """Build the ordered metric cards for the summary header."""
    return [
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
    ]


def build_sections(
    *,
    total_active_opportunity_count: int,
    active_bid_count: int,
    result_review_count: int,
) -> list[dict]:
    """Build the ordered navigation section cards for the summary footer."""
    return [
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
    ]


def build_dashboard_summary(
    db: Session, *, operator: User, limit: int, generated_at
) -> dict:
    """Assemble the mobile dashboard home payload.

    Orchestrates the per-section collectors and payload builders; the response
    dict is identical (fields, ordering, values) to the prior inline router.
    """
    due_until = generated_at + timedelta(hours=24)

    active_counts = _summarize_active_opportunity_counts(db, operator=operator)
    active_decision_project_ids = active_counts["active_decision_project_ids"]
    total_active_opportunity_count = active_counts["total_active_opportunity_count"]

    due = _summarize_due_opportunities(
        db,
        operator=operator,
        limit=limit,
        due_until=due_until,
        active_decision_project_ids=active_decision_project_ids,
    )

    recent_opportunities = _summarize_recent_opportunities(
        db, operator=operator, limit=limit, now=generated_at
    )

    bids = _summarize_active_bids(db, operator=operator, limit=limit)

    results = _summarize_recent_results(db, operator=operator, limit=limit)

    metrics = _summarize_operational_metrics(db, operator=operator)

    work_items = build_work_items(
        due_records=due["due_records"],
        due_paper_opportunities=due["due_paper_opportunities"],
        recent_bids=bids["recent_bids"],
        latest_results_for_bids=bids["latest_results_for_bids"],
        recent_result_items=results["recent_result_items"],
        limit=limit,
        now=generated_at,
    )

    return {
        "operator_id": int(operator.id),
        "current_operator_id": int(operator.id),
        "current_operator_username": str(operator.username or ""),
        "generated_at": generated_at,
        "today": generated_at.date(),
        "operational_status": metrics["operational_status"],
        "metrics": build_metric_cards(
            total_due_opportunity_count=due["total_due_opportunity_count"],
            total_active_opportunity_count=total_active_opportunity_count,
            active_bid_count=bids["active_bid_count"],
            result_review_count=results["result_review_count"],
            paper_backtest_metric=metrics["paper_backtest_metric"],
        ),
        "work_items": work_items,
        "sections": build_sections(
            total_active_opportunity_count=total_active_opportunity_count,
            active_bid_count=bids["active_bid_count"],
            result_review_count=results["result_review_count"],
        ),
        "recent_opportunities": recent_opportunities,
        "recent_bids": bids["recent_bid_items"],
        "recent_results": results["recent_result_items"],
        "realtime_href": "/api/v1/realtime/events",
    }
