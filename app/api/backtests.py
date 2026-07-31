"""Authenticated backtest and paper-bidding API routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.core.constants import HISTORICAL_BACKTEST_RUN_MODE, PAPER_BID_RUN_MODES
from app.core.database import get_db
from app.core.security import get_current_operator_from_bearer
from app.core.time import ensure_utc, utc_now
from app.models.models import PaperBid, PaperBidRun, PaperBidSettlement, Project, User
from app.schemas.paper_bidding_items import PaperBiddingRunSummary
from app.schemas.paper_bidding_runs import (
    SETTLEMENT_BASIS,
    PaperBiddingPaperBidRecord,
    PaperBiddingSettlementOverview,
    PaperBiddingSettlementRecord,
)
from app.schemas.schemas import (
    BacktestDataAuditResponse,
    ForwardPaperBiddingRunRequest,
    PaperBiddingRunDetailResponse,
    PaperBiddingRunExecutionResponse,
    PaperBiddingRunListItem,
    PaperBiddingRunListResponse,
    PaperBiddingRunRequest,
    PaperBiddingSummaryResponse,
)
from app.services.backtest_data_audit import BacktestDataAuditService
from app.services.paper_bidding_backtest import (
    OperatorNotFoundError,
    PaperBiddingBacktestService,
)
from app.services.paper_bidding_run_payload import (
    load_run_request_snapshot,
    load_run_summary,
)
from app.services.paper_bidding_settlement import (
    SettlementStatusContext,
    derive_settlement_status,
)

router = APIRouter()

# 목록 필터가 받는 run mode — 단일 출처(app.core.constants)에서 패턴을 만든다.
RUN_MODE_QUERY_PATTERN = f"^({'|'.join(sorted(PAPER_BID_RUN_MODES))})$"


def _result_time(result) -> datetime | None:
    return result.announced_at or result.created_at


def _positive_tender_results(project: Project | None) -> list:
    if project is None:
        return []
    return [
        result
        for result in project.tender_results
        if result.winning_amount is not None and float(result.winning_amount or 0.0) > 0
    ]


def _build_settlement_overview(run: PaperBidRun) -> PaperBiddingSettlementOverview:
    """Summarize whether paper bids can be settled against final tender results."""
    now = utc_now()
    paper_bids = list(run.paper_bids or [])
    paper_bid_count = len(paper_bids)

    settled_count = 0
    ready_to_settle_count = 0
    waiting_result_count = 0
    before_deadline_count = 0
    missing_deadline_count = 0
    next_deadline_at = None
    oldest_waiting_deadline_at = None
    next_ready_result_at = None
    latest_settled_at = None

    for paper_bid in paper_bids:
        if paper_bid.settlement is not None:
            settled_count += 1
            if paper_bid.settlement.settled_at is not None:
                settled_at = ensure_utc(paper_bid.settlement.settled_at)
                if latest_settled_at is None or settled_at > latest_settled_at:
                    latest_settled_at = settled_at
            continue

        positive_results = _positive_tender_results(paper_bid.project)
        if positive_results:
            ready_to_settle_count += 1
            result_times = [
                _result_time(result)
                for result in positive_results
                if _result_time(result) is not None
            ]
            if result_times:
                result_at = ensure_utc(min(result_times))
                if next_ready_result_at is None or result_at < next_ready_result_at:
                    next_ready_result_at = result_at
            continue

        deadline = paper_bid.project.deadline if paper_bid.project is not None else None
        if deadline is None:
            missing_deadline_count += 1
            continue

        deadline_at = ensure_utc(deadline)
        if deadline_at > now:
            before_deadline_count += 1
            if next_deadline_at is None or deadline_at < next_deadline_at:
                next_deadline_at = deadline_at
        else:
            waiting_result_count += 1
            if (
                oldest_waiting_deadline_at is None
                or deadline_at < oldest_waiting_deadline_at
            ):
                oldest_waiting_deadline_at = deadline_at

    unsettled_count = paper_bid_count - settled_count
    settlement_status = derive_settlement_status(
        SettlementStatusContext(
            paper_bid_count=paper_bid_count,
            settled_count=settled_count,
            unsettled_count=unsettled_count,
            ready_to_settle_count=ready_to_settle_count,
            waiting_result_count=waiting_result_count,
            before_deadline_count=before_deadline_count,
            latest_settled_at=latest_settled_at,
            next_ready_result_at=next_ready_result_at,
            oldest_waiting_deadline_at=oldest_waiting_deadline_at,
            next_deadline_at=next_deadline_at,
        )
    )

    return PaperBiddingSettlementOverview(
        status=settlement_status.status_key,
        label=settlement_status.label,
        detail=settlement_status.detail,
        settlement_basis=SETTLEMENT_BASIS,
        paper_bid_count=paper_bid_count,
        settled_count=settled_count,
        unsettled_count=unsettled_count,
        ready_to_settle_count=ready_to_settle_count,
        waiting_result_count=waiting_result_count,
        before_deadline_count=before_deadline_count,
        missing_deadline_count=missing_deadline_count,
        next_confirmable_at=settlement_status.next_confirmable_at,
        next_deadline_at=next_deadline_at,
        oldest_waiting_deadline_at=oldest_waiting_deadline_at,
        latest_settled_at=latest_settled_at,
    )


def _serialize_run(run: PaperBidRun) -> PaperBiddingRunListItem:
    return PaperBiddingRunListItem(
        id=int(run.id),
        operator_id=int(run.operator_id),
        status=str(run.status or "unknown"),
        mode=str(run.mode or HISTORICAL_BACKTEST_RUN_MODE),
        scenario=str(run.scenario or "base"),
        category_filter=run.category_filter,
        strategy_version=str(run.strategy_version or "local"),
        model_version=str(run.model_version or "current"),
        target_start_at=run.target_start_at,
        target_end_at=run.target_end_at,
        data_cutoff_policy=str(run.data_cutoff_policy or ""),
        started_at=run.started_at,
        completed_at=run.completed_at,
        candidate_count=int(run.candidate_count or 0),
        paper_bid_count=int(run.paper_bid_count or 0),
        settled_count=int(run.settled_count or 0),
        settlement_overview=_build_settlement_overview(run),
        summary=load_run_summary(run.result_payload, run_id=int(run.id)),
    )


def _serialize_paper_bid(paper_bid: PaperBid) -> PaperBiddingPaperBidRecord:
    project = paper_bid.project
    return PaperBiddingPaperBidRecord(
        id=int(paper_bid.id),
        run_id=int(paper_bid.run_id),
        project_id=int(paper_bid.project_id),
        project_title=project.title if project is not None else "",
        notice_number=paper_bid.notice_number,
        category=project.category if project is not None else None,
        action=paper_bid.action,
        decision_status=paper_bid.decision_status,
        data_cutoff_at=paper_bid.data_cutoff_at,
        paper_bid_amount=float(paper_bid.paper_bid_amount or 0.0),
        paper_bid_rate=float(paper_bid.paper_bid_rate or 0.0),
        scenario=paper_bid.scenario,
        priority_score=float(paper_bid.priority_score or 0.0),
        probability_score=float(paper_bid.probability_score or 0.0),
        matched_score=float(paper_bid.matched_score or 0.0),
        predicted_price=float(paper_bid.predicted_price or 0.0),
        predicted_bid_rate=float(paper_bid.predicted_bid_rate or 0.0),
        confidence_score=float(paper_bid.confidence_score or 0.0),
        predictor_name=paper_bid.predictor_name,
        input_snapshot_hash=paper_bid.input_snapshot_hash,
        created_at=paper_bid.created_at,
    )


def _serialize_settlement(
    settlement: PaperBidSettlement,
) -> PaperBiddingSettlementRecord:
    return PaperBiddingSettlementRecord(
        id=int(settlement.id),
        paper_bid_id=int(settlement.paper_bid_id),
        tender_result_id=(
            int(settlement.tender_result_id)
            if settlement.tender_result_id is not None
            else None
        ),
        result_status=settlement.result_status,
        winning_company=settlement.winning_company,
        winning_amount=float(settlement.winning_amount or 0.0),
        winning_rate=float(settlement.winning_rate or 0.0),
        amount_delta=float(settlement.amount_delta or 0.0),
        absolute_error_rate=float(settlement.absolute_error_rate or 0.0),
        bid_rate_delta=float(settlement.bid_rate_delta or 0.0),
        absolute_bid_rate_error=float(settlement.absolute_bid_rate_error or 0.0),
        price_close=bool(settlement.price_close),
        price_competitive=bool(settlement.price_competitive),
        would_have_won_price_only=settlement.would_have_won_price_only,
        would_have_won_final=settlement.would_have_won_final,
        settlement_reason=settlement.settlement_reason,
        settled_at=settlement.settled_at,
    )


def _get_run_or_404(db: Session, *, operator: User, run_id: int) -> PaperBidRun:
    run = (
        db.query(PaperBidRun)
        .options(
            selectinload(PaperBidRun.paper_bids).selectinload(PaperBid.settlement),
            selectinload(PaperBidRun.paper_bids)
            .selectinload(PaperBid.project)
            .selectinload(Project.tender_results),
        )
        .filter(PaperBidRun.id == run_id, PaperBidRun.operator_id == operator.id)
        .first()
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Paper bidding run not found"
        )
    return run


@router.get("/data-audit", response_model=BacktestDataAuditResponse)
def get_backtest_data_audit(
    category: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return DB readiness counts for backtesting."""
    del operator
    return BacktestDataAuditService().build_report(db, categories=category or [])


