"""Region-preference (지역가산점/지역우대) scoring tests for NoticeClassifierService.

These exercise the additive region-score boost in ``_assess_region`` and the
shared ``detect_regional_preference`` / ``REGION_PREFERENCE_PATTERN`` signal.

Domain: when a construction notice advertises 지역가산점/소재지 가산/본사 가산 AND the
operator's region overlaps the notice region, that in-region operator gets a
positive region-score boost (a competitive advantage) — previously it surfaced
ONLY as a risk flag with no positive scoring. Out-of-region operators get NO
boost. Non-construction, negated, and 전국/neutral notices are unaffected.

``_assess_region`` needs no DB session, so these are plain unit tests built from
in-memory ``Project`` / ``CompanyProfile`` instances.
"""

from __future__ import annotations

from app.models.models import CompanyProfile, Project
from app.services.classifier import (
    NoticeClassifierService,
    REGION_PREFERENCE_PATTERN,
    detect_regional_preference,
)
from app.services.opportunity_analysis import _detect_construction_risk_reasons


def _project(
    *,
    category: str | None = "construction",
    title: str = "지방 도로 정비 공사",
    description: str = "",
    requirements: str = "",
) -> Project:
    return Project(
        title=title,
        description=description,
        requirements=requirements,
        category=category,
        budget_estimate=120_000_000.0,
    )


def _profile(region_codes: str) -> CompanyProfile:
    return CompanyProfile(
        business_type="construction",
        license_codes="건축공사업",
        region_codes=region_codes,
        annual_revenue=500_000_000.0,
        capacity_score=0.8,
        total_awards=4,
    )


def _assess(project: Project, profile: CompanyProfile):
    return NoticeClassifierService()._assess_region(project, profile)


# ---- detect_regional_preference (shared signal) ----------------------------------------------


def test_detect_regional_preference_fires_on_each_variant():
    for text in ["지역 가산점 부여", "지역가산점", "소재지 가산 적용", "본사 가산 우대"]:
        assert detect_regional_preference(text) is True, text


def test_detect_regional_preference_negation_suppresses():
    for text in [
        "지역 가산점 없음",
        "소재지 가산 미적용",
        "본사 가산 불요",
        "지역가산점 미해당",
    ]:
        assert detect_regional_preference(text) is False, text


def test_detect_regional_preference_empty_or_unrelated():
    assert detect_regional_preference("") is False
    assert detect_regional_preference("일반 경쟁 입찰, 전국 동일 기준") is False


# ---- 1. boost applied (in-region + construction + preference) ---------------------------------


