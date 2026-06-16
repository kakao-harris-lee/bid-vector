"""Unit tests for construction-specific risk signal detection on opportunity analysis.

These exercise the pure ``_detect_construction_risk_reasons`` helper (and the
``OpportunityAnalysisService._build_risk_flags`` wiring around it) without
spinning up the full classifier / predictor pipeline. The helper is a
keyword/regex pass over the project's title + description + requirements,
gated by the project's category.
"""

from __future__ import annotations

from app.models.models import CompanyProfile, Project
from app.services.opportunity_analysis import (
    OpportunityAnalysisService,
    _REGION_BONUS_RISK_MESSAGE,
    _detect_construction_risk_reasons,
    _is_construction_project,
)


# Reason text constants (kept in sync with _CONSTRUCTION_RISK_PATTERNS).
JV_REASON = "공동도급/공동수급체 구성이 요구됩니다. 파트너 확보·지분 협상이 필요합니다."
REGION_JV_REASON = "지역의무공동도급 또는 지역 제한 요건이 명시돼 있어 해당 지역 파트너/소재가 필요합니다."
SIMILAR_REASON = "유사 실적/시공실적 요건이 명시돼 있어 운영자 실적 증빙이 필요합니다."
# Bind to the production constant so a future message change cannot leave these
# tests green against stale literal text.
REGION_BONUS_REASON = _REGION_BONUS_RISK_MESSAGE


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


# ---- negation context guard (false-positive defense) -------------------------------------------


def test_region_restriction_with_no_negation_word_does_fire():
    """Sanity baseline: bare '지역 제한' (no negation) should still fire."""
    project = _make_project(description="지역 제한 요건이 있는 공사 공고입니다.")
    assert REGION_JV_REASON in _detect_construction_risk_reasons(project)


def test_region_restriction_followed_by_negation_does_not_fire():
    """'지역 제한 없음' / '지역제한 미적용' 부정 문맥은 위험으로 잡지 않는다."""
    for text in ["지역 제한 없음", "지역제한 없음", "지역 제한 미적용", "지역제한 무관"]:
        project = _make_project(description=f"전국 입찰 — {text}.")
        assert REGION_JV_REASON not in _detect_construction_risk_reasons(project), text


def test_similar_experience_with_negation_does_not_fire():
    """'유사실적 불요' / '시공실적 미요구' 부정 문맥은 위험으로 잡지 않는다."""
    for text in ["유사실적 불요", "유사 실적 없음", "시공실적 미요구", "납품실적 면제"]:
        project = _make_project(description=f"공사 공고. {text}.")
        assert SIMILAR_REASON not in _detect_construction_risk_reasons(project), text


def test_joint_venture_with_negation_does_not_fire():
    project = _make_project(description="공동도급 미해당, 단독 입찰 가능.")
    assert JV_REASON not in _detect_construction_risk_reasons(project)


def test_region_bonus_with_negation_does_not_fire():
    project = _make_project(description="지역 가산점 없음, 전국 동일 기준.")
    assert REGION_BONUS_REASON not in _detect_construction_risk_reasons(project)


# ---- region_bonus suppression for in-region operators ----------------------------------------
#
# 지역가산점 + 업체 수행지역 일치 = 우대(가점)이지 리스크가 아니므로, in-region
# 운영자에게는 region_bonus 리스크를 숨기고, out-of-region 운영자에게는 유지한다.


def _construction_profile(region_codes: str) -> CompanyProfile:
    return CompanyProfile(
        business_type="construction",
        license_codes="건축공사업",
        region_codes=region_codes,
        annual_revenue=500_000_000.0,
        capacity_score=0.8,
        total_awards=4,
    )


def _region_bonus_project() -> Project:
    """Construction notice in 경기 that advertises 지역 가산점 (strict region limit)."""
    return _make_project(
        title="경기 도로 정비 공사",
        description="해당 지역 소재 업체만 참여 가능한 경기 지역 도로 공사",
        requirements="경기도 업체 대상, 지역 가산점 부여",
    )


