"""Unit tests for construction-specific risk signal detection on opportunity analysis.

These exercise the pure ``_detect_construction_risk_reasons`` helper (and the
``OpportunityAnalysisService._build_risk_flags`` wiring around it) without
spinning up the full classifier / predictor pipeline. The helper is a
keyword/regex pass over the project's title + description + requirements,
gated by the project's category.
"""

from __future__ import annotations

from app.models.models import Project
from app.services.opportunity_analysis import (
    OpportunityAnalysisService,
    _detect_construction_risk_reasons,
    _is_construction_project,
)


# Reason text constants (kept in sync with _CONSTRUCTION_RISK_PATTERNS).
JV_REASON = "공동도급/공동수급체 구성이 요구됩니다. 파트너 확보·지분 협상이 필요합니다."
REGION_JV_REASON = "지역의무공동도급 또는 지역 제한 요건이 명시돼 있어 해당 지역 파트너/소재가 필요합니다."
SIMILAR_REASON = "유사 실적/시공실적 요건이 명시돼 있어 운영자 실적 증빙이 필요합니다."
REGION_BONUS_REASON = "지역 가산점/소재지 가산 조건이 있어 비지역 업체는 점수 불리할 수 있습니다."


def _make_project(
    *,
    category: str | None = "construction",
    title: str = "",
    description: str = "",
    requirements: str = "",
) -> Project:
    """Build an in-memory Project for risk-flag unit tests (no DB needed)."""
    return Project(
        title=title,
        description=description,
        requirements=requirements,
        category=category,
        budget_estimate=100_000_000.0,
    )


# ---- _is_construction_project guard -----------------------------------------------------------


def test_is_construction_project_accepts_canonical_and_korean_aliases():
    assert _is_construction_project(_make_project(category="construction")) is True
    assert _is_construction_project(_make_project(category="공사")) is True
    assert _is_construction_project(_make_project(category="건설")) is True
    assert _is_construction_project(_make_project(category="건축공사")) is True


def test_is_construction_project_rejects_non_construction_categories():
    assert _is_construction_project(_make_project(category="software")) is False
    assert _is_construction_project(_make_project(category="goods")) is False
    assert _is_construction_project(_make_project(category=None)) is False
    assert _is_construction_project(_make_project(category="")) is False


# ---- Joint venture (공동도급) ------------------------------------------------------------------


