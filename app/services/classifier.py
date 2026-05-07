"""Bid notice classification service skeleton."""
from app.models.models import CompanyProfile, Project


class NoticeClassifierService:
    """Classify project fit against company profile."""

    def classify(self, project: Project, profile: CompanyProfile | None) -> dict:
        """Return a basic rule-based fit result placeholder."""
        reasons = []
        score = 0.0

        if not profile:
            reasons.append("업체 프로필이 없어 기본 점수만 반환합니다.")
            return {"matched": False, "score": score, "reasons": reasons}

        if project.category and profile.business_type:
            if project.category.lower() == profile.business_type.lower():
                score += 0.5
                reasons.append("업무 구분이 일치합니다.")

        if profile.annual_revenue and project.budget_estimate and profile.annual_revenue >= project.budget_estimate:
            score += 0.3
            reasons.append("예산 대비 수행 가능 범위에 들어옵니다.")

        matched = score >= 0.5
        if not reasons:
            reasons.append("추가 판별 데이터가 필요합니다.")

        return {"matched": matched, "score": round(score, 2), "reasons": reasons}
