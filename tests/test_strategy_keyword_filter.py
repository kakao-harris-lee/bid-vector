"""Keyword-text scoping tests for StrategyMonitoringService._apply_strategy_filters.

Regression for the keyword false-trigger fix: required_keywords/exclude_keywords
must match only ``title + requirements + category`` (NOT ``description``), because
KONEPS collectors store 공고기관/공고번호/URL metadata in ``description`` so an
기관명 like "해양수산부" would otherwise satisfy a marine keyword. Region matching
keeps the full text (description included) on purpose.

``_apply_strategy_filters`` needs no DB session, so these are plain unit tests
built from in-memory ``Project`` / ``OperatorStrategy`` instances.
"""

from __future__ import annotations

from app.core.single_user import join_multi_value_text
from app.models.models import OperatorStrategy, Project
from app.services.opportunity_monitoring import StrategyMonitoringService
from scripts.seed_marine_gate import (
    MARINE_FOCUS_CATEGORIES,
    MARINE_REQUIRED_KEYWORDS,
)


def _service() -> StrategyMonitoringService:
    return StrategyMonitoringService()


def _strategy(**overrides) -> OperatorStrategy:
    base = dict(
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        required_keywords="",
        exclude_keywords="",
        min_budget_estimate=0.0,
        max_budget_estimate=0.0,
    )
    base.update(overrides)
    return OperatorStrategy(**base)


def _marine_strategy() -> OperatorStrategy:
    return _strategy(
        focus_categories=join_multi_value_text(MARINE_FOCUS_CATEGORIES),
        required_keywords=join_multi_value_text(MARINE_REQUIRED_KEYWORDS),
    )


# ---------------------------------------------------------------------------
# Core mechanism: description is excluded from keyword matching
# ---------------------------------------------------------------------------


def test_required_keyword_only_in_description_does_not_match():
    """A keyword present ONLY in description must NOT pass the filter."""
    project = Project(
        title="시스템 유지보수 용역",
        description="공고기관: 항만공사, 공고번호 2026-001",
        requirements="",
        category="technical-service",
    )
    strategy = _strategy(
        focus_categories="technical-service",
        required_keywords="항만",
    )
    result = _service()._apply_strategy_filters(project, strategy)
    assert result.matched is False


def test_required_keyword_in_title_matches():
    """Same keyword in the title still matches (no recall loss)."""
    project = Project(
        title="항만 시설 유지보수 용역",
        description="공고기관: 한국토지주택공사",
        requirements="",
        category="technical-service",
    )
    strategy = _strategy(
        focus_categories="technical-service",
        required_keywords="항만",
    )
    result = _service()._apply_strategy_filters(project, strategy)
    assert result.matched is True


def test_required_keyword_in_requirements_matches():
    """Keyword in requirements (작업요건) still matches."""
    project = Project(
        title="해안 정비 용역",
        description="공고기관: 부산광역시",
        requirements="방파제 보강 및 케이슨 거치 포함",
        category="technical-service",
    )
    strategy = _strategy(
        focus_categories="technical-service",
        required_keywords="방파제",
    )
    result = _service()._apply_strategy_filters(project, strategy)
    assert result.matched is True


def test_exclude_keyword_only_in_description_does_not_drop():
    """An exclude keyword living only in description must NOT drop the candidate."""
    project = Project(
        title="항만 준설 용역",
        description="비고: 하도급 가능 여부 별도 협의",
        requirements="",
        category="technical-service",
    )
    strategy = _strategy(
        focus_categories="technical-service",
        required_keywords="준설",
        exclude_keywords="하도급",
    )
    result = _service()._apply_strategy_filters(project, strategy)
    assert result.matched is True


def test_region_match_still_uses_full_text_including_description():
    """focus_regions matching keeps description (지역 can live in metadata)."""
    project = Project(
        title="항만 시설 점검 용역",
        description="공고기관: 부산지방해양수산청",
        requirements="",
        category="technical-service",
    )
    strategy = _strategy(
        focus_categories="technical-service",
        focus_regions="부산",
        required_keywords="항만",
    )
    result = _service()._apply_strategy_filters(project, strategy)
    assert result.matched is True


# ---------------------------------------------------------------------------
# Marine gate end-to-end scenarios
# ---------------------------------------------------------------------------


def test_agency_name_in_description_does_not_false_trigger_marine_gate():
    """'공고기관: 해양수산부' in description must NOT pass the marine gate.

    The work (수산업 통계 시스템) is unrelated; the only marine-looking token is
    the 기관명 in description, which is now excluded from keyword matching.
    """
    project = Project(
        title="수산업 통계 시스템 유지보수",
        description="공고기관: 해양수산부",
        requirements="",
        category="technical-service",
    )
    result = _service()._apply_strategy_filters(project, _marine_strategy())
    assert result.matched is False


def test_marine_construction_passes_marine_gate():
    """A 해양 공사(category=construction) with marine keywords passes the gate."""
    project = Project(
        title="○○항 방파제 보강공사",
        description="공고기관: ○○지방해양수산청",
        requirements="방파제 케이슨 거치 및 사석 투하",
        category="construction",
        budget_estimate=900_000_000.0,
    )
    result = _service()._apply_strategy_filters(project, _marine_strategy())
    assert result.matched is True


def test_generic_construction_is_excluded_by_keyword_and():
    """A generic 건물 공사 (no marine keyword) is excluded despite construction focus."""
    project = Project(
        title="○○청사 신축공사",
        description="공고기관: ○○시청",
        requirements="철근콘크리트 구조, 지상 5층",
        category="construction",
        budget_estimate=900_000_000.0,
    )
    result = _service()._apply_strategy_filters(project, _marine_strategy())
    assert result.matched is False
