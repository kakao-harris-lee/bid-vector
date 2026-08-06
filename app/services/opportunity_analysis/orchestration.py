"""Top-level opportunity-analysis flow orchestration.

Owns ``analyze_project`` and its direct sub-steps: operator-context resolution,
analysis-input assembly, bid-recommendation build, decision evaluation,
probability-context resolution, strengths/risk assembly, and the final response
dict. The heavy scoring, prediction wiring, workload, market, and flag logic
live in sibling mixins and are invoked through ``self``. Methods are moved
verbatim from the original ``OpportunityAnalysisService`` body.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_profile_for,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.models.models import CompanyProfile, OperatorStrategy, Project, User
from app.schemas.schemas import BidDecisionRequest, OpportunityAnalysisRequest
from app.services.bid_base import resolve_notice_bid_base
from app.services.operator_strategy_tuning import resolve_category_priority_override
from app.services.opportunity_analysis.base import _AnalysisInputs, _OpportunityAnalysisBase


class _OrchestrationMixin(_OpportunityAnalysisBase):
    """Top-level ``analyze_project`` flow and its direct sub-steps."""

    def analyze_project(
        self,
        db: Session,
        project: Project,
        request: OpportunityAnalysisRequest,
        *,
        operator: User | None = None,
        read_only: bool = False,
    ) -> dict:
        """Build one analysis; scan reads use stored similarity state only."""
        operator, profile, strategy = self._resolve_operator_context(db, operator)

        analysis_inputs = self._build_analysis_inputs(
            db,
            project=project,
            request=request,
            profile=profile,
            operator_id=operator.id, read_only=read_only,
        )
        classification = analysis_inputs.classification
        similar_projects = analysis_inputs.similar_projects
        market_insights = analysis_inputs.market_insights
        user_historical_data = analysis_inputs.user_historical_data
        price_prediction, business_group = self._build_price_prediction(
            db,
            project=project,
            request=request,
            operator_id=operator.id,
        )
        bid_recommendation = self._build_bid_recommendation(project, user_historical_data)

        recommended_amount = self._resolve_recommended_amount(
            project=project,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
        )

        deadline_hours_remaining = self._compute_deadline_hours_remaining(project)
        current_active_bids, current_workload_score, workload_source = self._resolve_workload_context(
            db,
            operator_id=operator.id,
            request=request,
            exclude_project_id=project.id,
        )

        scores = self._compute_scores(
            project=project,
            classification=classification,
            price_prediction=price_prediction,
            recommended_amount=recommended_amount,
            market_insights=market_insights,
            capacity_score=float(profile.capacity_score or 0.0),
            deadline_hours_remaining=deadline_hours_remaining,
            current_active_bids=current_active_bids,
            max_active_bids=request.max_active_bids,
        )

        category_priority_override, matched_score, probability_score = (
            self._resolve_probability_context(
                strategy=strategy,
                project=project,
                classification=classification,
                price_prediction=price_prediction,
                bid_recommendation=bid_recommendation,
                similar_projects=similar_projects,
                competitiveness_score=scores.competitiveness_score,
                capacity_score=float(profile.capacity_score or 0.0),
                current_active_bids=current_active_bids,
                max_active_bids=request.max_active_bids,
                business_group=business_group,
            )
        )

        decision = self._evaluate_bid_decision(
            db=db,
            operator=operator,
            project=project,
            request=request,
            recommended_amount=recommended_amount,
            probability_score=probability_score,
            matched_score=matched_score,
            deadline_hours_remaining=deadline_hours_remaining,
            current_active_bids=current_active_bids,
            current_workload_score=current_workload_score,
            competitiveness_score=scores.competitiveness_score,
            expected_margin_score=scores.expected_margin_score,
            execution_complexity_score=scores.execution_complexity_score,
            workload_source=workload_source,
        )
        strengths, risk_flags = self._build_opportunity_flags(
            project=project,
            profile=profile,
            classification=classification,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
            current_active_bids=current_active_bids,
            max_active_bids=request.max_active_bids,
            deadline_hours_remaining=deadline_hours_remaining,
            competitiveness_score=scores.competitiveness_score,
            probability_score=probability_score,
            expected_margin_score=scores.expected_margin_score,
            execution_complexity_score=scores.execution_complexity_score,
            category_priority_override=category_priority_override,
        )

        return self._build_analysis_response(
            project=project,
            operator=operator,
            request=request,
            classification=classification,
            matched_score=matched_score,
            probability_score=probability_score,
            recommended_amount=recommended_amount,
            deadline_hours_remaining=deadline_hours_remaining,
            current_active_bids=current_active_bids,
            current_workload_score=current_workload_score,
            workload_source=workload_source,
            category_priority_override=category_priority_override,
            decision=decision,
            strengths=strengths,
            risk_flags=risk_flags,
            market_insights=market_insights,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
        )

    def _build_analysis_inputs(
        self,
        db: Session,
        *,
        project: Project,
        request: OpportunityAnalysisRequest,
        profile: CompanyProfile,
        operator_id: int,
        read_only: bool = False,
    ) -> _AnalysisInputs:
        classification = self.classifier.classify(project=project, profile=profile)
        similar_projects = self.similarity_service.find_similar_projects(
            db,
            project,
            limit=request.similar_limit,
            min_similarity=request.min_similarity,
            same_category_only=request.same_category_only,
            # Bulk candidate scans must not turn a missing/stale materialization
            # into inline model work. The single-project default remains the
            # existing refresh-capable path when ``read_only`` is false.
            stored_only=read_only,
        )
        return _AnalysisInputs(
            classification=classification,
            similar_projects=similar_projects,
            market_insights=self._build_market_insights(project, similar_projects),
            user_historical_data=self._build_user_historical_data(
                db,
                operator_id=operator_id,
                request_data=request.user_historical_data,
            ),
        )

    def _resolve_operator_context(
        self,
        db: Session,
        operator: User | None,
    ) -> tuple[User, CompanyProfile, OperatorStrategy]:
        if operator is None:
            return (
                ensure_operator_account(db),
                ensure_operator_profile(db),
                ensure_operator_strategy(db),
            )
        return (
            operator,
            ensure_operator_profile_for(db, operator),
            ensure_operator_strategy_for(db, operator),
        )

    def _build_bid_recommendation(
        self,
        project: Project,
        user_historical_data: dict,
    ) -> dict:
        return self.bid_recommendation_port.recommend(
            project_data={
                "budget": float(project.budget_estimate or 0.0),
                "category": project.category or "other",
                "description": f"{project.title} {project.description} {project.requirements}".strip(),
            },
            user_data=user_historical_data,
        )

    def _evaluate_bid_decision(
        self,
        *,
        db: Session,
        operator: User,
        project: Project,
        request: OpportunityAnalysisRequest,
        recommended_amount: float,
        probability_score: float,
        matched_score: float,
        deadline_hours_remaining: float,
        current_active_bids: int,
        current_workload_score: float,
        competitiveness_score: float,
        expected_margin_score: float,
        execution_complexity_score: float,
        workload_source: str,
    ) -> dict:
        return self.decision_service.evaluate_opportunity(
            BidDecisionRequest(
                project_id=project.id,
                recommended_amount=recommended_amount,
                probability_score=probability_score,
                matched_score=matched_score,
                deadline_hours_remaining=deadline_hours_remaining,
                current_active_bids=current_active_bids,
                max_active_bids=request.max_active_bids,
                current_workload_score=current_workload_score,
                # budget_capture_score = recommended_amount / budget_estimate.
                # recommended_amount is now 기초금액/사업금액(base)-relative, so the
                # denominator must be the SAME base — passing 추정가격(ex-VAT) here
                # would make capture ≈ rate × 1.1 for 과세 공고 (clamped to 1.0),
                # inflating opportunity/priority and the "기초금액 대비 추천가 유지율"
                # reason. In allocation.py budget_estimate feeds ONLY the capture
                # ratio + that reason, so aligning it to the base is correct.
                budget_estimate=resolve_notice_bid_base(db, project),
                competitiveness_score=float(competitiveness_score),
                expected_margin_score=expected_margin_score,
                execution_complexity_score=execution_complexity_score,
                workload_source=workload_source,
            ),
            db=db,
            operator=operator,
        )

    def _resolve_probability_context(
        self,
        *,
        strategy: OperatorStrategy,
        project: Project,
        classification: dict,
        price_prediction: dict,
        bid_recommendation: dict,
        similar_projects: list,
        competitiveness_score: float,
        capacity_score: float,
        current_active_bids: int,
        max_active_bids: int,
        business_group: str | None,
    ) -> tuple[float, float, float]:
        category_priority_override = resolve_category_priority_override(
            strategy,
            project.category,
        )
        matched_score = self._apply_category_priority_override(
            float(classification["score"]),
            category_priority_override * 0.5,
        )
        probability_score = self._resolve_final_probability_score(
            classification=classification,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            capacity_score=capacity_score,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
            business_group=business_group,
            category_priority_override=category_priority_override,
        )
        return category_priority_override, matched_score, probability_score

    def _build_opportunity_flags(
        self,
        *,
        project: Project,
        profile: CompanyProfile,
        classification: dict,
        price_prediction: dict,
        similar_projects: list,
        current_active_bids: int,
        max_active_bids: int,
        deadline_hours_remaining: float,
        competitiveness_score: float,
        probability_score: float,
        expected_margin_score: float,
        execution_complexity_score: float,
        category_priority_override: float,
    ) -> tuple[list[str], list[str]]:
        strengths = self._build_strengths(
            classification=classification,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            probability_score=probability_score,
            expected_margin_score=expected_margin_score,
        )
        risk_flags = self._build_risk_flags(
            classification=classification,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
            deadline_hours_remaining=deadline_hours_remaining,
            expected_margin_score=expected_margin_score,
            execution_complexity_score=execution_complexity_score,
            project=project,
            profile=profile,
        )
        if category_priority_override > 0:
            strengths.append(
                f"운영 전략에서 {project.category or '미분류'} 카테고리를 우선 검토 대상으로 보정했습니다."
            )
        elif category_priority_override < 0:
            risk_flags.append(
                f"운영 전략에서 {project.category or '미분류'} 카테고리 우선순위를 낮춰 보수적으로 평가했습니다."
            )
        return strengths, risk_flags

    def _build_analysis_response(
        self,
        *,
        project: Project,
        operator: User,
        request: OpportunityAnalysisRequest,
        classification: dict,
        matched_score: float,
        probability_score: float,
        recommended_amount: float,
        deadline_hours_remaining: int | None,
        current_active_bids: int,
        current_workload_score: float,
        workload_source: str,
        category_priority_override: float,
        decision: dict,
        strengths: list[str],
        risk_flags: list[str],
        market_insights: dict,
        price_prediction: dict,
        similar_projects: dict,
    ) -> dict:
        """Assemble the final analysis response dict.

        The ``bid_recommendation`` dict is computed internally (feeds
        ``recommended_amount`` and the probability blend) but is intentionally
        NOT surfaced here: it carried a caveat-less confidence_score with no
        external consumer. Other field names, values, ordering, and the nested
        ``_build_summary`` call are unchanged.
        """
        return {
            "project_id": project.id,
            "project_title": project.title,
            "operator_id": operator.id,
            "matched": bool(classification["matched"]),
            "matched_score": matched_score,
            "probability_score": probability_score,
            "recommended_amount": recommended_amount,
            "deadline_hours_remaining": deadline_hours_remaining,
            "current_active_bids": current_active_bids,
            "max_active_bids": request.max_active_bids,
            "current_workload_score": current_workload_score,
            "workload_source": workload_source,
            "strategy_adjustments": {
                "category_priority_override": round(float(category_priority_override), 4),
            },
            "analysis_summary": self._build_summary(
                project=project,
                decision=decision,
                probability_score=probability_score,
                recommended_amount=recommended_amount,
                strengths=strengths,
                risk_flags=risk_flags,
            ),
            "strengths": strengths,
            "risk_flags": risk_flags,
            "market_insights": market_insights,
            "classification": classification,
            "price_prediction": price_prediction,
            "similar_projects": similar_projects,
            "decision": decision,
        }
