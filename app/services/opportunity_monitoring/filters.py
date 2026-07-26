"""Cheap watch-rule pre-filters (no scoring, no ML, no DB).

``_apply_strategy_filters`` and its text/keyword/priority helpers, moved verbatim
from the original ``opportunity_monitoring`` module. These are the rules the
public ``matches_strategy_watch_rules`` gate exposes.
"""

from __future__ import annotations

from app.core.single_user import split_multi_value_text
from app.models.models import OperatorStrategy, Project
from app.services.opportunity_monitoring.base import (
    StrategyFilterResult,
    _MonitoringBase,
)


class _WatchFilterMixin(_MonitoringBase):
    """Apply the operator's watch rules to a single project."""

    def _apply_strategy_filters(self, project: Project, strategy: OperatorStrategy) -> StrategyFilterResult:
        """Apply cheap watch-rule filters before running heavier analysis."""
        project_text = self._build_project_text(project)
        # Keyword matching uses a narrower text (title + requirements + category,
        # NO description): KONEPS stores 공고기관/공고번호/URL metadata in the
        # description (persistence.py), so 기관명 like "해양수산부" would otherwise
        # false-trigger required_keywords. Region matching keeps the full text
        # because 지역 can legitimately live in those metadata fields.
        keyword_text = self._build_keyword_text(project)
        reasons: list[str] = []

        focus_categories = split_multi_value_text(strategy.focus_categories)
        if focus_categories:
            project_category = (project.category or "").strip().lower()
            normalized_categories = {value.lower() for value in focus_categories}
            if project_category not in normalized_categories:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 카테고리 일치: {project.category or '미분류'}")

        focus_regions = split_multi_value_text(strategy.focus_regions)
        matched_focus_regions = self._matched_terms(project_text, focus_regions)
        if focus_regions:
            if not matched_focus_regions:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 지역 일치: {', '.join(matched_focus_regions[:2])}")

        exclude_regions = split_multi_value_text(strategy.exclude_regions)
        if self._matched_terms(project_text, exclude_regions):
            return StrategyFilterResult(matched=False, reasons=[])

        required_keywords = split_multi_value_text(strategy.required_keywords)
        matched_keywords = self._matched_terms(keyword_text, required_keywords)
        if required_keywords:
            if not matched_keywords:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 키워드 일치: {', '.join(matched_keywords[:3])}")

        exclude_keywords = split_multi_value_text(strategy.exclude_keywords)
        if self._matched_terms(keyword_text, exclude_keywords):
            return StrategyFilterResult(matched=False, reasons=[])

        project_budget = float(project.budget_estimate or 0.0)
        min_budget = float(strategy.min_budget_estimate or 0.0)
        max_budget = float(strategy.max_budget_estimate or 0.0)
        if min_budget > 0 and project_budget < min_budget:
            return StrategyFilterResult(matched=False, reasons=[])
        if max_budget > 0 and project_budget > max_budget:
            return StrategyFilterResult(matched=False, reasons=[])
        if min_budget > 0 or max_budget > 0:
            reasons.append("예산 범위 일치")

        if not reasons:
            reasons.append("기본 전략 조건 통과")

        return StrategyFilterResult(matched=True, reasons=reasons)

    def _build_project_text(self, project: Project) -> str:
        """Flatten the main searchable project fields into lowercase text."""
        return " ".join(
            part.strip()
            for part in [project.title or "", project.description or "", project.requirements or "", project.category or ""]
            if part and part.strip()
        ).lower()

    def _build_keyword_text(self, project: Project) -> str:
        """Flatten only the work-describing fields for keyword matching.

        Excludes ``description`` on purpose: KONEPS collectors store metadata
        (공고기관/공고번호/URL) there, so 기관명 like "해양수산부" must NOT satisfy a
        required keyword. The actual work name lives in ``title`` and the
        requirements in ``requirements``; ``category`` is the coarse type. No
        recall loss for keyword intent.
        """
        return " ".join(
            part.strip()
            for part in [project.title or "", project.requirements or "", project.category or ""]
            if part and part.strip()
        ).lower()

    def _matched_terms(self, project_text: str, terms: list[str]) -> list[str]:
        """Return watch terms that appear in the project text, preserving user order."""
        matches: list[str] = []
        seen: set[str] = set()
        for term in terms:
            normalized = term.strip().lower()
            if not normalized or normalized in seen:
                continue
            if normalized in project_text:
                matches.append(term.strip())
                seen.add(normalized)
        return matches

    def _is_high_priority_candidate(self, analysis: dict) -> bool:
        """Align preview filtering with the service's high-priority action semantics."""
        decision = analysis.get("decision", {})
        return bool(decision.get("pursue_bid")) and str(decision.get("action")) == "bid_now"
