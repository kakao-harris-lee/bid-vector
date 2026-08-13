"""백테스트 리포트 공시 계약 — 회계 불변식·겹침 회계·판정 이름.

여기서 고정하는 것은 "리포트가 스스로 말하는가"다. 판정식은
``tests/test_award_rate_holdout.py``, 창 선택은 ``tests/test_award_rate_windows.py``,
진단 계산은 ``tests/test_award_rate_diagnostics.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.settlement_maturity import MaturityWindow
from app.services.ml_training.award_rate_backtest_report import (
    BacktestReport,
    build_backtest_report,
)
from app.services.ml_training.award_rate_diagnostics import GATE_STABILITY_SEEDS
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_windows import (
    GATE_STRATUM,
    WindowPolicy,
    plan_evaluation_windows,
)

_START = datetime(2026, 6, 1, tzinfo=UTC)
_WEEK = timedelta(days=7)
_AGENCY_RATES = {"가기관": 0.86, "나기관": 0.92, "다기관": 0.98}


def _window(index: int, *, maturity: float = 0.9) -> MaturityWindow:
    return MaturityWindow(
        start=_START + index * _WEEK,
        end=_START + (index + 1) * _WEEK,
        opened_count=1000,
        settled_count=int(1000 * maturity),
    )


def _rows(window_indices: list[int], *, per_window: int = 150):
    """창마다 평가 층 행을 심는다(기관 신호가 있어 GBM 이 이길 여지가 있다)."""
    agencies = list(_AGENCY_RATES)
    rows: list[AwardRateTrainingRow] = []
    for index in window_indices:
        start = _START + index * _WEEK
        for offset in range(per_window):
            agency = agencies[offset % len(agencies)]
            rows.append(
                AwardRateTrainingRow(
                    value=_AGENCY_RATES[agency] + 0.002 * ((offset % 7) - 3),
                    amount=1e8 * (1 + (offset % 4)),
                    category="construction" if offset % 2 else "service",
                    agency=agency,
                    denominator_source=GATE_STRATUM,
                    opened_at=start + timedelta(hours=offset % 168),
                    published_floor_rate=None,
                )
            )
    return rows


def _report(rows, windows, **kwargs) -> BacktestReport:
    defaults = {
        "maturity_observation_count": 1000,
        "folds": 2,
        "seed": 20260812,
        "feed_origin_only": True,
        "stability": False,
    }
    return build_backtest_report(
        rows,
        plan_evaluation_windows(
            rows, windows, policy=WindowPolicy(max_origins=kwargs.pop("max_origins", 5))
        ),
        **{**defaults, **kwargs},
    )


def test_every_evaluation_row_is_accounted_for():
    """회계 불변식: 모든 평가 층 행은 창에 귀속되거나 제외 회계에 잡힌다.

    성숙도 모집단은 항상 피드 공고인데 코퍼스는 표본 정의에 따라 넓어질 수 있다. 그 차이가
    조용히 사라지면 "제외 회계"가 전수를 설명한다는 인상만 남는다.
    """
    rows = _rows([0, 1, 2])
    report = _report(rows, [_window(index) for index in range(3)])

    stratum_rows = sum(1 for row in rows if row.denominator_source == GATE_STRATUM)
    accounted = (
        sum(item.evaluation_row_count for item in report.evaluation_windows)
        + report.excluded_row_count
    )
    assert report.unaccounted_row_count == 0
    assert accounted == stratum_rows


def test_rows_outside_every_maturity_window_are_reported_as_unaccounted():
    """성숙도 표가 코퍼스를 다 덮지 못하면 그 사실이 수로 남는다(침묵 금지).

    ``--no-feed-origin-only`` 가 만드는 상태를 그대로 재현한 것이다 — 코퍼스만 넓어지고
    성숙도 모집단은 피드로 고정되므로 어느 주에도 속하지 않는 행이 생긴다.
    """
    rows = _rows([0, 1]) + _rows([9], per_window=40)
    report = _report(rows, [_window(0), _window(1)])

    assert report.unaccounted_row_count == 40


def test_overlap_zero_is_distinguishable_from_no_pairs_measured():
    """창이 하나뿐일 때의 0은 "재서 0"이 아니라 "잴 쌍이 없었다"이다."""
    single = _report(_rows([0, 1]), [_window(0), _window(1)])
    assert single.holdout_overlap_pair_count == 0
    assert single.holdout_overlap_row_count == 0

    several = _report(_rows([0, 1, 2, 3]), [_window(index) for index in range(4)])
    assert several.holdout_overlap_pair_count == 3
    assert several.holdout_overlap_row_count == 0


def test_the_headline_verdict_name_carries_its_limit():
    """``gate_passed`` 로 두면 창 하나의 성적이 "게이트 통과"로 인용된다."""
    report = _report(_rows([0, 1, 2]), [_window(index) for index in range(3)])

    assert "gate_passed" not in report.model_dump()
    assert isinstance(report.gate_passed_at_latest_window, bool)
    assert isinstance(report.gate_passed_at_all_origins, bool)
    assert report.gate_window_start == report.evaluation_windows[-1].start


def test_no_evaluable_window_never_reads_as_a_pass():
    """잴 수 없었던 것이 통과로 보이면 안 된다."""
    rows = _rows([0, 1])
    report = _report(rows, [_window(0, maturity=0.2), _window(1, maturity=0.3)])

    assert report.gate_evaluable is False
    assert report.gate_passed_at_latest_window is False
    assert report.gate_passed_at_all_origins is False
    assert report.gate_window_start is None
    assert report.window_stability == []


def test_stability_sweep_includes_the_headline_seed():
    """헤드라인 판정을 낸 seed 가 안정성 표에 없으면 두 수치가 다른 실행에서 온 것이 된다."""
    rows = _rows([0, 1])
    report = _report(rows, [_window(0), _window(1)], stability=True, seed=42)

    assert report.stability_seeds[0] == 42
    assert set(report.stability_seeds) >= {42, *GATE_STABILITY_SEEDS}
    (stability,) = report.window_stability
    assert stability.trial_count == len(report.stability_seeds)
    assert 42 in [trial.seed for trial in stability.trials]
    assert stability.window_start == report.gate_window_start


def test_stability_is_absent_when_not_measured():
    """재지 않은 것을 "일관"으로 보이게 두지 않는다."""
    report = _report(_rows([0, 1]), [_window(0), _window(1)], stability=False)
    assert report.stability_seeds == []
    assert report.window_stability == []


def test_report_declares_the_policy_it_ran_under():
    """수치를 다시 읽는 사람이 어떤 임계 위에서 나온 것인지 알아야 한다."""
    report = _report(_rows([0, 1]), [_window(0), _window(1)], max_origins=3)

    assert report.maturity_threshold == pytest.approx(0.70)
    assert report.min_evaluation_rows == 100
    assert report.max_origins == 3
    assert report.gate_stratum == GATE_STRATUM
    assert report.gate_paired_t_threshold == pytest.approx(2.58)


def test_windows_carry_maturity_and_exclusion_reasons():
    rows = _rows([0, 1, 2])
    report = _report(
        rows, [_window(0), _window(1, maturity=0.4), _window(2)]
    )

    assert [item.excluded_reason for item in report.evaluation_windows] == [None]
    assert "immature" in {item.excluded_reason for item in report.excluded_windows}
    assert all(item.opened_count > 0 for item in report.excluded_windows)
