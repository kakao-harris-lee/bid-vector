"""지역 (region) signal detection and the region scoring axis.

Holds the construction/지역가산점 detectors that are re-exported from
``app.services.classifier`` (and imported by ``opportunity_analysis``) plus the
region axis itself. ``self.REGION_*`` weights become ``config`` constants and
``self._*`` region helpers become the module functions below; behaviour is
unchanged.
"""

import re

from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.assessment import RuleAssessment
from app.services.classification.text import (
    collect_project_text,
    extract_project_regions,
    extract_regions,
)

# Single source of truth for the 지역가산점/소재지 가산/본사 가산 signal. Both the
# classifier region-score boost (``assess_region``) and the opportunity_analysis
# region_bonus risk flag reuse THIS compiled pattern so the score-boost and the
# risk-flag can never disagree on what "지역가산점" means (PR #98 lesson — one
# definition). Detection is a regex heuristic over notice text and is
# construction-gated by callers; it is a matching signal, NOT a substitute for an
# actual 적격심사 가산점 계산.
REGION_PREFERENCE_PATTERN = re.compile(r"지역\s*가산(?:점)?|소재지\s*가산|본사\s*가산")

# 매치 직후 짧은 윈도우 내 부정어가 있으면 신호로 잡지 않음
# (예: "지역 가산점 없음", "소재지 가산 미적용" → 우대 아님). Mirrors
# opportunity_analysis._NEGATION_NEAR_MATCH so both modules negate identically.
_REGION_PREFERENCE_NEGATION = re.compile(r"\s*(?:없|미적용|불요|무관|면제|미요구|미해당)")


def is_construction_project(project: Project) -> bool:
    """Shared 건설/공사 detector used by both the 도급한도 budget gate and the
    construction risk heuristics (``opportunity_analysis``).

    Single source of truth so the budget axis and the risk-flag never disagree
    on ambiguous categories (e.g. "건설사업관리", "설계·감리"): the raw category is
    lowercased/stripped and treated as construction when it equals or contains
    one of ``construction``/``공사``/``건설``. An empty category returns False.
    """
    raw_category = (getattr(project, "category", None) or "").strip().lower()
    if not raw_category:
        return False
    # Direct hits + common Korean aliases used across the codebase.
    if raw_category in {"construction", "공사", "건설"}:
        return True
    # Tolerant prefix/suffix forms (e.g. "construction-civil", "건축공사", "토목공사업").
    if "construction" in raw_category:
        return True
    if "공사" in raw_category or "건설" in raw_category:
        return True
    return False


def detect_regional_preference(text: str) -> bool:
    """Return True if ``text`` advertises a 지역가산점/지역우대 condition.

    Hits ``REGION_PREFERENCE_PATTERN`` AND is not immediately negated — the ~12
    characters following the match are checked for a negation word (없/미적용/불요/
    무관/면제/미요구/미해당), matching opportunity_analysis's near-match negation
    guard so the shared signal cannot drift between the two modules.
    """
    if not text:
        return False
    normalized = str(text).lower()
    hit = REGION_PREFERENCE_PATTERN.search(normalized)
    if hit is None:
        return False
    tail = normalized[hit.end():hit.end() + 12]
    if _REGION_PREFERENCE_NEGATION.match(tail):
        return False
    return True


def region_match_context(
    project: Project, profile: CompanyProfile
) -> tuple[set[str], bool, set[str], set[str], bool]:
    """Derive the region-scoring context ONCE so the boost decision
    (``region_preference_boost_applies``) and the base scoring
    (``assess_region``) never re-derive regions independently.

    Returns ``(project_regions, has_strict_region_limit, profile_regions,
    overlap, is_neutral_notice)`` where ``is_neutral_notice`` mirrors the
    ``not project_regions or "전국" in project_regions`` short-circuit that
    grants NO positive region-match (and therefore NO boost).
    """
    project_regions, has_strict_region_limit = extract_project_regions(project)
    profile_regions = extract_regions(profile.region_codes)
    overlap = project_regions & profile_regions
    is_neutral_notice = not project_regions or "전국" in project_regions
    return (
        project_regions,
        has_strict_region_limit,
        profile_regions,
        overlap,
        is_neutral_notice,
    )


