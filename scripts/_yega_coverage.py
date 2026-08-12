"""예정가 분포 캘리브레이션의 커버리지(PIT) 축 — backtest_yega_distribution 의 분해 모듈.

시간순 단일 패스로 각 공고 **이전** 행만으로 계층 수축 사후분포를 만들고(시간 누수
차단) 실현 사정률의 PIT 균등성·중앙 커버리지를 잰다. 메커니즘 축(공고 자신의 완전
열거 분포 PIT — 4/15 균등 복권 가정 자체의 검정)도 여기서 계산한다. 파일 크기
한도(§4.5-4) 대응으로 본 스크립트에서 분해했다 — 주석을 깎는 대신 축 단위 분해가
규칙이 요구하는 대응이다(리뷰 M 라운드).

행→관측 추출은 엔진과 같은 함수(``observe_reserve_draw``)를 쓴다(리뷰 L4-1).
"""

from __future__ import annotations

from math import erf, sqrt
from statistics import fmean, pvariance

from pydantic import BaseModel

from app.ai.predictors.distribution_extraction import (
    ReserveDrawObservation,
    observe_reserve_draw,
)
from app.ai.predictors.historical import (
    normalize_agency_name,
    normalize_category_key,
)
from app.domain.assessment_shrinkage import (
    LevelObservation,
    resolve_assessment_posterior,
)
from app.domain.reserve_draw_distribution import exact_draw_mean_distribution
from app.models.models import HistoricalData
from app.utils.sequence_coercion import coerce_integer_list, coerce_numeric_list

# 명목 커버리지 수준(선언 데이터 §4.5)과, prior 축 평가를 시작할 최소 선행 이력.
COVERAGE_LEVELS = (0.5, 0.8, 0.9)
MIN_PRIOR_ROWS_FOR_COVERAGE = 50


class CoverageLevelStat(BaseModel):
    nominal: float
    empirical: float


class PitSummary(BaseModel):
    """PIT 균등성 요약 — 균등이면 mean 0.5, std ≈ 0.2887(√(1/12))."""

    sample_count: int
    pit_mean: float | None = None
    pit_std: float | None = None
    coverage: dict[str, CoverageLevelStat] = {}


class CoverageReport(BaseModel):
    prior_predictive: PitSummary
    prior_mean_absolute_center_error: float | None
    # 표준화 잔차 z = (실현 사정률 − 사후 중심) / 예측 std 의 모양 진단(균등이면
    # mean 0 / std 1 / 초과 첨도 0): mean≠0 은 계통 편향, 첨도≫0 은 정규근사가
    # 못 잡는 뾰족한 중심+두꺼운 꼬리(과커버의 실제 원인)다.
    prior_standardized_residual_mean: float | None
    prior_standardized_residual_std: float | None
    prior_excess_kurtosis: float | None
    mechanism_exact_draw: PitSummary
    skipped_no_selected_numbers: int
    # 엔진과 같은 관측 관문(15개·center 밴드)에서 걸러진 행 수 — 침묵 스킵 금지(L4-1).
    skipped_unobservable: int
    agency_count: int
    category_count: int


class _RunningLevel:
    """한 계층의 (count, mean, 모분산) 을 온라인으로 유지한다 (Welford)."""

    __slots__ = ("count", "mean", "_m2")

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)

    def observation(self) -> LevelObservation | None:
        if self.count == 0:
            return None
        return LevelObservation(
            sample_count=self.count,
            mean=self.mean,
            variance=(self._m2 / self.count) if self.count >= 2 else 0.0,
        )


def normal_cdf(value: float, *, mean: float, std: float) -> float:
    """예측분포 정규근사의 CDF — PIT 계산용."""
    if std <= 0:
        return 0.5
    return 0.5 * (1.0 + erf((value - mean) / (std * sqrt(2.0))))


def summarize_pit(pit_values: list[float]) -> PitSummary:
    """PIT 균등성 요약 — 명목 vs 실측 중앙 구간 커버리지."""
    if not pit_values:
        return PitSummary(sample_count=0)
    coverage: dict[str, CoverageLevelStat] = {}
    for level in COVERAGE_LEVELS:
        tail = (1.0 - level) / 2.0
        inside = sum(1 for pit in pit_values if tail <= pit <= 1.0 - tail)
        coverage[f"central_{int(level * 100)}"] = CoverageLevelStat(
            nominal=level, empirical=round(inside / len(pit_values), 4)
        )
    return PitSummary(
        sample_count=len(pit_values),
        pit_mean=round(fmean(pit_values), 4),
        pit_std=(
            round(sqrt(pvariance(pit_values)), 4) if len(pit_values) >= 2 else 0.0
        ),
        coverage=coverage,
    )


def standardized_shape_stats(
    values: list[float],
) -> tuple[float | None, float | None, float | None]:
    """표준화 잔차의 (mean, std, 초과 첨도) — 정규근사 모양 진단(균등이면 0/1/0)."""
    if len(values) < 2:
        return None, None, None
    mean_value = fmean(values)
    second_moment = pvariance(values)
    if second_moment <= 0:
        return round(mean_value, 4), 0.0, None
    fourth_moment = fmean([(value - mean_value) ** 4 for value in values])
    return (
        round(mean_value, 4),
        round(sqrt(second_moment), 4),
        round((fourth_moment / (second_moment**2)) - 3.0, 2),
    )


