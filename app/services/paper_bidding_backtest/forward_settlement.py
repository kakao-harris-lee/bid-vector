"""Deferred settlement of previously-generated forward paper bids."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import exists
from sqlalchemy.orm import Session, selectinload

from app.core.time import ensure_utc, utc_now
from app.models.models import (
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    TenderResult,
)
from app.services.query_predicates import settled_with_amount
from app.services.paper_bidding_backtest.base import _PaperBiddingBase


class _ForwardSettlementMixin(_PaperBiddingBase):
    """Scan past-deadline forward paper bids and settle them idempotently."""

    def run_forward_settlement(
        self,
        db: Session,
        *,
        operator_id: int | None = None,
        limit: int = 200,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Settle previously-generated forward paper bids whose deadline has passed.

        Forward paper bids are created without a settlement (the tender result does
        not exist yet). This method scans unsettled paper bids whose linked project
        deadline is in the past and for which a ``TenderResult`` now exists, then
        reuses the existing settlement math (:meth:`_build_settlement_item`) to
        persist a :class:`PaperBidSettlement` per paper bid.

        The scan is idempotent: paper bids that already carry a settlement are
        skipped, and the ``paper_bid_id`` unique constraint is a second line of
        defence against duplicates.

        When ``operator_id`` is provided only that operator's paper bids are
        considered; otherwise every operator's unsettled forward paper bid is
        eligible. ``persist=False`` computes settlements without writing them
        (used by tests).
        """
        safe_limit = max(1, int(limit or 1))
        now = utc_now()

        candidates = self._load_unsettled_forward_paper_bids(
            db, operator_id=operator_id, now=now, limit=safe_limit
        )

        settled_count = 0
        skipped_count = 0
        scanned_count = 0
        settlement_items: list[dict[str, Any]] = []

        for paper_bid in candidates:
            scanned_count += 1

            # Defensive idempotency: re-check the settlement relation in case a
            # concurrent run created one between the query and now.
            if paper_bid.settlement is not None:
                skipped_count += 1
                continue

            tender_result = self._latest_tender_result_for_project(
                db, project_id=int(paper_bid.project_id)
            )
            if tender_result is None:
                skipped_count += 1
                continue

            item = self._paper_bid_to_settlement_input(paper_bid)
            settlement = self._build_settlement_item(
                db, item=item, tender_result=tender_result
            )
            settlement_items.append(settlement)
            self._persist_settlement(
                db,
                paper_bid=paper_bid,
                tender_result=tender_result,
                settlement=settlement,
                persist=persist,
            )
            settled_count += 1

        if persist and settled_count:
            db.commit()

        summary = self._build_summary(
            candidate_items=[],
            settlement_items=settlement_items,
            skipped_by_strategy=0,
            action_counts=Counter(),
        )

        return {
            "operator_id": int(operator_id) if operator_id is not None else None,
            "scanned_count": scanned_count,
            "settled_count": settled_count,
            "skipped_count": skipped_count,
            "limit": safe_limit,
            "persist": persist,
            "summary": summary,
            "settlements": settlement_items,
        }

    def _load_unsettled_forward_paper_bids(
        self,
        db: Session,
        *,
        operator_id: int | None,
        now: datetime,
        limit: int,
    ) -> list[PaperBid]:
        """Load unsettled paper bids whose linked project deadline has passed.

        Prefers paper bids belonging to ``forward_paper`` runs (the ones that are
        never settled at generation time) but also picks up any unsettled paper bid
        whose run mode is unknown/null, so legacy rows are not stranded. Bids with
        an existing settlement, a null/future deadline, or no deadline at all are
        excluded. The ``deadline`` comparison is done in Python via ``ensure_utc``
        so naive timestamps (SQLite test rows) are normalised consistently.

        Only bids whose project already has a *usable* ``TenderResult``
        (``winning_amount > 0``) are loaded, via a correlated ``EXISTS`` filter.
        This prevents head-of-line starvation: old past-deadline bids that will
        never receive a result no longer consume the bounded scan budget and
        starve genuinely settle-able (but later-ordered) bids.
        """
        query = (
            db.query(PaperBid)
            .join(Project, Project.id == PaperBid.project_id)
            .outerjoin(PaperBidSettlement, PaperBidSettlement.paper_bid_id == PaperBid.id)
            .outerjoin(PaperBidRun, PaperBidRun.id == PaperBid.run_id)
            .options(
                selectinload(PaperBid.project),
                selectinload(PaperBid.settlement),
            )
            .filter(
                PaperBidSettlement.id.is_(None),
                Project.deadline.isnot(None),
                (PaperBidRun.mode == "forward_paper") | (PaperBidRun.mode.is_(None)),
                exists().where(
                    (TenderResult.project_id == PaperBid.project_id)
                    & settled_with_amount()
                ),
            )
        )
        if operator_id is not None:
            query = query.filter(PaperBid.operator_id == int(operator_id))

        rows = (
            query.order_by(Project.deadline.asc(), PaperBid.id.asc())
            .limit(limit * 4)
            .all()
        )

        eligible: list[PaperBid] = []
        for paper_bid in rows:
            project = paper_bid.project
            if project is None or project.deadline is None:
                continue
            if ensure_utc(project.deadline) >= now:
                continue
            eligible.append(paper_bid)
            if len(eligible) >= limit:
                break
        return eligible

    def _latest_tender_result_for_project(
        self, db: Session, *, project_id: int
    ) -> TenderResult | None:
        """Return the most relevant TenderResult for *project_id*, if any.

        Picks the row with a usable winning amount, preferring the most recently
        announced/created result so re-notices settle against the final award.
        """
        rows = (
            db.query(TenderResult)
            .filter(
                TenderResult.project_id == int(project_id),
                settled_with_amount(),
            )
            .all()
        )
        if not rows:
            return None
        return max(
            rows,
            key=lambda result: (self._result_time(result), int(result.id or 0)),
        )

    def _paper_bid_to_settlement_input(self, paper_bid: PaperBid) -> dict[str, Any]:
        """Adapt a persisted :class:`PaperBid` to the candidate dict shape.

        :meth:`_build_settlement_item` only reads ``project_id``, ``category``,
        ``budget_estimate``, ``paper_bid_amount`` and ``paper_bid_rate`` from the
        item, so this thin adapter avoids re-implementing any settlement math. The
        budget estimate comes from the linked project (falling back to 0.0 so the
        existing guards in ``_build_settlement_item`` apply unchanged).
        """
        project = paper_bid.project
        budget = self._resolve_project_budget(project) if project is not None else 0.0
        return {
            "project_id": int(paper_bid.project_id),
            "category": getattr(project, "category", None) if project else None,
            "budget_estimate": float(budget or 0.0),
            "paper_bid_amount": float(paper_bid.paper_bid_amount or 0.0),
            "paper_bid_rate": float(paper_bid.paper_bid_rate or 0.0),
        }
