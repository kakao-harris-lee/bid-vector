"""Unit tests for the awarded_contract_limit (도급한도) v2 single-notice guard.

These exercise the pure ``_detect_awarded_contract_limit_risks`` helper and the
``OpportunityAnalysisService._build_risk_flags`` wiring around it without
spinning up the full classifier / predictor pipeline.

v2 spec (PR #66 v1 → v2):
- Construction-only (`_is_construction_project` gate).
- Requires a positive ``CompanyProfile.awarded_contract_limit`` (0 = not provided).
- Flags when ``project.budget_estimate`` strictly exceeds the limit.
- Cumulative "current held awards + new" accounting is intentionally v3.
"""

from __future__ import annotations

from app.models.models import CompanyProfile, Project
from app.services.opportunity_analysis import (
    OpportunityAnalysisService,
    _detect_awarded_contract_limit_risks,
)


def _make_project(
    *,
    category: str | None = "construction",
    budget_estimate: float = 1_500_000_000.0,
    title: str = "도로 확장 공사",
    description: str = "",
    requirements: str = "",
) -> Project:
    """Build an in-memory Project for limit-risk unit tests (no DB needed)."""
    return Project(
        title=title,
        description=description,
        requirements=requirements,
        category=category,
        budget_estimate=budget_estimate,
    )


def _make_profile(*, awarded_contract_limit: float = 0.0) -> CompanyProfile:
    """Build an in-memory CompanyProfile for limit-risk unit tests (no DB needed)."""
    return CompanyProfile(awarded_contract_limit=awarded_contract_limit)


# ---- _detect_awarded_contract_limit_risks: positive case ----------------------------------------


def test_construction_with_budget_exceeding_limit_emits_risk_reason():
    """Construction notice + positive limit + budget > limit → risk reason with both numbers."""
    project = _make_project(budget_estimate=1_500_000_000.0)  # 15억
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)  # 10억

    reasons = _detect_awarded_contract_limit_risks(project, profile)

    assert len(reasons) == 1
    reason = reasons[0]
    # 천단위 구분자로 두 숫자 모두 포함되어야 함
    assert "1,500,000,000" in reason
    assert "1,000,000,000" in reason
    assert "도급한도" in reason
    assert "초과" in reason


# ---- gates that suppress the risk ---------------------------------------------------------------


def test_construction_with_budget_within_limit_does_not_fire():
    """Budget ≤ limit → no risk reason."""
    project = _make_project(budget_estimate=800_000_000.0)  # 8억
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)  # 10억

    assert _detect_awarded_contract_limit_risks(project, profile) == []


def test_construction_with_zero_limit_treated_as_not_provided_does_not_fire():
    """awarded_contract_limit == 0 means 'not provided' → no risk reason."""
    project = _make_project(budget_estimate=1_500_000_000.0)
    profile = _make_profile(awarded_contract_limit=0.0)

    assert _detect_awarded_contract_limit_risks(project, profile) == []


def test_non_construction_with_budget_exceeding_limit_does_not_fire():
    """Construction guard: software notice must not raise the construction-specific limit risk."""
    project = _make_project(
        category="software",
        budget_estimate=2_000_000_000.0,  # 20억
        title="AI 분석 플랫폼 구축",
    )
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)  # 10억

    assert _detect_awarded_contract_limit_risks(project, profile) == []


def test_profile_none_does_not_fire_and_does_not_crash():
    """A missing profile must short-circuit cleanly with no crash and no reason."""
    project = _make_project(budget_estimate=1_500_000_000.0)

    assert _detect_awarded_contract_limit_risks(project, None) == []


# ---- Wiring through _build_risk_flags ----------------------------------------------------------


def test_build_risk_flags_appends_awarded_limit_risk_when_exceeded():
    """_build_risk_flags should append the awarded-limit reason when the budget exceeds the limit."""
    service = OpportunityAnalysisService()
    project = _make_project(
        title="시청 청사 증축 공사",
        budget_estimate=1_500_000_000.0,
    )
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)

    risks = service._build_risk_flags(
        classification={"matched": True, "score": 0.8},
        price_prediction={"confidence_score": 0.9},
        similar_projects={"results": [{"similarity_score": 0.7}]},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=72,
        expected_margin_score=0.6,
        execution_complexity_score=0.4,
        project=project,
        profile=profile,
    )

    assert any("도급한도" in flag and "초과" in flag for flag in risks)


def test_build_risk_flags_preserves_existing_risks_alongside_awarded_limit_risk():
    """Legacy risk flags must still surface when the awarded-limit signal also fires."""
    service = OpportunityAnalysisService()
    project = _make_project(
        title="댐 정비 공사",
        budget_estimate=1_500_000_000.0,
    )
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)

    risks = service._build_risk_flags(
        classification={"matched": False, "score": 0.3},  # legacy mismatch risk
        price_prediction={"confidence_score": 0.5},  # legacy low-confidence risk
        similar_projects={"results": []},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=72,
        expected_margin_score=0.6,
        execution_complexity_score=0.4,
        project=project,
        profile=profile,
    )

    # Legacy reasons still present.
    assert any("자격·지역·면허" in flag for flag in risks)
    assert any("가격 예측 신뢰도" in flag for flag in risks)
    # New awarded-limit reason appended.
    assert any("도급한도" in flag and "초과" in flag for flag in risks)


def test_build_risk_flags_does_not_fire_awarded_limit_for_software_project():
    """Construction guard end-to-end: software notice does not trigger the awarded-limit risk."""
    service = OpportunityAnalysisService()
    project = _make_project(
        category="software",
        title="AI 분석 플랫폼 구축",
        budget_estimate=2_000_000_000.0,
    )
    profile = _make_profile(awarded_contract_limit=1_000_000_000.0)

    risks = service._build_risk_flags(
        classification={"matched": True, "score": 0.8},
        price_prediction={"confidence_score": 0.9},
        similar_projects={"results": [{"similarity_score": 0.7}]},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=72,
        expected_margin_score=0.6,
        execution_complexity_score=0.4,
        project=project,
        profile=profile,
    )

    assert not any("도급한도" in flag for flag in risks)


def test_build_risk_flags_omits_awarded_limit_when_profile_not_provided():
    """When profile is omitted (default None), the awarded-limit risk must not fire."""
    service = OpportunityAnalysisService()
    project = _make_project(
        title="시청 청사 증축 공사",
        budget_estimate=1_500_000_000.0,
    )

    risks = service._build_risk_flags(
        classification={"matched": True, "score": 0.8},
        price_prediction={"confidence_score": 0.9},
        similar_projects={"results": [{"similarity_score": 0.7}]},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=72,
        expected_margin_score=0.6,
        execution_complexity_score=0.4,
        project=project,
        # profile intentionally omitted (default None)
    )

    assert not any("도급한도" in flag for flag in risks)
