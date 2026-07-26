"""Strengths / risk-flag / summary rendering for opportunity analysis.

Builds the human-facing strengths and risk reasons (including the construction
and 도급한도 risk signals from :mod:`.risk_signals`), suppresses the region_bonus
risk for in-region operators, and renders the concise analysis summary. Methods
are moved verbatim from the original ``OpportunityAnalysisService`` body.
"""

from __future__ import annotations

from app.core.config import settings
from app.models.models import CompanyProfile, Project
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase
from app.services.opportunity_analysis.risk_signals import (
    _detect_awarded_contract_limit_risks,
    _detect_construction_risk_reasons,
)


class _FlagsMixin(_OpportunityAnalysisBase):
    """Strengths, risk flags, region-preference suppression, and summary text."""

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
        if competitiveness_score >= settings.OPPORTUNITY_STRENGTH_COMPETITIVENESS_MIN:
            strengths.append("추천 투찰가가 비교 기준 대비 경쟁력 있는 구간에 있습니다.")
        if expected_margin_score >= settings.OPPORTUNITY_STRENGTH_MARGIN_MIN:
            strengths.append("예상 수익성 여력이 비교적 양호한 편입니다.")
        if float(price_prediction.get("confidence_score", 0.0)) >= settings.OPPORTUNITY_STRENGTH_PRICE_CONFIDENCE_MIN:
            strengths.append("가격 예측 신뢰도가 양호해 투찰 범위 판단에 활용할 수 있습니다.")
        if int(similar_projects.get("result_count", 0) or 0) > 0:
            strengths.append(f"유사 공고 {similar_projects['result_count']}건을 함께 비교해 참고 사례를 확보했습니다.")
        if probability_score >= settings.OPPORTUNITY_STRENGTH_PROBABILITY_MIN:
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
        elif (sum(similarity_scores) / len(similarity_scores)) < settings.OPPORTUNITY_RISK_SIMILARITY_MIN:
            risks.append("유사 사례와의 의미적 일치도가 높지 않아 비교 신뢰도가 제한적입니다.")

        if float(price_prediction.get("confidence_score", 0.0)) < settings.OPPORTUNITY_RISK_PRICE_CONFIDENCE_MIN:
            risks.append("가격 예측 신뢰도가 아직 보수적 수준입니다.")

        if expected_margin_score <= settings.OPPORTUNITY_RISK_MARGIN_MAX:
            risks.append("추천 투찰가 기준 예상 수익 여력이 낮아 손익 검토가 필요합니다.")

        if execution_complexity_score >= settings.OPPORTUNITY_RISK_COMPLEXITY_MAX:
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
