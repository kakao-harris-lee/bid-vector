"""Budget and capability (financial capacity) scoring axes.

The budget axis routes to one of three capacity indicators (시공능력평가액 →
연매출 → capacity_score) after a construction 도급한도 hard ceiling; the
capability axis compares an estimated company capability to an estimated project
requirement. Extracted verbatim from ``NoticeClassifierService`` with
``self.<CONST>``→``config`` and ``self._<helper>``→module functions.
"""

from app.core.bands import resolve_band
from app.models.models import CompanyProfile, Project
from app.services.classification import config, taxonomy
from app.services.classification.assessment import RuleAssessment
from app.services.classification.region import is_construction_project
from app.services.classification.text import (
    collect_project_text,
    normalize_business_type,
    normalize_capacity_score,
)


def assess_budget(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """Evaluate whether the company appears to have enough financial capacity."""
    budget_estimate = float(project.budget_estimate or 0.0)
    annual_revenue = float(profile.annual_revenue or 0.0)
    capacity_score = normalize_capacity_score(profile.capacity_score)
    construction_capacity_amount = float(
        getattr(profile, "construction_capacity_amount", 0.0) or 0.0
    )
    awarded_contract_limit = float(
        getattr(profile, "awarded_contract_limit", 0.0) or 0.0
    )
    project_type = normalize_business_type(project.category)

    if budget_estimate <= 0:
        return RuleAssessment(
            score=0.0,
            passed=True,
            reasons=["공고 예산 정보가 없어 예산 적합도는 참고 점수에서 제외했습니다."],
        )

    # Construction HARD CEILING: 도급한도(awarded_contract_limit) is a legal cap
    # on a single award — exceeding it means 신규 도급 불가 regardless of how
    # strong 시공능력평가액 is. This must fail the budget axis BEFORE the 시공능력
    # ratio branch can pass it. Uses the broad 건설/공사 predicate
    # (``is_construction_project``) — the SAME definition as the
    # opportunity_analysis 도급한도 risk-flag — so the budget gate and the risk
    # reason never disagree on ambiguous categories (e.g. "건설사업관리",
    # "설계·감리"). When 도급한도 is not provided (<= 0) the check is skipped
    # entirely so all existing behaviour is preserved.
    if (
        is_construction_project(project)
        and awarded_contract_limit > 0
        and budget_estimate > awarded_contract_limit
    ):
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=config.BUDGET_MISMATCH_PENALTY,
            reasons=[
                f"공고 예산({budget_estimate:,.0f}원)이 업체 도급한도({awarded_contract_limit:,.0f}원)를 "
                f"초과해 신규 도급이 어렵습니다."
            ],
        )

    # Construction-only: when the operator has filled in 시공능력평가액 it
    # is the canonical 도급가능규모 indicator and outranks annual_revenue
    # /capacity_score. The legacy fallbacks below run unchanged when the
    # field is left at its default (0.0).
    if project_type == "construction" and construction_capacity_amount > 0:
        return _assess_construction_capacity_budget(
            budget_estimate=budget_estimate,
            construction_capacity_amount=construction_capacity_amount,
        )

    if annual_revenue > 0:
        return _assess_revenue_budget(
            budget_estimate=budget_estimate,
            annual_revenue=annual_revenue,
        )

    return assess_capacity_score_budget(capacity_score)


def _assess_construction_capacity_budget(
    *,
    budget_estimate: float,
    construction_capacity_amount: float,
) -> RuleAssessment:
    capacity_ratio = construction_capacity_amount / budget_estimate
    capacity_label = f"시공능력평가액 {construction_capacity_amount:,.0f}원"
    if capacity_ratio >= 3:
        return RuleAssessment(
            score=config.BUDGET_STRONG_SCORE,
            passed=True,
            reasons=[
                f"{capacity_label}이 공고 예산의 {capacity_ratio:.1f}배 수준으로 도급 가능 규모가 충분합니다."
            ],
        )
    if capacity_ratio >= 1:
        return RuleAssessment(
            score=config.BUDGET_GOOD_SCORE,
            passed=True,
            reasons=[
                f"{capacity_label}이 공고 예산 이상으로 시공능력 기준 필터를 충족합니다. (배수: {capacity_ratio:.1f})"
            ],
        )
    if capacity_ratio >= config.BUDGET_BORDERLINE_RATIO:
        return RuleAssessment(
            score=config.BUDGET_BORDERLINE_SCORE,
            passed=True,
            reasons=[
                f"{capacity_label}이 공고 예산 대비 다소 타이트하지만 시공능력 기준으로 보수적 통과 처리했습니다. (배수: {capacity_ratio:.1f})"
            ],
        )
    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.BUDGET_MISMATCH_PENALTY,
        reasons=[
            f"{capacity_label}이 공고 예산 대비 부족해 시공능력 기준 필터를 통과하지 못했습니다. (배수: {capacity_ratio:.1f})"
        ],
    )


