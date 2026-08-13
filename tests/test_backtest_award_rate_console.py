"""백테스트 콘솔 요약 — 오독을 **생산하지 않는가**.

JSON 이 정직해도 사람이 인용하는 것은 콘솔 한 줄이다. 이 트랙의 존재 이유가 "모델이 못
이겼다"와 "이 창은 아무것도 재지 못했다"를 가르는 것이므로, 그 구별이 요약 줄에서 살아
있는지를 여기서 고정한다.

실제로 있었던 오독: 판정 일관성(``verdict_consistent``)만 보는 술어는 5회 전부 실패한
창(부호는 −3.77% ~ +7.12% 로 뒤집힘)을 걸러내지 못했고, 그 결과 콘솔이
``gate_passed_at_all_origins=false`` 와 ``unstable_windows=[]`` 를 나란히 내놓아
"불안정한 창은 없는데 한 창이 실패했다 = 모델이 못 이겼다"로 읽혔다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from scripts.backtest_award_rate_gbm import ConsoleSummary, _console_summary
from app.services.ml_training.award_rate_backtest_report import (
    BacktestReport,
    WindowStabilitySummary,
)
from app.services.ml_training.award_rate_diagnostics import StabilityTrial
from app.services.ml_training.award_rate_holdout import (
    AwardRateHoldoutReport,
    GATE_STRATUM,
)
from app.services.ml_training.award_rate_scoring import (
    GATE_BASELINE_NAME,
    ModelScore,
    SegmentScore,
)

_START = datetime(2026, 6, 28, tzinfo=UTC)
_OUT = "reports/phase2c-holdout.json"


def _origin(
    *,
    start: datetime = _START,
    improvement: float = 0.0462,
    paired_t: float = -1.414,
    mde: float = 0.0860,
    passed: bool = False,
    segments: list[SegmentScore] | None = None,
) -> AwardRateHoldoutReport:
    return AwardRateHoldoutReport(
        window_start=start.isoformat(),
        window_end=(start + timedelta(days=7)).isoformat(),
        window_maturity=0.713,
        window_opened_count=595,
        window_settled_count=424,
        cutoff_at=start.isoformat(),
        train_row_count=283,
        gate_train_row_count=187,
        gate_test_row_count=145,
        train_mean=0.975,
        test_mean=0.950,
        test_std=0.05,
        baselines=[
            ModelScore(
                name=GATE_BASELINE_NAME,
                rmse=0.0527,
                bias=0.018,
                residual_std=0.049,
                test_coverage=0.986,
            )
        ],
        segments=segments or [],
        gate_baseline_rmse=0.0527,
        gate_model_rmse=0.0502,
        gate_improvement_ratio=improvement,
        gate_paired_t=paired_t,
        gate_min_detectable_improvement=mde,
        gate_required_row_count=483,
        gate_passed=passed,
    )


def _stability(
    *,
    start: datetime = _START,
    sign_consistent: bool,
    verdict_consistent: bool,
    passed_count: int = 0,
) -> WindowStabilitySummary:
    return WindowStabilitySummary(
        window_start=start.isoformat(),
        row_count=145,
        passed_count=passed_count,
        trial_count=5,
        sign_consistent=sign_consistent,
        verdict_consistent=verdict_consistent,
        min_improvement_ratio=-0.0377,
        max_improvement_ratio=0.0712,
        min_abs_paired_t=0.019,
        max_abs_paired_t=2.130,
        trials=[
            StabilityTrial(seed=1, improvement_ratio=-0.0377, paired_t=0.94, passed=False)
        ],
    )


def _report(**kwargs) -> BacktestReport:
    defaults = {
        "generated_at": "2026-08-13T00:00:00+00:00",
        "corpus_row_count": 9271,
        "feed_origin_only": True,
        "gate_stratum": GATE_STRATUM,
        "gate_baseline_name": GATE_BASELINE_NAME,
        "gate_paired_t_threshold": 2.58,
        "maturity_threshold": 0.70,
        "min_evaluation_rows": 100,
        "max_origins": 5,
        "maturity_observation_count": 24723,
        "encoding_folds": 5,
        "training_seed": 20260812,
        "gate_evaluable": True,
        "gate_passed_at_latest_window": False,
        "gate_passed_at_all_origins": False,
    }
    return BacktestReport(**{**defaults, **kwargs})


def _summary(**kwargs) -> ConsoleSummary:
    from pathlib import Path

    return _console_summary(_report(**kwargs), Path(_OUT))


def test_a_window_that_only_flips_sign_still_reaches_the_console():
    """5회 전부 실패이면서 부호가 뒤집히는 창 — 판정 일관성만 보면 사라진다."""
    summary = _summary(
        origins=[_origin()],
        stability_seeds=[20260812, 1, 7, 42, 2026],
        window_stability=[_stability(sign_consistent=False, verdict_consistent=True)],
    )

    assert len(summary.seed_unstable_windows) == 1
    assert "2026-06-28" in summary.seed_unstable_windows[0]
    assert "-3.77%" in summary.seed_unstable_windows[0]
    assert "+7.12%" in summary.seed_unstable_windows[0]


def test_a_window_that_flips_its_verdict_also_reaches_the_console():
    summary = _summary(
        origins=[_origin()],
        stability_seeds=[20260812, 1],
        window_stability=[
            _stability(sign_consistent=True, verdict_consistent=False, passed_count=1)
        ],
    )
    assert len(summary.seed_unstable_windows) == 1


def test_a_stable_window_is_not_listed():
    summary = _summary(
        origins=[_origin(passed=True, improvement=0.0789, mde=0.0631)],
        stability_seeds=[20260812, 1],
        window_stability=[
            _stability(sign_consistent=True, verdict_consistent=True, passed_count=5)
        ],
    )
    assert summary.seed_unstable_windows == []


def test_not_measuring_stability_is_distinguishable_from_measuring_none():
    """``--no-stability`` 의 ``[]`` 와 "쟀는데 없다"의 ``[]`` 가 같아 보이면 안 된다."""
    unmeasured = _summary(origins=[_origin()], stability_seeds=[], window_stability=[])
    measured = _summary(
        origins=[_origin(passed=True, improvement=0.0789, mde=0.0631)],
        stability_seeds=[20260812, 1, 7, 42, 2026],
        window_stability=[
            _stability(sign_consistent=True, verdict_consistent=True, passed_count=5)
        ],
    )

    assert unmeasured.stability_seed_count == 0
    assert measured.stability_seed_count == 5
    assert unmeasured.seed_unstable_windows == measured.seed_unstable_windows == []


def test_underpowered_windows_reach_the_console():
    """게이트 창의 MDE 만 실으면 실패한 창의 검정력은 콘솔에서 볼 수 없다."""
    summary = _summary(
        origins=[
            _origin(improvement=0.0462, mde=0.0860),
            _origin(
                start=_START + timedelta(days=7),
                improvement=0.0789,
                mde=0.0631,
                paired_t=-3.197,
                passed=True,
            ),
        ],
        stability_seeds=[20260812],
    )

    assert len(summary.underpowered_windows) == 1
    assert "2026-06-28" in summary.underpowered_windows[0]
    assert "+4.62%" in summary.underpowered_windows[0]
    assert "8.60%" in summary.underpowered_windows[0]


def _segment(segment: str, rows: int, improvement: float) -> SegmentScore:
    return SegmentScore(
        axis="category",
        segment=segment,
        row_count=rows,
        baseline_rmse=0.066,
        baseline_bias=0.024,
        model_rmse=0.069,
        model_bias=0.024,
        model_residual_std=0.06,
        improvement_ratio=improvement,
        paired_t=1.434,
    )


def test_single_row_segments_stay_out_of_the_headline():
    """1행 세그먼트는 대응 t 가 구조상 존재할 수 없어 진짜 신호를 희석한다."""
    summary = _summary(
        origins=[
            _origin(
                segments=[
                    _segment("service", 212, -0.040),
                    _segment("noise", 1, -0.091),
                ]
            )
        ],
    )

    assert summary.gate_regressed_segments == ["category/service n=212 -4.0%"]


def test_regressed_segments_are_ordered_by_row_count():
    summary = _summary(
        origins=[
            _origin(
                segments=[
                    _segment("small", 30, -0.02),
                    _segment("service", 212, -0.04),
                ]
            )
        ],
    )

    assert [entry.split()[0] for entry in summary.gate_regressed_segments] == [
        "category/service",
        "category/small",
    ]


def test_scores_are_null_when_nothing_was_evaluable():
    """잴 수 없었던 것을 0.0 으로 채우면 "베이스라인과 동률"처럼 읽힌다."""
    summary = _summary(origins=[], gate_evaluable=False)

    assert summary.gate_improvement_ratio is None
    assert summary.gate_min_detectable_improvement is None
    assert summary.gate_baseline_coverage is None
    assert summary.underpowered_windows == []


def test_console_carries_the_gate_window_power_next_to_the_verdict():
    summary = _summary(
        origins=[_origin(improvement=0.0789, mde=0.0631, paired_t=-3.197, passed=True)],
        gate_passed_at_latest_window=True,
    )

    assert summary.gate_min_detectable_improvement == pytest.approx(0.0631)
    assert summary.gate_baseline_coverage == pytest.approx(0.986)
    assert summary.gate_passed_at_latest_window is True
