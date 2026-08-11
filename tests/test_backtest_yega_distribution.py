"""예정가 분포 캘리브레이션 스크립트의 순수 헬퍼 검증 (DB 없이)."""

from statistics import fmean, pvariance

import pytest

from scripts.backtest_yega_distribution import (
    _RunningLevel,
    normal_cdf,
    realized_assessment,
    run_coverage_backtest,
    summarize_pit,
)


class _Row:
    """HistoricalData 형태의 최소 픽스처(속성 접근만 쓰인다)."""

    def __init__(
        self,
        *,
        base_amount: float = 100_000_000.0,
        center: float = 0.99,
        spread: float = 0.02,
        selected=(1, 5, 10, 15),
        agency_name: str = "조달청",
        category: str = "construction",
    ) -> None:
        ratios = [center - spread + ((2 * spread) * index / 14) for index in range(15)]
        self.reserve_prices = [base_amount * ratio for ratio in ratios]
        self.selected_numbers = list(selected)
        self.base_amount = base_amount
        self.agency_name = agency_name
        self.category = category


def test_running_level_matches_batch_statistics():
    values = [0.99, 1.01, 0.98, 1.02, 1.0]
    level = _RunningLevel()
    for value in values:
        level.add(value)
    observation = level.observation()

    assert observation.sample_count == len(values)
    assert observation.mean == pytest.approx(fmean(values))
    assert observation.variance == pytest.approx(pvariance(values))


def test_running_level_empty_returns_none_and_single_sample_zero_variance():
    level = _RunningLevel()
    assert level.observation() is None
    level.add(1.0)
    assert level.observation().variance == 0.0


def test_normal_cdf_value_table():
    assert normal_cdf(0.0, mean=0.0, std=1.0) == pytest.approx(0.5)
    assert normal_cdf(1.2816, mean=0.0, std=1.0) == pytest.approx(0.9, abs=1e-3)
    assert normal_cdf(5.0, mean=0.0, std=0.0) == 0.5  # 퇴화 분포는 중립 PIT


def test_realized_assessment_uses_picked_reserve_mean():
    row = _Row(center=1.0, spread=0.014, selected=(1, 15))
    # 대칭 격자에서 양 끝 평균 == 중심.
    assert realized_assessment(row) == pytest.approx(1.0)
    assert realized_assessment(_Row(selected=())) is None


def test_summarize_pit_uniform_grid_hits_nominal_coverage():
    pit_values = [(index + 0.5) / 100 for index in range(100)]
    summary = summarize_pit(pit_values)
    assert summary.pit_mean == pytest.approx(0.5)
    assert summary.coverage["central_80"].empirical == pytest.approx(0.8, abs=0.01)
    assert summary.coverage["central_50"].empirical == pytest.approx(0.5, abs=0.01)


def test_run_coverage_backtest_no_leakage_before_min_prior_rows():
    # MIN_PRIOR_ROWS_FOR_COVERAGE(50) 미만 프리픽스에서는 prior 축 표본이 쌓이지 않는다.
    rows = [_Row() for _ in range(30)]
    report = run_coverage_backtest(rows)
    assert report.prior_predictive.sample_count == 0
    # 메커니즘 축(자기 분포 PIT)은 프리픽스와 무관하게 전 행에서 측정된다.
    assert report.mechanism_exact_draw.sample_count == 30


def test_run_coverage_backtest_prior_axis_activates_after_prefix():
    rows = [_Row(center=0.99 + ((index % 5) * 0.001)) for index in range(80)]
    report = run_coverage_backtest(rows)
    assert report.prior_predictive.sample_count == 30
    assert report.prior_mean_absolute_center_error is not None
    assert report.agency_count == 1
    assert report.category_count == 1


def test_standardized_shape_stats_value_table():
    from scripts.backtest_yega_distribution import standardized_shape_stats

    # 대칭 2점 분포: mean 0, std 1, 첨도 m4/m2²−3 = 1−3 = −2 (플랫한 꼬리).
    assert standardized_shape_stats([-1.0, 1.0]) == (0.0, 1.0, -2.0)
    assert standardized_shape_stats([0.5]) == (None, None, None)
    assert standardized_shape_stats([]) == (None, None, None)


def test_run_coverage_backtest_reports_standardized_residual_shape():
    rows = [_Row(center=0.99 + ((index % 5) * 0.001)) for index in range(80)]
    report = run_coverage_backtest(rows)
    assert report.prior_standardized_residual_mean is not None
    assert report.prior_standardized_residual_std is not None
