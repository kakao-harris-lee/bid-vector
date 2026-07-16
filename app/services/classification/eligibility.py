"""Business-type and license (hard categorical) scoring axes.

Both axes are pass/penalty gates over the project↔profile category and the
license-code requirement. Extracted verbatim from ``NoticeClassifierService``
with ``self.<CONST>``→``config`` and ``self._<helper>``→the ``text`` module fn.
"""

from app.models.models import CompanyProfile, Project
from app.services.classification import config, taxonomy
from app.services.classification.assessment import RuleAssessment
from app.services.classification.text import (
    collect_project_text,
    extract_license_tokens,
    normalize_business_type,
)


def assess_business_type(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """Evaluate whether the company business type fits the project category."""
    project_type = normalize_business_type(project.category)
    profile_type = normalize_business_type(profile.business_type)

    if not project_type or not profile_type:
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=config.BUSINESS_TYPE_MISMATCH_PENALTY,
            reasons=["업무 구분 정보가 부족해 1차 업종 필터를 통과시키지 않았습니다."],
        )

    if project_type == profile_type:
        return RuleAssessment(
            score=config.EXACT_BUSINESS_TYPE_SCORE,
            passed=True,
            reasons=[f"업무 구분이 일치합니다. (공고: {project.category} / 업체: {profile.business_type})"],
        )

    if profile_type in taxonomy.RELATED_BUSINESS_TYPES.get(project_type, set()) or project_type in taxonomy.RELATED_BUSINESS_TYPES.get(profile_type, set()):
        return RuleAssessment(
            score=config.RELATED_BUSINESS_TYPE_SCORE,
            passed=True,
            reasons=[f"업무 구분이 인접 분야로 확인되어 부분 적합으로 반영했습니다. (공고: {project.category} / 업체: {profile.business_type})"],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.BUSINESS_TYPE_MISMATCH_PENALTY,
        reasons=[f"업무 구분이 달라 1차 업종 필터를 통과하지 못했습니다. (공고: {project.category} / 업체: {profile.business_type})"],
    )


def assess_license(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """Evaluate whether the company holds the licenses explicitly required by the project."""
    required_licenses = extract_license_tokens(
        collect_project_text(project, include_title=False),
        require_context=True,
    )
    profile_licenses = extract_license_tokens(profile.license_codes)

    if not required_licenses:
        return RuleAssessment(
            score=config.LICENSE_NEUTRAL_SCORE,
            passed=True,
            reasons=["공고에 필수 면허 코드가 명시되지 않아 면허 조건은 중립 처리했습니다."],
        )

    if not profile_licenses:
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=config.LICENSE_MISMATCH_PENALTY,
            reasons=[
                f"공고 필수 면허({', '.join(sorted(required_licenses))})가 확인됐지만 업체 보유 면허 정보가 없습니다."
            ],
        )

    missing_licenses = required_licenses - profile_licenses
    if not missing_licenses:
        return RuleAssessment(
            score=config.LICENSE_MATCH_SCORE,
            passed=True,
            reasons=[f"필수 면허 코드를 모두 보유하고 있습니다: {', '.join(sorted(required_licenses))}."],
        )

    matched_licenses = required_licenses & profile_licenses
    matched_suffix = (
        f" 보유 면허 중 일치 항목: {', '.join(sorted(matched_licenses))}." if matched_licenses else ""
    )
    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.LICENSE_MISMATCH_PENALTY,
        reasons=[
            f"필수 면허 코드가 일부 부족합니다. 누락: {', '.join(sorted(missing_licenses))}.{matched_suffix}".strip()
        ],
    )