def region_preference_boost_applies(
    project: Project, profile: CompanyProfile | None
) -> bool:
    """Single source of truth for "does the 지역가산점/지역우대 boost apply to
    this (project, profile)?"

    BOTH the classifier region-score boost (``assess_region``) and the
    opportunity_analysis region_bonus risk-suppression call THIS function, so
    the boost and the risk-suppression can never disagree (PR #98/#101
    pattern-consolidation lesson — one definition).

    Returns True iff ALL of:
      1. the notice is a construction project (``is_construction_project``);
      2. the notice text advertises 지역가산점/지역우대 (negation-guarded
         ``detect_regional_preference``);
      3. the operator earns a positive region MATCH under the SAME rules
         ``assess_region`` uses — i.e. the notice has a specific region
         restriction (NOT the 전국/neutral short-circuit) AND the operator is
         region-eligible (``project_regions & profile_regions`` non-empty OR
         ``"전국" in profile_regions``, matching the strict-match branch that
         already treats a 전국 profile as eligible).

    This is a regex-heuristic matching signal over notice text, NOT a
    substitute for an actual 적격심사 가산점 계산.
    """
    if profile is None:
        return False
    if not is_construction_project(project):
        return False
    if not detect_regional_preference(collect_project_text(project)):
        return False

    (
        _project_regions,
        _has_strict_region_limit,
        profile_regions,
        overlap,
        is_neutral_notice,
    ) = region_match_context(project, profile)

    if is_neutral_notice:
        return False
    return bool(overlap) or "전국" in profile_regions


def boosted_region_assessment(
    base_score: float, matched_regions: set[str]
) -> RuleAssessment:
    """Build the boosted region assessment (advisory 0.12→0.20,
    strict/전국-match 0.2→0.28), capped at
    ``REGION_MATCH_SCORE + REGION_PREFERENCE_BONUS`` (= 0.28).

    Only called when ``region_preference_boost_applies`` returned True, so
    the boost and the risk-suppression stay byte-for-byte symmetric.
    """
    cap = config.REGION_MATCH_SCORE + config.REGION_PREFERENCE_BONUS
    boosted_score = min(cap, base_score + config.REGION_PREFERENCE_BONUS)
    if matched_regions:
        reason = (
            "공고에 지역가산점/지역우대 조건이 있고 업체 수행지역"
            f"({', '.join(sorted(matched_regions))})이 일치해 지역우대 가점을 반영했습니다."
        )
    else:
        reason = (
            "공고에 지역가산점/지역우대 조건이 있고 업체가 전국 대응 가능으로 "
            "등록되어 있어 지역우대 가점을 반영했습니다."
        )
    return RuleAssessment(score=boosted_score, passed=True, reasons=[reason])


def assess_region(project: Project, profile: CompanyProfile) -> RuleAssessment:
    """Evaluate whether the company can serve the project's regions."""
    (
        project_regions,
        has_strict_region_limit,
        profile_regions,
        overlap,
        is_neutral_notice,
    ) = region_match_context(project, profile)

    if is_neutral_notice:
        return RuleAssessment(
            score=config.REGION_NEUTRAL_SCORE,
            passed=True,
            reasons=["공고에 지역 제한이 명확하지 않거나 전국 대상으로 보여 지역 조건은 중립 처리했습니다."],
        )

    boost_applies = region_preference_boost_applies(project, profile)

    if not has_strict_region_limit:
        if "전국" in profile_regions or overlap:
            if boost_applies:
                return boosted_region_assessment(config.REGION_ADVISORY_SCORE, overlap)
            return RuleAssessment(
                score=config.REGION_ADVISORY_SCORE,
                passed=True,
                reasons=[
                    f"공고에 언급된 지역({', '.join(sorted(project_regions))})과 업체 수행지역이 겹쳐 참고 가점을 반영했습니다."
                ],
            )

        return RuleAssessment(
            score=config.REGION_NEUTRAL_SCORE,
            passed=True,
            reasons=["공고에 지역 언급은 있으나 명시적 제한으로 보지 않아 지역 불일치로 감점하지 않았습니다."],
        )

    if "전국" in profile_regions:
        if boost_applies:
            return boosted_region_assessment(config.REGION_MATCH_SCORE, overlap)
        return RuleAssessment(
            score=config.REGION_MATCH_SCORE,
            passed=True,
            reasons=[f"업체가 전국 대응 가능으로 등록되어 있어 제한지역({', '.join(sorted(project_regions))})을 충족합니다."],
        )

    if not profile_regions:
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=config.REGION_MISMATCH_PENALTY,
            reasons=[f"공고 제한지역은 {', '.join(sorted(project_regions))}인데 업체 수행 지역 정보가 등록되어 있지 않습니다."],
        )

    if overlap:
        if boost_applies:
            return boosted_region_assessment(config.REGION_MATCH_SCORE, overlap)
        return RuleAssessment(
            score=config.REGION_MATCH_SCORE,
            passed=True,
            reasons=[f"수행 가능 지역이 공고 제한지역과 일치합니다: {', '.join(sorted(overlap))}."],
        )

    return RuleAssessment(
        score=0.0,
        passed=False,
        penalty=config.REGION_MISMATCH_PENALTY,
        reasons=[
            f"공고 제한지역({', '.join(sorted(project_regions))})과 업체 수행지역({', '.join(sorted(profile_regions))})이 일치하지 않습니다."
        ],
    )