def test_strict_match_region_preference_boost_applied():
    """Construction + '지역 가산점' + strict region limit + profile region overlap →
    region score boosted above the strict-match base (0.2 → 0.28) with 지역우대 reason."""
    project = _project(
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 도로 공사",
        requirements="경기도 업체 대상, 지역 가산점 부여",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    base = NoticeClassifierService.REGION_MATCH_SCORE  # 0.2
    cap = base + NoticeClassifierService.REGION_PREFERENCE_BONUS  # 0.28
    assert assessment.passed is True
    assert assessment.score > base
    assert assessment.score == cap
    assert any("지역우대 가점" in r for r in assessment.reasons)


def test_advisory_region_preference_boost_applied():
    """Construction + '지역 가산점' + region mention WITHOUT a strict limit + overlap →
    advisory base (0.12) boosted to 0.20 with 지역우대 reason."""
    project = _project(
        description="경기 지역 도로 정비 공사 — 지역 가산점 부여",
        requirements="경기 지역 우대",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    base = NoticeClassifierService.REGION_ADVISORY_SCORE  # 0.12
    expected = base + NoticeClassifierService.REGION_PREFERENCE_BONUS  # 0.20
    assert assessment.passed is True
    assert assessment.score == expected
    assert assessment.score > base
    assert any("지역우대 가점" in r for r in assessment.reasons)


# ---- 3. out-of-region keeps base score, NO boost ---------------------------------------------


def test_out_of_region_operator_gets_no_boost():
    """Construction + '지역 가산점' but profile region does NOT overlap → no boost.

    With a strict limit and no overlap this is a region mismatch (penalty), and
    critically there is NO 지역우대 reason."""
    project = _project(
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 도로 공사",
        requirements="경기도 업체 대상, 지역 가산점 부여",
    )
    profile = _profile("부산")  # out of region
    assessment = _assess(project, profile)
    assert assessment.passed is False
    assert assessment.penalty == NoticeClassifierService.REGION_MISMATCH_PENALTY
    assert assessment.score == 0.0
    assert not any("지역우대 가점" in r for r in assessment.reasons)


# ---- 4. negation → no boost, base score ------------------------------------------------------


def test_negated_preference_gets_no_boost():
    """'지역 가산점 없음' → detect_regional_preference False → strict-match base only."""
    project = _project(
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 도로 공사",
        requirements="경기도 업체 대상, 지역 가산점 없음",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    assert assessment.passed is True
    assert assessment.score == NoticeClassifierService.REGION_MATCH_SCORE  # 0.2, not 0.28
    assert not any("지역우대 가점" in r for r in assessment.reasons)


# ---- 5. no regression --------------------------------------------------------------------------


def test_non_construction_region_match_not_boosted():
    """A software notice with region overlap + '지역 가산점' text keeps the base score
    (construction gate blocks the boost)."""
    project = _project(
        category="software",
        title="공공기관 데이터 플랫폼 구축",
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 사업, 지역 가산점 부여",
        requirements="경기 업체 대상",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    assert assessment.passed is True
    assert assessment.score == NoticeClassifierService.REGION_MATCH_SCORE  # 0.2, not boosted
    assert not any("지역우대 가점" in r for r in assessment.reasons)


def test_construction_region_match_without_preference_text_keeps_base():
    """Construction + strict region match but NO preference text → strict base 0.2."""
    project = _project(
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 도로 공사",
        requirements="경기도 업체 대상",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    assert assessment.score == NoticeClassifierService.REGION_MATCH_SCORE  # 0.2
    assert any("일치합니다" in r for r in assessment.reasons)


def test_advisory_region_match_without_preference_text_keeps_base():
    """Construction + advisory (non-strict) region match but NO preference → 0.12."""
    project = _project(
        description="경기 지역 도로 정비 공사",
        requirements="경기 지역 참고",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    assert assessment.score == NoticeClassifierService.REGION_ADVISORY_SCORE  # 0.12
    assert not any("지역우대 가점" in r for r in assessment.reasons)


def test_neutral_nationwide_notice_unchanged():
    """A 전국 / no-region notice stays neutral (0.05) even with preference text present."""
    project = _project(
        description="전국 대상 도로 정비 공사, 지역 가산점 부여",
        requirements="전국 업체 참여 가능",
    )
    profile = _profile("경기")
    assessment = _assess(project, profile)
    assert assessment.score == NoticeClassifierService.REGION_NEUTRAL_SCORE  # 0.05
    assert not any("지역우대 가점" in r for r in assessment.reasons)


# ---- 6. consistency: classifier signal == opportunity_analysis region_bonus risk -------------


def test_shared_pattern_consistency_between_modules():
    """detect_regional_preference and the opportunity_analysis region_bonus risk fire on
    the SAME example string (single shared compiled pattern)."""
    example = "도청 청사 리모델링 공사. 지역 가산점 부여, 소재지 가산 적용."
    # classifier signal
    assert detect_regional_preference(example) is True
    # opportunity_analysis risk uses the SAME REGION_PREFERENCE_PATTERN
    assert REGION_PREFERENCE_PATTERN.search(example.lower()) is not None
    project = _project(description=example)
    region_bonus_reason = (
        "지역 가산점/소재지 가산 조건이 있어 비지역 업체는 점수 불리할 수 있습니다."
    )
    # Without in-region suppression the region_bonus risk fires for the same text.
    assert region_bonus_reason in _detect_construction_risk_reasons(project)
