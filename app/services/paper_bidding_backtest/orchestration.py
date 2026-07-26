"""Historical backtest and forward paper-bidding run orchestration."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.core.single_user import split_multi_value_text
from app.core.time import ensure_utc, utc_now
from app.models.models import (
    CompanyProfile,
    OperatorStrategy,
    PaperBidRun,
    Project,
    TenderResult,
)
from app.services.paper_bidding_backtest.base import _PaperBiddingBase

logger = logging.getLogger(__name__)

# ``run_historical_backtest`` / ``run_forward_paper_bidding`` default the
# ``scenario`` keyword to the class default via a *bare* name (evaluated in this
# module's namespace at def time). Alias it from the base class so the signatures
# stay byte-identical while ``_PaperBiddingBase.DEFAULT_SCENARIO`` remains the
# single source of truth.
DEFAULT_SCENARIO = _PaperBiddingBase.DEFAULT_SCENARIO


@dataclass(frozen=True)
class _PreparedHistoricalRun:
    """Resolved + normalized inputs for one historical-backtest run.

    Built by ``_prepare_historical_run`` (operator/strategy/profile resolution,
    award-category scoping, scenario/limit normalization, request payload, and
    the persisted ``PaperBidRun``) so ``run_historical_backtest`` reads as a
    ``prepare -> execute`` pipeline. ``_create_run`` runs in the prepare phase —
    outside the execute ``try/except`` — exactly as before, so a create-time
    failure still propagates without ``_fail_run`` being called.
    """

    run: PaperBidRun | None
    request_payload: dict[str, Any]
    operator_id: int
    strategy: OperatorStrategy
    profile: CompanyProfile | None
    resolved_award_categories: tuple[str, ...]
    normalized_settle_actions: tuple[str, ...]
    normalized_scenario: str
    start_at: datetime | None
    end_at: datetime | None
    safe_limit: int


class _BacktestRunMixin(_PaperBiddingBase):
    """Historical-replay and forward paper-bidding run orchestration."""

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
        prepared = self._prepare_historical_run(
            db,
            operator_id=operator_id,
            category=category,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            scenario=scenario,
            strategy_version=strategy_version,
            model_version=model_version,
            cutoff_hours_before_deadline=cutoff_hours_before_deadline,
            history_limit=history_limit,
            settle_actions=settle_actions,
            persist=persist,
            award_categories=award_categories,
        )
        return self._execute_historical_run(
            db,
            prepared,
            category=category,
            strategy_version=strategy_version,
            model_version=model_version,
            cutoff_hours_before_deadline=cutoff_hours_before_deadline,
            history_limit=history_limit,
            persist=persist,
        )

    def _resolve_award_categories(
        self,
        strategy: OperatorStrategy,
        *,
        award_categories: Sequence[str] | None,
        category: str | None,
    ) -> tuple[str, ...]:
        """Scope the replay award pool to project categories.

        Explicit ``award_categories`` win. Otherwise, when no explicit
        ``category`` filter is given, default to the operator strategy's focus
        categories so a focus-category operator draws its window from its OWN
        categories (a minority category is not starved out of a bounded ``limit``
        window). An explicit ``category`` with no ``award_categories`` yields
        ``()`` — no extra category scoping.
        """
        if award_categories:
            return tuple(
                str(value).strip() for value in award_categories if str(value).strip()
            )
        if not category:
            return tuple(
                split_multi_value_text(getattr(strategy, "focus_categories", None))
            )
        return ()

    def _prepare_historical_run(
        self,
        db: Session,
        *,
        operator_id: int | None,
        category: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
        scenario: str,
        strategy_version: str,
        model_version: str,
        cutoff_hours_before_deadline: int,
        history_limit: int,
        settle_actions: Sequence[str] | None,
        persist: bool,
        award_categories: Sequence[str] | None,
    ) -> _PreparedHistoricalRun:
        """Resolve/normalize inputs and open the ``PaperBidRun`` for the replay.

        Runs before the execute ``try/except`` (identical ordering to the inline
        prologue), so a resolution or ``_create_run`` failure propagates without
        ``_fail_run``.
        """
        operator = self._resolve_operator(db, operator_id=operator_id)
        strategy = self._resolve_operator_strategy(db, operator=operator)
        profile = self._resolve_operator_profile(db, operator=operator)
        resolved_award_categories = self._resolve_award_categories(
            strategy, award_categories=award_categories, category=category
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
        return _PreparedHistoricalRun(
            run=run,
            request_payload=request_payload,
            operator_id=int(operator.id),
            strategy=strategy,
            profile=profile,
            resolved_award_categories=resolved_award_categories,
            normalized_settle_actions=normalized_settle_actions,
            normalized_scenario=normalized_scenario,
            start_at=start_at,
            end_at=end_at,
            safe_limit=safe_limit,
        )

    def _execute_historical_run(
        self,
        db: Session,
        prepared: _PreparedHistoricalRun,
        *,
        category: str | None,
        strategy_version: str,
        model_version: str,
        cutoff_hours_before_deadline: int,
        history_limit: int,
        persist: bool,
    ) -> dict[str, Any]:
        """Replay + settle awards for a prepared run; fail the run on any error.

        The ``try/except`` here is the moved-verbatim run body: on any exception
        ``_fail_run`` marks the persisted run failed and the error re-raises.
        """
        candidate_items: list[dict[str, Any]] = []
        settlement_items: list[dict[str, Any]] = []
        action_counts: Counter[str] = Counter()

        try:
            awards = self._load_eligible_awards(
                db,
                category=category,
                start_at=prepared.start_at,
                end_at=prepared.end_at,
                limit=prepared.safe_limit,
                categories=prepared.resolved_award_categories or None,
            )
            skipped_by_strategy = self._process_historical_awards(
                db,
                awards=awards,
                strategy=prepared.strategy,
                profile=prepared.profile,
                run=prepared.run,
                operator_id=prepared.operator_id,
                scenario=prepared.normalized_scenario,
                strategy_version=strategy_version,
                model_version=model_version,
                cutoff_hours_before_deadline=cutoff_hours_before_deadline,
                history_limit=history_limit,
                settle_actions=prepared.normalized_settle_actions,
                persist=persist,
                candidate_items=candidate_items,
                settlement_items=settlement_items,
                action_counts=action_counts,
            )
            return self._complete_historical_backtest(
                db,
                run=prepared.run,
                persist=persist,
                request_payload=prepared.request_payload,
                candidate_items=candidate_items,
                settlement_items=settlement_items,
                skipped_by_strategy=skipped_by_strategy,
                action_counts=action_counts,
                settle_actions=prepared.normalized_settle_actions,
            )
        except Exception as exc:
            self._fail_run(
                db, run=prepared.run, persist=persist, error_message=str(exc)
            )
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
