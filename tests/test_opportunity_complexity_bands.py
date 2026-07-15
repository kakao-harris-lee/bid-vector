"""Characterization tests for the opportunity-analysis complexity bands and the
strength/risk cutoffs that a follow-up declarative refactor moves into config.

These pin the CURRENT behavior of three pure helpers on
``OpportunityAnalysisService`` — no DB, classifier, or predictor needed:

* ``_estimate_execution_complexity_score`` — budget ladder (>= 500M/200M/100M)
  and deadline ladder (<= 6/24/72h, plus the ``None`` special case). Every band
  boundary is exercised directly above and directly below, plus mixed
  combinations that also fold in keyword/load/friction terms.
* ``_build_strengths`` / ``_build_risk_flags`` — the Korean phrases gated by the
  competitiveness / margin / price-confidence / probability / similarity /
  complexity cutoffs, one firing + one non-firing case per cutoff, with the
  phrase text snapshotted byte-for-byte.

Expected values are the current implementation's actual outputs (captured, not
re-derived), so the declarative refactor must reproduce them exactly.
"""

from __future__ import annotations

import pytest

from app.models.models import Project
from app.services.opportunity_analysis import OpportunityAnalysisService


# ---- fixtures / helpers -----------------------------------------------------

_NEUTRAL_TITLE = "테스트"  # contains none of EXECUTION_COMPLEXITY_KEYWORDS


def _service() -> OpportunityAnalysisService:
    return OpportunityAnalysisService()


def _project(
    *,
    budget: float = 0.0,
    budget_max: float = 0.0,
    title: str = _NEUTRAL_TITLE,
    description: str = "",
    requirements: str = "",
) -> Project:
    """In-memory Project for the pure complexity/strength/risk helpers."""
    return Project(
        title=title,
        description=description,
        requirements=requirements,
        category="service",
        budget_estimate=budget,
        budget_max=budget_max,
    )


def _complexity(
    *,
    budget: float = 0.0,
    budget_max: float = 0.0,
    title: str = _NEUTRAL_TITLE,
    description: str = "",
    requirements: str = "",
    cls_score: float = 1.0,
    deadline: int | None = 100,
    active: int = 0,
    max_active: int = 5,
    capacity: float = 1.0,
) -> float:
    return _service()._estimate_execution_complexity_score(
        project=_project(
            budget=budget,
            budget_max=budget_max,
            title=title,
            description=description,
            requirements=requirements,
        ),
        classification={"score": cls_score},
        deadline_hours_remaining=deadline,
        current_active_bids=active,
        max_active_bids=max_active,
        capacity_score=capacity,
    )


# ---- budget band (>= ladder) ------------------------------------------------
# deadline fixed >72h, neutral title, matched=1.0, no load, full capacity so
# only the budget signal moves. Boundaries probed directly above/below each rung.


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (0, 0.21),
        (99_999_999, 0.21),
        (100_000_000, 0.28),  # 100M rung boundary (>=)
        (199_999_999, 0.28),
        (200_000_000, 0.33),  # 200M rung boundary (>=)
        (499_999_999, 0.33),
        (500_000_000, 0.37),  # 500M rung boundary (>=)
        (900_000_000, 0.37),
    ],
)
def test_complexity_budget_band_boundaries(budget: int, expected: float) -> None:
    assert _complexity(budget=budget) == expected


@pytest.mark.parametrize(
    ("budget_max", "expected"),
    [
        (100_000_000, 0.28),
        (500_000_000, 0.37),
    ],
)
def test_complexity_budget_band_uses_max_of_estimate_and_max(
    budget_max: int, expected: float
) -> None:
    # project_budget = max(budget_estimate, budget_max); estimate is 0 here.
    assert _complexity(budget=0, budget_max=budget_max) == expected


# ---- deadline band (<= ladder + None special case) --------------------------
# budget fixed at 0 (budget signal 0.38); only the deadline signal moves.


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        (None, 0.22),  # missing deadline special case
        (0, 0.32),
        (6, 0.32),  # <=6 rung boundary
        (7, 0.29),
        (24, 0.29),  # <=24 rung boundary
        (25, 0.25),
        (72, 0.25),  # <=72 rung boundary
        (73, 0.21),
        (100, 0.21),
    ],
)
def test_complexity_deadline_band_boundaries(
    deadline: int | None, expected: float
) -> None:
    assert _complexity(budget=0, deadline=deadline) == expected


# ---- combinations (budget + deadline + keyword/load/friction folded in) ------


def test_complexity_combination_high_pressure() -> None:
    assert (
        _complexity(
            budget=500_000_000,
            deadline=6,
            title="통합 고도화",
            cls_score=0.0,
            active=5,
            max_active=5,
            capacity=0.0,
        )
        == 0.83
    )


