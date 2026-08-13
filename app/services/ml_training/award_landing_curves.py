"""곡선 조립 — 격자·층 A 요약·α 트레이드오프·층 A↔층 C 대조 (순수).

여기 있는 것은 **조립뿐**이다. 곡선·argmax·동률 규칙·α-제약·수축은 전부 PR1 순수 커널
(:mod:`app.domain.award_landing_curve_builders` · :mod:`app.domain.award_landing_curve` ·
:mod:`app.domain.award_margin_distribution`)이 계산하고, 여기서 같은 통계를 다시 쓰지
않는다(§4.5-8). I/O 도 시계도 없다.

리포트 조립(:mod:`app.services.ml_training.award_landing_report`)과 나눈 이유는 두 축의
관심사가 다르기 때문이다: 저쪽은 "어떤 셀을 어떤 순서로 신고하는가", 이쪽은 "곡선 하나를
어떻게 만들고 무엇을 함께 재는가".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.core.time import to_kst
from app.domain.award_landing_curve import (
    MAX_GRID_POINTS,
    ConstrainedOptimumStatus,
    WinProxyCurve,
    build_bid_rate_grid,
)
from app.domain.award_landing_curve_builders import (
    composed_win_proxy_curve,
    empirical_win_proxy_curve,
)
from app.domain.award_landing_distribution import equiprobable_assessment_support
from app.domain.award_margin_distribution import (
    MarginDistribution,
    build_margin_ecdf,
    shrink_margin_distribution,
)
from app.schemas._base import StrictModel
from app.services.ml_training.award_landing_ladder import LandingRow

__all__ = [
    "ALPHA_TRADEOFF_GRID",
    "CALIBRATION_GRID_STEP",
    "AlphaTradeoffPoint",
    "CalibrationComparison",
    "CurveSummary",
    "alpha_tradeoff",
    "build_calibration",
    "layer_a_summary",
    "parent_margin_distribution",
]

# α 격자 — **추천이 아니라 축**이다(NEW-3). 운영자가 자기 리스크 허용치를 이 곡선 위에서
# 읽을 수 있게 실격 예산을 넓게 훑는다. 값을 여기 선언하는 것은 실행마다 격자가 달라져
# 수치가 비교 불가가 되는 것을 막기 위함이다(§4.5-1).
ALPHA_TRADEOFF_GRID: Final[tuple[float, ...]] = (
    0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50,
)

# 층 A vs 층 C 대조 격자의 간격. 판정 격자(0.1bp)보다 **거칠다**: 대조는 두 곡선의
# 차이 규모를 재는 것이라 판정 좌표만큼 촘촘할 필요가 없고, 층 C 평가 비용은
# O(격자 × 지지집합 × log 표본) 이라 판정 격자로 돌리면 셀 하나가 분 단위로 늘어난다.
# ω 경계 잔여는 격자 해상도와 무관하게 **정확한 ω 좌표**에서 따로 잰다.
CALIBRATION_GRID_STEP: Final[float] = 1e-4


class CurveSummary(StrictModel):
    """곡선 하나의 판정 + **불안정성 증거**(설계 §11-7).

    ``optimal_bid_rate`` 만 실으면 곡선이 평탄하거나 다봉일 때 그 좌표가 표본 잡음으로
    흔들리는 것을 숨기게 된다. 동률 구간 폭·봉우리 수·진폭이 판정 옆에 함께 간다.
    """

    grid_lower_rate: float
    grid_upper_rate: float
    grid_point_count: int
    grid_step_effective: float
    grid_step_widened: bool
    peak_value: float
    optimal_bid_rate: float
    disqualification_at_optimum: float | None
    plateau_lower_rate: float
    plateau_upper_rate: float
    plateau_width: float
    mode_count: int
    value_range: float
    is_flat: bool
    is_multimodal: bool


class CalibrationComparison(StrictModel):
    """층 A(접지 진리) vs 층 C(합성)의 대조 — **독립 가정의 실측 검증**.

    두 곡선의 차이는 **세** 원인이 합쳐진 것이라, 총 차이를 그대로 "독립 가정의 오차"로
    읽으면 안 된다:

    1. 층 C 가 ``a ⊥ Δ`` 로 결합을 분해한 효과 — 재려는 것.
    2. 이김 임계 ``δ = b/a − f`` 의 나눗셈이 만드는 ω 경계 잔여(커널이 제거 불가로
       선언한 것) — ``omega_boundary_*`` 가 **따로** 잰다.
    3. 표본 잡음의 비대칭: 층 A 는 관측 쌍 ``N`` 개(대각선)를 세고 층 C 는 사실상
       ``(a_i, Δ_j)`` 조합 ``N²`` 개를 세므로, 두 분포가 정확히 독립이어도 유한 표본
       에서 계단 모양이 다르다. 얕은 셀일수록 이 항이 커진다.

    그래서 ``max_abs_difference`` 는 1번의 **상한**이고, 셀 깊이(``score_row_count``)와
    함께 읽어야 한다. 셋을 한 수로 뭉치면 "가정이 틀렸다"·"부동소수가 원자를 하나
    흘렸다"·"표본이 얕다"가 구별되지 않는다.

    ``mean_signed_difference`` 는 방향이다(음수 = 층 C 가 층 A 보다 크다 = 낙관).

    **창 필드의 계약**: 두 창은 **시각 경계**로 분리된다(``fit_window_end <
    score_window_start`` 가 항상 성립). ``*_distinct_open_days`` 는 그 창을 KST 달력일로
    센 값이라 **경계일에서 맞닿을 수 있다** — 시각 경계가 하루 안쪽에 떨어지면 그 날은
    양쪽에서 한 번씩 잡힌다. 그래서 두 일수의 합은 전체 기간의 일수보다 클 수 있고(경계일
    이중 계수), 일수는 "창이 며칠에 걸쳐 있나"를 읽는 값이지 합산용이 아니다. 분리 자체는
    시각 경계가 보장하므로 이 이중 계수는 누수가 아니다.
    """

    scope: str
    floor_rate: float
    fit_row_count: int
    score_row_count: int
    fit_window_start: str | None
    fit_window_end: str | None
    score_window_start: str | None
    score_window_end: str | None
    fit_distinct_open_days: int
    score_distinct_open_days: int
    """채점 창이 며칠에 걸쳐 있는가(KST 달력일).

    as-of 격차가 "합성 가정의 비용"인지 "단기 분포 이동"인지는 **창 길이 없이 구별할 수
    없다**. 이 코퍼스는 한 달 창에 개찰이 뭉쳐 있어 채점 창이 며칠뿐인 셀이 나오고,
    그런 셀의 큰 격차는 가정이 아니라 시기를 재고 있을 수 있다(Phase 2c: 판정 옆에
    한계를 둔다). 개찰은 한국 업무시간에 일어나므로 UTC 자정이 아니라 KST 로 센다.
    """
    grid_point_count: int
    shrinkage_cell_weight: float
    max_abs_difference: float
    max_abs_difference_bid_rate: float
    mean_abs_difference: float
    mean_signed_difference: float
    omega_boundary_sample_count: int
    omega_boundary_lost_atom_count: int
    omega_boundary_max_gap: float | None
    omega_boundary_direction: str
    omega_boundary_exceeds_one_over_n: bool


class AlphaTradeoffPoint(StrictModel):
    """실격 예산 ``α`` 한 점의 판정 — 값 추천 없음(NEW-3).

    ``binding`` 은 ``b*`` 가 실행가능 집합의 **하단 끝**(``b_min(α)``)이라는 뜻이다.
    ``P(φ>b)`` 가 ``b`` 에 비증가라 실행가능 집합은 상향폐집합이고, 그 하단에 서면
    ``WW`` 곡선의 모양은 선택에 관여하지 않았다 — 그것이 ``shape_irrelevant`` 다.

    **거리는 "제약 바로 위"가 아니라 "곡선이 골랐다"는 증거다**(리뷰 정정). 설계 §7 은
    제약이 걸리면 통상 ``b* = b_min(α)`` 로 붕괴한다고 적었지만(합성 fixture 실측 93%),
    실코퍼스에서는 붕괴하지 않는다. ``distance_above_feasible_lower > 0`` 은 실행가능
    구간 **안에서** ``WW`` 가 더 높은 좌표를 곡선이 실제로 골랐다는 뜻이고, 그 선택의
    근거가 얼마나 단단한지는 함께 실리는 ``win_proxy_at_feasible_lower`` 와의 차이로
    읽어야 한다 — 차이가 ``1/N`` 규모면 그 선택은 **표본 잡음**이다(실코퍼스 ``WW`` 는
    1/N 계단의 다봉 곡선이고 최심 셀 ``mode_count`` 가 194 였다). bp 단위 좌표를 그대로
    인용하기 전에 ``mode_count`` · ``plateau_width`` 를 함께 보라는 뜻이다(§11-7).
    """

    alpha: float
    status: str
    feasible_point_count: int
    optimal_bid_rate: float | None
    feasible_lower_rate: float | None
    distance_above_feasible_lower: float | None
    win_proxy: float | None
    win_proxy_at_feasible_lower: float | None
    """``b_min(α)`` 에서의 ``WW`` — ``win_proxy`` 와의 차이가 곧 "곡선이 준 이득"이다.

    그 이득이 ``1/N`` 미만이면 ``b*`` 가 ``b_min(α)`` 위에 앉은 것은 신호가 아니라
    계단 하나만큼의 잡음이다. 이 값 없이 ``distance_above_feasible_lower`` 만 보면
    거리를 그대로 실질 개선으로 읽게 된다.
    """
    disqualification_probability: float | None
    plateau_width: float | None
    mode_count: int | None
    binding: bool
    shape_irrelevant: bool


def _grid_for(
    rows: Sequence[LandingRow], *, step: float
) -> tuple[tuple[float, ...], float]:
    """``[min φ, max ω]`` 균등 격자. 점 수 상한을 넘으면 **범위가 아니라 간격**을 늘린다.

    자르지 않는 이유는 실측이 가르쳤다: 용역 코퍼스에는 ``f = 0.47995`` 같은 게시 하한이
    섞여 있어(개연 밴드 안의 실값 — :mod:`app.domain.published_floor_rate` 참조)
    ``[min φ, max ω]`` 가 0.5 를 넘는다. 그 범위를 0.1bp 격자의 점 수 상한(20,001점 =
    0.2)에 맞춰 자르면 **낙찰자 대부분이 사는 0.86~0.90 구간이 격자에서 통째로 빠져**
    곡선이 조용히 다른 대상을 재게 된다. 간격을 늘리면 해상도만 떨어지고 대상은 그대로다.

    **대가는 있다**: 소수의 극단 ``f`` 행이 셀 전체의 격자 해상도를 낮춘다(실측 용역 셀
    4개가 0.1bp → 최대 2.6배). 범위 밖으로 밀려나는 것보다 낫다는 판단이지만 무료는
    아니므로 ``grid_step_effective`` 를 리포트에 싣는다 — bp 단위 좌표를 인용하기 전에
    그 좌표의 해상도를 볼 수 있어야 한다.

    Returns:
        (격자, 실제 사용된 간격). 요청 간격보다 커졌는지는 호출부가 두 값을 비교해 안다.
    """
    lower = min(row.observation.realized_floor_rate for row in rows)
    upper = max(row.observation.realized_winning_rate for row in rows)
    span = upper - lower
    effective = max(step, span / (MAX_GRID_POINTS - 1)) if span > 0.0 else step
    return build_bid_rate_grid(lower, upper, step=effective), effective


def _curve_summary(
    curve: WinProxyCurve, *, requested_step: float, effective_step: float
) -> CurveSummary:
    diagnostics = curve.diagnostics()
    budget = curve.disqualification_probabilities
    index = curve.bid_rates.index(diagnostics.optimal_bid_rate)
    return CurveSummary(
        grid_lower_rate=curve.bid_rates[0],
        grid_upper_rate=curve.bid_rates[-1],
        grid_point_count=len(curve.bid_rates),
        grid_step_effective=effective_step,
        grid_step_widened=effective_step > requested_step,
        peak_value=diagnostics.peak_value,
        optimal_bid_rate=diagnostics.optimal_bid_rate,
        disqualification_at_optimum=None if budget is None else budget[index],
        plateau_lower_rate=diagnostics.plateau_lower_rate,
        plateau_upper_rate=diagnostics.plateau_upper_rate,
        plateau_width=diagnostics.plateau_width,
        mode_count=diagnostics.mode_count,
        value_range=diagnostics.value_range,
        is_flat=diagnostics.is_flat,
        is_multimodal=diagnostics.is_multimodal,
    )


def layer_a_summary(
    rows: Sequence[LandingRow], *, step: float
) -> tuple[WinProxyCurve, CurveSummary]:
    """층 A 곡선과 그 요약 — 접지 진리(가정 0개, 자기 모집단 채점)."""
    grid, effective = _grid_for(rows, step=step)
    curve = empirical_win_proxy_curve(grid, [row.observation for row in rows])
    return curve, _curve_summary(curve, requested_step=step, effective_step=effective)


def _tradeoff_point(curve: WinProxyCurve, alpha: float) -> AlphaTradeoffPoint:
    optimum = curve.constrained_optimum(alpha=alpha)
    diagnostics = optimum.feasible_diagnostics
    # 실행가능 하단 좌표는 커널이 준다 — 격자 길이에서 역산하면 "실행가능 집합은
    # 상향폐집합"이라는 성질을 이쪽에서 다시 가정하게 된다(§4.5-8).
    feasible_lower = optimum.feasible_lower_rate
    chosen = optimum.optimal_bid_rate
    binding = (
        chosen is not None and feasible_lower is not None and chosen == feasible_lower
    )
    return AlphaTradeoffPoint(
        alpha=alpha,
        status=optimum.status.value,
        feasible_point_count=optimum.feasible_point_count,
        optimal_bid_rate=chosen,
        feasible_lower_rate=feasible_lower,
        distance_above_feasible_lower=(
            None if chosen is None or feasible_lower is None else chosen - feasible_lower
        ),
        win_proxy=optimum.peak_value,
        win_proxy_at_feasible_lower=(
            None
            if feasible_lower is None
            else curve.values[curve.bid_rates.index(feasible_lower)]
        ),
        disqualification_probability=optimum.disqualification_probability,
        plateau_width=None if diagnostics is None else diagnostics.plateau_width,
        mode_count=None if diagnostics is None else diagnostics.mode_count,
        binding=binding,
        shape_irrelevant=binding
        and optimum.status is ConstrainedOptimumStatus.OPTIMAL,
    )


def alpha_tradeoff(curve: WinProxyCurve) -> list[AlphaTradeoffPoint]:
    """α 격자 위의 제약 판정 — 3-status 를 그대로 싣는다(조용한 fallback 없음)."""
    return [_tradeoff_point(curve, alpha) for alpha in ALPHA_TRADEOFF_GRID]


def omega_boundary_residual(
    rows: Sequence[LandingRow], *, floor_rate: float
) -> tuple[int, float | None, str]:
    """``b = ω_i`` 좌표에서 층 C 가 자기 원자를 잃는 건수·최대 gap·방향.

    층 C 는 이김 임계를 ``δ = b/a − f`` 로 만든다(계약상 나눗셈을 피할 수 없다). ``b``
    가 정확히 ``ω_i = a_i·(f + Δ_i)`` 일 때 실수 산술이라면 ``δ = Δ_i`` 지만 부동소수
    에서는 ``δ > Δ_i`` 가 될 수 있고, 생존이 ``Δ ≥ δ`` 라 그 순간 관측 ``i`` 의 기여가
    통째로 빠진다 — 층 A 대비 **1/N 씩 과소**(비관 방향).

    여기서 재는 것은 두 곡선의 차이가 아니라 그 **기전 자체**다. 격자 해상도와 무관
    하므로 정확한 ω 좌표에서만 성립하는 잔여를 격자가 놓치는 일이 없다.
    """
    lost = 0
    max_gap: float | None = None
    reversed_direction = False
    for row in rows:
        observation = row.observation
        delta = (
            observation.realized_winning_rate / observation.assessment_ratio
        ) - floor_rate
        gap = delta - observation.margin
        if gap > 0.0:
            lost += 1
            max_gap = gap if max_gap is None else max(max_gap, gap)
        elif gap < 0.0:
            reversed_direction = True
    if lost == 0:
        return 0, None, "reversed" if reversed_direction else "none"
    return lost, max_gap, "mixed" if reversed_direction else "understates"


def parent_margin_distribution(
    rows: Sequence[LandingRow], *, before: datetime | None
) -> MarginDistribution | None:
    """수축 부모(공종 층) 분포. ``before`` **미만** 개찰만 — as-of 경로의 누수 차단.

    경계가 **strict** 인 것이 계약이다(B1 회귀). ``<=`` 로 두면 채점 창의 첫 시각을
    공유하는 형제 행이 부모 분포로 새어 들어가고, 그 행들은 셀 분포로 수축될 때 자기
    자신을 채점하는 셈이 된다. 방향은 층 C 에 유리하므로 조용히 낙관을 만든다.

    **크기와 인과를 섞지 말 것**(N3): 이 누수는 **구조적으로 제거**됐고 회귀 테스트가
    경계를 고정한다. 다만 수정 전후 리포트 수치가 움직인 것을 "누수를 없애서"로 읽으면
    안 된다 — 실측 누수량은 부모 표본의 0.13~4.63%(1~36행)이고 부모는 수축 가중
    (깊은 셀 기준 6~9%)으로만 들어가는 반면, 수치 이동은 **경계 재분할**(적합/채점
    모집단 자체가 바뀐다)이 지배한다. 두 축이 무관함은 실측이 보여 준다: 누수가 가장
    컸던 셀(36행·3.54%)의 이동이 가장 작았고(+0.08%p), 가장 작았던 셀(1행·0.13%)의
    이동이 가장 컸다(+0.94%p).
    """
    scoped = [row for row in rows if before is None or row.opened_at < before]
    return build_margin_ecdf([row.margin for row in scoped]) if scoped else None


@dataclass(frozen=True)
class _CurveGap:
    """두 곡선 차이의 요약 — 최대 |차이|와 그 좌표, 평균 |차이|, 평균 부호."""

    max_abs: float
    max_abs_bid_rate: float
    mean_abs: float
    mean_signed: float


def _curve_gap(
    observed: Sequence[float], composed: Sequence[float], grid: Sequence[float]
) -> _CurveGap:
    """층 A − 층 C 를 격자 위에서 한 번에 요약한다(부호 유지)."""
    differences = [
        left - right for left, right in zip(observed, composed, strict=True)
    ]
    peak = max(range(len(differences)), key=lambda index: abs(differences[index]))
    return _CurveGap(
        max_abs=abs(differences[peak]),
        max_abs_bid_rate=grid[peak],
        mean_abs=sum(abs(value) for value in differences) / len(differences),
        mean_signed=sum(differences) / len(differences),
    )


@dataclass(frozen=True)
class _Window:
    """한 창의 경계와 폭 — 판정 옆에 붙는 "그래서 며칠을 봤나"."""

    start: str | None
    end: str | None
    distinct_open_days: int


def _window(rows: Sequence[LandingRow]) -> _Window:
    """개찰 시각 경계(ISO)와 KST 달력일 수. 빈 창은 경계 ``None`` · 0일."""
    if not rows:
        return _Window(start=None, end=None, distinct_open_days=0)
    stamps = [row.opened_at for row in rows]
    return _Window(
        start=min(stamps).isoformat(),
        end=max(stamps).isoformat(),
        distinct_open_days=len({to_kst(stamp).date() for stamp in stamps}),
    )


def _shrunk_margins(
    fit_rows: Sequence[LandingRow],
    *,
    parent_margins: MarginDistribution | None,
    prior_strength: float,
) -> tuple[MarginDistribution, float]:
    """셀 마진 ECDF(필요하면 부모로 수축) + 셀 가중치."""
    cell_margins: MarginDistribution = build_margin_ecdf(
        [row.margin for row in fit_rows]
    )
    if parent_margins is None:
        return cell_margins, 1.0
    shrunk = shrink_margin_distribution(
        cell=cell_margins,
        parent=parent_margins,
        cell_sample_count=len(fit_rows),
        prior_strength=prior_strength,
    )
    return shrunk, shrunk.cell_weight


def _layer_gap(
    fit_rows: Sequence[LandingRow],
    score_rows: Sequence[LandingRow],
    *,
    floor_rate: float,
    margins: MarginDistribution,
) -> tuple[tuple[float, ...], _CurveGap]:
    """두 층을 **같은 격자**에 올려 차이를 요약한다.

    격자는 **채점 행**의 ``[min φ, max ω]`` 에서 나온다(적합 행이 아니다). 층 A 가
    정의되는 구간이 채점 행의 구간이기 때문인데, 그래서 격자는 적합 창에 대해
    비대칭이다: 적합 행이 더 넓게 퍼져 있어도 그 바깥은 평가되지 않는다. 두 층을 같은
    좌표에서 빼려면 한쪽을 기준으로 잡아야 하고, **접지 진리 쪽**을 잡는 것이 "무엇을
    재는가"에 맞는다.
    """
    grid, _effective = _grid_for(score_rows, step=CALIBRATION_GRID_STEP)
    layer_a = empirical_win_proxy_curve(grid, [row.observation for row in score_rows])
    layer_c = composed_win_proxy_curve(
        grid,
        floor_rate=floor_rate,
        assessment=equiprobable_assessment_support(
            [row.assessment_ratio for row in fit_rows]
        ),
        margins=margins,
    )
    return grid, _curve_gap(layer_a.values, layer_c.values, grid)


def build_calibration(
    *,
    scope: str,
    fit_rows: Sequence[LandingRow],
    score_rows: Sequence[LandingRow],
    floor_rate: float,
    parent_margins: MarginDistribution | None,
    prior_strength: float,
) -> CalibrationComparison | None:
    """층 A(채점 행) vs 층 C(적합 행으로 만든 합성)를 같은 격자에서 나란히 잰다."""
    if not fit_rows or not score_rows:
        return None
    margins, cell_weight = _shrunk_margins(
        fit_rows, parent_margins=parent_margins, prior_strength=prior_strength
    )
    grid, gap = _layer_gap(
        fit_rows, score_rows, floor_rate=floor_rate, margins=margins
    )
    lost, max_gap, direction = omega_boundary_residual(
        score_rows, floor_rate=floor_rate
    )
    fit_window = _window(fit_rows)
    score_window = _window(score_rows)
    return CalibrationComparison(
        scope=scope,
        floor_rate=floor_rate,
        fit_row_count=len(fit_rows),
        score_row_count=len(score_rows),
        fit_window_start=fit_window.start,
        fit_window_end=fit_window.end,
        score_window_start=score_window.start,
        score_window_end=score_window.end,
        fit_distinct_open_days=fit_window.distinct_open_days,
        score_distinct_open_days=score_window.distinct_open_days,
        grid_point_count=len(grid),
        shrinkage_cell_weight=cell_weight,
        max_abs_difference=gap.max_abs,
        max_abs_difference_bid_rate=gap.max_abs_bid_rate,
        mean_abs_difference=gap.mean_abs,
        mean_signed_difference=gap.mean_signed,
        omega_boundary_sample_count=len(score_rows),
        omega_boundary_lost_atom_count=lost,
        omega_boundary_max_gap=max_gap,
        omega_boundary_direction=direction,
        omega_boundary_exceeds_one_over_n=lost > 1,
    )