class _CoverageAccumulator:
    """시간순 단일 패스 상태 — 평가는 항상 프리픽스 편입 **전**(시간 누수 차단)."""

    def __init__(self) -> None:
        self.agency_levels: dict[str, _RunningLevel] = {}
        self.category_levels: dict[str, _RunningLevel] = {}
        self.global_level = _RunningLevel()
        self.draw_variance_level = _RunningLevel()
        self.prior_pit: list[float] = []
        self.mechanism_pit: list[float] = []
        self.prior_absolute_errors: list[float] = []
        self.prior_standardized_residuals: list[float] = []
        self.skipped_no_pick = 0
        self.skipped_unobservable = 0

    def prior_pit_for(
        self, *, realized: float, agency_key: str, category_key: str
    ) -> None:
        """선행 이력만으로 만든 예측분포에서 실현 사정률의 PIT.

        추첨 분산은 평가 대상 행 자신의 값이 아니라 **프리픽스 평균**
        (``draw_variance_level.mean``)만 쓴다 — 자기 행 값을 넣으면 시간 누수다.
        """
        agency_level = self.agency_levels.get(agency_key)
        category_level = self.category_levels.get(category_key)
        global_observation = self.global_level.observation()
        if global_observation is None:
            return
        posterior = resolve_assessment_posterior(
            agency=agency_level.observation() if agency_level else None,
            category=category_level.observation() if category_level else None,
            global_level=global_observation,
        )
        predictive_std = sqrt(
            (posterior.std**2) + max(self.draw_variance_level.mean, 0.0)
        )
        self.prior_pit.append(
            normal_cdf(realized, mean=posterior.mean, std=predictive_std)
        )
        self.prior_absolute_errors.append(abs(posterior.mean - realized))
        if predictive_std > 0:
            self.prior_standardized_residuals.append(
                (realized - posterior.mean) / predictive_std
            )

    def evaluate_and_absorb(
        self,
        observed: ReserveDrawObservation,
        *,
        agency_key: str,
        category_key: str,
    ) -> None:
        """평가(선행 프리픽스만 사용) 후에야 관측을 집계에 편입한다(누수 차단)."""
        realized = observed.realized_assessment
        if realized is None:
            self.skipped_no_pick += 1
        else:
            # 메커니즘 축: 공고 자신의 15개 완전 열거 분포에서 실현 추첨 평균의 PIT.
            self.mechanism_pit.append(
                exact_draw_mean_distribution(
                    list(observed.ratios)
                ).cumulative_probability(realized)
            )
            if self.global_level.count >= MIN_PRIOR_ROWS_FOR_COVERAGE:
                self.prior_pit_for(
                    realized=realized,
                    agency_key=agency_key,
                    category_key=category_key,
                )
        self.absorb(
            center=observed.center,
            draw_variance=observed.draw_variance,
            agency_key=agency_key,
            category_key=category_key,
        )

    def absorb(
        self, *, center: float, draw_variance: float, agency_key: str, category_key: str
    ) -> None:
        if agency_key:
            self.agency_levels.setdefault(agency_key, _RunningLevel()).add(center)
        if category_key:
            self.category_levels.setdefault(category_key, _RunningLevel()).add(center)
        self.global_level.add(center)
        self.draw_variance_level.add(draw_variance)


def run_coverage_backtest(rows: list[HistoricalData]) -> CoverageReport:
    """시간순 단일 패스: 선행 행만으로 사후분포를 만들고 실현 사정률의 PIT 를 잰다.

    행→관측 추출은 엔진과 **같은 함수**(``observe_reserve_draw``, 리뷰 L4-1)를
    쓴다 — 관문 밖 행은 크래시 대신 관측 불가로 계수·skip 된다.
    """
    state = _CoverageAccumulator()
    for row in rows:
        observed = observe_reserve_draw(
            reserve_prices=coerce_numeric_list(row.reserve_prices),
            base_amount=float(row.base_amount or 0.0),
            picked_numbers=coerce_integer_list(row.selected_numbers),
            bid_rate=None,
        )
        if observed is None:
            state.skipped_unobservable += 1
            continue
        state.evaluate_and_absorb(
            observed,
            agency_key=normalize_agency_name(row.agency_name),
            category_key=normalize_category_key(row.category),
        )

    residual_mean, residual_std, excess_kurtosis = standardized_shape_stats(
        state.prior_standardized_residuals
    )
    return CoverageReport(
        prior_predictive=summarize_pit(state.prior_pit),
        prior_mean_absolute_center_error=(
            round(fmean(state.prior_absolute_errors), 6)
            if state.prior_absolute_errors
            else None
        ),
        prior_standardized_residual_mean=residual_mean,
        prior_standardized_residual_std=residual_std,
        prior_excess_kurtosis=excess_kurtosis,
        mechanism_exact_draw=summarize_pit(state.mechanism_pit),
        skipped_no_selected_numbers=state.skipped_no_pick,
        skipped_unobservable=state.skipped_unobservable,
        agency_count=len(state.agency_levels),
        category_count=len(state.category_levels),
    )
