"""win-proxy 백테스트 리포트의 값 계약 — 셀·공종·실행 선언·코퍼스 회계 DTO.

조립(:mod:`app.services.ml_training.award_landing_report`)과 분리한 이유는 **계약이 곧
JSON 산출물의 모양**이기 때문이다. 이 파일만 읽으면 리포트가 무엇을 신고하는지 알 수
있고, 조립 규칙이 바뀌어도 계약은 그 자리에 남는다.

계약의 규칙 하나: **못 잰 값은 0 이 아니라 ``None``** 이다. 0 으로 채우면 "베이스라인과
동률"이나 "위험 없음"으로 읽히고, 그 오독이 판정을 만든다(Phase 2c 교훈).
"""

from __future__ import annotations

from app.schemas._base import StrictModel
from app.services.ml_training.award_landing_curves import (
    AlphaTradeoffPoint,
    CalibrationComparison,
    CurveSummary,
)
from app.services.ml_training.award_landing_diagnostics import (
    AssessmentSummary,
    CorrelationSummary,
    NegativeMarginSummary,
    QuotedComparison,
)

__all__ = [
    "AwardLandingReport",
    "CategoryReport",
    "CellReport",
    "ConstrainedRollup",
    "CorpusAccounting",
    "RunParameters",
]


class CellReport(StrictModel):
    """셀 하나 — 깊이·층 A·f 분포·층 C 대조·α 곡선·진단. 못 쟀으면 사유가 남는다."""

    cell_key: str
    category: str
    era_tier: str
    amount_band: str
    row_count: int
    meets_depth_gate: bool
    unmeasurable_reason: str | None
    ambiguous_share: float
    """이 셀에서 레짐이 ``ambiguous``(=분류기가 floor_bound 로 **확정하지 못함**)인 비율.

    ``parameters.regime_gate`` 한 줄로는 셀별 크기가 보이지 않는다. 대조군 정책에서만
    새로 통과하는 셀은 이 값이 높을 수 있고, 그런 셀의 수치는 "가격경쟁 코퍼스의 측정"
    이 아니라 **포함 가정 위의 측정**이다. 엄격 게이트에서는 항상 0 이다.
    """
    layer_a: CurveSummary | None
    naive_argmax_disqualification: float | None
    floor_rate_shares: list[dict[str, float]]
    dominant_floor_rate: float | None
    dominant_floor_share: float | None
    dominant_floor_row_count: int
    floor_mixture_caveat: str | None
    """셀 안에 ``f`` 가 섞여 있을 때의 해석 경고(설계 §6 M5).

    ``layer_a`` · ``naive_argmax_disqualification`` · ``alpha_tradeoff`` 는 **셀 전체**
    행으로 만든다. ``WW_emp`` 는 각 행의 ``f_i`` 를 담으므로 f 가 섞이면 그 곡선은 한
    대상의 함수가 아니다 — 층 C 대조가 최빈 f 부분군으로 좁히는 것과 달리 이 셋은 좁히지
    않으므로, 좁히지 않았다는 사실을 필드로 남긴다. 동질이면 ``None``.
    """
    margin_median: float | None
    correlations: CorrelationSummary
    assessment: AssessmentSummary
    calibration_in_sample: CalibrationComparison | None
    calibration_as_of: CalibrationComparison | None
    calibration_as_of_unmeasurable_reason: str | None
    """as-of 대조를 못 낸 사유. ``calibration_as_of`` 가 ``None`` 이면 반드시 채워진다 —
    "못 쟀으면 사유"를 이 자리에도 일관되게 적용한다."""
    alpha_tradeoff: list[AlphaTradeoffPoint]


class CategoryReport(StrictModel):
    """공종 축 요약 — 설계 §7 M2 가 인용한 소박한 argmax 실격률의 재산출 자리.

    ``layer_a`` 는 f-이질 모집단 위의 곡선이라 **판정이 아니라 인용 대조용**이다:
    ``WW_emp`` 는 각 행의 ``f_i`` 를 담으므로 f 가 섞이면 곡선이 한 대상의 함수가
    아니게 된다(설계 §6 M5). 셀 축이 그 이질성을 걷어낸 자리다.
    """

    category: str
    row_count: int
    layer_a: CurveSummary | None
    naive_argmax_disqualification: float | None
    margin_median: float | None
    correlations: CorrelationSummary
    floor_rate_shares: list[dict[str, float]]