@router.post("/paper-bidding/runs", response_model=PaperBiddingRunExecutionResponse)
def create_historical_paper_bidding_run(
    request: PaperBiddingRunRequest,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Run a historical paper-bidding backtest."""
    try:
        return PaperBiddingBacktestService().run_historical_backtest(
            db,
            operator_id=operator.id,
            category=request.category,
            start_at=request.start_at,
            end_at=request.end_at,
            limit=request.limit,
            scenario=request.scenario,
            strategy_version=request.strategy_version,
            model_version=request.model_version,
            cutoff_hours_before_deadline=request.cutoff_hours_before_deadline,
            history_limit=request.history_limit,
            settle_actions=request.settle_actions,
            persist=request.persist,
        )
    except OperatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post(
    "/paper-bidding/forward-runs", response_model=PaperBiddingRunExecutionResponse
)
def create_forward_paper_bidding_run(
    request: ForwardPaperBiddingRunRequest,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Generate paper bids for currently open/re-notice projects."""
    try:
        return PaperBiddingBacktestService().run_forward_paper_bidding(
            db,
            operator_id=operator.id,
            category=request.category,
            limit=request.limit,
            scenario=request.scenario,
            strategy_version=request.strategy_version,
            model_version=request.model_version,
            history_limit=request.history_limit,
            persist=request.persist,
        )
    except OperatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/paper-bidding/runs", response_model=PaperBiddingRunListResponse)
def list_paper_bidding_runs(
    mode: str | None = Query(default=None, pattern=RUN_MODE_QUERY_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """List persisted paper-bidding runs for the authenticated operator."""
    query = db.query(PaperBidRun).filter(PaperBidRun.operator_id == operator.id)
    if mode:
        query = query.filter(PaperBidRun.mode == mode)
    runs = (
        query.options(
            selectinload(PaperBidRun.paper_bids).selectinload(PaperBid.settlement),
            selectinload(PaperBidRun.paper_bids)
            .selectinload(PaperBid.project)
            .selectinload(Project.tender_results),
        )
        .order_by(PaperBidRun.started_at.desc(), PaperBidRun.id.desc())
        .limit(limit)
        .all()
    )
    return PaperBiddingRunListResponse(
        operator_id=int(operator.id),
        returned_count=len(runs),
        limit=limit,
        items=[_serialize_run(run) for run in runs],
    )


@router.get("/paper-bidding/summary", response_model=PaperBiddingSummaryResponse)
def get_paper_bidding_summary(
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return the latest persisted paper-bidding run summary."""
    latest_run = (
        db.query(PaperBidRun)
        .options(
            selectinload(PaperBidRun.paper_bids).selectinload(PaperBid.settlement),
            selectinload(PaperBidRun.paper_bids)
            .selectinload(PaperBid.project)
            .selectinload(Project.tender_results),
        )
        .filter(PaperBidRun.operator_id == operator.id)
        .order_by(PaperBidRun.started_at.desc(), PaperBidRun.id.desc())
        .first()
    )
    completed_count = (
        db.query(PaperBidRun)
        .filter(
            PaperBidRun.operator_id == operator.id, PaperBidRun.status == "completed"
        )
        .count()
    )
    run_count = (
        db.query(PaperBidRun).filter(PaperBidRun.operator_id == operator.id).count()
    )
    latest_payload = _serialize_run(latest_run) if latest_run is not None else None
    return PaperBiddingSummaryResponse(
        operator_id=int(operator.id),
        latest_run=latest_payload,
        run_count=int(run_count or 0),
        completed_count=int(completed_count or 0),
        # 실행이 없으면 빈(0) 요약 — 종전 ``{}`` 와 달리 항상 객체 모양을 유지한다.
        latest_summary=(
            latest_payload.summary
            if latest_payload is not None
            else PaperBiddingRunSummary()
        ),
    )


@router.get(
    "/paper-bidding/runs/{run_id}", response_model=PaperBiddingRunDetailResponse
)
def get_paper_bidding_run_detail(
    run_id: int,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
):
    """Return one persisted paper-bidding run with bids and settlements."""
    run = _get_run_or_404(db, operator=operator, run_id=run_id)
    paper_bids = sorted(run.paper_bids, key=lambda item: int(item.id or 0))
    settlements = [
        _serialize_settlement(paper_bid.settlement)
        for paper_bid in paper_bids
        if paper_bid.settlement is not None
    ]
    return PaperBiddingRunDetailResponse.from_list_item(
        _serialize_run(run),
        request=load_run_request_snapshot(
            mode=run.mode, raw_value=run.request_payload, run_id=int(run.id)
        ),
        paper_bids=[_serialize_paper_bid(paper_bid) for paper_bid in paper_bids],
        settlements=settlements,
    )
