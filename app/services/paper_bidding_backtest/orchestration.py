"""Historical backtest and forward paper-bidding run orchestration."""

from __future__ import annotations

import logging
from collections import Counter
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