def _assess_revenue_budget(
    *,
    budget_estimate: float,
    annual_revenue: float,
) -> RuleAssessment:
    revenue_ratio = annual_revenue / budget_estimate
    if revenue_ratio >= 3:
        return RuleAssessment(
            score=config.BUDGET_STRONG_SCORE,
            passed=True,
            reasons=[f"연매출이 공고 예산의 {revenue_ratio:.1f}배 수준으로 예산 대응 여력이 충분합니다."],
        )
    if revenue_ratio >= 1:
        return RuleAssessment(
            score=config.BUDGET_GOOD_SCORE,
            passed=True,
            reasons=[f"연매출이 공고 예산 이상으로 기본 예산 필터를 충족합니다. (배수: {revenue_ratio:.1f})"],
        )
    if revenue_ratio >= config.BUDGET_BORDERLINE_RATIO:
        return RuleAssessment(
            score=config.BUDGET_BORDERLINE_SCORE,
            passed=True,
            reasons=[f"연매출이 공고 예산 대비 다소 타이트하지만 1차 수행 가능 범위로 판단했습니다. (배수: {revenue_ratio:.1f})"],
        )
    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.BUDGET_MISMATCH_PENALTY,
        reasons=[f"연매출이 공고 예산 대비 낮아 예산 적합도 필터를 통과하지 못했습니다. (배수: {revenue_ratio:.1f})"],
    )


def assess_capacity_score_budget(capacity_score: float) -> RuleAssessment:
    score, reason_template = resolve_band(
        capacity_score, config.CAPACITY_SCORE_BUDGET_BANDS
    )
    return RuleAssessment(
        score=score,
        passed=True,
        reasons=[reason_template.format(capacity_score=capacity_score)],
    )


def assess_capability(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """Evaluate whether the company's operational capability seems adequate for the project size."""
    available_capability = estimate_company_capability(profile)
    required_capability = estimate_required_capability(project)

    if available_capability >= required_capability + 0.15:
        return RuleAssessment(
            score=config.CAPABILITY_STRONG_SCORE,
            passed=True,
            reasons=[
                f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})보다 충분히 높습니다."
            ],
        )

    if available_capability >= required_capability:
        return RuleAssessment(
            score=config.CAPABILITY_GOOD_SCORE,
            passed=True,
            reasons=[
                f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})을 충족합니다."
            ],
        )

    if available_capability + 0.1 >= required_capability:
        return RuleAssessment(
            score=config.CAPABILITY_BORDERLINE_SCORE,
            passed=True,
            reasons=[
                f"업체 수행능력이 요구 수준({required_capability:.2f})에 근접해 보수적으로 통과 처리했습니다. 현재 추정치: {available_capability:.2f}."
            ],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.CAPABILITY_MISMATCH_PENALTY,
        reasons=[
            f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})보다 낮아 수행 범위 필터를 통과하지 못했습니다."
        ],
    )


def estimate_company_capability(profile: CompanyProfile) -> float:
    """Estimate company execution capability using profile score and delivery history."""
    capacity_score = normalize_capacity_score(profile.capacity_score)
    award_bonus = min(
        config.AWARD_BONUS_CAP,
        max(0, int(profile.total_awards or 0)) * config.AWARD_BONUS_PER_AWARD,
    )
    return min(1.0, max(capacity_score, capacity_score + award_bonus))


def estimate_required_capability(project: Project) -> float:
    """Estimate the capability level a project likely requires."""
    project_budget = max(float(project.budget_estimate or 0.0), float(project.budget_max or 0.0))
    if project_budget >= 500_000_000:
        required = 0.85
    elif project_budget >= 200_000_000:
        required = 0.7
    elif project_budget >= 100_000_000:
        required = 0.55
    else:
        required = 0.4

    project_text = collect_project_text(project)
    required += min(0.15, sum(0.03 for keyword in taxonomy.COMPLEXITY_KEYWORDS if keyword in project_text))
    return min(1.0, required)