class RunParameters(StrictModel):
    """이 실행의 선언값 — 수치를 나중에 읽는 사람이 "무엇 위에서 났는가"를 알게 한다.

    κ·격자·분할 비율·깊이 게이트가 실행마다 다르면 두 리포트의 수치는 비교 불가다.
    그래서 판정 옆이 아니라 **리포트 머리에** 싣는다.
    """

    regime_gate: str
    as_of: str | None
    prior_strength: float
    grid_step: float
    calibration_grid_step: float
    as_of_fit_share: float
    min_cell_rows: int
    alpha_grid: list[float]


class CorpusAccounting(StrictModel):
    """코퍼스 회계 — 사다리 각 단계의 탈락과 코퍼스 전역 진단.

    탈락 계수를 싣는 이유는 "표본이 얕다"(시간이 해법)와 "표본이 오염됐다"(데이터가
    해법)를 구별하기 위해서다. 침묵 제외는 그 구별을 지운다.
    """

    candidate_count: int
    accepted_count: int
    ladder_counts: dict[str, int]
    regime_histogram: dict[str, int]
    negative_margins: NegativeMarginSummary
    correlations: CorrelationSummary
    assessment: AssessmentSummary
    stored_label_floor_mismatch: dict[str, float]


class ConstrainedRollup(StrictModel):
    """α 판정의 집계 한 벌 — **스코프를 자기가 신고한다**.

    같은 이름의 수치가 "전 셀"과 "G3 통과 셀"에서 크게 갈리므로(얕은 셀이 평균을
    끌어내린다) 값만 두면 어느 모집단인지 알 수 없다. ``scope`` 와 ``cell_count`` 가
    그 질문에 먼저 답한다.
    """

    scope: str
    cell_count: int
    alpha_point_count: int
    status_histogram: dict[str, int]
    binding_share: float | None
    median_distance_above_feasible_lower: float | None
    median_alpha_gain_over_feasible_lower: float | None
    """``WW(b*) − WW(b_min(α))`` 의 중앙값 — 곡선이 제약 위에서 준 **실질 이득**.

    ``median_distance_above_feasible_lower`` 만 보면 "b* 가 제약 위 몇 bp"까지만 알 수
    있고 그 이동이 승률을 실제로 올렸는지는 모른다. 이득이 셀별 ``1/N`` 규모면 그
    이동은 계단 하나만큼의 **표본 잡음**이다 — 두 수를 함께 봐야 갈린다.
    """


class AwardLandingReport(StrictModel):
    """리포트 전체 — 실행 선언 + 코퍼스 회계 + 셀별 판정 + 인용 대조."""

    generated_at: str
    parameters: RunParameters
    corpus: CorpusAccounting
    cells: list[CellReport]
    categories: list[CategoryReport]
    quoted_comparisons: list[QuotedComparison]
    measurable_cell_count: int
    constrained_all_cells: ConstrainedRollup
    constrained_deep_cells: ConstrainedRollup
    """G3 를 넘긴 셀만의 α 집계 — **인용해야 하는 쪽은 이쪽**이다.

    전-셀 집계는 판정 불가 셀(얕아서 "못 쟀다"로 남긴 셀)을 같은 평균에 섞는다. 그러면
    판정과 못-쟀다가 한 수로 뭉쳐(Phase 2c 원칙 위반) 헤드라인이 희석된다 — 실측에서
    전-셀 ``median_alpha_gain_over_feasible_lower`` 가 0.0 인 게이트도 깊은 셀만 보면
    0 이 아니었다. 두 값을 **이름으로 구별해 병기**해 어느 쪽을 인용하는지 읽는 쪽이
    알게 한다(전-셀 값도 버리지 않는다 — 얕은 셀이 무엇을 하고 있는지도 사실이다).
    """