def test_detect_construction_risk_reasons_excludes_region_bonus_when_flagged():
    """The exclude_region_bonus flag drops ONLY the region_bonus reason."""
    project = _region_bonus_project()
    # Default: region_bonus present.
    assert REGION_BONUS_REASON in _detect_construction_risk_reasons(project)
    # Excluded: region_bonus gone, other reasons (region_jv from 지역 제한) remain.
    excluded = _detect_construction_risk_reasons(project, exclude_region_bonus=True)
    assert REGION_BONUS_REASON not in excluded
    assert REGION_JV_REASON in excluded


def test_build_risk_flags_suppresses_region_bonus_for_in_region_operator():
    """In-region operator (경기 ↔ 경기 notice with 지역 가산점) → region_bonus risk suppressed."""
    service = OpportunityAnalysisService()
    project = _region_bonus_project()
    profile = _construction_profile("경기")  # matches the notice region
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
    assert REGION_BONUS_REASON not in risks
    # The genuine region-restriction risk still surfaces.
    assert REGION_JV_REASON in risks


def test_build_risk_flags_keeps_region_bonus_for_out_of_region_operator():
    """Out-of-region operator (부산 vs 경기 notice) → region_bonus risk STILL shown."""
    service = OpportunityAnalysisService()
    project = _region_bonus_project()
    profile = _construction_profile("부산")  # does NOT match the notice region
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
    assert REGION_BONUS_REASON in risks


def test_build_risk_flags_keeps_region_bonus_when_no_profile():
    """No profile → cannot establish in-region → region_bonus risk preserved (default path)."""
    service = OpportunityAnalysisService()
    project = _region_bonus_project()
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
        profile=None,
    )
    assert REGION_BONUS_REASON in risks


# ---- boost ⇔ risk-suppression symmetry (regression for the two asymmetries) -------------------
#
# The region-preference boost (classifier._assess_region) and the region_bonus
# risk-suppression (here) MUST agree: an operator either benefits (boost applied
# AND risk suppressed) or does not (no boost AND risk shown). Both surfaces now
# call the SAME classifier.region_preference_boost_applies, so they cannot drift.


def _nationwide_notice_with_local_bonus() -> Project:
    """전국 notice ('전국 어디서나 가능하나 충북 지역 가산점 적용') — neutral region
    short-circuit, so NO positive region match and NO boost despite literal 충북
    overlap. Asymmetry #1."""
    return _make_project(
        title="전국 대상 도로 정비 공사",
        description="전국 어디서나 참여 가능하나 충북 지역 가산점 적용",
        requirements="전국 업체 참여 가능",
    )


def _strict_local_bonus_notice() -> Project:
    """Strict 충북 지역가산점 notice (specific region restriction)."""
    return _make_project(
        title="충북 도로 정비 공사",
        description="해당 지역 소재 업체만 참여 가능한 충북 지역 도로 공사",
        requirements="충북 업체 대상, 지역 가산점 부여",
    )


def _region_bonus_risk_shown(project: Project, profile: CompanyProfile | None) -> bool:
    """Whether the region_bonus risk reason actually surfaces in _build_risk_flags."""
    service = OpportunityAnalysisService()
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
    return REGION_BONUS_REASON in risks


def _suppression_decision(project: Project, profile: CompanyProfile | None) -> bool:
    """The region_bonus risk-suppression DECISION fed into _build_risk_flags —
    i.e. the boolean the in-region check passes as exclude_region_bonus. This is
    the shared classifier method, so it always equals the classifier boost
    decision. (Distinct from whether the risk text actually fires, which also
    depends on the notice text matching/negation.)"""
    return OpportunityAnalysisService()._operator_is_in_region_with_preference(
        project, profile
    )


def test_asymmetry_1_nationwide_notice_local_profile_no_boost_risk_shown():
    """#1: 전국 notice + 충북 가산점, profile=충북 → boost does NOT apply (neutral
    short-circuit) AND the region_bonus risk is STILL shown (not suppressed)."""
    from app.services.classifier import NoticeClassifierService

    project = _nationwide_notice_with_local_bonus()
    profile = _construction_profile("충북")
    classifier = NoticeClassifierService()

    assert classifier.region_preference_boost_applies(project, profile) is False
    # No boost: neutral region (전국) keeps the neutral score, no 지역우대 reason.
    assessment = classifier._assess_region(project, profile)
    assert assessment.score == NoticeClassifierService.REGION_NEUTRAL_SCORE  # 0.05
    assert not any("지역우대 가점" in r for r in assessment.reasons)
    # Suppression decision False (not in-region for boost purposes) AND the
    # region_bonus risk is STILL shown — symmetric with no boost.
    assert _suppression_decision(project, profile) is False
    assert _region_bonus_risk_shown(project, profile) is True


