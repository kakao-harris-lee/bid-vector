"""Integrated opportunity analysis for award-oriented bidding decisions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.bid_recommendation import calculate_competitiveness_score, get_bid_recommendation
from app.ai.price_prediction import get_price_insights, predict_price
from app.core.single_user import ensure_operator_account, ensure_operator_profile
from app.core.time import ensure_utc, utc_now
from app.models.models import Bid, BidDecisionRecord, HistoricalData, Project
from app.schemas.schemas import BidDecisionRequest, OpportunityAnalysisRequest
from app.services.allocation import BidDecisionService
from app.services.classifier import NoticeClassifierService
from app.services.prediction_feedback import PredictionFeedbackService
from app.services.project_similarity import ProjectSimilarityService


class OpportunityAnalysisService:
    """Combine fit, price, market, similarity, and action guidance into one analysis."""

    ACTIVE_DECISION_STATUSES = ("planned", "reviewing")
    DEFAULT_SIMILARITY_SCORE = 0.35

    def __init__(self) -> None:
        self.classifier = NoticeClassifierService()
        self.decision_service = BidDecisionService()
        self.feedback_service = PredictionFeedbackService()
        self.similarity_service = ProjectSimilarityService()

    def analyze_project(self, db: Session, project: Project, request: OpportunityAnalysisRequest) -> dict:
        """Build a multi-angle bid opportunity analysis for one project."""
        operator = ensure_operator_account(db)
        profile = ensure_operator_profile(db)

        classification = self.classifier.classify(project=project, profile=profile)
        similar_projects = self.similarity_service.find_similar_projects(
            db,
            project,
            limit=request.similar_limit,
            min_similarity=request.min_similarity,
            same_category_only=request.same_category_only,
        )

        market_insights = self._build_market_insights(project, similar_projects)
        user_historical_data = self._build_user_historical_data(
            db,
            operator_id=operator.id,
            request_data=request.user_historical_data,
        )

        feedback_calibration = self.feedback_service.build_calibration_context(
            db,
            operator_id=operator.id,
            category=project.category,
            agency_name=request.agency_name,
        )

        price_prediction = predict_price(
            budget=float(project.budget_estimate or 0.0),
            category=project.category or "other",
            description=f"{project.description or ''} {project.requirements or ''}".strip(),
            historical_records=self._load_price_history(db, project),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
        )

        bid_recommendation = get_bid_recommendation(
            project_data={
                "budget": float(project.budget_estimate or 0.0),
                "category": project.category or "other",
                "description": f"{project.title} {project.description} {project.requirements}".strip(),
            },
            user_data=user_historical_data,
        )

        recommended_amount = self._resolve_recommended_amount(
            project=project,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
        )

        competitiveness_score = calculate_competitiveness_score(
            recommended_amount,
            project_data={
                "budget": float(project.budget_estimate or 0.0),
                "category": project.category or "other",
            },
            market_data=market_insights,
        )
        market_insights["competitiveness_score"] = round(float(competitiveness_score), 4)

        deadline_hours_remaining = self._compute_deadline_hours_remaining(project)
        current_active_bids = (
            request.current_active_bids
            if request.current_active_bids is not None
            else self._count_current_active_bids(db, operator.id, exclude_project_id=project.id)
        )

        probability_score = self._estimate_probability_score(
            classification=classification,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            capacity_score=float(profile.capacity_score or 0.0),
            current_active_bids=current_active_bids,
            max_active_bids=request.max_active_bids,
        )

        decision = self.decision_service.evaluate_opportunity(
            BidDecisionRequest(
                project_id=project.id,
                recommended_amount=recommended_amount,
                probability_score=probability_score,
                matched_score=float(classification["score"]),
                deadline_hours_remaining=deadline_hours_remaining,
                current_active_bids=current_active_bids,
                max_active_bids=request.max_active_bids,
                current_workload_score=request.current_workload_score,
            )
        )

        strengths = self._build_strengths(
            classification=classification,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            probability_score=probability_score,
        )
        risk_flags = self._build_risk_flags(
            classification=classification,
            price_prediction=price_prediction,
            similar_projects=similar_projects,
            current_active_bids=current_active_bids,
            max_active_bids=request.max_active_bids,
            deadline_hours_remaining=deadline_hours_remaining,
        )

        return {
            "project_id": project.id,
            "project_title": project.title,
            "operator_id": operator.id,
            "matched": bool(classification["matched"]),
            "matched_score": round(float(classification["score"]), 2),
            "probability_score": probability_score,
            "recommended_amount": recommended_amount,
            "deadline_hours_remaining": deadline_hours_remaining,
            "current_active_bids": current_active_bids,
            "max_active_bids": request.max_active_bids,
            "current_workload_score": request.current_workload_score,
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
            "bid_recommendation": bid_recommendation,
            "similar_projects": similar_projects,
            "decision": decision,
        }

    def _build_user_historical_data(
        self,
        db: Session,
        *,
        operator_id: int,
        request_data: dict | None,
    ) -> dict:
        """Blend stored bid history with any caller-provided what-if inputs."""
        payload = dict(request_data or {})
        bids = db.query(Bid).filter(Bid.user_id == operator_id).all()
        if bids:
            payload.setdefault("average_bid", round(sum(float(bid.bid_amount or 0.0) for bid in bids) / len(bids), 2))
            accepted_count = sum(1 for bid in bids if bid.status == "accepted")
            payload.setdefault("win_rate", round(accepted_count / len(bids), 4))
            payload.setdefault("bid_count", len(bids))
        return payload

    def _build_market_insights(self, project: Project, similar_projects: dict) -> dict:
        """Estimate market context from similar notice budgets."""
        historical_bids = [
            {"amount": float(item.get("budget_estimate") or 0.0)}
            for item in similar_projects.get("results", [])
            if float(item.get("budget_estimate") or 0.0) > 0
        ]

        if not historical_bids and float(project.budget_estimate or 0.0) > 0:
            historical_bids = [{"amount": float(project.budget_estimate or 0.0)}]

        insights = get_price_insights(historical_bids)
        insights["competitiveness_score"] = 0.0
        return insights

    def _load_price_history(self, db: Session, project: Project, *, limit: int = 40) -> list[HistoricalData]:
        """Load recent historical bid-rate samples for price prediction."""
        query = db.query(HistoricalData)
        if project.category:
            query = query.filter(HistoricalData.category == project.category)
        return query.order_by(HistoricalData.opened_at.desc(), HistoricalData.created_at.desc()).limit(limit).all()

    def _resolve_recommended_amount(self, project: Project, price_prediction: dict, bid_recommendation: dict) -> float:
        """Clamp the bid recommendation into a sensible, budget-aware range."""
        budget_cap = float(project.budget_estimate or 0.0)
        price_lower = float(price_prediction.get("price_range_min", 0.0) or 0.0)
        price_upper = float(price_prediction.get("price_range_max", 0.0) or 0.0)
        recommended_amount = float(bid_recommendation.get("recommended_bid", 0.0) or 0.0)

        if budget_cap > 0:
            price_lower = min(price_lower, budget_cap)
            price_upper = min(price_upper if price_upper > 0 else budget_cap, budget_cap)
            recommended_amount = min(recommended_amount or budget_cap, budget_cap)

        if price_upper > 0:
            recommended_amount = min(recommended_amount, price_upper)
        if price_lower > 0:
            recommended_amount = max(recommended_amount, min(price_lower, budget_cap or price_lower))

        return round(max(0.0, recommended_amount), 2)

    def _count_current_active_bids(self, db: Session, operator_id: int, *, exclude_project_id: int | None = None) -> int:
        """Count other active bid decisions already on the operator's plate."""
        query = db.query(BidDecisionRecord).filter(
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.decision_status.in_(self.ACTIVE_DECISION_STATUSES),
        )
        if exclude_project_id is not None:
            query = query.filter(BidDecisionRecord.project_id != exclude_project_id)
        return int(query.count())

    def _compute_deadline_hours_remaining(self, project: Project) -> int | None:
        """Convert a project deadline into remaining whole hours."""
        if not project.deadline:
            return None

        remaining_seconds = (ensure_utc(project.deadline) - utc_now()).total_seconds()
        if remaining_seconds <= 0:
            return 0
        return int(remaining_seconds // 3600)

    def _estimate_probability_score(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        bid_recommendation: dict,
        similar_projects: dict,
        competitiveness_score: float,
        capacity_score: float,
        current_active_bids: int,
        max_active_bids: int,
    ) -> float:
        """Blend the main analysis signals into one pursuit probability score."""
        similarity_scores = [float(item.get("similarity_score", 0.0) or 0.0) for item in similar_projects.get("results", [])]
        similarity_signal = (
            sum(similarity_scores) / len(similarity_scores)
            if similarity_scores
            else self.DEFAULT_SIMILARITY_SCORE
        )
        normalized_capacity = self._normalize_capacity_score(capacity_score)

        probability_score = (
            float(classification.get("score", 0.0)) * 0.34
            + float(bid_recommendation.get("confidence_score", 0.0)) * 0.22
            + float(price_prediction.get("confidence_score", 0.0)) * 0.14
            + float(competitiveness_score) * 0.18
            + similarity_signal * 0.07
            + normalized_capacity * 0.05
        )

        if not classification.get("matched", False):
            probability_score = min(probability_score, 0.49)

        if current_active_bids >= max_active_bids:
            probability_score -= 0.05

        return round(max(0.0, min(1.0, probability_score)), 2)

    def _build_strengths(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        similar_projects: dict,
        competitiveness_score: float,
        probability_score: float,
    ) -> list[str]:
        """Highlight the strongest reasons to pursue the bid."""
        strengths: list[str] = []
        if classification.get("matched", False):
            strengths.append("업체 자격·지역·역량 기준을 전반적으로 충족합니다.")
        if competitiveness_score >= 0.75:
            strengths.append("추천 투찰가가 비교 기준 대비 경쟁력 있는 구간에 있습니다.")
        if float(price_prediction.get("confidence_score", 0.0)) >= 0.75:
            strengths.append("가격 예측 신뢰도가 양호해 투찰 범위 판단에 활용할 수 있습니다.")
        if int(similar_projects.get("result_count", 0) or 0) > 0:
            strengths.append(f"유사 공고 {similar_projects['result_count']}건을 함께 비교해 참고 사례를 확보했습니다.")
        if probability_score >= 0.75:
            strengths.append("종합 낙찰 가능성 점수가 높게 산출되었습니다.")
        return strengths[:5]

    def _build_risk_flags(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        similar_projects: dict,
        current_active_bids: int,
        max_active_bids: int,
        deadline_hours_remaining: int | None,
    ) -> list[str]:
        """Highlight the main risks or constraints that still need human review."""
        risks: list[str] = []
        if not classification.get("matched", False):
            risks.append("자격·지역·면허 조건이 완전히 맞지 않아 수주 리스크가 있습니다.")

        similarity_scores = [float(item.get("similarity_score", 0.0) or 0.0) for item in similar_projects.get("results", [])]
        if not similarity_scores:
            risks.append("직접 비교 가능한 유사 공고 사례가 부족합니다.")
        elif (sum(similarity_scores) / len(similarity_scores)) < 0.4:
            risks.append("유사 사례와의 의미적 일치도가 높지 않아 비교 신뢰도가 제한적입니다.")

        if float(price_prediction.get("confidence_score", 0.0)) < 0.75:
            risks.append("가격 예측 신뢰도가 아직 보수적 수준입니다.")

        if current_active_bids >= max_active_bids:
            risks.append("현재 진행 중인 입찰 건수가 한도에 도달해 실행 부담이 큽니다.")

        if deadline_hours_remaining is not None and deadline_hours_remaining <= 6:
            risks.append("마감 시간이 매우 촉박해 즉시 대응 체계가 필요합니다.")

        return risks

    def _build_summary(
        self,
        *,
        project: Project,
        decision: dict,
        probability_score: float,
        recommended_amount: float,
        strengths: list[str],
        risk_flags: list[str],
    ) -> str:
        """Build a concise analysis summary for web and Telegram surfaces."""
        action_label = {
            "bid_now": "즉시 투찰",
            "review": "추가 검토",
            "skip": "보류",
        }.get(str(decision.get("action", "skip")), "보류")

        parts = [
            f"'{project.title}' 공고는 현재 {action_label} 권장입니다.",
            f"낙찰 가능성 {probability_score:.2f}, 우선순위 {float(decision.get('priority_score', 0.0)):.2f}, 추천 투찰가 {recommended_amount:,.0f}원 기준입니다.",
        ]

        if strengths:
            parts.append(f"강점: {strengths[0]}")
        if risk_flags:
            parts.append(f"유의점: {risk_flags[0]}")

        return " ".join(parts)

    def _normalize_capacity_score(self, value: float | None) -> float:
        """Normalize capacity scores that may arrive on either 0-1 or 0-100 scales."""
        if value is None:
            return 0.0

        normalized = float(value)
        if normalized > 1:
            normalized /= 100.0
        return max(0.0, min(1.0, normalized))