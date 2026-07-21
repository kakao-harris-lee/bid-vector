"""Aggregation tests for scripts/report_license_gate_impact.py (read-only sim).

The simulation must count only notices that reach the gate (pass the strategy
watch rules), and among those exclude only ``ineligible`` verdicts while leaving
``unknown``/``eligible`` untouched. Pure aggregation is exercised from in-memory
``Project`` / ``OperatorStrategy`` instances — no DB, no external calls.
"""
from __future__ import annotations

from app.models.models import OperatorStrategy, Project
from scripts.report_license_gate_impact import (
    ImpactSummary,
    render_summary,
    simulate_gate_impact,
)

WASTE_LIMITS = {
    "license_limits": [
        {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1", "lmtSno": "1"},
    ]
}
HELD_LICENSES = "정보통신공사업"


def _project(**overrides) -> Project:
    base = dict(
        id=1,
        title="공고 용역",
        description="",
        requirements="",
        category="technical-service",
        budget_estimate=100_000_000.0,
        eligibility_raw=None,
        notice_number="R0001",
    )
    base.update(overrides)
    return Project(**base)


def _keyword_strategy() -> OperatorStrategy:
    return OperatorStrategy(
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        required_keywords="용역",
        exclude_keywords="",
        min_budget_estimate=0.0,
        max_budget_estimate=0.0,
    )


def test_simulation_only_counts_gate_reaching_notices():
    """Notices failing the strategy watch rules never reach the gate."""
    off_scope = _project(id=1, title="정보시스템 구축 사업", eligibility_raw=WASTE_LIMITS)
    ineligible = _project(id=2, title="폐기물 처리 용역", eligibility_raw=WASTE_LIMITS)
    unknown = _project(id=3, title="시스템 유지보수 용역", eligibility_raw=None)

    summary = simulate_gate_impact(
        [off_scope, ineligible, unknown], _keyword_strategy(), HELD_LICENSES
    )

    assert summary.open_total == 3
    # off_scope has no 용역 keyword → filtered out before the gate.
    assert summary.reaches_gate == 2
    assert summary.verdict_counts["ineligible"] == 1
    assert summary.verdict_counts["unknown"] == 1
    assert summary.verdict_counts["eligible"] == 0


def test_simulation_lists_excluded_notice_with_required_license():
    """Excluded (ineligible) notices carry their number, title, and required license."""
    ineligible = _project(
        id=2, notice_number="R2026-2", title="폐기물 처리 용역", eligibility_raw=WASTE_LIMITS
    )

    summary = simulate_gate_impact([ineligible], _keyword_strategy(), HELD_LICENSES)

    assert len(summary.excluded) == 1
    excluded = summary.excluded[0]
    assert excluded.notice_number == "R2026-2"
    assert "건설폐기물 중간처리업" in excluded.required_licenses
    assert summary.ineligible_required["건설폐기물 중간처리업"] == 1


def test_simulation_does_not_exclude_unknown_or_eligible():
    """unknown / eligible verdicts are never added to the exclusion list (neutral)."""
    unknown = _project(id=1, title="유지보수 용역", eligibility_raw=None)
    eligible = _project(id=2, title="폐기물 처리 용역", eligibility_raw=WASTE_LIMITS)

    # Operator holds exactly the required waste license → eligible.
    summary = simulate_gate_impact(
        [unknown, eligible], _keyword_strategy(), "건설폐기물 중간처리업/1253"
    )

    assert summary.excluded == []
    assert summary.verdict_counts["ineligible"] == 0


def test_render_summary_surfaces_honesty_caveats():
    """Rendered output must carry the unknown≠ineligible and precision caveats."""
    summary = ImpactSummary(open_total=1, reaches_gate=1)
    summary.verdict_counts["unknown"] = 1
    text = render_summary(summary, profile_license_codes=HELD_LICENSES, has_gate=True)

    assert "ineligible(부적격)이 아니다" in text
    assert "eligible 정밀도" in text
    assert "reaches_gate=1" in text
