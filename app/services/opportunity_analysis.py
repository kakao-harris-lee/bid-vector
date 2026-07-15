"""Integrated opportunity analysis for award-oriented bidding decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai.bid_recommendation import calculate_competitiveness_score
from app.ai.bid_target import build_bid_target_menu
from app.ai.business_group import resolve_business_group
from app.ai.factory import build_bid_recommendation_port, build_price_prediction_port
from app.ai.predictors.historical import apply_probability_calibration
from app.ai.price_prediction import get_price_insights
from app.ai.service_interfaces import BidRecommendationPort, PricePredictionPort
from app.core.constants import ACTIVE_DECISION_STATUSES as _ACTIVE_DECISION_STATUSES
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_profile_for,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.core.time import ensure_utc, utc_now
from app.models.models import Bid, BidDecisionRecord, CompanyProfile, OperatorStrategy, Project, User
from app.schemas.schemas import BidDecisionRequest, OpportunityAnalysisRequest
from app.services.allocation import BidDecisionService
from app.services.bid_base import resolve_notice_bid_base
from app.services.bid_target_signals import resolve_bid_target_signals
from app.services.classifier import (
    NoticeClassifierService,
    REGION_PREFERENCE_PATTERN,
    is_construction_project,
)
from app.services.prediction_dataset import PredictionDatasetService
from app.services.prediction_feedback import PredictionFeedbackService
from app.services.project_similarity import ProjectSimilarityService
from app.services.operator_strategy_tuning import resolve_category_priority_override


# Named constant for the region_bonus risk message so the in-region suppression
# filter (see _build_risk_flags) matches by identity, not a fragile substring.
_REGION_BONUS_RISK_MESSAGE = "지역 가산점/소재지 가산 조건이 있어 비지역 업체는 점수 불리할 수 있습니다."

# Construction-specific risk signals (v1) — keyword/regex heuristics applied only
# when the notice is classified as construction. Each tuple is
# (category_id, compiled_pattern, reason_text). Ordered so reasons render in a
# stable sequence. One reason per category_id is appended to risk_flags even if
# multiple patterns in the same category match.
_CONSTRUCTION_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # Region-restricted joint-venture is matched FIRST so its more specific
    # signal wins when the text uses 지역의무공동도급 (which also contains
    # 공동도급). Categories are deduplicated, so a generic 공동도급 hit will
    # NOT also trigger when the region variant already matched, but the
    # opposite would erroneously fire generic-only — hence ordering matters.
    (
        "region_joint_venture",
        re.compile(r"지역\s*의무\s*공동\s*도급|지역\s*의무\s*공동\s*수급|지역\s*제한|지역\s*업체|해당\s*지역\s*소재"),
        "지역의무공동도급 또는 지역 제한 요건이 명시돼 있어 해당 지역 파트너/소재가 필요합니다.",
    ),
    (
        "joint_venture",
        re.compile(r"공동\s*도급|공동\s*수급(?:체)?|컨소시엄|\bjv\b"),
        "공동도급/공동수급체 구성이 요구됩니다. 파트너 확보·지분 협상이 필요합니다.",
    ),
    (
        "similar_experience",
        re.compile(r"유사\s*실적|동종\s*실적|최근\s*\d+\s*년\s*실적|시공\s*실적|납품\s*실적"),
        "유사 실적/시공실적 요건이 명시돼 있어 운영자 실적 증빙이 필요합니다.",
    ),
    (
        # Reuse the SHARED compiled pattern from classifier so the region_bonus
        # risk-flag and the classifier's region-score boost can never disagree on
        # what "지역가산점" means (single source of truth).
        "region_bonus",
        REGION_PREFERENCE_PATTERN,
        _REGION_BONUS_RISK_MESSAGE,
    ),
)


def _is_construction_project(project: Project) -> bool:
    """Decide whether to run construction-specific risk heuristics on this notice.

    Delegates to the shared ``classifier.is_construction_project`` so the
    construction risk heuristics here and the 도급한도 budget gate in
    ``_assess_budget`` always use ONE definition (no divergence on ambiguous
    categories such as "건설사업관리" / "설계·감리"). Signals only fire when the
    project category normalizes to a construction tag, which keeps unrelated
    software/goods notices that happen to mention 공동도급/유사실적 in different
    domain contexts from raising construction-flavored risk reasons.
    """
    return is_construction_project(project)


# 매치 직후 짧은 윈도우 내 부정어가 있으면 신호로 잡지 않음
# (예: "지역 제한 없음", "유사실적 불요" → 위험 아님)
_NEGATION_NEAR_MATCH = re.compile(r"\s*(?:없|미적용|불요|무관|면제|미요구|미해당)")


def _detect_construction_risk_reasons(
    project: Project,
    *,
    exclude_region_bonus: bool = False,
) -> list[str]:
    """Return ordered, deduplicated construction risk reasons matched on the notice text.

    When ``exclude_region_bonus`` is True the ``region_bonus`` reason is dropped —
    used for an in-region operator, for whom 지역가산점 is a competitive advantage
    (reflected as a classifier score boost) rather than a risk. Out-of-region
    operators keep the reason because for them it is a genuine disadvantage.
    """
    if not _is_construction_project(project):
        return []

    parts = [getattr(project, "title", None) or "",
             getattr(project, "description", None) or "",
             getattr(project, "requirements", None) or ""]
    notice_text = " ".join(part for part in parts if part).lower()
    if not notice_text:
        return []

    matched: list[str] = []
    seen_categories: set[str] = set()
    for category_id, pattern, reason in _CONSTRUCTION_RISK_PATTERNS:
        if category_id in seen_categories:
            continue
        if exclude_region_bonus and category_id == "region_bonus":
            continue
        hit = pattern.search(notice_text)
        if hit is None:
            continue
        # 부정 문맥 가드: 매치 직후 12자 내 부정어가 있으면 잡지 않음
        tail = notice_text[hit.end():hit.end() + 12]
        if _NEGATION_NEAR_MATCH.match(tail):
            continue
        matched.append(reason)
        seen_categories.add(category_id)
    return matched


def _detect_awarded_contract_limit_risks(
    project: Project,
    profile: CompanyProfile | None,
) -> list[str]:
    """Return risk reasons when a construction notice's budget exceeds the operator's awarded contract limit.

    v2 single-notice guard: when the operator has provided an
    ``awarded_contract_limit`` (도급한도) and the current construction notice's
    ``budget_estimate`` exceeds that limit, surface a risk reason. Cumulative
    accounting (sum of already-held awards + this new one) is intentionally
    out of scope here and tracked as v3.

    Gates (any one returns []):
      - profile is None
      - project is not a construction project
      - profile.awarded_contract_limit is missing / non-positive (treated as "not provided")
    """
    if profile is None:
        return []
    if not _is_construction_project(project):
        return []

    limit = float(getattr(profile, "awarded_contract_limit", 0.0) or 0.0)
    if limit <= 0:
        return []

    budget = float(getattr(project, "budget_estimate", 0.0) or 0.0)
    if budget <= limit:
        return []

    return [
        f"공고 예산({budget:,.0f}원)이 업체 도급한도({limit:,.0f}원)를 초과해 신규 도급이 어렵습니다."
    ]


@dataclass(frozen=True)
class _AnalysisScores:
    """Bundle of the per-analysis derived scores produced by ``_compute_scores``.

    A plain value carrier so ``analyze_project`` can keep its existing local
    variable names. Holds the exact float values the inline code produced — no
    additional rounding or transformation happens in this container.
    """

    competitiveness_score: float
    expected_margin_score: float
    execution_complexity_score: float


@dataclass(frozen=True)
class _AnalysisInputs:
    classification: dict
    similar_projects: list[Project]
    market_insights: dict
    user_historical_data: dict


class OpportunityAnalysisService:
    """Combine fit, price, market, similarity, and action guidance into one analysis."""

    ACTIVE_DECISION_STATUSES = _ACTIVE_DECISION_STATUSES
    DEFAULT_SIMILARITY_SCORE = 0.35
    # Upper bound on probability_score for notices that did NOT match the
    # operator profile. This is the honesty-spec non-matched gate / invariant:
    # an unmatched notice can never present as a high-pursuit opportunity,
    # regardless of heuristic or calibrated inputs. Value is load-bearing — do
    # not change it; this only names the existing 0.49 literal.
    NON_MATCHED_PROBABILITY_CAP = 0.49
    # Weights that blend the main analysis signals into the pursuit
    # probability_score (see _estimate_probability_score). These weights are
    # specific to THIS module's probability_score composition and are unrelated
    # to the BidDecisionService opportunity-score weights in allocation.py or
    # the paper-bidding P(win) fallback weights — do not merge them. The six
    # weights sum to 1.0.
    PROBABILITY_BLEND_CLASSIFICATION_WEIGHT = 0.34
    PROBABILITY_BLEND_RECOMMENDATION_WEIGHT = 0.22
    PROBABILITY_BLEND_PRICE_WEIGHT = 0.14
    PROBABILITY_BLEND_COMPETITIVENESS_WEIGHT = 0.18
    PROBABILITY_BLEND_SIMILARITY_WEIGHT = 0.07
    PROBABILITY_BLEND_CAPACITY_WEIGHT = 0.05
    EXECUTION_COMPLEXITY_KEYWORDS = (
        "통합",
        "고도화",
        "운영",
        "유지관리",
        "24시간",
        "대규모",
        "다기관",
        "클라우드",
        "센터",
        "실시간",
        "연계",
        "보안",
        "이관",
        "플랫폼",
    )

    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
        bid_recommendation_port: BidRecommendationPort | None = None,
    ) -> None:
        self.classifier = NoticeClassifierService()
        self.dataset_service = PredictionDatasetService()
        self.decision_service = BidDecisionService()
        self.feedback_service = PredictionFeedbackService()
        self.similarity_service = ProjectSimilarityService()
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
        self.bid_recommendation_port = bid_recommendation_port or build_bid_recommendation_port()

    def analyze_project(
        self,
        db: Session,
        project: Project,
        request: OpportunityAnalysisRequest,
        *,
        operator: User | None = None,
    ) -> dict:
        """Build a multi-angle bid opportunity analysis for one project."""
        operator, profile, strategy = self._resolve_operator_context(db, operator)

        analysis_inputs = self._build_analysis_inputs(
            db,
            project=project,
            request=request,
            profile=profile,
            operator_id=operator.id,
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
            bid_recommendation=bid_recommendation,
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
    ) -> _AnalysisInputs:
        classification = self.classifier.classify(project=project, profile=profile)
        similar_projects = self.similarity_service.find_similar_projects(
            db,
            project,
            limit=request.similar_limit,
            min_similarity=request.min_similarity,
            same_category_only=request.same_category_only,
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

    def _build_price_prediction(
        self,
        db: Session,
        *,
        project: Project,
        request: OpportunityAnalysisRequest,
        operator_id: int,
    ) -> tuple[dict, str | None]:
        feedback_calibration = self.feedback_service.build_calibration_context(
            db,
            operator_id=operator_id,
            category=project.category,
            agency_name=request.agency_name,
        )
        business_type_code = getattr(project, "business_type_code", None)
        business_group = resolve_business_group(business_type_code)
        # 투찰가는 추정가격이 아니라 기초금액/사업금액(배정예산) 기준으로 산정한다.
        # 과세 공고에서 추정가격을 넘기면 ~10% 낮게 산정되어 낙찰하한 미만으로 낙될
        # 위험이 있으므로 base_amount 를 해석해 넘긴다.
        bid_base = resolve_notice_bid_base(db, project)
        prediction = self.price_prediction_port.predict_price(
            budget=bid_base,
            category=project.category or "other",
            description=f"{project.description or ''} {project.requirements or ''}".strip(),
            historical_records=self._load_price_history(db, project),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
            business_type_code=business_type_code,
            business_group=business_group,
            legal_floor_bid_rate=request.legal_floor_bid_rate,
        )
        # 발주처 밴드(floor/ceiling) 위에 공고별 신호로 위치를 조정한 3종 투찰가
        # 메뉴를 additive 레이어로 첨부한다. 밴드가 없으면(None) 메뉴도 없다.
        menu = build_bid_target_menu(
            floor_bid_rate=prediction.get("floor_bid_rate"),
            ceiling_bid_rate=prediction.get("ceiling_bid_rate"),
            budget=bid_base,
            signals=resolve_bid_target_signals(
                db, agency_name=request.agency_name, category=project.category
            ),
        )
        if menu is not None:
            prediction["bid_target_menu"] = menu
        return (prediction, business_group)

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
                # inflating opportunity/priority and the "예산 대비 추천가 유지율"
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

    def _compute_scores(
        self,
        *,
        project: Project,
        classification: dict,
        price_prediction: dict,
        recommended_amount: float,
        market_insights: dict,
        capacity_score: float,
        deadline_hours_remaining: int | None,
        current_active_bids: int,
        max_active_bids: int,
    ) -> _AnalysisScores:
        """Compute the competitiveness, expected-margin, and execution-complexity scores.

        Pure extraction of the three derived score computations from
        ``analyze_project``. Competitiveness is computed first and writes its
        rounded value back into ``market_insights`` exactly as the inline code
        did (so the market block in the response is unchanged), then margin and
        complexity reuse the existing ``_estimate_*`` helpers with identical
        arguments. No values are altered here.
        """
        competitiveness_score = calculate_competitiveness_score(
            recommended_amount,
            project_data={
                "budget": float(project.budget_estimate or 0.0),
                "category": project.category or "other",
            },
            market_data=market_insights,
        )
        market_insights["competitiveness_score"] = round(float(competitiveness_score), 4)

        expected_margin_score = self._estimate_expected_margin_score(
            project=project,
            recommended_amount=recommended_amount,
            price_prediction=price_prediction,
            competitiveness_score=float(competitiveness_score),
            capacity_score=capacity_score,
        )
        execution_complexity_score = self._estimate_execution_complexity_score(
            project=project,
            classification=classification,
            deadline_hours_remaining=deadline_hours_remaining,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
            capacity_score=capacity_score,
        )
        return _AnalysisScores(
            competitiveness_score=competitiveness_score,
            expected_margin_score=expected_margin_score,
            execution_complexity_score=execution_complexity_score,
        )

    def _resolve_final_probability_score(
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
        business_group: str | None,
        category_priority_override: float,
    ) -> float:
        """Resolve the final pursuit ``probability_score`` through its full override chain.

        Pure extraction of the probability_score reassignment chain from
        ``analyze_project``. The reassignment order, operations, and honesty-spec
        gates are byte-identical to the inline code:

          1. heuristic blend via ``_estimate_probability_score``
          2. calibrated-or-heuristic override via
             ``_apply_calibrated_or_heuristic_probability`` (preserves the 0.49
             non-matched cap internally)
          3. category-priority offset via ``_apply_category_priority_override``
          4. non-matched 0.49 cap re-applied last

        The ``matched_score`` override (which uses ``override * 0.5`` and is
        independent of this chain) stays in ``analyze_project``.
        """
        probability_score = self._estimate_probability_score(
            classification=classification,
            price_prediction=price_prediction,
            bid_recommendation=bid_recommendation,
            similar_projects=similar_projects,
            competitiveness_score=competitiveness_score,
            capacity_score=capacity_score,
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
        )
        # When a settlement-calibrated curve is published, replace the heuristic
        # P(낙찰) with the calibrated value; otherwise keep the heuristic. The
        # non-matched 0.49 gate is preserved either way. (Offline / fresh
        # environments fall back to the heuristic unchanged.)
        probability_score = self._apply_calibrated_or_heuristic_probability(
            heuristic_probability=probability_score,
            classification=classification,
            price_prediction=price_prediction,
            business_group=business_group,
        )
        probability_score = self._apply_category_priority_override(
            probability_score,
            category_priority_override,
        )
        if not classification.get("matched", False):
            probability_score = min(probability_score, self.NON_MATCHED_PROBABILITY_CAP)
        return probability_score

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
        bid_recommendation: dict,
        similar_projects: dict,
    ) -> dict:
        """Assemble the final analysis response dict.

        Pure extraction of the terminal ``return {...}`` in ``analyze_project``.
        Field names, values, ordering, and the nested ``_build_summary`` call are
        unchanged.
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

    def _load_price_history(self, db: Session, project: Project, *, limit: int = 40) -> list[dict[str, object]]:
        """Load recent historical bid-rate samples for price prediction."""
        return self.dataset_service.load_historical_series(
            db,
            category=project.category,
            limit=limit,
            explicit_bid_rate_only=True,
        )

    def _resolve_recommended_amount(self, project: Project, price_prediction: dict, bid_recommendation: dict) -> float:
        """Clamp the bid recommendation into a sensible, budget-aware range.

        ★결정1: when a per-notice 투찰가 메뉴 was assembled (발주처 밴드가 있어
        메뉴가 존재), align the recommended amount to the menu's 'recommended'
        option 투찰가 (사업금액 base × 위치조정 rate) so the headline
        recommended_amount and the menu never disagree. The budget-aware clamping
        below is kept as the fallback when there is no menu.
        """
        menu = (price_prediction or {}).get("bid_target_menu")
        if menu:
            for option in menu.get("options", []):
                if option.get("label") == "recommended" and option.get("bid_price") is not None:
                    return float(option["bid_price"])

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
        return len(self._load_current_active_bid_records(db, operator_id, exclude_project_id=exclude_project_id))

    def _load_current_active_bid_records(
        self,
        db: Session,
        operator_id: int,
        *,
        exclude_project_id: int | None = None,
    ) -> list[BidDecisionRecord]:
        """Return active bid-decision records currently occupying operator capacity."""
        query = db.query(BidDecisionRecord).filter(
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.decision_status.in_(self.ACTIVE_DECISION_STATUSES),
        )
        if exclude_project_id is not None:
            query = query.filter(BidDecisionRecord.project_id != exclude_project_id)
        return query.all()

    def _resolve_workload_context(
        self,
        db: Session,
        *,
        operator_id: int,
        request: OpportunityAnalysisRequest,
        exclude_project_id: int | None = None,
    ) -> tuple[int, float, str]:
        """Resolve active bid count and workload score from explicit input or stored active decisions."""
        active_records = self._load_current_active_bid_records(
            db,
            operator_id,
            exclude_project_id=exclude_project_id,
        )
        current_active_bids = (
            int(request.current_active_bids)
            if request.current_active_bids is not None
            else len(active_records)
        )

        if request.current_workload_score is not None:
            normalized_workload = max(0.0, min(1.0, float(request.current_workload_score)))
            return current_active_bids, round(normalized_workload, 2), "provided"

        auto_workload_score = self._estimate_current_workload_score(
            active_records=active_records,
            current_active_bids=current_active_bids,
            max_active_bids=request.max_active_bids,
        )
        return current_active_bids, auto_workload_score, "auto"

    def _estimate_current_workload_score(
        self,
        *,
        active_records: list[BidDecisionRecord],
        current_active_bids: int,
        max_active_bids: int,
    ) -> float:
        """Estimate current workload from persisted active bid decisions when the caller omits it."""
        if current_active_bids <= 0:
            return 0.0

        safe_max = max(1, max_active_bids)
        active_load_ratio = min(1.0, current_active_bids / safe_max)

        if not active_records:
            return round(active_load_ratio * 0.65, 2)

        average_priority = sum(float(record.priority_score or 0.0) for record in active_records) / len(active_records)
        urgent_ratio = sum(
            1 for record in active_records
            if record.deadline_hours_remaining is not None and int(record.deadline_hours_remaining) <= 24
        ) / len(active_records)
        review_ratio = sum(1 for record in active_records if str(record.decision_status) == "reviewing") / len(active_records)

        workload_score = (
            active_load_ratio * 0.5
            + average_priority * 0.25
            + urgent_ratio * 0.15
            + review_ratio * 0.1
        )
        return round(max(0.0, min(1.0, workload_score)), 2)

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
            float(classification.get("score", 0.0)) * self.PROBABILITY_BLEND_CLASSIFICATION_WEIGHT
            + float(bid_recommendation.get("confidence_score", 0.0)) * self.PROBABILITY_BLEND_RECOMMENDATION_WEIGHT
            + float(price_prediction.get("confidence_score", 0.0)) * self.PROBABILITY_BLEND_PRICE_WEIGHT
            + float(competitiveness_score) * self.PROBABILITY_BLEND_COMPETITIVENESS_WEIGHT
            + similarity_signal * self.PROBABILITY_BLEND_SIMILARITY_WEIGHT
            + normalized_capacity * self.PROBABILITY_BLEND_CAPACITY_WEIGHT
        )

        if not classification.get("matched", False):
            probability_score = min(probability_score, self.NON_MATCHED_PROBABILITY_CAP)

        if current_active_bids >= max_active_bids:
            probability_score -= 0.05

        return round(max(0.0, min(1.0, probability_score)), 2)

    def _apply_calibrated_or_heuristic_probability(
        self,
        *,
        heuristic_probability: float,
        classification: dict,
        price_prediction: dict,
        business_group: str | None,
    ) -> float:
        """Override the heuristic P(낙찰) with the calibrated curve when published.

        Uses the SAME inference-time signals (confidence/matched) the backtest path
        feeds the curve. When no calibration artifact exists, the heuristic is kept
        unchanged. The non-matched 0.49 gate is enforced here so calibration can
        never let an unmatched notice present as a high-pursuit opportunity.
        """
        probability = float(heuristic_probability)
        calibrated = apply_probability_calibration(
            {
                "confidence_score": float(
                    price_prediction.get("confidence_score", 0.0) or 0.0
                ),
                "matched_score": float(classification.get("score", 0.0) or 0.0),
                "business_group": business_group,
            }
        )
        if calibrated is not None:
            probability = calibrated
        if not classification.get("matched", False):
            probability = min(probability, self.NON_MATCHED_PROBABILITY_CAP)
        return round(max(0.0, min(1.0, probability)), 2)

    def _apply_category_priority_override(self, score: float, override: float) -> float:
        """Apply a bounded category priority offset to an analysis score."""
        return round(max(0.0, min(1.0, float(score) + float(override))), 2)

    def _estimate_expected_margin_score(
        self,
        *,
        project: Project,
        recommended_amount: float,
        price_prediction: dict,
        competitiveness_score: float,
        capacity_score: float,
    ) -> float:
        """Estimate a profitability proxy from budget retention, floor headroom, and execution confidence."""
        budget_estimate = float(project.budget_estimate or 0.0)
        if budget_estimate <= 0:
            return 0.5

        recommended_rate = max(0.0, min(1.0, float(recommended_amount or 0.0) / budget_estimate))
        floor_bid_rate = max(0.0, min(1.0, float(price_prediction.get("floor_bid_rate", 0.0) or 0.0)))
        predicted_bid_rate = max(
            0.0,
            min(1.0, float(price_prediction.get("predicted_bid_rate", recommended_rate) or recommended_rate)),
        )
        price_confidence = max(0.0, min(1.0, float(price_prediction.get("confidence_score", 0.0) or 0.0)))
        normalized_capacity = self._normalize_capacity_score(capacity_score)

        if floor_bid_rate > 0:
            floor_headroom = max(0.0, min(1.0, (recommended_rate - floor_bid_rate) / max(1e-6, 1.0 - floor_bid_rate)))
        else:
            floor_headroom = recommended_rate

        prediction_alignment = max(0.0, 1.0 - min(abs(recommended_rate - predicted_bid_rate) / 0.12, 1.0))

        expected_margin_score = (
            recommended_rate * 0.35
            + floor_headroom * 0.2
            + prediction_alignment * 0.2
            + price_confidence * 0.15
            + normalized_capacity * 0.1
        )
        return round(max(0.0, min(1.0, expected_margin_score)), 2)

    def _estimate_execution_complexity_score(
        self,
        *,
        project: Project,
        classification: dict,
        deadline_hours_remaining: int | None,
        current_active_bids: int,
        max_active_bids: int,
        capacity_score: float,
    ) -> float:
        """Estimate delivery complexity from project scale, wording, schedule pressure, and current capacity."""
        project_budget = max(float(project.budget_estimate or 0.0), float(project.budget_max or 0.0))
        if project_budget >= 500_000_000:
            budget_signal = 0.92
        elif project_budget >= 200_000_000:
            budget_signal = 0.78
        elif project_budget >= 100_000_000:
            budget_signal = 0.62
        else:
            budget_signal = 0.38

        project_text = " ".join(part for part in [project.title or "", project.description or "", project.requirements or ""] if part).lower()
        keyword_hits = sum(1 for keyword in self.EXECUTION_COMPLEXITY_KEYWORDS if keyword in project_text)
        keyword_signal = min(1.0, 0.24 + (keyword_hits * 0.08))

        if deadline_hours_remaining is None:
            deadline_signal = 0.3
        elif deadline_hours_remaining <= 6:
            deadline_signal = 1.0
        elif deadline_hours_remaining <= 24:
            deadline_signal = 0.78
        elif deadline_hours_remaining <= 72:
            deadline_signal = 0.52
        else:
            deadline_signal = 0.24

        active_load_ratio = min(1.0, current_active_bids / max(1, max_active_bids))
        match_friction = max(0.0, min(1.0, 1.0 - float(classification.get("score", 0.0) or 0.0)))
        capacity_friction = max(0.0, min(1.0, 1.0 - self._normalize_capacity_score(capacity_score)))

        complexity_score = (
            budget_signal * 0.3
            + keyword_signal * 0.25
            + deadline_signal * 0.15
            + active_load_ratio * 0.1
            + match_friction * 0.1
            + capacity_friction * 0.1
        )
        return round(max(0.0, min(1.0, complexity_score)), 2)

    def _build_strengths(
        self,
        *,
        classification: dict,
        price_prediction: dict,
        similar_projects: dict,
        competitiveness_score: float,
        probability_score: float,
        expected_margin_score: float,
    ) -> list[str]:
        """Highlight the strongest reasons to pursue the bid."""
        strengths: list[str] = []
        if classification.get("matched", False):
            strengths.append("업체 자격·지역·역량 기준을 전반적으로 충족합니다.")
        if competitiveness_score >= 0.75:
            strengths.append("추천 투찰가가 비교 기준 대비 경쟁력 있는 구간에 있습니다.")
        if expected_margin_score >= 0.72:
            strengths.append("예상 수익성 여력이 비교적 양호한 편입니다.")
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
        expected_margin_score: float,
        execution_complexity_score: float,
        project: Project | None = None,
        profile: CompanyProfile | None = None,
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

        if expected_margin_score <= 0.45:
            risks.append("추천 투찰가 기준 예상 수익 여력이 낮아 손익 검토가 필요합니다.")

        if execution_complexity_score >= 0.72:
            risks.append("통합 범위와 일정 압박을 감안할 때 실행 복잡도가 높은 편입니다.")

        if current_active_bids >= max_active_bids:
            risks.append("현재 진행 중인 입찰 건수가 한도에 도달해 실행 부담이 큽니다.")

        if deadline_hours_remaining is not None and deadline_hours_remaining <= 6:
            risks.append("마감 시간이 매우 촉박해 즉시 대응 체계가 필요합니다.")

        if project is not None:
            # 지역가산점 + 업체 수행지역 일치 = 우대(가점)이지 리스크가 아니므로,
            # in-region 운영자에게는 region_bonus 리스크를 숨긴다. 비지역 운영자는
            # 여전히 불리하므로 리스크를 유지한다.
            exclude_region_bonus = self._operator_is_in_region_with_preference(project, profile)
            risks.extend(
                _detect_construction_risk_reasons(
                    project, exclude_region_bonus=exclude_region_bonus
                )
            )
            risks.extend(_detect_awarded_contract_limit_risks(project, profile))

        return risks

    def _operator_is_in_region_with_preference(
        self,
        project: Project,
        profile: CompanyProfile | None,
    ) -> bool:
        """True when the 지역가산점/지역우대 boost applies to this (project, profile) —
        the in-region case where 지역가산점 is a strength (classifier score boost),
        not a risk, so the region_bonus risk is suppressed.

        Delegates to the classifier's SINGLE source of truth
        (``region_preference_boost_applies``) so the risk-suppression and the
        region-score boost can never disagree (e.g. they now agree symmetrically
        on 전국-notice and 전국-profile cases). No-profile → False → risk shown.
        """
        return self.classifier.region_preference_boost_applies(project, profile)

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