def test_construction_text_with_joint_venture_keyword_emits_joint_venture_reason():
    project = _make_project(
        title="○○지구 도로 확장 공사",
        description="공동도급으로 추진하는 토목 공사",
        requirements="건축공사업 면허 보유",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert JV_REASON in reasons
    assert REGION_JV_REASON not in reasons


def test_construction_text_with_consortium_keyword_emits_joint_venture_reason():
    project = _make_project(
        title="공공기관 청사 신축 공사",
        description="컨소시엄 구성 가능 업체 참여",
        requirements="시공 능력 평가액 기준",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert JV_REASON in reasons


# ---- 지역의무공동도급 / 지역제한 -------------------------------------------------------------


def test_construction_text_with_region_mandated_joint_venture_emits_region_reason():
    """When 지역의무공동도급 appears, the region-specific reason must be in the list.

    The bare ``공동도급`` substring is also present inside ``지역의무공동도급``, so
    the joint_venture pattern naturally fires too — the spec calls this out
    ("공동도급 키워드도 함께 있으면 두 사유 모두"). Both reasons are acceptable;
    only the region reason is mandatory to assert here.
    """
    project = _make_project(
        title="지방 도로 정비 공사",
        description="지역의무공동도급으로 진행되는 사업",
        requirements="해당 지역 소재 업체 참여 의무",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert REGION_JV_REASON in reasons


def test_construction_text_with_explicit_region_restriction_emits_region_reason():
    project = _make_project(
        title="군청 청사 보수 공사",
        description="지역 제한 입찰로 진행되는 보수 공사",
        requirements="해당 지역 소재 업체만 참여 가능",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert REGION_JV_REASON in reasons


def test_construction_text_with_both_region_and_separate_joint_venture_emits_both():
    """When BOTH 지역의무공동도급 AND a separate 공동도급 phrase are present, both fire."""
    project = _make_project(
        title="도로 확장 공사",
        description="지역의무공동도급 + 일반 공동도급 양식 병행",
        requirements="시공 능력 평가",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert REGION_JV_REASON in reasons
    assert JV_REASON in reasons


# ---- 유사실적 / 시공실적 ---------------------------------------------------------------------


def test_construction_text_with_similar_experience_requirement_emits_experience_reason():
    project = _make_project(
        title="배수지 정비 공사",
        description="최근 3년 이내 유사 실적 보유 업체",
        requirements="시공실적 증빙 필요",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert SIMILAR_REASON in reasons


def test_construction_text_with_minimum_years_experience_emits_experience_reason():
    project = _make_project(
        title="공원 조경 공사",
        description="최근 5년 실적 30억 원 이상",
        requirements="동종 실적 보유 우대",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert SIMILAR_REASON in reasons


# ---- 지역 가산점 -------------------------------------------------------------------------------


def test_construction_text_with_region_bonus_emits_region_bonus_reason():
    project = _make_project(
        title="도청 청사 리모델링 공사",
        description="지역 가산점 부여 사업",
        requirements="소재지 가산 적용",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert REGION_BONUS_REASON in reasons


# ---- Construction category guard --------------------------------------------------------------


def test_software_notice_with_same_keywords_does_not_fire_construction_signals():
    """Same trigger keywords on a software notice must NOT emit construction reasons.

    Guards against bleed-through where a non-construction notice happens to
    mention 공동도급 / 유사실적 in a SaaS / IT context.
    """
    project = _make_project(
        category="software",
        title="공공기관 데이터 분석 플랫폼 구축",
        description="공동도급 / 유사 실적 / 지역 가산점 / 지역의무공동도급 키워드 모두 포함",
        requirements="SW001 보유 업체",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert reasons == []


def test_construction_notice_without_risk_keywords_emits_no_construction_reasons():
    project = _make_project(
        title="○○지구 도로 보수 공사",
        description="포장 보수 및 정비 공사",
        requirements="건축공사업 면허 보유",
    )
    reasons = _detect_construction_risk_reasons(project)
    assert reasons == []


# ---- Wiring through _build_risk_flags --------------------------------------------------------


def test_build_risk_flags_appends_construction_reasons_for_construction_project():
    """_build_risk_flags should chain construction reasons after the existing ones."""
    service = OpportunityAnalysisService()
    project = _make_project(
        title="시청 청사 증축 공사",
        description="공동도급 구성 의무, 최근 3년 유사 실적 보유",
        requirements="건축공사업 보유",
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
    )
    # No legacy flags expected (all gates passed), only the construction ones.
    assert JV_REASON in risks
    assert SIMILAR_REASON in risks


def test_build_risk_flags_preserves_existing_risks_alongside_construction_reasons():
    """Legacy risk flags must still surface when construction signals also fire."""
    service = OpportunityAnalysisService()
    project = _make_project(
        title="댐 정비 공사",
        description="컨소시엄 구성 가능",
        requirements="시공실적 보유",
    )
    risks = service._build_risk_flags(
        classification={"matched": False, "score": 0.3},  # triggers legacy mismatch risk
        price_prediction={"confidence_score": 0.5},  # triggers legacy low-confidence risk
        similar_projects={"results": []},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=72,
        expected_margin_score=0.6,
        execution_complexity_score=0.4,
        project=project,
    )
    # Legacy reasons still present.
    assert any("자격·지역·면허" in flag for flag in risks)
    assert any("가격 예측 신뢰도" in flag for flag in risks)
    # Construction reasons appended.
    assert JV_REASON in risks
    assert SIMILAR_REASON in risks


def test_build_risk_flags_does_not_fire_construction_signals_for_software_project():
    service = OpportunityAnalysisService()
    project = _make_project(
        category="software",
        title="AI 분석 플랫폼 구축",
        description="공동도급 / 유사 실적 (소프트웨어 도메인) 키워드 포함",
        requirements="SW001 보유 업체",
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
    )
    assert JV_REASON not in risks
    assert SIMILAR_REASON not in risks
    assert REGION_JV_REASON not in risks
    assert REGION_BONUS_REASON not in risks
