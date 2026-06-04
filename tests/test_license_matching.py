"""License-axis matching tests for the rule-based NoticeClassifierService.

Covers construction-license aliases added so that a "공사 업체" profile whose
``license_codes`` hold Korean construction-license names (건축공사업, 토목공사업,
…) match notices that require the same license. The front-end stores these exact
strings via ``frontend/src/features/profile/constants.ts``; the canonical-code
mapping is:

    ARC001     ← 건축공사업
    CIV001     ← 토목공사업
    CIVARC001  ← 토목건축공사업
    LND001     ← 조경공사업
    ENV001     ← 산업환경설비공사업 / 산업·환경설비공사업 / 산업·환경설비
    INT001     ← 실내건축공사업
    MEC001     ← 기계설비공사업
    GAS001     ← 가스시설공사업

``_assess_license`` needs no DB session, so these are plain unit tests built from
in-memory ``Project`` / ``CompanyProfile`` instances.
"""

from __future__ import annotations

import pytest

from app.models.models import CompanyProfile, Project
from app.services.classifier import NoticeClassifierService


def _project(requirements: str) -> Project:
    return Project(
        title="공사 발주",
        description="",
        requirements=requirements,
        budget_estimate=120000000.0,
        category="construction",
    )


def _profile(license_codes: str) -> CompanyProfile:
    return CompanyProfile(
        business_type="construction",
        license_codes=license_codes,
        region_codes="전국",
        annual_revenue=500000000.0,
        capacity_score=0.8,
        total_awards=4,
    )


def _assess(requirements: str, license_codes: str):
    service = NoticeClassifierService()
    return service._assess_license(_project(requirements), _profile(license_codes))


# ---------------------------------------------------------------------------
# Token extraction — canonical-code mapping for the new construction aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("건축공사업 면허 필요", {"ARC001"}),
        ("토목공사업 면허 필요", {"CIV001"}),
        # nested names extract the substring code too (documented behaviour)
        ("토목건축공사업 면허 필요", {"ARC001", "CIVARC001"}),
        ("조경공사업 면허 필요", {"LND001"}),
        ("산업환경설비공사업 면허 필요", {"ENV001"}),
        ("산업·환경설비공사업 면허 필요", {"ENV001"}),
        ("실내건축공사업 면허 필요", {"ARC001", "INT001"}),
        ("기계설비공사업 면허 필요", {"MEC001"}),
        ("가스시설공사업 면허 필요", {"GAS001"}),
    ],
)
def test_extract_construction_license_tokens(text, expected):
    service = NoticeClassifierService()
    assert service._extract_license_tokens(text, require_context=True) == expected


def test_bare_generic_terms_do_not_match_construction_licenses():
    """Bare '건축'/'토목'/'기계'/'가스'/'조경' must NOT be aliases (over-match guard)."""
    service = NoticeClassifierService()
    for bare in ("건축 면허", "토목 면허", "기계 면허", "가스 면허", "조경 면허"):
        assert service._extract_license_tokens(bare, require_context=True) == set()


# ---------------------------------------------------------------------------
# Positive: construction profile holds the required construction license
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "license_name",
    ["건축공사업", "토목공사업", "기계설비공사업", "가스시설공사업"],
)
def test_construction_license_matches_when_held(license_name):
    result = _assess(f"{license_name} 면허 필요", license_name)
    assert result.passed is True
    assert result.penalty == 0.0
    assert any("모두 보유" in reason for reason in result.reasons)


def test_construction_license_matches_with_dot_variant_alias():
    """ENV001 should match across the '산업·환경설비공사업' / '산업환경설비공사업' forms."""
    result = _assess("산업·환경설비공사업 면허 필요", "산업환경설비공사업")
    assert result.passed is True
    assert result.penalty == 0.0


# ---------------------------------------------------------------------------
# Negative: profile lacks the required construction license
# ---------------------------------------------------------------------------


def test_construction_requirement_mismatches_software_only_profile():
    result = _assess("건축공사업 면허 필요", "SW001")
    assert result.passed is False
    assert result.penalty == NoticeClassifierService.LICENSE_MISMATCH_PENALTY
    assert any("ARC001" in reason for reason in result.reasons)


def test_construction_requirement_mismatches_empty_profile():
    result = _assess("토목공사업 면허 필요", "")
    assert result.passed is False
    assert result.penalty == NoticeClassifierService.LICENSE_MISMATCH_PENALTY


# ---------------------------------------------------------------------------
# Nesting: substring overlap of construction-license names
# ---------------------------------------------------------------------------


def test_nesting_pure_arc_profile_misses_interior_requirement():
    """A業체 holding only '건축공사업' must NOT satisfy an '실내건축공사업' notice.

    '실내건축공사업' contains '건축공사업', so the notice extracts {ARC001, INT001}
    while the profile only holds {ARC001}; INT001 is correctly reported missing.
    """
    result = _assess("실내건축공사업 면허 필요", "건축공사업")
    assert result.passed is False
    assert result.penalty == NoticeClassifierService.LICENSE_MISMATCH_PENALTY
    assert any("INT001" in reason for reason in result.reasons)


def test_nesting_symmetric_long_name_matches_on_both_sides():
    """When notice and profile use the same long name, the superset is symmetric.

    Both sides extract {ARC001, INT001}, so the match succeeds even though the
    shorter ARC001 was extracted by substring nesting.
    """
    result = _assess("실내건축공사업 면허 필요", "실내건축공사업")
    assert result.passed is True
    assert result.penalty == 0.0


def test_nesting_civil_architecture_symmetric_match():
    result = _assess("토목건축공사업 면허 필요", "토목건축공사업")
    assert result.passed is True
    assert result.penalty == 0.0


# ---------------------------------------------------------------------------
# Regression: the original six aliases keep matching unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "license_name, canonical",
    [
        ("전기공사업", "ELE001"),
        ("정보통신공사업", "NET001"),
        ("소방시설", "FIRE001"),
        ("엔지니어링", "ENG001"),
        ("소프트웨어사업자", "SW001"),
        ("정보보호전문서비스", "SEC001"),
    ],
)
def test_legacy_license_aliases_still_match(license_name, canonical):
    service = NoticeClassifierService()
    assert service._extract_license_tokens(f"{license_name} 면허 필요", require_context=True) == {
        canonical
    }
    result = _assess(f"{license_name} 면허 필요", license_name)
    assert result.passed is True
    assert result.penalty == 0.0


def test_legacy_software_requirement_mismatch_regression():
    """SW001 notice vs construction-only profile still mismatches (unchanged)."""
    result = _assess("필수 면허: SW001, NET001 보유 업체만 참여 가능", "건축공사업")
    assert result.passed is False
    assert any("SW001" in reason or "NET001" in reason for reason in result.reasons)
