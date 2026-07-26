"""Persistence of paper bids, settlements, and run lifecycle rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import (
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    TenderResult,
)
from app.services.paper_bidding_backtest.base import _PaperBiddingBase


class _PersistenceMixin(_PaperBiddingBase):
    """Write ``PaperBid`` / ``PaperBidSettlement`` / ``PaperBidRun`` rows."""

    def _persist_paper_bid(
        self,
        db: Session,
        *,
        run: PaperBidRun | None,
        operator_id: int,
        item: dict[str, Any],
        persist: bool,
        model_version: str,
        strategy_version: str,
    ) -> PaperBid | None:
        if not persist or run is None:
            return None
        paper_bid = PaperBid(
            run_id=run.id,
            project_id=item["project_id"],
            operator_id=operator_id,
            notice_number=item.get("notice_number"),
            action=item["action"],
            decision_status=item["decision_status"],
            data_cutoff_at=datetime.fromisoformat(item["data_cutoff_at"]),
            paper_bid_amount=item["paper_bid_amount"],
            paper_bid_rate=item["paper_bid_rate"],
            scenario=item["scenario"],
            priority_score=item["priority_score"],
            probability_score=item["probability_score"],
            matched_score=item["matched_score"],
            predicted_price=item["predicted_price"],
            predicted_bid_rate=item["predicted_bid_rate"],
            price_range_min=item["price_range_min"],
            price_range_max=item["price_range_max"],
            confidence_score=item["confidence_score"],
            predictor_name=item["predictor_name"],
            predictor_family=item["predictor_family"],
            model_version=model_version or item["model_version"],
            strategy_version=strategy_version,
            input_snapshot_hash=item["input_snapshot_hash"],
            reasoning=item["reasoning"],
        )
        db.add(paper_bid)
        db.flush()
        return paper_bid

    def _persist_settlement(
        self,
        db: Session,
        *,
        paper_bid: PaperBid | None,
        tender_result: TenderResult,
        settlement: dict[str, Any],
        persist: bool,
    ) -> None:
        if not persist or paper_bid is None:
            return
        db.add(
            PaperBidSettlement(
                paper_bid_id=paper_bid.id,
                tender_result_id=tender_result.id,
                result_status=settlement["result_status"],
                winning_company=settlement.get("winning_company"),
                winning_amount=settlement["winning_amount"],
                winning_rate=settlement["winning_rate"],
                amount_delta=settlement["amount_delta"],
                absolute_error_rate=settlement["absolute_error_rate"],
                bid_rate_delta=settlement["bid_rate_delta"],
                absolute_bid_rate_error=settlement["absolute_bid_rate_error"],
                price_close=settlement["price_close"],
                price_competitive=settlement["price_competitive"],
                would_have_won_price_only=settlement["would_have_won_price_only"],
                would_have_won_final=settlement["would_have_won_final"],
                estimated_price=settlement.get("estimated_price"),
                minimum_bid_price=settlement.get("minimum_bid_price"),
                settlement_reason=settlement["settlement_reason"],
                settled_at=utc_now(),
            )
        )
        db.flush()

    def _create_run(
        self,
        db: Session,
        *,
        operator_id: int,
        request_payload: dict[str, Any],
        persist: bool,
        category: str | None,
        scenario: str,
        strategy_version: str,
        model_version: str,
        start_at: datetime | None,
        end_at: datetime | None,
        cutoff_hours_before_deadline: int,
        mode: str,
    ) -> PaperBidRun | None:
        if not persist:
            return None
        run = PaperBidRun(
            operator_id=operator_id,
            strategy_version=strategy_version,
            model_version=model_version,
            status="running",
            mode=mode,
            scenario=scenario,
            category_filter=category,
            target_start_at=start_at,
            target_end_at=end_at,
            data_cutoff_policy=(
                f"deadline_minus_{max(0, int(cutoff_hours_before_deadline or 0))}h"
                if mode == "historical_backtest"
                else "execution_time"
            ),
            started_at=utc_now(),
            request_payload=json.dumps(
                request_payload, ensure_ascii=False, default=str
            ),
        )
        db.add(run)
        db.flush()
        return run

    def _complete_run(
        self,
        db: Session,
        *,
        run: PaperBidRun | None,
        persist: bool,
        summary: dict[str, Any],
        candidate_count: int,
        paper_bid_count: int,
        settled_count: int,
    ) -> None:
        if not persist or run is None:
            return
        run.status = "completed"
        run.completed_at = utc_now()
        run.candidate_count = candidate_count
        run.paper_bid_count = paper_bid_count
        run.settled_count = settled_count
        run.result_payload = json.dumps(summary, ensure_ascii=False, default=str)
        db.add(run)
        db.commit()
        db.refresh(run)

    def _fail_run(
        self, db: Session, *, run: PaperBidRun | None, persist: bool, error_message: str
    ) -> None:
        if not persist or run is None:
            return
        run.status = "failed"
        run.completed_at = utc_now()
        run.error_message = error_message
        db.add(run)
        db.commit()
