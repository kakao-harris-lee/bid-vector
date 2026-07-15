"""Characterization tests for the classifier capability tier ladders.

These lock the boundary behaviour of the two ``NoticeClassifierService`` tier
ladders BEFORE they are re-expressed as declarative ``resolve_band`` tables:

* ``_assess_capacity_score_budget`` — the ``capacity_score >= 0.8`` / ``>= 0.4``
  fallback ladder (used when neither 시공능력평가액 nor 연매출 is available).
* ``_describe_project_scope`` — the ``required_capability >= 0.85`` / ``>= 0.65``
  / ``>= 0.5`` project-scale ladder used to build the semantic scope text.

Each boundary is exercised just-at / just-below (and just-above where the ladder
returns a distinct payload) so a ``>=`` -> ``>`` drift, an off-by-one rung, or a
reordered band would fail loudly. They must stay green both before and after the
resolve_band conversion (byte-identical output).
"""

from __future__ import annotations

import pytest

from app.models.models import Project
from app.services.classifier import NoticeClassifierService


# ---------------------------------------------------------------------------
# _assess_capacity_score_budget — capacity_score >= 0.8 / >= 0.4 / else ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("capacity_score", "expected_score", "reason_fragment"),
    [
        # HIGH tier: capacity_score >= 0.8 (inclusive boundary + just-above)
        (0.9, NoticeClassifierService.CAPACITY_HIGH_SCORE, "높아"),
        (0.8, NoticeClassifierService.CAPACITY_HIGH_SCORE, "높아"),
        # BASE tier: 0.4 <= capacity_score < 0.8 (just-below 0.8, mid, boundary 0.4)
        (0.79, NoticeClassifierService.CAPACITY_BASE_SCORE, "기준으로"),
        (0.5, NoticeClassifierService.CAPACITY_BASE_SCORE, "기준으로"),
        (0.4, NoticeClassifierService.CAPACITY_BASE_SCORE, "기준으로"),
        # FALLBACK tier: capacity_score < 0.4 (just-below 0.4 + floor)
        (0.39, 0.0, "정보가 부족해"),
        (0.0, 0.0, "정보가 부족해"),
    ],
)
def test_assess_capacity_score_budget_tier_boundaries(
    capacity_score: float, expected_score: float, reason_fragment: str
) -> None:
    service = NoticeClassifierService()

    result = service._assess_capacity_score_budget(capacity_score)

    # All three tiers pass the budget axis (0.0-score fallback still passes).
    assert result.passed is True
    assert result.penalty == 0.0
    assert result.score == expected_score
    assert any(reason_fragment in reason for reason in result.reasons)


def test_assess_capacity_score_budget_reason_interpolates_score() -> None:
    """The HIGH/BASE reasons echo the capacity_score to 2 decimals; the
    fallback reason carries no number. Locks the format so the refactor's
    ``.format(capacity_score=...)`` template stays byte-identical."""
    service = NoticeClassifierService()

    assert "capacity_score=0.85" in service._assess_capacity_score_budget(0.85).reasons[0]
    assert "capacity_score=0.42" in service._assess_capacity_score_budget(0.42).reasons[0]
    fallback_reason = service._assess_capacity_score_budget(0.1).reasons[0]
    assert "capacity_score" not in fallback_reason


# ---------------------------------------------------------------------------
# _describe_project_scope — required_capability >= 0.85 / >= 0.65 / >= 0.5 ladder
# ---------------------------------------------------------------------------


def _scope_project(budget: float) -> Project:
    """A complexity-keyword-free project so ``_estimate_required_capability``
    returns the pure budget-band value (no COMPLEXITY_KEYWORDS bonus)."""
    return Project(
        title="도로 포장 공사",
        description="도로 포장 공사 도급",
        requirements="",
        budget_estimate=budget,
    )


@pytest.mark.parametrize(
    ("budget", "expected_text"),
    [
        # required=0.85 → 대형 복합 (exact 0.85 boundary, inclusive)
        (500_000_000.0, "대형 복합 프로젝트 요구역량 0.85"),
        # required=0.70 → 중대형 (just-below 0.85 boundary; 0.70 >= 0.65)
        (499_999_999.0, "중대형 프로젝트 요구역량 0.70"),
        (200_000_000.0, "중대형 프로젝트 요구역량 0.70"),
        # required=0.55 → 중형 (just-below 0.65 boundary; 0.55 >= 0.5)
        (100_000_000.0, "중형 프로젝트 요구역량 0.55"),
        # required=0.40 → 기본 (below 0.5 boundary)
        (50_000_000.0, "기본 프로젝트 요구역량 0.40"),
    ],
)
def test_describe_project_scope_scale_boundaries(
    budget: float, expected_text: str
) -> None:
    service = NoticeClassifierService()

    assert service._describe_project_scope(_scope_project(budget)) == expected_text
