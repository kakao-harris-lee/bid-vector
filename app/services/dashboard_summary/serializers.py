"""Row serializers: opportunity/paper/bid/result dicts + award outcome judge."""

from __future__ import annotations

from app.models.models import (
    Bid,
    BidDecisionRecord,
    PaperBid,
    PricePrediction,
    TenderResult,
    User,
)
from app.schemas.schemas import _extract_decision_reasons
from app.services.award_verification import AWARD_OUTCOME_WON, determine_award_outcome

from .constants import _TERMINAL_RESULT_STATUSES
from .normalizers import (
    _compute_delta,
    _compute_error_rate,
    _hours_until,
    _normalize_action,
    _normalize_bid_status,
    _normalize_opportunity_status,
    _paper_status_from_action,
    _project_brief,
    _round_optional,
)


def _serialize_opportunity(record: BidDecisionRecord, *, now) -> dict:
    project = record.project
    deadline_hours = (
        int(record.deadline_hours_remaining)
        if record.deadline_hours_remaining is not None
        else _hours_until(project.deadline if project is not None else None, now=now)
    )
    strengths, risk_flags = _extract_decision_reasons(record.score_breakdown)
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
        "strengths": strengths,
        "risk_flags": risk_flags,
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
    """Judge won/lost/unknown, reusing the canonical 상호 판정(정본).

    The 상호 비교는 ``award_verification.determine_award_outcome`` 에 위임한다
    (정규화 후 정확매치, 금액 미사용). 과거의 substring 매칭은 폐기했다 — 부분
    문자열 포함은 "몬딱솔류션" ⊃ "션" 처럼 무관한 상호를 오판(won)시켜 피드백
    라벨을 오염시켰다. 정본과 동일 규칙을 쓰면 대시보드와 실투찰 트랙이 "우리가
    이겼나"에 대해 절대 어긋나지 않는다.

    운영자 참여 게이트는 유지한다: 운영자가 실제 투찰(``bid``)했고 개찰이
    종료(terminal status)된 경우에만 상호 불일치를 확정 패찰(lost)로 라벨하고,
    투찰하지 않은 공고는 unknown 으로 남긴다.
    """
    # 운영자가 명시적으로 낙찰 확정한 투찰(accepted)은 상호 근거와 무관하게 won.
    if bid is not None and _normalize_bid_status(bid.status) == "accepted":
        return "won"

    name_outcome = determine_award_outcome(
        winning_company=result.winning_company,
        winning_amount=result.winning_amount,
        operator_company_name=operator.company,
        submitted_bid_amount=bid.bid_amount if bid is not None else None,
    )
    if name_outcome == AWARD_OUTCOME_WON:
        return "won"

    if (
        bid is not None
        and str(result.result_status or "").lower() in _TERMINAL_RESULT_STATUSES
    ):
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