def test_complexity_combination_mid_pressure_capacity_on_100_scale() -> None:
    assert (
        _complexity(
            budget=200_000_000,
            deadline=24,
            title="운영 센터 보안",
            cls_score=0.5,
            active=2,
            max_active=4,
            capacity=50.0,
        )
        == 0.62
    )


def test_complexity_combination_low_pressure() -> None:
    assert (
        _complexity(
            budget=150_000_000,
            deadline=48,
            title="클라우드 플랫폼 연계 실시간",
            cls_score=0.8,
            active=1,
            max_active=3,
            capacity=0.7,
        )
        == 0.49
    )


def test_complexity_combination_none_deadline_small_budget() -> None:
    assert (
        _complexity(
            budget=50_000_000,
            deadline=None,
            title=_NEUTRAL_TITLE,
            cls_score=1.0,
            active=0,
            max_active=5,
            capacity=1.0,
        )
        == 0.22
    )


# ---- strength cutoff phrases (byte-equal snapshots) -------------------------

STRENGTH_COMPETITIVENESS = "추천 투찰가가 비교 기준 대비 경쟁력 있는 구간에 있습니다."
STRENGTH_MARGIN = "예상 수익성 여력이 비교적 양호한 편입니다."
STRENGTH_PRICE_CONFIDENCE = "가격 예측 신뢰도가 양호해 투찰 범위 판단에 활용할 수 있습니다."
STRENGTH_PROBABILITY = "종합 낙찰 가능성 점수가 높게 산출되었습니다."


def _strengths(
    *,
    comp: float = 0.0,
    margin: float = 0.0,
    price_conf: float = 0.0,
    prob: float = 0.0,
) -> list[str]:
    # matched=False + result_count=0 suppress the always-on phrases so exactly
    # one cutoff-gated phrase is under test at a time.
    return _service()._build_strengths(
        classification={"matched": False, "score": 1.0},
        price_prediction={"confidence_score": price_conf},
        similar_projects={"result_count": 0, "results": []},
        competitiveness_score=comp,
        probability_score=prob,
        expected_margin_score=margin,
    )


def test_strength_competitiveness_cutoff() -> None:
    assert _strengths(comp=0.75) == [STRENGTH_COMPETITIVENESS]
    assert _strengths(comp=0.74) == []


def test_strength_margin_cutoff() -> None:
    assert _strengths(margin=0.72) == [STRENGTH_MARGIN]
    assert _strengths(margin=0.71) == []


def test_strength_price_confidence_cutoff() -> None:
    assert _strengths(price_conf=0.75) == [STRENGTH_PRICE_CONFIDENCE]
    assert _strengths(price_conf=0.74) == []


def test_strength_probability_cutoff() -> None:
    assert _strengths(prob=0.75) == [STRENGTH_PROBABILITY]
    assert _strengths(prob=0.74) == []


# ---- risk cutoff phrases (byte-equal snapshots) -----------------------------

RISK_SIMILARITY = "유사 사례와의 의미적 일치도가 높지 않아 비교 신뢰도가 제한적입니다."
RISK_PRICE_CONFIDENCE = "가격 예측 신뢰도가 아직 보수적 수준입니다."
RISK_MARGIN = "추천 투찰가 기준 예상 수익 여력이 낮아 손익 검토가 필요합니다."
RISK_COMPLEXITY = "통합 범위와 일정 압박을 감안할 때 실행 복잡도가 높은 편입니다."


def _risks(
    *,
    price_conf: float = 0.75,
    results: list[dict] | None = None,
    margin: float = 0.5,
    complexity: float = 0.5,
) -> list[str]:
    # matched=True, in-limit load, distant deadline, and a >=0.4 similarity keep
    # every other risk quiet so exactly one cutoff-gated phrase is under test.
    if results is None:
        results = [{"similarity_score": 0.5}]
    return _service()._build_risk_flags(
        classification={"matched": True, "score": 1.0},
        price_prediction={"confidence_score": price_conf},
        similar_projects={"result_count": len(results), "results": results},
        current_active_bids=0,
        max_active_bids=5,
        deadline_hours_remaining=100,
        expected_margin_score=margin,
        execution_complexity_score=complexity,
        project=None,
        profile=None,
    )


def test_risk_similarity_cutoff() -> None:
    assert _risks(results=[{"similarity_score": 0.39}]) == [RISK_SIMILARITY]
    assert _risks(results=[{"similarity_score": 0.40}]) == []


def test_risk_price_confidence_cutoff() -> None:
    assert _risks(price_conf=0.74) == [RISK_PRICE_CONFIDENCE]
    assert _risks(price_conf=0.75) == []


def test_risk_margin_cutoff() -> None:
    assert _risks(margin=0.45) == [RISK_MARGIN]
    assert _risks(margin=0.46) == []


def test_risk_complexity_cutoff() -> None:
    assert _risks(complexity=0.72) == [RISK_COMPLEXITY]
    assert _risks(complexity=0.71) == []
