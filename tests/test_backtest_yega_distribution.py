"""예정가 분포 캘리브레이션 스크립트의 순수 헬퍼 검증 (DB 없이)."""

from statistics import fmean, pvariance

import pytest

from app.ai.predictors.distribution_extraction import observe_reserve_draw
from scripts._yega_coverage import (
    _RunningLevel,
    normal_cdf,
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


def test_observe_reserve_draw_is_the_shared_row_gateway():
    """엔진·스크립트 공용 관측 관문(L4-1) — 실현값·15개·개연 밴드 값표."""
    row = _Row(center=1.0, spread=0.014, selected=(1, 15))
    observed = observe_reserve_draw(
        reserve_prices=row.reserve_prices,
        base_amount=row.base_amount,
        picked_numbers=row.selected_numbers,
        bid_rate=None,
    )
    # 대칭 격자에서 양 끝 평균 == 중심 == 비율 평균.
    assert observed is not None
    assert observed.realized_assessment == pytest.approx(1.0)
    assert observed.center == pytest.approx(1.0)
    # 추첨번호 미보고면 관측은 성립하되 실현값만 None.
    no_pick = _Row(selected=())
    observed_no_pick = observe_reserve_draw(
        reserve_prices=no_pick.reserve_prices,
        base_amount=no_pick.base_amount,
        picked_numbers=no_pick.selected_numbers,
        bid_rate=None,
    )
    assert observed_no_pick is not None
    assert observed_no_pick.realized_assessment is None
    # 15개 미만·center 밴드 밖은 관측 불가(None).
    short = _Row()
    assert (
        observe_reserve_draw(
            reserve_prices=short.reserve_prices[:14],
            base_amount=short.base_amount,
            picked_numbers=[1, 2],
            bid_rate=None,
        )
        is None
    )
    out_of_band = _Row(center=1.5)
    assert (
        observe_reserve_draw(
            reserve_prices=out_of_band.reserve_prices,
            base_amount=out_of_band.base_amount,
            picked_numbers=[1, 2],
            bid_rate=None,
        )
        is None
    )


def test_run_coverage_backtest_skips_unobservable_rows_instead_of_crashing():
    """엔진 관문 밖 행(밴드 밖·15개 미만)은 계수하고 skip — 리포트 중단 금지(L4-1)."""
    rows = [_Row() for _ in range(10)]
    bad_band = _Row(center=1.5)
    short = _Row()
    short.reserve_prices = short.reserve_prices[:3]  # 유효 비율 4개 미만이던 크래시 경로
    rows += [bad_band, short]

    report = run_coverage_backtest(rows)

    assert report.skipped_unobservable == 2
    # 관측 불가 행은 프리픽스 집계에도 들어가지 않는다.
    assert report.mechanism_exact_draw.sample_count == 10


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
    from scripts._yega_coverage import standardized_shape_stats

    # 대칭 2점 분포: mean 0, std 1, 첨도 m4/m2²−3 = 1−3 = −2 (플랫한 꼬리).
    assert standardized_shape_stats([-1.0, 1.0]) == (0.0, 1.0, -2.0)
    assert standardized_shape_stats([0.5]) == (None, None, None)
    assert standardized_shape_stats([]) == (None, None, None)


def test_run_coverage_backtest_reports_standardized_residual_shape():
    rows = [_Row(center=0.99 + ((index % 5) * 0.001)) for index in range(80)]
    report = run_coverage_backtest(rows)
    assert report.prior_standardized_residual_mean is not None
    assert report.prior_standardized_residual_std is not None


def test_observe_center_gate_includes_band_boundaries():
    """center 관문도 같은 술어를 타므로 경계(0.8·1.2)가 관측에 포함된다(리뷰 N1)."""
    for boundary in (0.8, 1.2):
        row = _Row(center=boundary, spread=0.0)
        observed = observe_reserve_draw(
            reserve_prices=row.reserve_prices,
            base_amount=row.base_amount,
            picked_numbers=row.selected_numbers,
            bid_rate=None,
        )
        assert observed is not None
        assert observed.center == pytest.approx(boundary)
