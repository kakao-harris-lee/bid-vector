"""평가 창 정책 — embargo·표본 하한·겹침 0 계약.

여기서 고정하는 것은 **어디서 재는가**다. 판정식(무엇을 재는가)은
``tests/test_award_rate_holdout.py``.

이 파일이 존재하는 이유: 이전 게이트는 비율 분할이라 다섯 origin 의 홀드아웃이 100% 겹쳤고
(실측), 리포트가 그 사실을 드러내지 못했다. 겹침 0과 제외 기록은 이제 **테스트가 고정하는
성질**이다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.settlement_maturity import MaturityWindow
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_windows import (
    EXCLUDED_BEYOND_MAX_ORIGINS,
    EXCLUDED_IMMATURE,
    EXCLUDED_NO_TRAINING_ROWS,
    EXCLUDED_TOO_FEW_ROWS,
    GATE_MATURITY_THRESHOLD,
    GATE_STRATUM,
    WindowPolicy,
    holdout_overlaps,
    plan_evaluation_windows,
    rows_in_window,
    summarize_exclusions,
    summarize_overlaps,
)

_START = datetime(2026, 6, 1, tzinfo=UTC)
_WEEK = timedelta(days=7)


def _window(index: int, *, maturity: float, opened: int = 100) -> MaturityWindow:
    return MaturityWindow(
        start=_START + index * _WEEK,
        end=_START + (index + 1) * _WEEK,
        opened_count=opened,
        settled_count=round(opened * maturity),
    )


def _rows_for(window_indices: list[int], *, per_window: int = 120):
    """창마다 같은 수의 평가 층 행을 심는다(하루 간격으로 창 안에 흩어 놓는다)."""
    rows: list[AwardRateTrainingRow] = []
    for index in window_indices:
        start = _START + index * _WEEK
        for offset in range(per_window):
            rows.append(
                AwardRateTrainingRow(
                    value=0.9,
                    amount=1e8,
                    category="service",
                    agency="가기관",
                    denominator_source=GATE_STRATUM,
                    opened_at=start + timedelta(hours=offset % 168),
                    published_floor_rate=None,
                )
            )
    return rows


def test_immature_windows_are_excluded_with_a_reason():
    """embargo 는 침묵이 아니다 — 뺀 구간과 사유가 리포트에 남아야 한다."""
    rows = _rows_for([-1, 0, 1, 2])
    windows = [
        _window(0, maturity=0.85),
        _window(1, maturity=0.40),
        _window(2, maturity=0.90),
    ]
    plan = plan_evaluation_windows(rows, windows)

    assert [window.start for window in plan.selected] == [
        windows[0].start,
        windows[2].start,
    ]
    immature = [item for item in plan.excluded if item.reason == EXCLUDED_IMMATURE]
    assert [item.window.start for item in immature] == [windows[1].start]
    assert immature[0].evaluation_row_count == 120


def test_the_declared_threshold_is_the_boundary():
    """임계는 손잡이가 아니라 선언이다. 정확히 임계인 창은 통과하고 그 아래는 빠진다."""
    rows = _rows_for([-1, 0, 1])
    plan = plan_evaluation_windows(
        rows,
        [
            _window(0, maturity=GATE_MATURITY_THRESHOLD),
            _window(1, maturity=GATE_MATURITY_THRESHOLD - 0.01),
        ],
    )

    assert len(plan.selected) == 1
    assert plan.selected[0].maturity == pytest.approx(GATE_MATURITY_THRESHOLD)
    assert plan.excluded[0].reason == EXCLUDED_IMMATURE


def test_windows_with_too_few_rows_are_excluded():
    """대응 t 임계 2.58 은 정규근사를 전제한다. 수십 행짜리 창에서 찍히는 통과/실패는
    그 전제가 깨진 채로 나온 값이다."""
    rows = _rows_for([-1]) + _rows_for([0], per_window=200) + _rows_for(
        [1], per_window=20
    )
    plan = plan_evaluation_windows(
        rows, [_window(0, maturity=0.9), _window(1, maturity=0.9)]
    )

    assert len(plan.selected) == 1
    (excluded,) = plan.excluded
    assert excluded.reason == EXCLUDED_TOO_FEW_ROWS
    assert excluded.evaluation_row_count == 20


def test_the_first_window_has_no_training_rows_and_is_excluded():
    """학습측이 비면 베이스라인도 GBM 도 만들 수 없다 — 조용히 0건을 평가하지 않는다."""
    rows = _rows_for([0, 1])
    plan = plan_evaluation_windows(
        rows, [_window(0, maturity=0.9), _window(1, maturity=0.9)]
    )

    assert [window.start for window in plan.selected] == [_window(1, maturity=0.9).start]
    (excluded,) = plan.excluded
    assert excluded.reason == EXCLUDED_NO_TRAINING_ROWS


def test_only_the_most_recent_origins_are_kept_and_the_rest_are_recorded():
    rows = _rows_for([-1, 0, 1, 2, 3])
    windows = [_window(index, maturity=0.9) for index in range(4)]
    plan = plan_evaluation_windows(rows, windows, policy=WindowPolicy(max_origins=2))

    assert [window.start for window in plan.selected] == [
        windows[2].start,
        windows[3].start,
    ]
    dropped = [
        item for item in plan.excluded if item.reason == EXCLUDED_BEYOND_MAX_ORIGINS
    ]
    assert [item.window.start for item in dropped] == [
        windows[0].start,
        windows[1].start,
    ]


def test_the_root_reason_is_recorded_when_several_apply():
    """성숙도 미달이면서 표본도 적은 창은 ``immature`` 로 남는다 — 규칙 표의 순서가 곧
    사유의 우선순위이고, 사후에 "표본이 적어서 뺐다"로 읽히면 embargo 가 보이지 않는다."""
    rows = _rows_for([-1]) + _rows_for([0], per_window=200) + _rows_for(
        [1], per_window=5
    )
    plan = plan_evaluation_windows(
        rows, [_window(0, maturity=0.9), _window(1, maturity=0.2)]
    )

    (excluded,) = plan.excluded
    assert excluded.reason == EXCLUDED_IMMATURE


def test_selected_windows_share_no_holdout_row():
    """겹침 0은 주장이 아니라 측정이다. 이 성질이 깨지면 "모든 origin 통과"가 같은 행을
    여러 번 잰 것이 된다(이전 비율 분할에서 실제로 그랬다)."""
    rows = _rows_for([-1, 0, 1, 2, 3])
    plan = plan_evaluation_windows(
        rows, [_window(index, maturity=0.9) for index in range(4)]
    )

    overlaps = holdout_overlaps(rows, plan.selected)
    assert overlaps  # 창이 둘 이상이라 쌍이 존재한다
    assert all(overlap.row_count == 0 for overlap in overlaps)
    assert all(item.row_count == 0 for item in summarize_overlaps(rows, plan.selected))


def test_holdout_overlap_is_detected_when_windows_do_overlap():
    """계측기 자체가 0만 낼 줄 아는 것이면 위 테스트는 아무것도 지키지 못한다."""
    rows = _rows_for([0, 1])
    wide = MaturityWindow(
        start=_START, end=_START + 2 * _WEEK, opened_count=100, settled_count=90
    )
    overlaps = holdout_overlaps(rows, [wide, _window(1, maturity=0.9)])

    assert len(overlaps) == 1
    assert overlaps[0].row_count == 120


def test_only_the_evaluation_stratum_counts():
    """평가 층 고정은 게이트의 선언이다. 다른 층 행이 창에 있어도 홀드아웃이 아니다."""
    rows = _rows_for([1]) + [
        AwardRateTrainingRow(
            value=0.9,
            amount=1e8,
            category="service",
            agency="가기관",
            denominator_source="reserve-estimate",
            opened_at=_START + _WEEK + timedelta(hours=1),
            published_floor_rate=None,
        )
    ]
    selected = rows_in_window(rows, _window(1, maturity=0.9))

    assert len(selected) == 120
    assert all(row.denominator_source == GATE_STRATUM for row in selected)


def test_exclusion_summary_carries_the_window_and_the_reason():
    rows = _rows_for([0, 1])
    plan = plan_evaluation_windows(
        rows, [_window(0, maturity=0.9), _window(1, maturity=0.3)]
    )
    summaries = summarize_exclusions(plan)

    assert {summary.excluded_reason for summary in summaries} == {
        EXCLUDED_NO_TRAINING_ROWS,
        EXCLUDED_IMMATURE,
    }
    immature = next(
        summary
        for summary in summaries
        if summary.excluded_reason == EXCLUDED_IMMATURE
    )
    assert immature.maturity == pytest.approx(0.3)
    assert immature.opened_count == 100
    assert immature.settled_count == 30


def test_no_mature_window_yields_an_empty_plan_not_a_crash():
    """"지금 데이터로는 정직하게 평가할 수 없다"도 결과다. 빈 계획은 예외가 아니라 사실로
    보고돼야 하고, 호출부가 ``gate_evaluable=false`` 로 옮긴다."""
    rows = _rows_for([0, 1])
    plan = plan_evaluation_windows(
        rows, [_window(0, maturity=0.2), _window(1, maturity=0.3)]
    )

    assert plan.selected == ()
    assert len(plan.excluded) == 2
    assert holdout_overlaps(rows, plan.selected) == []
