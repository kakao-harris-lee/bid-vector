"""Historical paper-bidding backtest service."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session, selectinload

from app.ai.price_prediction import predict_price
from app.core.single_user import ensure_operator_account, ensure_operator_strategy, split_multi_value_text
from app.core.time import ensure_utc, utc_now
from app.models.models import PaperBid, PaperBidRun, PaperBidSettlement, Project, TenderResult, User
from app.schemas.schemas import BidDecisionRequest
from app.services.allocation import BidDecisionService
from app.services.backtest_cutoff import BacktestCutoffService


class PaperBiddingBacktestService:
    """Replay historical awards as paper-bidding opportunities."""

    DEFAULT_SETTLE_ACTIONS = ("bid_now",)
    DEFAULT_SCENARIO = "base"

    def __init__(self) -> None:
        self.cutoff_service = BacktestCutoffService()
        self.decision_service = BidDecisionService()

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
    ) -> dict[str, Any]:
        """Generate paper bids from historical awards and settle them immediately."""
        operator = self._resolve_operator(db, operator_id=operator_id)
        strategy = ensure_operator_strategy(db)
        normalized_settle_actions = self._normalize_actions(settle_actions or self.DEFAULT_SETTLE_ACTIONS)
        normalized_scenario = str(scenario or self.DEFAULT_SCENARIO).strip() or self.DEFAULT_SCENARIO
        start_at = ensure_utc(start_at) if start_at is not None else None
        end_at = ensure_utc(end_at) if end_at is not None else None
        safe_limit = max(1, int(limit or 1))

        request_payload = {
            "category": category,
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
        skipped_by_strategy = 0

        try:
            awards = self._load_eligible_awards(
                db,
                category=category,
                start_at=start_at,
                end_at=end_at,
                limit=safe_limit,
            )
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
                    scenario=normalized_scenario,
                    strategy_version=strategy_version,
                    cutoff_hours_before_deadline=cutoff_hours_before_deadline,
                    history_limit=history_limit,
                )
                action_counts[item["action"]] += 1
                candidate_items.append(item)

                paper_bid = self._persist_paper_bid(
                    db,
                    run=run,
                    operator_id=int(operator.id),
                    item=item,
                    persist=persist,
                    model_version=model_version,
                    strategy_version=strategy_version,
                )
                if item["action"] not in normalized_settle_actions:
                    continue

                settlement = self._build_settlement_item(item=item, tender_result=tender_result)
                settlement_items.append(settlement)
                self._persist_settlement(
                    db,
                    paper_bid=paper_bid,
                    tender_result=tender_result,
                    settlement=settlement,
                    persist=persist,
                )

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
                paper_bid_count=sum(1 for item in candidate_items if item["action"] in normalized_settle_actions),
                settled_count=len(settlement_items),
            )
            return {
                "run_id": int(run.id) if run is not None and run.id is not None else None,
                "request": request_payload,
                "summary": summary,
                "items": candidate_items,
                "settlements": settlement_items,
            }
        except Exception as exc:
            self._fail_run(db, run=run, persist=persist, error_message=str(exc))
            raise

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
        strategy = ensure_operator_strategy(db)
        normalized_scenario = str(scenario or self.DEFAULT_SCENARIO).strip() or self.DEFAULT_SCENARIO
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
        skipped_by_strategy = 0

        try:
            projects = self._load_forward_projects(db, category=category, limit=safe_limit, data_cutoff_at=data_cutoff_at)
            for project in projects:
                if not self._passes_strategy(project, strategy):
                    skipped_by_strategy += 1
                    continue
                item = self._build_candidate_item(
                    db,
                    project=project,
                    tender_result=None,
                    data_cutoff_at=data_cutoff_at,
                    scenario=normalized_scenario,
                    strategy_version=strategy_version,
                    cutoff_hours_before_deadline=0,
                    history_limit=history_limit,
                )
                action_counts[item["action"]] += 1
                candidate_items.append(item)
                self._persist_paper_bid(
                    db,
                    run=run,
                    operator_id=int(operator.id),
                    item=item,
                    persist=persist,
                    model_version=model_version,
                    strategy_version=strategy_version,
                )

            summary = self._build_summary(
                candidate_items=candidate_items,
                settlement_items=[],
                skipped_by_strategy=skipped_by_strategy,
                action_counts=action_counts,
            )
            self._complete_run(
                db,
                run=run,
                persist=persist,
                summary=summary,
                candidate_count=len(candidate_items),
                paper_bid_count=sum(1 for item in candidate_items if item["action"] in {"bid_now", "review"}),
                settled_count=0,
            )
            return {
                "run_id": int(run.id) if run is not None and run.id is not None else None,
                "request": request_payload,
                "summary": summary,
                "items": candidate_items,
                "settlements": [],
            }
        except Exception as exc:
            self._fail_run(db, run=run, persist=persist, error_message=str(exc))
            raise

    def _load_eligible_awards(
        self,
        db: Session,
        *,
        category: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
    ) -> list[TenderResult]:
        query = (
            db.query(TenderResult)
            .join(Project, Project.id == TenderResult.project_id)
            .options(selectinload(TenderResult.project))
            .filter(
                TenderResult.project_id.isnot(None),
                TenderResult.winning_amount.isnot(None),
                TenderResult.winning_amount > 0,
            )
        )
        if category:
            query = query.filter(Project.category == category)

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
            if current is None or self._result_time(row) > self._result_time(current) or int(row.id or 0) > int(current.id or 0):
                latest_by_project[project_id] = row

        return sorted(
            latest_by_project.values(),
            key=lambda result: (self._result_time(result), int(result.id or 0)),
        )[:limit]

    def _load_forward_projects(
        self,
        db: Session,
        *,
        category: str | None,
        limit: int,
        data_cutoff_at: datetime,
    ) -> list[Project]:
        query = db.query(Project).filter(Project.status.in_(["open", "re_notice"]))
        if category:
            query = query.filter(Project.category == category)
        query = query.filter(
            (Project.deadline.is_(None)) | (Project.deadline > data_cutoff_at),
        )
        return (
            query.order_by(Project.deadline.asc().nullslast(), Project.created_at.desc(), Project.id.desc())
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
    ) -> dict[str, Any]:
        budget = self._resolve_project_budget(project)
        if budget <= 0:
            raise ValueError(f"Project {project.id} has no usable budget")

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
        prediction = predict_price(
            budget=budget,
            category=project.category or "other",
            description=" ".join(part for part in [project.title, project.description, project.requirements] if part),
            historical_records=history,
            agency_name=project.issuing_agency or project.demand_agency,
            feedback_calibration=None,
        )
        selected_scenario = self._select_scenario(prediction, scenario=scenario)
        paper_bid_amount = round(float(selected_scenario["predicted_price"]), 2)
        paper_bid_rate = self._normalize_rate(float(selected_scenario.get("bid_rate") or (paper_bid_amount / budget)))
        matched_score = self._estimate_matched_score(project=project)
        probability_score = self._estimate_probability_score(
            matched_score=matched_score,
            prediction=prediction,
            history_count=len(history),
        )
        deadline_hours_remaining = self._deadline_hours_remaining(project=project, data_cutoff_at=data_cutoff_at)
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
                competitiveness_score=self._estimate_competitiveness_score(paper_bid_rate),
                expected_margin_score=self._estimate_expected_margin_score(paper_bid_rate),
                execution_complexity_score=self._estimate_execution_complexity_score(project),
                workload_source="provided",
            ),
            db=db,
        )
        action = str(decision["action"])
        return {
            "project_id": int(project.id),
            "project_title": project.title,
            "notice_number": project.notice_number,
            "category": project.category,
            "issuing_agency": project.issuing_agency,
            "data_cutoff_at": data_cutoff_at.isoformat(),
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "budget_estimate": round(budget, 2),
            "scenario": str(selected_scenario["label"]),
            "action": action,
            "decision_status": self._decision_status_for_action(action),
            "paper_bid_amount": paper_bid_amount,
            "paper_bid_rate": round(paper_bid_rate, 6),
            "priority_score": float(decision["priority_score"]),
            "probability_score": probability_score,
            "matched_score": matched_score,
            "predicted_price": float(prediction.get("predicted_price", 0.0) or 0.0),
            "predicted_bid_rate": self._normalize_rate(float(prediction.get("predicted_bid_rate", 0.0) or 0.0)),
            "price_range_min": float(prediction.get("price_range_min", 0.0) or 0.0),
            "price_range_max": float(prediction.get("price_range_max", 0.0) or 0.0),
            "confidence_score": float(prediction.get("confidence_score", 0.0) or 0.0),
            "predictor_name": str(prediction.get("predictor_name") or "historical_statistical"),
            "predictor_family": str(prediction.get("predictor_family") or "statistical"),
            "model_version": str(prediction.get("model_version") or "current"),
            "strategy_version": strategy_version,
            "historical_sample_size": len(history),
            "history_ids": [int(record["historical_data_id"]) for record in history if record.get("historical_data_id") is not None],
            "input_snapshot_hash": self._build_input_hash(
                project=project,
                data_cutoff_at=data_cutoff_at,
                scenario=str(selected_scenario["label"]),
                history=history,
                paper_bid_amount=paper_bid_amount,
                strategy_version=strategy_version,
            ),
            "reasoning": str(decision["reasoning"]),
        }

    def _build_settlement_item(self, *, item: dict[str, Any], tender_result: TenderResult) -> dict[str, Any]:
        winning_amount = float(tender_result.winning_amount or 0.0)
        budget = float(item["budget_estimate"] or 0.0)
        winning_rate = self._normalize_rate(float(tender_result.winning_rate or 0.0))
        if winning_rate <= 0 and budget > 0:
            winning_rate = self._normalize_rate(winning_amount / budget)

        paper_bid_amount = float(item["paper_bid_amount"] or 0.0)
        paper_bid_rate = self._normalize_rate(float(item["paper_bid_rate"] or 0.0))
        amount_delta = paper_bid_amount - winning_amount
        absolute_error_rate = abs(amount_delta) / winning_amount if winning_amount > 0 else 0.0
        bid_rate_delta = paper_bid_rate - winning_rate
        absolute_bid_rate_error = abs(bid_rate_delta)
        price_close = absolute_bid_rate_error <= 0.003
        price_competitive = absolute_bid_rate_error <= 0.01
        would_have_won_price_only = "plausible" if price_close else "competitive" if price_competitive else "unlikely"

        return {
            "project_id": item["project_id"],
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
            "would_have_won_final": "unknown",
            "settlement_reason": (
                "Price is within 0.3 percentage points of the winning rate."
                if price_close
                else "Price is within 1 percentage point of the winning rate."
                if price_competitive
                else "Price is outside the initial competitive band."
            ),
        }

    def _build_summary(
        self,
        *,
        candidate_items: list[dict[str, Any]],
        settlement_items: list[dict[str, Any]],
        skipped_by_strategy: int,
        action_counts: Counter[str],
    ) -> dict[str, Any]:
        rate_errors = [float(item["absolute_bid_rate_error"]) for item in settlement_items]
        amount_errors = [float(item["absolute_error_rate"]) for item in settlement_items]
        return {
            "candidate_count": len(candidate_items),
            "paper_bid_count": int(action_counts.get("bid_now", 0) + action_counts.get("review", 0)),
            "review_count": int(action_counts.get("review", 0)),
            "skip_count": int(action_counts.get("skip", 0)),
            "skipped_by_strategy_count": skipped_by_strategy,
            "settled_count": len(settlement_items),
            "action_counts": dict(action_counts),
            "average_absolute_bid_rate_error": self._average(rate_errors),
            "average_absolute_amount_error_rate": self._average(amount_errors),
            "within_0_1pct_count": sum(1 for value in rate_errors if value <= 0.001),
            "within_0_3pct_count": sum(1 for value in rate_errors if value <= 0.003),
            "within_1pct_count": sum(1 for value in rate_errors if value <= 0.01),
            "price_close_count": sum(1 for item in settlement_items if item["price_close"]),
            "price_competitive_count": sum(1 for item in settlement_items if item["price_competitive"]),
            "would_have_won_price_only_count": sum(
                1 for item in settlement_items if item["would_have_won_price_only"] == "plausible"
            ),
        }

    def _passes_strategy(self, project: Project, strategy) -> bool:
        category = str(project.category or "").strip().lower()
        focus_categories = [value.lower() for value in split_multi_value_text(strategy.focus_categories)]
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
        required_keywords = [value.lower() for value in split_multi_value_text(strategy.required_keywords)]
        if required_keywords and not all(keyword in searchable_text for keyword in required_keywords):
            return False

        exclude_keywords = [value.lower() for value in split_multi_value_text(strategy.exclude_keywords)]
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
            request_payload=json.dumps(request_payload, ensure_ascii=False, default=str),
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

    def _fail_run(self, db: Session, *, run: PaperBidRun | None, persist: bool, error_message: str) -> None:
        if not persist or run is None:
            return
        run.status = "failed"
        run.completed_at = utc_now()
        run.error_message = error_message
        db.add(run)
        db.commit()

    def _select_scenario(self, prediction: dict[str, Any], *, scenario: str) -> dict[str, Any]:
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

    def _estimate_matched_score(self, *, project: Project) -> float:
        score = 0.72
        if project.category:
            score += 0.08
        if project.issuing_agency or project.demand_agency:
            score += 0.05
        if project.requirements or project.description:
            score += 0.05
        return round(min(1.0, score), 2)

    def _estimate_probability_score(self, *, matched_score: float, prediction: dict[str, Any], history_count: int) -> float:
        confidence = max(0.0, min(1.0, float(prediction.get("confidence_score", 0.0) or 0.0)))
        history_signal = min(1.0, max(0.0, history_count / 30))
        probability = matched_score * 0.38 + confidence * 0.42 + history_signal * 0.20
        return round(max(0.0, min(1.0, probability)), 2)

    def _estimate_competitiveness_score(self, paper_bid_rate: float) -> float:
        target_rate = 0.88
        return round(max(0.0, min(1.0, 1.0 - (abs(paper_bid_rate - target_rate) / 0.15))), 2)

    def _estimate_expected_margin_score(self, paper_bid_rate: float) -> float:
        return round(max(0.0, min(1.0, paper_bid_rate)), 2)

    def _estimate_execution_complexity_score(self, project: Project) -> float:
        budget = self._resolve_project_budget(project)
        if budget >= 500_000_000:
            return 0.82
        if budget >= 200_000_000:
            return 0.68
        if budget >= 100_000_000:
            return 0.52
        return 0.36

    def _deadline_hours_remaining(self, *, project: Project, data_cutoff_at: datetime) -> int | None:
        if project.deadline is None:
            return None
        seconds = (ensure_utc(project.deadline) - ensure_utc(data_cutoff_at)).total_seconds()
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
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
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
        if operator_id is None:
            return ensure_operator_account(db)
        operator = db.query(User).filter(User.id == int(operator_id)).first()
        if operator is None:
            return ensure_operator_account(db)
        return operator

    def _normalize_rate(self, value: float) -> float:
        rate = float(value or 0.0)
        if rate > 2.0:
            rate = rate / 100.0
        return rate

    def _average(self, values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 6)
