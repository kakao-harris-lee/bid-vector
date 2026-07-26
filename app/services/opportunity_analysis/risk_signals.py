"""Construction risk-signal detection for opportunity analysis.

Pure, DB-free helpers plus their declarative pattern table: the construction
risk keyword/regex heuristics (``_CONSTRUCTION_RISK_PATTERNS``), the negation
guard, and the 도급한도 (awarded-contract-limit) single-notice guard. Moved
verbatim from the original ``opportunity_analysis`` module and re-exported from
the package ``__init__`` so existing test imports keep working.
"""

from __future__ import annotations

import re

from app.models.models import CompanyProfile, Project
from app.services.classifier import (
    REGION_PREFERENCE_PATTERN,
    is_construction_project,
)

# Named constant for the region_bonus risk message so the in-region suppression
# filter (see _build_risk_flags) matches by identity, not a fragile substring.
_REGION_BONUS_RISK_MESSAGE = "지역 가산점/소재지 가산 조건이 있어 비지역 업체는 점수 불리할 수 있습니다."

# Construction-specific risk signals (v1) — keyword/regex heuristics applied only
# when the notice is classified as construction. Each tuple is
# (category_id, compiled_pattern, reason_text). Ordered so reasons render in a
# stable sequence. One reason per category_id is appended to risk_flags even if
# multiple patterns in the same category match.
_CONSTRUCTION_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # Region-restricted joint-venture is matched FIRST so its more specific
    # signal wins when the text uses 지역의무공동도급 (which also contains
    # 공동도급). Categories are deduplicated, so a generic 공동도급 hit will
    # NOT also trigger when the region variant already matched, but the
    # opposite would erroneously fire generic-only — hence ordering matters.
    (
        "region_joint_venture",
        re.compile(r"지역\s*의무\s*공동\s*도급|지역\s*의무\s*공동\s*수급|지역\s*제한|지역\s*업체|해당\s*지역\s*소재"),
        "지역의무공동도급 또는 지역 제한 요건이 명시돼 있어 해당 지역 파트너/소재가 필요합니다.",
    ),
    (
        "joint_venture",
        re.compile(r"공동\s*도급|공동\s*수급(?:체)?|컨소시엄|\bjv\b"),
        "공동도급/공동수급체 구성이 요구됩니다. 파트너 확보·지분 협상이 필요합니다.",
    ),
    (
        "similar_experience",
        re.compile(r"유사\s*실적|동종\s*실적|최근\s*\d+\s*년\s*실적|시공\s*실적|납품\s*실적"),
        "유사 실적/시공실적 요건이 명시돼 있어 운영자 실적 증빙이 필요합니다.",
    ),
    (
        # Reuse the SHARED compiled pattern from classifier so the region_bonus
        # risk-flag and the classifier's region-score boost can never disagree on
        # what "지역가산점" means (single source of truth).
        "region_bonus",
        REGION_PREFERENCE_PATTERN,
        _REGION_BONUS_RISK_MESSAGE,
    ),
)


def _is_construction_project(project: Project) -> bool:
    """Decide whether to run construction-specific risk heuristics on this notice.

    Delegates to the shared ``classifier.is_construction_project`` so the
    construction risk heuristics here and the 도급한도 budget gate in
    ``_assess_budget`` always use ONE definition (no divergence on ambiguous
    categories such as "건설사업관리" / "설계·감리"). Signals only fire when the
    project category normalizes to a construction tag, which keeps unrelated
    software/goods notices that happen to mention 공동도급/유사실적 in different
    domain contexts from raising construction-flavored risk reasons.
    """
    return is_construction_project(project)


# 매치 직후 짧은 윈도우 내 부정어가 있으면 신호로 잡지 않음
# (예: "지역 제한 없음", "유사실적 불요" → 위험 아님)
_NEGATION_NEAR_MATCH = re.compile(r"\s*(?:없|미적용|불요|무관|면제|미요구|미해당)")


def _detect_construction_risk_reasons(
    project: Project,
    *,
    exclude_region_bonus: bool = False,
) -> list[str]:
    """Return ordered, deduplicated construction risk reasons matched on the notice text.

    When ``exclude_region_bonus`` is True the ``region_bonus`` reason is dropped —
    used for an in-region operator, for whom 지역가산점 is a competitive advantage
    (reflected as a classifier score boost) rather than a risk. Out-of-region
    operators keep the reason because for them it is a genuine disadvantage.
    """
    if not _is_construction_project(project):
        return []

    parts = [getattr(project, "title", None) or "",
             getattr(project, "description", None) or "",
             getattr(project, "requirements", None) or ""]
    notice_text = " ".join(part for part in parts if part).lower()
    if not notice_text:
        return []

    matched: list[str] = []
    seen_categories: set[str] = set()
    for category_id, pattern, reason in _CONSTRUCTION_RISK_PATTERNS:
        if category_id in seen_categories:
            continue
        if exclude_region_bonus and category_id == "region_bonus":
            continue
        hit = pattern.search(notice_text)
        if hit is None:
            continue
        # 부정 문맥 가드: 매치 직후 12자 내 부정어가 있으면 잡지 않음
        tail = notice_text[hit.end():hit.end() + 12]
        if _NEGATION_NEAR_MATCH.match(tail):
            continue
        matched.append(reason)
        seen_categories.add(category_id)
    return matched


def _detect_awarded_contract_limit_risks(
    project: Project,
    profile: CompanyProfile | None,
) -> list[str]:
    """Return risk reasons when a construction notice's budget exceeds the operator's awarded contract limit.

    v2 single-notice guard: when the operator has provided an
    ``awarded_contract_limit`` (도급한도) and the current construction notice's
    ``budget_estimate`` exceeds that limit, surface a risk reason. Cumulative
    accounting (sum of already-held awards + this new one) is intentionally
    out of scope here and tracked as v3.

    Gates (any one returns []):
      - profile is None
      - project is not a construction project
      - profile.awarded_contract_limit is missing / non-positive (treated as "not provided")
    """
    if profile is None:
        return []
    if not _is_construction_project(project):
        return []

    limit = float(getattr(profile, "awarded_contract_limit", 0.0) or 0.0)
    if limit <= 0:
        return []

    budget = float(getattr(project, "budget_estimate", 0.0) or 0.0)
    if budget <= limit:
        return []

    return [
        f"공고 예산({budget:,.0f}원)이 업체 도급한도({limit:,.0f}원)를 초과해 신규 도급이 어렵습니다."
    ]
