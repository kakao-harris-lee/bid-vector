"""Historical paper-bidding backtest service."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import exists, func
from sqlalchemy.orm import Session, selectinload

from app.ai.business_group import resolve_business_group
from app.ai.factory import build_price_prediction_port
from app.ai.predictors.historical import apply_probability_calibration
from app.ai.price_prediction import _resolve_floor_bid_rate
from app.ai.service_interfaces import PricePredictionPort
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile_for,
    ensure_operator_strategy_for,
    split_multi_value_text,
)
from app.core.time import ensure_utc, utc_now
from app.domain.aggregates import average, error_rate
from app.domain.rate_normalization import to_bid_rate_fraction
from app.models.models import (
    CompanyProfile,
    HistoricalData,
    OperatorStrategy,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    TenderResult,
    User,
)
from app.schemas.schemas import BidDecisionRequest
from app.services.allocation import BidDecisionService
from app.services.backtest_cutoff import BacktestCutoffService
from app.services.bid_base import (
    prepare_prediction_inputs,
    resolve_notice_bid_base,
)
from app.services.classifier import NoticeClassifierService
from app.services.query_predicates import open_projects, settled_with_amount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidatePredictionContext:
    budget: float
    data_cutoff_at: datetime
    history: list[dict[str, Any]]
    business_group: str | None
    prediction: dict[str, Any]


@dataclass(frozen=True)
class CandidateDecisionContext:
    selected_scenario: dict[str, Any]
    paper_bid_amount: float
    paper_bid_rate: float
    matched_score: float
    match_reasons: list[str]
    match_source: str
    probability_score: float
    decision: dict[str, Any]
    action: str


class OperatorNotFoundError(ValueError):
    """Raised when a backtest is requested for an ``operator_id`` that does not exist.

    Per-operator backtests (e.g. synthetic operator comparisons) must never silently
    fall back to the canonical operator, since that would let a non-existent operator
    pollute the canonical operator's strategy/profile/paper-bid data. Callers that accept
    an ``operator_id`` (the API layer) translate this into a ``404``.
    """


class PaperBiddingBacktestService:
    """Replay historical awards as paper-bidding opportunities."""

    DEFAULT_SETTLE_ACTIONS = ("bid_now",)
    DEFAULT_SCENARIO = "base"

    # Fallback heuristic weights for the backtest P(win) signal, used only when
    # no settlement-calibrated curve is published (see _estimate_win_probability).
    # These weights are specific to the backtest P(win) fallback and are
    # unrelated to the opportunity_analysis probability_score blend weights or
    # the allocation opportunity-score weights — do not merge them. They sum to
    # 1.0.
    WIN_PROBABILITY_MATCHED_WEIGHT = 0.38
    WIN_PROBABILITY_CONFIDENCE_WEIGHT = 0.42
    WIN_PROBABILITY_HISTORY_WEIGHT = 0.20

    # Competitiveness heuristic: a paper-bid rate closest to the target rate is
    # most competitive; the tolerance scales how quickly the score decays away
    # from the target. Specific to _estimate_competitiveness_score.
    COMPETITIVENESS_TARGET_RATE = 0.88
    COMPETITIVENESS_RATE_TOLERANCE = 0.15

    # Budget-tier execution-complexity scores: larger budgets map to higher
    # complexity. Thresholds are in KRW. Specific to
    # _estimate_execution_complexity_score.
    EXECUTION_COMPLEXITY_TIER1_BUDGET = 500_000_000
    EXECUTION_COMPLEXITY_TIER2_BUDGET = 200_000_000
    EXECUTION_COMPLEXITY_TIER3_BUDGET = 100_000_000
    EXECUTION_COMPLEXITY_TIER1_SCORE = 0.82
    EXECUTION_COMPLEXITY_TIER2_SCORE = 0.68
    EXECUTION_COMPLEXITY_TIER3_SCORE = 0.52
    EXECUTION_COMPLEXITY_DEFAULT_SCORE = 0.36

    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
    ) -> None:
        self.cutoff_service = BacktestCutoffService()
        self.decision_service = BidDecisionService()
        self.classifier = NoticeClassifierService()
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()

    def run_historical_backtest(
        self,
        db: Session,
        *,
        operator_id: int | None = None,
        category: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
        scenario: str = DEFAULT_SCENARIO,
        strategy_version: str = "local-backtest",
        model_version: str = "current",
        cutoff_hours_before_deadline: int = 2,
        history_limit: int = 80,
        settle_actions: Sequence[str] | None = None,
        persist: bool = False,
        award_categories: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Generate paper bids from historical awards and settle them immediately.

        ``award_categories`` scopes the replay award pool to those project
        categories (in addition to the explicit ``category`` filter). When the
        caller omits it AND no explicit ``category`` is given, it defaults to the
        operator strategy's focus categories so a focus-category operator draws
        its window from its OWN categories — without this, a minority category
        (e.g. recently-backfilled goods) is starved out of any bounded ``limit``
        window dominated by service/construction awards.
        """
        operator = self._resolve_operator(db, operator_id=operator_id)
        strategy = self._resolve_operator_strategy(db, operator=operator)
        profile = self._resolve_operator_profile(db, operator=operator)
        resolved_award_categories: tuple[str, ...] = ()
        if award_categories:
            resolved_award_categories = tuple(
                str(value).strip() for value in award_categories if str(value).strip()
            )
        elif not category:
            resolved_award_categories = tuple(
                split_multi_value_text(getattr(strategy, "focus_categories", None))
            )
        normalized_settle_actions = self._normalize_actions(
            settle_actions or self.DEFAULT_SETTLE_ACTIONS
        )
        normalized_scenario = (
            str(scenario or self.DEFAULT_SCENARIO).strip() or self.DEFAULT_SCENARIO
        )
        start_at = ensure_utc(start_at) if start_at is not None else None
        end_at = ensure_utc(end_at) if end_at is not None else None
        safe_limit = max(1, int(limit or 1))

        request_payload = {
            "category": category,
            "award_categories": list(resolved_award_categories),
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "limit": safe_limit,
            "scenario": normalized_scenario,
            "strategy_version": strategy_version,
            "model_version": model_version,
            "cutoff_hours_before_deadline": cutoff_hours_before_deadline,
            "history_limit": history_limit,
            "settle_actions": list(normalized_settle_actions),
            "persist": persist,
        }
        run = self._create_run(
            db,
            operator_id=int(operator.id),
            request_payload=request_payload,
            persist=persist,
            category=category,
            scenario=normalized_scenario,
            strategy_version=strategy_version,
            model_version=model_version,
            start_at=start_at,
            end_at=end_at,
            cutoff_hours_before_deadline=cutoff_hours_before_deadline,
            mode="historical_backtest",
        )

        candidate_items: list[dict[str, Any]] = []
        settlement_items: list[dict[str, Any]] = []
        action_counts: Counter[str] = Counter()

        try:
            awards = self._load_eligible_awards(
                db,
                category=category,
                start_at=start_at,
                end_at=end_at,
                limit=safe_limit,
                categories=resolved_award_categories or None,
            )
            skipped_by_strategy = self._process_historical_awards(
                db,
                awards=awards,
                strategy=strategy,
                profile=profile,
                run=run,
                operator_id=int(operator.id),
                scenario=normalized_scenario,
                strategy_version=strategy_version,
                model_version=model_version,
                cutoff_hours_before_deadline=cutoff_hours_before_deadline,
                history_limit=history_limit,
                settle_actions=normalized_settle_actions,
                persist=persist,
                candidate_items=candidate_items,
                settlement_items=settlement_items,
                action_counts=action_counts,
            )
            return self._complete_historical_backtest(
                db,
                run=run,
                persist=persist,
                request_payload=request_payload,
                candidate_items=candidate_items,
                settlement_items=settlement_items,
                skipped_by_strategy=skipped_by_strategy,
                action_counts=action_counts,
                settle_actions=normalized_settle_actions,
            )
        except Exception as exc:
            self._fail_run(db, run=run, persist=persist, error_message=str(exc))
            raise

    def _process_historical_awards(
        self,
        db: Session,
        *,
        awards: Sequence[TenderResult],
        strategy: OperatorStrategy,
        profile: CompanyProfile | None,
        run: PaperBidRun | None,
        operator_id: int,
        scenario: str,
        strategy_version: str,
        model_version: str,
        cutoff_hours_before_deadline: int,
        history_limit: int,
        settle_actions: Sequence[str],
        persist: bool,
        candidate_items: list[dict[str, Any]],
        settlement_items: list[dict[str, Any]],
        action_counts: Counter[str],
    ) -> int:
        skipped_by_strategy = 0
        for tender_result in awards:
            project = tender_result.project
            if project is None:
                continue
            if not self._passes_strategy(project, strategy):
                skipped_by_strategy += 1
                continue

            item = self._build_candidate_item(
                db,
                project=project,
                tender_result=tender_result,
                data_cutoff_at=None,
                scenario=scenario,
                strategy_version=strategy_version,
                cutoff_hours_before_deadline=cutoff_hours_before_deadline,
                history_limit=history_limit,
                profile=profile,
            )
            action_counts[item["action"]] += 1
            candidate_items.append(item)

            paper_bid = self._persist_paper_bid(
                db,
                run=run,
                operator_id=operator_id,
                item=item,
                persist=persist,
                model_version=model_version,
                strategy_version=strategy_version,
            )
            if item["action"] not in settle_actions:
                continue

            settlement = self._build_settlement_item(
                db,
                item=item,
                tender_result=tender_result,
            )
            settlement_items.append(settlement)
            self._persist_settlement(
                db,
                paper_bid=paper_bid,
                tender_result=tender_result,
                settlement=settlement,
                persist=persist,
            )
        return skipped_by_strategy

    def _complete_historical_backtest(
        self,
        db: Session,
        *,
        run: PaperBidRun | None,
        persist: bool,
        request_payload: dict[str, Any],
        candidate_items: list[dict[str, Any]],
        settlement_items: list[dict[str, Any]],
        skipped_by_strategy: int,
        action_counts: Counter[str],
        settle_actions: Sequence[str],
    ) -> dict[str, Any]:
        summary = self._build_summary(
            candidate_items=candidate_items,
            settlement_items=settlement_items,
            skipped_by_strategy=skipped_by_strategy,
            action_counts=action_counts,
        )
        self._complete_run(
            db,
            run=run,
            persist=persist,
            summary=summary,
            candidate_count=len(candidate_items),
            paper_bid_count=sum(
                1 for item in candidate_items if item["action"] in settle_actions
            ),
            settled_count=len(settlement_items),
        )
        return {
            "run_id": int(run.id) if run is not None and run.id is not None else None,
            "request": request_payload,
            "summary": summary,
            "items": candidate_items,
            "settlements": settlement_items,
        }

    def run_forward_paper_bidding(
        self,
        db: Session,
        *,
        operator_id: int | None = None,
        category: str | None = None,
        limit: int = 100,
        scenario: str = DEFAULT_SCENARIO,
        strategy_version: str = "forward-paper",
        model_version: str = "current",
        history_limit: int = 80,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Generate paper bids for currently open/re-notice projects without settlement."""
        operator = self._resolve_operator(db, operator_id=operator_id)
        strategy = self._resolve_operator_strategy(db, operator=operator)
        profile = self._resolve_operator_profile(db, operator=operator)
        normalized_scenario = (
            str(scenario or self.DEFAULT_SCENARIO).strip() or self.DEFAULT_SCENARIO
        )
        safe_limit = max(1, int(limit or 1))
        data_cutoff_at = utc_now()
        request_payload = {
            "category": category,
            "limit": safe_limit,
            "scenario": normalized_scenario,
            "strategy_version": strategy_version,
            "model_version": model_version,
            "history_limit": history_limit,
            "persist": persist,
            "data_cutoff_at": data_cutoff_at.isoformat(),
        }
        run = self._create_run(
            db,
            operator_id=int(operator.id),
            request_payload=request_payload,
            persist=persist,
            category=category,
            scenario=normalized_scenario,
            strategy_version=strategy_version,
            model_version=model_version,
            start_at=None,
            end_at=None,
            cutoff_hours_before_deadline=0,
            mode="forward_paper",
        )

        candidate_items: list[dict[str, Any]] = []
        action_counts: Counter[str] = Counter()

        try:
            projects = self._load_forward_projects(
                db, category=category, limit=safe_limit, data_cutoff_at=data_cutoff_at
            )
            skipped_by_strategy, skipped_invalid = self._process_forward_projects(
                db,
                projects=projects,
                strategy=strategy,
                profile=profile,
                run=run,
                operator_id=int(operator.id),
                data_cutoff_at=data_cutoff_at,
                scenario=normalized_scenario,
                strategy_version=strategy_version,
                model_version=model_version,
                history_limit=history_limit,
                persist=persist,
                candidate_items=candidate_items,
                action_counts=action_counts,
            )
            return self._complete_forward_paper_run(
                db,
                run=run,
                persist=persist,
                request_payload=request_payload,
                candidate_items=candidate_items,
                skipped_by_strategy=skipped_by_strategy,
                skipped_invalid=skipped_invalid,
                action_counts=action_counts,
            )
        except Exception as exc:
            self._fail_run(db, run=run, persist=persist, error_message=str(exc))
            raise

    def _process_forward_projects(
        self,
        db: Session,
        *,
        projects: Sequence[Project],
        strategy: OperatorStrategy,
        profile: CompanyProfile | None,
        run: PaperBidRun | None,
        operator_id: int,
        data_cutoff_at: datetime,
        scenario: str,
        strategy_version: str,
        model_version: str,
        history_limit: int,
        persist: bool,
        candidate_items: list[dict[str, Any]],
        action_counts: Counter[str],
    ) -> tuple[int, int]:
        skipped_by_strategy = 0
        skipped_invalid = 0
        for project in projects:
            if not self._passes_strategy(project, strategy):
                skipped_by_strategy += 1
                continue
            try:
                item = self._build_candidate_item(
                    db,
                    project=project,
                    tender_result=None,
                    data_cutoff_at=data_cutoff_at,
                    scenario=scenario,
                    strategy_version=strategy_version,
                    cutoff_hours_before_deadline=0,
                    history_limit=history_limit,
                    profile=profile,
                )
            except ValueError as project_exc:
                # A single malformed project (e.g. 0 budget for ebiz4u-link
                # imports) must not abort the whole run — skip + count it.
                logger.warning(
                    "forward_paper: skipping project %s due to %s",
                    getattr(project, "id", "?"),
                    project_exc,
                )
                skipped_invalid += 1
                continue
            action_counts[item["action"]] += 1
            candidate_items.append(item)
            self._persist_paper_bid(
                db,
                run=run,
                operator_id=operator_id,
                item=item,
                persist=persist,
                model_version=model_version,
                strategy_version=strategy_version,
            )
        return skipped_by_strategy, skipped_invalid

    def _complete_forward_paper_run(
        self,
        db: Session,
        *,
        run: PaperBidRun | None,
        persist: bool,
        request_payload: dict[str, Any],
        candidate_items: list[dict[str, Any]],
        skipped_by_strategy: int,
        skipped_invalid: int,
        action_counts: Counter[str],
    ) -> dict[str, Any]:
        summary = self._build_summary(
            candidate_items=candidate_items,
            settlement_items=[],
            skipped_by_strategy=skipped_by_strategy,
            action_counts=action_counts,
            skipped_invalid=skipped_invalid,
        )
        self._complete_run(
            db,
            run=run,
            persist=persist,
            summary=summary,
            candidate_count=len(candidate_items),
            paper_bid_count=sum(
                1 for item in candidate_items if item["action"] in {"bid_now", "review"}
            ),
            settled_count=0,
        )
        return {
            "run_id": int(run.id) if run is not None and run.id is not None else None,
            "request": request_payload,
            "summary": summary,
            "items": candidate_items,
            "settlements": [],
        }

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

    def _load_eligible_awards(
        self,
        db: Session,
        *,
        category: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
        categories: Sequence[str] | None = None,
    ) -> list[TenderResult]:
        """Load the most recent settled award per project for the replay window.

        ``categories`` (when given) restricts the pool to those project
        categories — this is how a focus-category operator draws its window from
        its OWN categories instead of the global pool. Without it, a minority
        category (e.g. recently-backfilled goods) is starved out of any bounded
        ``limit`` window dominated by service/construction awards. ``category``
        (singular) stays for the explicit single-category path; when both are
        passed the singular value is appended to the set.

        The category filter is case-insensitive (``func.lower(Project.category)``)
        so the explicit ``category=`` path matches the same way the downstream
        ``_passes_strategy`` lower-cased comparison does — no silent miss on a
        mixed-case stored category.

        Caveat: the window is cut MOST-RECENT-FIRST (this changed from the prior
        oldest-first ordering). A ``limit``-bounded run therefore keeps the
        freshest awards, so absolute counts from a bounded run are NOT directly
        comparable to a pre-change run over the same ``limit``.
        """
        category_filter: set[str] = set()
        if category:
            category_filter.add(str(category).strip().lower())
        for value in categories or ():
            normalized = str(value or "").strip().lower()
            if normalized:
                category_filter.add(normalized)

        query = (
            db.query(TenderResult)
            .join(Project, Project.id == TenderResult.project_id)
            .options(selectinload(TenderResult.project))
            .filter(
                TenderResult.project_id.isnot(None),
                settled_with_amount(),
            )
        )
        if category_filter:
            query = query.filter(
                func.lower(Project.category).in_(sorted(category_filter))
            )

        rows = query.all()
        latest_by_project: dict[int, TenderResult] = {}
        for row in rows:
            result_time = self._result_time(row)
            if start_at is not None and result_time < start_at:
                continue
            if end_at is not None and result_time > end_at:
                continue
            project_id = int(row.project_id or 0)
            current = latest_by_project.get(project_id)
            if (
                current is None
                or self._result_time(row) > self._result_time(current)
                or int(row.id or 0) > int(current.id or 0)
            ):
                latest_by_project[project_id] = row

        # Most-recent-first so a bounded window keeps the freshest awards (and so
        # a focus-category operator sees its newest settled opportunities).
        return sorted(
            latest_by_project.values(),
            key=lambda result: (self._result_time(result), int(result.id or 0)),
            reverse=True,
        )[:limit]

    def _load_forward_projects(
        self,
        db: Session,
        *,
        category: str | None,
        limit: int,
        data_cutoff_at: datetime,
    ) -> list[Project]:
        query = db.query(Project).filter(open_projects())
        if category:
            query = query.filter(Project.category == category)
        query = query.filter(
            (Project.deadline.is_(None)) | (Project.deadline > data_cutoff_at),
        )
        return (
            query.order_by(
                Project.deadline.asc().nullslast(),
                Project.created_at.desc(),
                Project.id.desc(),
            )
            .limit(max(1, int(limit or 1)))
            .all()
        )

    def _build_candidate_item(
        self,
        db: Session,
        *,
        project: Project,
        tender_result: TenderResult | None,
        data_cutoff_at: datetime | None,
        scenario: str,
        strategy_version: str,
        cutoff_hours_before_deadline: int,
        history_limit: int,
        profile: CompanyProfile | None = None,
    ) -> dict[str, Any]:
        prediction_context = self._build_candidate_prediction_context(
            db,
            project=project,
            tender_result=tender_result,
            data_cutoff_at=data_cutoff_at,
            cutoff_hours_before_deadline=cutoff_hours_before_deadline,
            history_limit=history_limit,
        )
        decision_context = self._build_candidate_decision_context(
            db,
            project=project,
            prediction_context=prediction_context,
            scenario=scenario,
            profile=profile,
        )
        return self._build_candidate_payload(
            project=project,
            prediction_context=prediction_context,
            decision_context=decision_context,
            strategy_version=strategy_version,
        )

    def _build_candidate_prediction_context(
        self,
        db: Session,
        *,
        project: Project,
        tender_result: TenderResult | None,
        data_cutoff_at: datetime | None,
        cutoff_hours_before_deadline: int,
        history_limit: int,
    ) -> CandidatePredictionContext:
        budget = self._resolve_project_budget(project)
        if budget <= 0:
            raise ValueError(f"Project {project.id} has no usable budget")

        # 예측 전처리를 라이브 경로와 동일한 단일 조합 헬퍼로 해석한다: 기초금액 base
        # (과거 낙찰률이 base 기준으로 정규화돼 있어 과세 공고 systematically 탈락 왜곡
        # 방지), title 포함 text, 그리고 이전까지 백테스트가 빠뜨렸던 공고 published
        # 낙찰하한(award_floor_rate, #201) 을 함께 받아 라이브가 강제하는 floor 를
        # 정확도 측정에도 태운다. (candidate 는 Project+TenderResult 에서 만들어져
        # HistoricalData 가 이 지점에 없으므로 base 가 0 이면 est(``budget``) 폴백.)
        # 보고용 budget_estimate 필드와 전략 예산밴드 필터는 그대로 est(``budget``)를 쓴다.
        inputs = prepare_prediction_inputs(db, project)
        bid_base = inputs.bid_base
        if bid_base <= 0:
            bid_base = budget

        data_cutoff_at = data_cutoff_at or self.cutoff_service.resolve_data_cutoff_at(
            project,
            tender_result=tender_result,
            hours_before_deadline=cutoff_hours_before_deadline,
        )
        history = self.cutoff_service.load_price_history_at_cutoff(
            db,
            category=project.category,
            agency_name=project.issuing_agency or project.demand_agency,
            cutoff_at=data_cutoff_at,
            exclude_project_id=int(project.id),
            limit=history_limit,
            explicit_bid_rate_only=True,
        )
        business_type_code = getattr(project, "business_type_code", None)
        business_group = resolve_business_group(business_type_code)
        prediction = self.price_prediction_port.predict_price(
            budget=bid_base,
            category=project.category or "other",
            description=inputs.text,
            historical_records=history,
            agency_name=project.issuing_agency or project.demand_agency,
            feedback_calibration=None,
            business_type_code=business_type_code,
            business_group=business_group,
            # 공고 자신의 published 낙찰하한율(era-correct — 공고 시점 공개, 개찰 후
            # 정보 아님). guardrail_core 가 max() 로만 폴드하므로 floor 를 올리기만 한다.
            legal_floor_bid_rate=inputs.legal_floor_bid_rate,
            estimation_amount=inputs.estimation_amount,
            reference_date=inputs.reference_date,
        )
        return CandidatePredictionContext(
            budget=budget,
            data_cutoff_at=data_cutoff_at,
            history=history,
            business_group=business_group,
            prediction=prediction,
        )

    def _build_candidate_decision_context(
        self,
        db: Session,
        *,
        project: Project,
        prediction_context: CandidatePredictionContext,
        scenario: str,
        profile: CompanyProfile | None,
    ) -> CandidateDecisionContext:
        budget = prediction_context.budget
        prediction = prediction_context.prediction
        selected_scenario = self._select_scenario(prediction, scenario=scenario)
        paper_bid_amount = round(float(selected_scenario["predicted_price"]), 2)
        paper_bid_rate = self._normalize_rate(
            float(selected_scenario.get("bid_rate") or (paper_bid_amount / budget))
        )
        matched_score, match_reasons, match_source = self._resolve_matched_score(
            project=project, profile=profile
        )
        probability_score = self._estimate_probability_score(
            matched_score=matched_score,
            prediction=prediction,
            history_count=len(prediction_context.history),
            business_group=prediction_context.business_group,
        )
        deadline_hours_remaining = self._deadline_hours_remaining(
            project=project,
            data_cutoff_at=prediction_context.data_cutoff_at,
        )
        decision = self.decision_service.evaluate_opportunity(
            BidDecisionRequest(
                project_id=int(project.id),
                recommended_amount=paper_bid_amount,
                probability_score=probability_score,
                matched_score=matched_score,
                deadline_hours_remaining=deadline_hours_remaining,
                current_active_bids=0,
                max_active_bids=3,
                current_workload_score=0.0,
                budget_estimate=budget,
                competitiveness_score=self._estimate_competitiveness_score(
                    paper_bid_rate
                ),
                expected_margin_score=self._estimate_expected_margin_score(
                    paper_bid_rate
                ),
                execution_complexity_score=self._estimate_execution_complexity_score(
                    project
                ),
                workload_source="provided",
            ),
            db=db,
        )
        action = str(decision["action"])
        return CandidateDecisionContext(
            selected_scenario=selected_scenario,
            paper_bid_amount=paper_bid_amount,
            paper_bid_rate=paper_bid_rate,
            matched_score=matched_score,
            match_reasons=match_reasons,
            match_source=match_source,
            probability_score=probability_score,
            decision=decision,
            action=action,
        )

    def _build_candidate_payload(
        self,
        *,
        project: Project,
        prediction_context: CandidatePredictionContext,
        decision_context: CandidateDecisionContext,
        strategy_version: str,
    ) -> dict[str, Any]:
        budget = prediction_context.budget
        prediction = prediction_context.prediction
        selected_scenario = decision_context.selected_scenario
        decision = decision_context.decision
        return {
            "project_id": int(project.id),
            "project_title": project.title,
            "notice_number": project.notice_number,
            "category": project.category,
            "issuing_agency": project.issuing_agency,
            "data_cutoff_at": prediction_context.data_cutoff_at.isoformat(),
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "budget_estimate": round(budget, 2),
            "scenario": str(selected_scenario["label"]),
            "action": decision_context.action,
            "decision_status": self._decision_status_for_action(decision_context.action),
            "paper_bid_amount": decision_context.paper_bid_amount,
            "paper_bid_rate": round(decision_context.paper_bid_rate, 6),
            "priority_score": float(decision["priority_score"]),
            "probability_score": decision_context.probability_score,
            "matched_score": decision_context.matched_score,
            "predicted_price": float(prediction.get("predicted_price", 0.0) or 0.0),
            "predicted_bid_rate": self._normalize_rate(
                float(prediction.get("predicted_bid_rate", 0.0) or 0.0)
            ),
            "price_range_min": float(prediction.get("price_range_min", 0.0) or 0.0),
            "price_range_max": float(prediction.get("price_range_max", 0.0) or 0.0),
            "confidence_score": float(prediction.get("confidence_score", 0.0) or 0.0),
            "predictor_name": str(
                prediction.get("predictor_name") or "historical_statistical"
            ),
            "predictor_family": str(
                prediction.get("predictor_family") or "statistical"
            ),
            "model_version": str(prediction.get("model_version") or "current"),
            "strategy_version": strategy_version,
            "historical_sample_size": len(prediction_context.history),
            "history_ids": [
                int(record["historical_data_id"])
                for record in prediction_context.history
                if record.get("historical_data_id") is not None
            ],
            "input_snapshot_hash": self._build_input_hash(
                project=project,
                data_cutoff_at=prediction_context.data_cutoff_at,
                scenario=str(selected_scenario["label"]),
                history=prediction_context.history,
                paper_bid_amount=decision_context.paper_bid_amount,
                strategy_version=strategy_version,
            ),
            "matched_score_source": decision_context.match_source,
            "match_reasons": decision_context.match_reasons,
            "reasoning": self._compose_reasoning(
                decision_reasoning=str(decision["reasoning"]),
                match_reasons=decision_context.match_reasons,
                match_source=decision_context.match_source,
            ),
        }

    def _build_settlement_item(
        self, db: Session, *, item: dict[str, Any], tender_result: TenderResult
    ) -> dict[str, Any]:
        winning_amount = float(tender_result.winning_amount or 0.0)
        winning_rate = self._normalize_rate(float(tender_result.winning_rate or 0.0))
        if winning_rate <= 0:
            # winning_rate 는 winning_amount / base_amount (기초금액; scsbid.py 와
            # 동일한 base-relative 정규화)이므로, 결측 시 유도값도 추정가격(ex-VAT)이
            # 아니라 base 로 나눠야 한다. base 로 나누지 않으면 과세 공고에서
            # winning_rate 가 ~10% 높게 잡혀 paper bid(이제 base 기준)와 비교가
            # 어긋난다. base 를 못 구하면 보고용 budget_estimate 로 폴백한다.
            bid_base = self._resolve_settlement_bid_base(
                db, tender_result=tender_result, item=item
            )
            if bid_base > 0:
                winning_rate = self._normalize_rate(winning_amount / bid_base)

        paper_bid_amount = float(item["paper_bid_amount"] or 0.0)
        paper_bid_rate = self._normalize_rate(float(item["paper_bid_rate"] or 0.0))
        amount_delta = paper_bid_amount - winning_amount
        # error_rate 는 winning_amount <= 0 이면 None → 기존 else 0.0 폴백을 or 0.0 로 흡수.
        absolute_error_rate = error_rate(paper_bid_amount, winning_amount) or 0.0
        bid_rate_delta = paper_bid_rate - winning_rate
        absolute_bid_rate_error = abs(bid_rate_delta)
        price_close = absolute_bid_rate_error <= 0.003
        price_competitive = absolute_bid_rate_error <= 0.01
        would_have_won_price_only = (
            "plausible"
            if price_close
            else "competitive"
            if price_competitive
            else "unlikely"
        )

        # Eligibility gate. The 예정가격/낙찰하한가 come from the award's settled
        # reserve-price detail, so using them at settlement (scoring) time is not a
        # leak -- they are part of the tender *result*, not the prediction input.
        estimated_price, floor_price = self._resolve_settlement_floor(
            db,
            project_id=item.get("project_id"),
            category=item.get("category"),
        )
        would_have_won_final, eligibility_reason = self._score_eligibility_gate(
            our_amount=paper_bid_amount,
            winning_amount=winning_amount,
            estimated_price=estimated_price,
            floor_price=floor_price,
        )

        return {
            "project_id": item["project_id"],
            # Breakdown keys (Phase 2 Experiment Lab). Additive only -- existing
            # consumers ignore unknown keys. ``category``/``budget_estimate`` come
            # straight from the candidate item so the settlement can be grouped by
            # category and budget band downstream without re-querying the project.
            "category": item.get("category"),
            "budget_estimate": float(item.get("budget_estimate") or 0.0),
            # Award announcement time (안내일/개찰일) -- used downstream to surface
            # per-category data freshness so thin/stale categories are visible.
            "result_time": self._result_time(tender_result).isoformat(),
            "tender_result_id": int(tender_result.id),
            "result_status": str(tender_result.result_status or "awarded"),
            "winning_company": tender_result.winning_company,
            "winning_amount": round(winning_amount, 2),
            "winning_rate": round(winning_rate, 6),
            "amount_delta": round(amount_delta, 2),
            "absolute_error_rate": round(absolute_error_rate, 6),
            "bid_rate_delta": round(bid_rate_delta, 6),
            "absolute_bid_rate_error": round(absolute_bid_rate_error, 6),
            "price_close": price_close,
            "price_competitive": price_competitive,
            "would_have_won_price_only": would_have_won_price_only,
            "would_have_won_final": would_have_won_final,
            "estimated_price": (
                round(estimated_price, 2) if estimated_price is not None else None
            ),
            "minimum_bid_price": (
                round(floor_price, 2) if floor_price is not None else None
            ),
            "settlement_reason": eligibility_reason,
        }

    def _resolve_settlement_bid_base(
        self,
        db: Session,
        *,
        tender_result: TenderResult,
        item: dict[str, Any],
    ) -> float:
        """Resolve the base (기초금액/사업금액) to normalize a missing winning_rate against.

        ``winning_rate`` is base-relative (``winning_amount / base_amount``), so a
        derived fallback must divide by the notice base — not 추정가격(ex-VAT). Uses
        the live-path helper on the award's project (기초금액 is a pre-bid attribute,
        leak-safe) and falls back to the reported ``budget_estimate`` (면세 공고면
        두 값이 같아 무해) when the project/base is unavailable.
        """
        project = getattr(tender_result, "project", None)
        if project is not None:
            base = resolve_notice_bid_base(db, project)
            if base > 0:
                return base
        try:
            return float(item.get("budget_estimate") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _score_eligibility_gate(
        self,
        *,
        our_amount: float,
        winning_amount: float,
        estimated_price: float | None,
        floor_price: float | None,
    ) -> tuple[str, str]:
        """Classify a paper bid against the 낙찰하한가 eligibility gate.

        Returns ``(would_have_won_final, settlement_reason)``. ``settlement_reason``
        is Korean and records the gate verdict for audit.

        * ``estimated_price``/``floor_price`` missing -> ``"unknown"`` (honest
          absence -- no reserve/planned data for the project).
        * ``our_amount < floor_price`` -> ``"disqualified"``: below the 낙찰하한가,
          so the bid is invalidated regardless of price proximity (price_close is
          NOT a win).
        * ``floor_price <= our_amount <= winning_amount`` -> ``"eligible_favorable"``:
          eligible and at/under the realized winning amount (lowest-price family).
        * otherwise (eligible but ``our_amount > winning_amount``) ->
          ``"eligible_but_outbid"``.
        """
        if estimated_price is None or floor_price is None:
            return (
                "unknown",
                "예가 정보가 없어 낙찰하한 기준 판정을 보류합니다(데이터 부재).",
            )
        if our_amount < floor_price:
            return (
                "disqualified",
                (
                    f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                    "미만이라 적격 미달로 탈락 처리합니다(가격 근접 여부와 무관)."
                ),
            )
        if our_amount <= winning_amount:
            return (
                "eligible_favorable",
                (
                    f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                    f"이상이며 실낙찰가 {winning_amount:,.0f}원 이하라 적격·유리합니다."
                ),
            )
        return (
            "eligible_but_outbid",
            (
                f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                f"이상으로 적격이나 실낙찰가 {winning_amount:,.0f}원보다 높아 패찰합니다."
            ),
        )

    def _resolve_settlement_floor(
        self,
        db: Session,
        *,
        project_id: Any,
        category: str | None,
    ) -> tuple[float | None, float | None]:
        """Resolve ``(estimated_price, floor_price)`` for a settled project.

        ``estimated_price`` (예정가격) is derived from the project's settled
        ``HistoricalData`` (the award's reserve-price detail):

        1. mean of the *selected* 복수예비가격 (``reserve_prices[n-1]`` for each
           ``n`` in ``selected_numbers``) -- the KONEPS 예정가격 derivation, or
        2. the collected ``predicted_price`` (planned price) when no usable
           selected-reserve mean exists.

        ``floor_price`` (낙찰하한가) = ``estimated_price`` × the category
        minimum-bid-rate floor (``_resolve_floor_bid_rate``). Both are ``None``
        when no estimated price can be derived -- an honest "not enough data"
        signal the gate maps to ``"unknown"``.

        Uses the *category-only* floor (no group-calibrated floor): per §4.7 the
        category floor is the hard lower bound of any group floor, so scoring with
        the category floor never disqualifies a bid the predictor's (possibly
        higher group) floor would have allowed -- it errs on the safe side.
        """
        if project_id is None:
            return (None, None)

        record = (
            db.query(HistoricalData)
            .filter(HistoricalData.project_id == int(project_id))
            .order_by(
                HistoricalData.opened_at.desc().nullslast(),
                HistoricalData.id.desc(),
            )
            .first()
        )
        if record is None:
            return (None, None)

        estimated_price = self._derive_estimated_price(record)
        if estimated_price is None or estimated_price <= 0:
            return (None, None)

        floor_rate = _resolve_floor_bid_rate(category)
        if floor_rate is None or floor_rate <= 0:
            # No configured floor for this category -> cannot apply the gate.
            return (estimated_price, None)

        return (estimated_price, estimated_price * float(floor_rate))

    def _derive_estimated_price(self, record: HistoricalData) -> float | None:
        """Derive 예정가격 from a settled ``HistoricalData`` row.

        Prefers the mean of the *selected* reserve prices (복수예비가격), falling
        back to the collected ``predicted_price``. Returns ``None`` when neither is
        usable.
        """
        reserve_prices = self._coerce_float_list(record.reserve_prices)
        selected_numbers = self._coerce_int_list(record.selected_numbers)
        picked = [
            reserve_prices[number - 1]
            for number in selected_numbers
            if 1 <= number <= len(reserve_prices)
        ]
        picked = [value for value in picked if value and value > 0]
        if picked:
            return sum(picked) / len(picked)

        predicted_price = float(record.predicted_price or 0.0)
        if predicted_price > 0:
            return predicted_price
        return None

    @staticmethod
    def _coerce_float_list(raw_value: Any) -> list[float]:
        """Parse a JSON-encoded numeric list (``reserve_prices`` Text column)."""
        values = PaperBiddingBacktestService._coerce_json_list(raw_value)
        result: list[float] = []
        for value in values:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _coerce_int_list(raw_value: Any) -> list[int]:
        """Parse a JSON-encoded integer list (``selected_numbers`` Text column)."""
        values = PaperBiddingBacktestService._coerce_json_list(raw_value)
        result: list[int] = []
        for value in values:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _coerce_json_list(raw_value: Any) -> list[Any]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple)):
            return list(raw_value)
        text = str(raw_value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return parsed
        return []

    def _build_summary(
        self,
        *,
        candidate_items: list[dict[str, Any]],
        settlement_items: list[dict[str, Any]],
        skipped_by_strategy: int,
        action_counts: Counter[str],
        skipped_invalid: int = 0,
    ) -> dict[str, Any]:
        rate_errors = [
            float(item["absolute_bid_rate_error"]) for item in settlement_items
        ]
        amount_errors = [
            float(item["absolute_error_rate"]) for item in settlement_items
        ]
        return {
            "candidate_count": len(candidate_items),
            "paper_bid_count": int(
                action_counts.get("bid_now", 0) + action_counts.get("review", 0)
            ),
            "review_count": int(action_counts.get("review", 0)),
            "skip_count": int(action_counts.get("skip", 0)),
            "skipped_by_strategy_count": skipped_by_strategy,
            "skipped_invalid_count": skipped_invalid,
            "settled_count": len(settlement_items),
            "action_counts": dict(action_counts),
            "average_absolute_bid_rate_error": self._average(rate_errors),
            "average_absolute_amount_error_rate": self._average(amount_errors),
            "within_0_1pct_count": sum(1 for value in rate_errors if value <= 0.001),
            "within_0_3pct_count": sum(1 for value in rate_errors if value <= 0.003),
            "within_1pct_count": sum(1 for value in rate_errors if value <= 0.01),
            "price_close_count": sum(
                1 for item in settlement_items if item["price_close"]
            ),
            "price_competitive_count": sum(
                1 for item in settlement_items if item["price_competitive"]
            ),
            "would_have_won_price_only_count": sum(
                1
                for item in settlement_items
                if item["would_have_won_price_only"] == "plausible"
            ),
            "would_have_won_final_eligible_favorable_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "eligible_favorable"
            ),
            "would_have_won_final_eligible_but_outbid_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "eligible_but_outbid"
            ),
            "would_have_won_final_disqualified_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "disqualified"
            ),
            "would_have_won_final_unknown_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "unknown"
            ),
        }

    def _passes_strategy(self, project: Project, strategy) -> bool:
        category = str(project.category or "").strip().lower()
        focus_categories = [
            value.lower() for value in split_multi_value_text(strategy.focus_categories)
        ]
        if focus_categories and category not in focus_categories:
            return False

        budget = self._resolve_project_budget(project)
        min_budget = float(strategy.min_budget_estimate or 0.0)
        max_budget = float(strategy.max_budget_estimate or 0.0)
        if min_budget > 0 and budget < min_budget:
            return False
        if max_budget > 0 and budget > max_budget:
            return False

        searchable_text = " ".join(
            part
            for part in [
                project.title,
                project.description,
                project.requirements,
                project.issuing_agency,
                project.demand_agency,
            ]
            if part
        ).lower()
        required_keywords = [
            value.lower()
            for value in split_multi_value_text(strategy.required_keywords)
        ]
        if required_keywords and not all(
            keyword in searchable_text for keyword in required_keywords
        ):
            return False

        exclude_keywords = [
            value.lower() for value in split_multi_value_text(strategy.exclude_keywords)
        ]
        if any(keyword in searchable_text for keyword in exclude_keywords):
            return False
        return True

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

    def _select_scenario(
        self, prediction: dict[str, Any], *, scenario: str
    ) -> dict[str, Any]:
        candidates = prediction.get("bid_rate_candidates") or []
        for candidate in candidates:
            if str(candidate.get("label") or "") == scenario:
                return candidate
        for candidate in candidates:
            if str(candidate.get("label") or "") == self.DEFAULT_SCENARIO:
                return candidate
        return {
            "label": self.DEFAULT_SCENARIO,
            "bid_rate": prediction.get("predicted_bid_rate", 0.0),
            "predicted_price": prediction.get("predicted_price", 0.0),
        }

    def _resolve_project_budget(self, project: Project) -> float:
        for value in [project.budget_estimate, project.budget_max, project.budget_min]:
            budget = float(value or 0.0)
            if budget > 0:
                return budget
        return 0.0

    def _resolve_matched_score(
        self, *, project: Project, profile: CompanyProfile | None
    ) -> tuple[float, list[str], str]:
        """Resolve the matched score for *project* against the operator *profile*.

        When a profile is available the real classifier (license/region/budget/
        capability/semantic axes) drives the score so per-operator profile
        differences are reflected. When no profile exists or the classifier raises,
        fall back to the legacy field-presence heuristic so a single bad project can
        never abort an entire run.

        Returns ``(matched_score, reasons, source)`` where ``source`` is one of
        ``"classifier"`` or ``"heuristic"`` for audit purposes.
        """
        if profile is None:
            return self._estimate_matched_score(project=project), [], "heuristic"

        try:
            classification = self.classifier.classify(project=project, profile=profile)
            score = float(classification.get("score") or 0.0)
            reasons = [str(reason) for reason in (classification.get("reasons") or [])]
            return round(max(0.0, min(1.0, score)), 2), reasons, "classifier"
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(
                "paper_bidding_backtest: classifier failed for project %s, "
                "falling back to heuristic matched score (%s)",
                getattr(project, "id", "?"),
                exc,
            )
            return self._estimate_matched_score(project=project), [], "heuristic"

    def _compose_reasoning(
        self, *, decision_reasoning: str, match_reasons: list[str], match_source: str
    ) -> str:
        """Append profile-fit reasons to the decision reasoning for audit trails.

        Keeps the existing decision reasoning as the leading text so downstream
        consumers that only parse the first sentence stay compatible.
        """
        base = str(decision_reasoning or "").strip()
        if match_source != "classifier" or not match_reasons:
            return base
        joined = " ".join(
            reason.strip() for reason in match_reasons if reason and reason.strip()
        )
        if not joined:
            return base
        fit_note = f"[프로필 매칭] {joined}"
        return f"{base} {fit_note}".strip() if base else fit_note

    def _estimate_matched_score(self, *, project: Project) -> float:
        score = 0.72
        if project.category:
            score += 0.08
        if project.issuing_agency or project.demand_agency:
            score += 0.05
        if project.requirements or project.description:
            score += 0.05
        return round(min(1.0, score), 2)

    def _estimate_probability_score(
        self,
        *,
        matched_score: float,
        prediction: dict[str, Any],
        history_count: int,
        business_group: str | None = None,
    ) -> float:
        """Estimate the낙찰 가능성 (P(win)) signal consumed by the decision engine.

        Prefers the settlement-calibrated curve (summary.probability_calibration in
        the active ensemble artifact) when present; otherwise falls back to the
        legacy heuristic so offline / fresh environments keep working unchanged.
        """
        confidence = max(
            0.0, min(1.0, float(prediction.get("confidence_score", 0.0) or 0.0))
        )
        calibrated = apply_probability_calibration(
            {
                "confidence_score": confidence,
                "matched_score": matched_score,
                "business_group": business_group,
            }
        )
        if calibrated is not None:
            return calibrated
        history_signal = min(1.0, max(0.0, history_count / 30))
        probability = (
            matched_score * self.WIN_PROBABILITY_MATCHED_WEIGHT
            + confidence * self.WIN_PROBABILITY_CONFIDENCE_WEIGHT
            + history_signal * self.WIN_PROBABILITY_HISTORY_WEIGHT
        )
        return round(max(0.0, min(1.0, probability)), 2)

    def _estimate_competitiveness_score(self, paper_bid_rate: float) -> float:
        target_rate = self.COMPETITIVENESS_TARGET_RATE
        return round(
            max(
                0.0,
                min(
                    1.0,
                    1.0
                    - (
                        abs(paper_bid_rate - target_rate)
                        / self.COMPETITIVENESS_RATE_TOLERANCE
                    ),
                ),
            ),
            2,
        )

    def _estimate_expected_margin_score(self, paper_bid_rate: float) -> float:
        return round(max(0.0, min(1.0, paper_bid_rate)), 2)

    def _estimate_execution_complexity_score(self, project: Project) -> float:
        budget = self._resolve_project_budget(project)
        if budget >= self.EXECUTION_COMPLEXITY_TIER1_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER1_SCORE
        if budget >= self.EXECUTION_COMPLEXITY_TIER2_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER2_SCORE
        if budget >= self.EXECUTION_COMPLEXITY_TIER3_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER3_SCORE
        return self.EXECUTION_COMPLEXITY_DEFAULT_SCORE

    def _deadline_hours_remaining(
        self, *, project: Project, data_cutoff_at: datetime
    ) -> int | None:
        if project.deadline is None:
            return None
        seconds = (
            ensure_utc(project.deadline) - ensure_utc(data_cutoff_at)
        ).total_seconds()
        return max(0, int(seconds // 3600))

    def _decision_status_for_action(self, action: str) -> str:
        if action == "bid_now":
            return "planned"
        if action == "review":
            return "reviewing"
        return "skipped"

    def _build_input_hash(
        self,
        *,
        project: Project,
        data_cutoff_at: datetime,
        scenario: str,
        history: list[dict[str, Any]],
        paper_bid_amount: float,
        strategy_version: str,
    ) -> str:
        payload = {
            "project_id": int(project.id),
            "data_cutoff_at": data_cutoff_at.isoformat(),
            "scenario": scenario,
            "history_ids": [record.get("historical_data_id") for record in history],
            "paper_bid_amount": paper_bid_amount,
            "strategy_version": strategy_version,
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _result_time(self, result: TenderResult) -> datetime:
        value = result.announced_at or result.created_at
        if value is None:
            return utc_now()
        return ensure_utc(value)

    def _normalize_actions(self, actions: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for action in actions:
            value = str(action or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        return tuple(normalized or self.DEFAULT_SETTLE_ACTIONS)

    def _resolve_operator(self, db: Session, *, operator_id: int | None) -> User:
        """Resolve the operator that owns a backtest.

        ``operator_id is None`` keeps the single-operator default path and bootstraps
        the canonical operator. When an explicit ``operator_id`` is supplied it must
        resolve to a real row: a missing operator raises :class:`OperatorNotFoundError`
        rather than silently falling back to the canonical operator, which would let a
        non-existent (e.g. synthetic) operator pollute canonical paper-bid data.
        """
        if operator_id is None:
            return ensure_operator_account(db)
        operator = db.query(User).filter(User.id == int(operator_id)).first()
        if operator is None:
            raise OperatorNotFoundError(f"Operator {int(operator_id)} not found")
        return operator

    def _resolve_operator_strategy(
        self, db: Session, *, operator: User
    ) -> OperatorStrategy:
        """Return the strategy belonging to *operator*.

        Scoped to ``operator.id`` via :func:`ensure_operator_strategy_for`, which never
        reassigns the canonical operator's strategy. Synthetic operators already carry a
        strategy from seeding; if one is somehow missing a row is created for *that*
        operator (never the canonical one).
        """
        return ensure_operator_strategy_for(db, operator)

    def _resolve_operator_profile(
        self, db: Session, *, operator: User
    ) -> CompanyProfile | None:
        """Return the company profile belonging to *operator*.

        Mirrors :meth:`_resolve_operator_strategy` using
        :func:`ensure_operator_profile_for`, so each operator's
        license/region/budget/capability profile drives its own matched score and the
        canonical profile is never returned or mutated as a fallback.
        """
        return ensure_operator_profile_for(db, operator)

    def _normalize_rate(self, value: float) -> float:
        # 스케일 판별을 단일 출처(app.domain.rate_normalization)로 통일한다. 종전 이곳만
        # 임계치가 2.0이라 (1.5, 2.0] 밴드의 율을 다른 6개 구현과 다르게 해석했다.
        return to_bid_rate_fraction(float(value or 0.0))

    def _average(self, values: list[float]) -> float | None:
        return average(values, digits=6)
