"""Award/project loading and candidate paper-bid construction."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.ai.business_group import resolve_business_group
from app.models.models import CompanyProfile, Project, TenderResult
from app.schemas.paper_bidding_items import PaperBiddingCandidateItem
from app.schemas.schemas import BidDecisionRequest
from app.services.bid_base import prepare_prediction_inputs
from app.services.query_predicates import open_projects, settled_with_amount
from app.services.paper_bidding_backtest.base import (
    CandidateDecisionContext,
    CandidatePredictionContext,
    _PaperBiddingBase,
)


class _CandidateMixin(_PaperBiddingBase):
    """Load the replay award/project pool and build candidate paper bids."""

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
    ) -> PaperBiddingCandidateItem:
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
    ) -> PaperBiddingCandidateItem:
        budget = prediction_context.budget
        prediction = prediction_context.prediction
        selected_scenario = decision_context.selected_scenario
        decision = decision_context.decision
        return PaperBiddingCandidateItem(
            project_id=int(project.id),
            project_title=project.title,
            notice_number=project.notice_number,
            category=project.category,
            issuing_agency=project.issuing_agency,
            data_cutoff_at=prediction_context.data_cutoff_at.isoformat(),
            deadline=project.deadline.isoformat() if project.deadline else None,
            budget_estimate=round(budget, 2),
            scenario=str(selected_scenario["label"]),
            action=decision_context.action,
            decision_status=self._decision_status_for_action(decision_context.action),
            paper_bid_amount=decision_context.paper_bid_amount,
            paper_bid_rate=round(decision_context.paper_bid_rate, 6),
            priority_score=float(decision["priority_score"]),
            probability_score=decision_context.probability_score,
            matched_score=decision_context.matched_score,
            predicted_price=float(prediction.get("predicted_price", 0.0) or 0.0),
            predicted_bid_rate=self._normalize_rate(
                float(prediction.get("predicted_bid_rate", 0.0) or 0.0)
            ),
            price_range_min=float(prediction.get("price_range_min", 0.0) or 0.0),
            price_range_max=float(prediction.get("price_range_max", 0.0) or 0.0),
            confidence_score=float(prediction.get("confidence_score", 0.0) or 0.0),
            predictor_name=str(
                prediction.get("predictor_name") or "historical_statistical"
            ),
            predictor_family=str(prediction.get("predictor_family") or "statistical"),
            model_version=str(prediction.get("model_version") or "current"),
            strategy_version=strategy_version,
            historical_sample_size=len(prediction_context.history),
            history_ids=[
                int(record["historical_data_id"])
                for record in prediction_context.history
                if record.get("historical_data_id") is not None
            ],
            input_snapshot_hash=self._build_input_hash(
                project=project,
                data_cutoff_at=prediction_context.data_cutoff_at,
                scenario=str(selected_scenario["label"]),
                history=prediction_context.history,
                paper_bid_amount=decision_context.paper_bid_amount,
                strategy_version=strategy_version,
            ),
            matched_score_source=decision_context.match_source,
            match_reasons=decision_context.match_reasons,
            reasoning=self._compose_reasoning(
                decision_reasoning=str(decision["reasoning"]),
                match_reasons=decision_context.match_reasons,
                match_source=decision_context.match_source,
            ),
        )
