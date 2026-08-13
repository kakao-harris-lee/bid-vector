"""게이트 판정의 자기 진단 — 검정력·커버리지 분해·seed 안정성 계약.

이 파일이 지키는 것은 수치가 아니라 **구별**이다: "모델이 못 이겼다"와 "이 창은 아무것도
재지 못했다"가 리포트에서 갈라져야 하고, 통과가 소수 폴백 행에 걸려 있으면 그 사실이
드러나야 한다. 실 코퍼스 수치는 백테스트 리포트가 낸다.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.core.constants import AWARD_RATE_SAMPLE_SCOPE_FEED_ORIGIN
from app.domain.settlement_maturity import MaturityWindow
from app.services.ml_training.award_rate_diagnostics import (
    COVERAGE_COVERED,
    COVERAGE_FALLBACK,
    GATE_STABILITY_SEEDS,
    StabilityTrial,
    category_counts,
    coverage_splits,
    minimum_detectable_improvement,
    required_row_count,
    summarize_stability,
    unlearned_cells,
)
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_holdout import (
    GATE_PAIRED_T_THRESHOLD,
    GATE_STRATUM,
    evaluate_award_rate_holdout,
)
from app.services.ml_training.award_rate_scoring import BASELINE_SPECS, GATE_BASELINE_NAME

_START = datetime(2026, 1, 1, tzinfo=UTC)
_SCOPE = AWARD_RATE_SAMPLE_SCOPE_FEED_ORIGIN


def _trial(seed: int, improvement: float, t: float, passed: bool) -> StabilityTrial:
    return StabilityTrial(
        seed=seed, improvement_ratio=improvement, paired_t=t, passed=passed
    )


def test_sign_flip_across_seeds_is_reported_as_inconsistent():
    """부호가 뒤집히면 그 창은 방향조차 재지 못한 것이다."""
    summary = summarize_stability(
        [_trial(1, 0.07, -1.4, False), _trial(2, -0.04, 0.5, False)]
    )

    assert summary.sign_consistent is False
    assert summary.verdict_consistent is True  # 둘 다 실패이긴 하다
    assert summary.min_improvement_ratio == pytest.approx(-0.04)
    assert summary.max_improvement_ratio == pytest.approx(0.07)


def test_verdict_flip_across_seeds_is_reported_as_inconsistent():
    """seed 하나로 통과가 갈리는 창의 "판정"은 판정이 아니다."""
    summary = summarize_stability(
        [_trial(1, 0.09, -2.9, True), _trial(2, 0.05, -1.2, False)]
    )

    assert summary.verdict_consistent is False
    assert summary.passed_count == 1
    assert summary.min_abs_paired_t == pytest.approx(1.2)
    assert summary.max_abs_paired_t == pytest.approx(2.9)


def test_empty_stability_is_neutral_not_consistent():
    """재지 않은 것을 "일관"으로 채우면 안정성 칸이 먼저 거짓말을 한다."""
    summary = summarize_stability([])
    assert summary.passed_count == 0
    assert summary.trials == []


def test_declared_seeds_are_several_and_distinct():
    """seed 하나로는 부호 뒤집힘을 볼 수 없다."""
    assert len(set(GATE_STABILITY_SEEDS)) >= 3


def _unstable_rows(count: int = 200) -> list[AwardRateTrainingRow]:
    """기관 신호가 약하고 산포가 큰 소표본 — GBM 이 잡을 것이 거의 없는 코퍼스.

    실 코퍼스의 145행 창이 보인 성질(개선률 +7.12% ~ −3.77%)을 결정적으로 재현한다.
    """
    rates = {"가기관": 0.90, "나기관": 0.91, "다기관": 0.92}
    agencies = list(rates)
    return [
        AwardRateTrainingRow(
            value=rates[agencies[index % len(agencies)]]
            + 0.02 * (((index * 37) % 11) - 5) / 5,
            amount=1e8 * (1 + (index % 4)),
            category="construction" if index % 2 else "service",
            agency=agencies[index % len(agencies)],
            denominator_source=GATE_STRATUM,
            opened_at=_START + timedelta(hours=index),
            published_floor_rate=None,
        )
        for index in range(count)
    ]


def test_a_window_can_flip_sign_across_seeds_even_when_the_verdict_does_not():
    """게이트에 오른 창이 **아무것도 재지 못할 수 있다** — 이 성질을 고정한다.

    ``GATE_MIN_EVALUATION_ROWS`` 는 통계량이 성립하는 하한만 지킨다. 검정력은 그 상수가
    아니라 창마다 리포트가 말해야 하고, 이 테스트가 그 필요를 고정한다.
    """
    rows = _unstable_rows()
    window = MaturityWindow(
        start=_START + timedelta(hours=140),
        end=_START + timedelta(hours=200),
        opened_count=100,
        settled_count=80,
    )
    improvements = [
        evaluate_award_rate_holdout(
            rows, window=window, sample_scope=_SCOPE, folds=3, seed=seed
        ).gate_improvement_ratio
        for seed in (20260812, 1)
    ]

    assert max(improvements) > 0 > min(improvements)


def test_report_carries_power_next_to_the_verdict():
    """MDE 와 필요 표본 수가 없으면 실패가 "못 이겼다"인지 "못 쟀다"인지 모른다."""
    rows = _unstable_rows()
    window = MaturityWindow(
        start=_START + timedelta(hours=140),
        end=_START + timedelta(hours=200),
        opened_count=100,
        settled_count=80,
    )
    report = evaluate_award_rate_holdout(
        rows, window=window, sample_scope=_SCOPE, folds=3, seed=20260812
    )

    # 관측 개선이 이 창의 검출 한계보다 작다 = 못 쟀다.
    assert report.gate_improvement_ratio < report.gate_min_detectable_improvement
    assert report.gate_passed is False
    assert report.gate_required_row_count is not None
    assert report.gate_required_row_count > report.gate_test_row_count


def test_required_rows_is_none_when_the_model_is_worse():
    """모델이 더 나쁠 때 "몇 행 더 모으면 된다"는 답은 없다 — 0으로 채우지 않는다."""
    assert required_row_count(0.4, 300, threshold=GATE_PAIRED_T_THRESHOLD) is None
    assert required_row_count(-2.58, 300, threshold=GATE_PAIRED_T_THRESHOLD) == 300


def test_required_rows_scales_with_the_square_of_the_shortfall():
    """t 는 √n 에 비례한다 — 절반의 t 는 네 배의 표본을 요구한다."""
    assert required_row_count(-1.29, 100, threshold=2.58) == 400


def test_minimum_detectable_improvement_shrinks_as_rows_grow():
    """같은 잔차라면 표본이 늘수록 더 작은 차이를 검출할 수 있다."""
    rng = np.random.default_rng(7)

    def mde(n: int) -> float:
        targets = rng.normal(0.9, 0.05, n)
        baseline = np.full(n, 0.9)
        model = baseline - 0.001
        return minimum_detectable_improvement(
            model, baseline, targets, threshold=GATE_PAIRED_T_THRESHOLD
        )

    assert mde(2000) < mde(200)


def _coverage_inputs() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """폴백 2행에서만 모델이 크게 이기는 6행 홀드아웃."""
    covered = np.array([True, True, True, True, False, False])
    targets = np.array([0.90, 0.91, 0.89, 0.90, 0.70, 0.72])
    baseline = np.array([0.90, 0.90, 0.90, 0.90, 0.90, 0.90])
    model = np.array([0.90, 0.90, 0.90, 0.90, 0.71, 0.71])
    return covered, model, baseline, targets


def test_coverage_split_separates_the_covered_rows_from_the_fallback_rows():
    """통과가 폴백 소수 행에 걸려 있으면 리포트가 그것을 말해야 한다."""
    covered, model, baseline, targets = _coverage_inputs()
    splits = {
        split.segment: split
        for split in coverage_splits(covered, model, baseline, targets)
    }

    assert set(splits) == {COVERAGE_COVERED, COVERAGE_FALLBACK}
    assert splits[COVERAGE_COVERED].row_count == 4
    assert splits[COVERAGE_FALLBACK].row_count == 2
    # 이득은 전부 폴백 쪽이다.
    assert splits[COVERAGE_FALLBACK].improvement_ratio > 0.5
    assert splits[COVERAGE_COVERED].improvement_ratio == pytest.approx(0.0, abs=1e-9)


def test_coverage_split_omits_a_side_that_has_no_rows():
    """전부 커버된 창에서 빈 조각을 0행으로 싣지 않는다(있는 것만 말한다)."""
    covered = np.array([True, True, True])
    targets = np.array([0.9, 0.91, 0.92])
    baseline = np.full(3, 0.9)
    splits = coverage_splits(covered, baseline - 0.005, baseline, targets)

    assert [split.segment for split in splits] == [COVERAGE_COVERED]


def _cell_rows(category: str, amount: float, count: int) -> list[AwardRateTrainingRow]:
    return [
        AwardRateTrainingRow(
            value=0.9,
            amount=amount,
            category=category,
            agency="가기관",
            denominator_source=GATE_STRATUM,
            opened_at=_START + timedelta(hours=index),
            published_floor_rate=None,
        )
        for index in range(count)
    ]


def test_unlearned_cells_name_the_holes_in_the_comparison_baseline():
    """학습에 없던 셀에서는 베이스라인이 전역 평균으로 떨어진다 — 그 목록이 리포트에 남는다."""
    spec = next(spec for spec in BASELINE_SPECS if spec.name == GATE_BASELINE_NAME)
    train = _cell_rows("service", 1e8, 5)
    test = _cell_rows("service", 1e8, 2) + _cell_rows("construction", 5e9, 3)

    cells = unlearned_cells(spec, train, test)

    assert [cell.row_count for cell in cells] == [3]
    assert cells[0].key.startswith("construction|")


def test_unlearned_cells_is_empty_when_the_baseline_knows_every_cell():
    spec = next(spec for spec in BASELINE_SPECS if spec.name == GATE_BASELINE_NAME)
    train = _cell_rows("service", 1e8, 5)
    assert unlearned_cells(spec, train, _cell_rows("service", 1e8, 2)) == []


def test_category_counts_expose_the_regime_break():
    """공사 수집이 켜지기 전후의 창은 out-of-time 이 아니라 out-of-regime 분할이다."""
    rows = _cell_rows("service", 1e8, 7) + _cell_rows("construction", 1e8, 2)
    counts = category_counts(rows)

    assert [(item.category, item.row_count) for item in counts] == [
        ("service", 7),
        ("construction", 2),
    ]