def test_asymmetry_2_strict_local_notice_nationwide_profile_boost_and_suppressed():
    """#2: strict 충북 지역가산점 notice, profile=전국 → boost applies (0.28) AND the
    region_bonus risk is suppressed (전국 profile is region-eligible)."""
    from app.services.classifier import NoticeClassifierService

    project = _strict_local_bonus_notice()
    profile = _construction_profile("전국")
    classifier = NoticeClassifierService()

    assert classifier.region_preference_boost_applies(project, profile) is True
    assessment = classifier._assess_region(project, profile)
    cap = (
        NoticeClassifierService.REGION_MATCH_SCORE
        + NoticeClassifierService.REGION_PREFERENCE_BONUS
    )  # 0.28
    assert assessment.score == cap
    assert any("지역우대 가점" in r for r in assessment.reasons)
    # Suppression decision True AND the region_bonus risk is hidden — symmetric
    # with boost applied.
    assert _suppression_decision(project, profile) is True
    assert _region_bonus_risk_shown(project, profile) is False


def test_boost_and_suppression_are_always_symmetric():
    """Parametrized invariant covering literal-overlap, out-of-region,
    전국-notice, 전국-profile, negated, and non-construction inputs.

    (a) The classifier boost decision and the opportunity_analysis suppression
        decision are ALWAYS the same boolean — they call the SAME method, so they
        cannot drift.
    (b) boost-applied ⟺ region_bonus risk hidden: the risk surfaces iff it would
        fire on the notice text (``_detect_construction_risk_reasons`` with no
        suppression) AND the boost does NOT apply. This pins "an operator that
        benefits never sees the risk, and an operator that does not benefit always
        sees it (when present)" across both surfaces."""
    from app.services.classifier import NoticeClassifierService

    classifier = NoticeClassifierService()

    strict = _strict_local_bonus_notice()
    advisory = _make_project(
        title="충북 도로 정비 공사",
        description="충북 지역 도로 정비 공사 — 지역 가산점 부여",
        requirements="충북 지역 우대",
    )
    nationwide_notice = _nationwide_notice_with_local_bonus()
    non_construction = _make_project(
        category="software",
        title="공공기관 데이터 플랫폼 구축",
        description="해당 지역 소재 업체만 참여 가능한 충북 지역 사업, 지역 가산점 부여",
        requirements="충북 업체 대상",
    )
    negated = _make_project(
        title="충북 도로 정비 공사",
        description="해당 지역 소재 업체만 참여 가능한 충북 지역 도로 공사",
        requirements="충북 업체 대상, 지역 가산점 없음",
    )

    local = _construction_profile("충북")
    nationwide_profile = _construction_profile("전국")
    out_of_region = _construction_profile("부산")

    cases: list[tuple[str, Project, CompanyProfile | None]] = [
        ("strict-literal-overlap", strict, local),
        ("strict-out-of-region", strict, out_of_region),
        ("strict-nationwide-profile", strict, nationwide_profile),
        ("advisory-literal-overlap", advisory, local),
        ("nationwide-notice-local", nationwide_notice, local),
        ("nationwide-notice-nationwide-profile", nationwide_notice, nationwide_profile),
        ("negated-overlap", negated, local),
        ("non-construction-overlap", non_construction, local),
        ("no-profile", strict, None),
    ]

    for label, project, profile in cases:
        boost = classifier.region_preference_boost_applies(project, profile)
        suppressed = _suppression_decision(project, profile)
        # (a) both surfaces share the SAME decision.
        assert boost == suppressed, f"{label}: boost={boost} suppressed={suppressed}"

        # (b) boost-applied ⟺ region_bonus risk hidden (given it would fire).
        would_fire = REGION_BONUS_REASON in _detect_construction_risk_reasons(project)
        risk_shown = _region_bonus_risk_shown(project, profile)
        assert risk_shown == (would_fire and not boost), (
            f"{label}: risk_shown={risk_shown} would_fire={would_fire} boost={boost}"
        )
