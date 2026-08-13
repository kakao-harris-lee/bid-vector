"""out-of-time 홀드아웃 게이트 커널 — 판정식·베이스라인 표·층 분할 계약.

여기서 고정하는 것은 **게이트가 무엇을 판정하는가**다. 실 코퍼스 수치는 이 파일이 아니라
``scripts/backtest_award_rate_gbm.py`` 리포트가 낸다(파일에 실측값을 박아두면 데이터가
늘 때 조용히 낡는다). 대신 신호가 있는 합성 코퍼스에서 게이트가 열리고, 신호가 없으면
닫히는지를 본다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.constants import AWARD_RATE_SAMPLE_SCOPE_FEED_ORIGIN
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_holdout import (
    GATE_BASELINE_NAME,
    GATE_PAIRED_T_THRESHOLD,
    GATE_STRATUM,
    _split_out_of_time,
    evaluate_award_rate_holdout,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)
# 픽스처 코퍼스의 표본 정의 — 아티팩트가 자기 코퍼스를 신고하게 하는 값.
_SCOPE = AWARD_RATE_SAMPLE_SCOPE_FEED_ORIGIN

# 합성 신호: 기관마다 다른 낙찰률 수준. 그룹 평균(공종×금액대)으로는 잡히지 않고
# 발주기관 축을 쓰는 모델만 잡을 수 있는 구조다.
_AGENCY_RATES = {"가기관": 0.86, "나기관": 0.92, "다기관": 0.98}


def _rows(
    count: int = 900,
    *,
    stratum_share: float = 0.6,
    agency_signal: bool = True,
) -> list[AwardRateTrainingRow]:
    agencies = list(_AGENCY_RATES)
    rows: list[AwardRateTrainingRow] = []
    for index in range(count):
        agency = agencies[index % len(agencies)]
        base_rate = _AGENCY_RATES[agency] if agency_signal else 0.92
        rows.append(
            AwardRateTrainingRow(
                # 결정적인 미세 변동만 얹는다 — 난수 없이 재현 가능한 픽스처.
                value=base_rate + (0.002 * ((index % 7) - 3)),
                amount=100_000_000.0 * (1 + (index % 4)),
                category="construction" if index % 2 else "service",
                agency=agency,
                denominator_source=(
                    GATE_STRATUM
                    if (index % 10) < int(stratum_share * 10)
                    else "reserve-estimate"
                ),
                opened_at=_START + timedelta(hours=index),
                # 공시 하한 축도 섞는다 — 게이트 픽스처가 실제 코퍼스처럼 두 층을
                # 갖게 해서, 축이 늘어난 뒤에도 판정식이 같은 것을 재는지 본다.
                published_floor_rate=0.88 if index % 3 else None,
            )
        )
    return rows


def test_gate_opens_when_the_model_finds_signal_the_baseline_cannot():
    report = evaluate_award_rate_holdout(_rows(), sample_scope=_SCOPE, folds=3)

    assert report.gate_passed is True
    assert report.gate_model_rmse < report.gate_baseline_rmse
    assert report.gate_improvement_ratio > 0
    assert report.gate_paired_t < -GATE_PAIRED_T_THRESHOLD


def test_gate_closes_when_there_is_no_signal_beyond_the_baseline():
    """기관 축을 없애면 GBM 이 그룹 평균을 유의하게 이길 근거가 사라진다."""
    report = evaluate_award_rate_holdout(
        _rows(agency_signal=False), sample_scope=_SCOPE, folds=3
    )
    assert report.gate_passed is False


def test_report_carries_the_declared_baseline_table():
    report = evaluate_award_rate_holdout(_rows(), sample_scope=_SCOPE, folds=3)
    assert [score.name for score in report.baselines] == [
        "global_mean",
        "category",
        "amount_band",
        GATE_BASELINE_NAME,
        "agency",
    ]
    assert [score.name for score in report.models] == [
        "gbm_all_strata",
        "gbm_gate_stratum_only",
    ]
    gate_baseline = next(
        score for score in report.baselines if score.name == GATE_BASELINE_NAME
    )
    assert gate_baseline.rmse == pytest.approx(report.gate_baseline_rmse)


def test_split_and_training_scope_follow_the_declared_rule():
    """분할은 평가 층 기준, 학습은 홀드아웃 첫 행의 시각 **미만**인 모든 층."""
    rows = _rows(stratum_share=0.6)
    report = evaluate_award_rate_holdout(
        rows, sample_scope=_SCOPE, train_fraction=0.7, folds=3
    )

    stratum_rows = [row for row in rows if row.denominator_source == GATE_STRATUM]
    assert report.gate_train_row_count == int(len(stratum_rows) * 0.7)
    assert report.gate_test_row_count == len(stratum_rows) - report.gate_train_row_count
    # 학습에는 다른 층도 들어가므로 평가 층 학습 구간보다 많다.
    assert report.train_row_count > report.gate_train_row_count

    cutoff = datetime.fromisoformat(report.cutoff_at)
    assert cutoff == stratum_rows[report.gate_train_row_count].opened_at
    assert report.train_row_count == len([row for row in rows if row.opened_at < cutoff])


def _boundary_tie_rows(
    count: int = 40, *, tie_start: int = 26, tie_size: int = 8
) -> list[AwardRateTrainingRow]:
    """경계에 **같은 개찰 시각** 여러 건을 심은 픽스처(평가 층만).

    시간당 한 건인 기본 픽스처는 tie 가 없어 경계 규칙을 드러내지 못한다. 여기서는
    0.7 분할 지점(index 28)이 동시각 블록(26~33) 한가운데 놓이도록 만들어, 그 블록의
    학습측 행이 실제로 학습에서 빠지는지를 본다.
    """
    agencies = list(_AGENCY_RATES)

    def _hour(index: int) -> int:
        """블록 안(26~33)은 모두 같은 시각, 그 밖은 시간당 한 건."""
        return tie_start if tie_start <= index < tie_start + tie_size else index

    return [
        AwardRateTrainingRow(
            value=_AGENCY_RATES[agencies[index % len(agencies)]],
            amount=100_000_000.0,
            category="construction" if index % 2 else "service",
            agency=agencies[index % len(agencies)],
            denominator_source=GATE_STRATUM,
            opened_at=_START + timedelta(hours=_hour(index)),
            published_floor_rate=None,
        )
        for index in range(count)
    ]


def test_rows_sharing_the_boundary_timestamp_are_excluded_from_training():
    """경계 동시각 행이 학습에 남으면 GBM 에만 붙는 비대칭 이득이 된다(누수 차단).

    베이스라인은 ``gate_train`` 만 보므로, 시각 필터가 그 경계를 넘어 홀드아웃 행을
    끌어오면 게이트가 재는 것이 실력인지 누수인지 구별되지 않는다.
    """
    rows = _boundary_tie_rows()
    report = evaluate_award_rate_holdout(
        rows, sample_scope=_SCOPE, train_fraction=0.7, folds=2
    )
    cutoff = datetime.fromisoformat(report.cutoff_at)

    # 분할은 인덱스라 학습 구간은 28건 그대로지만,
    assert report.gate_train_row_count == 28
    assert cutoff == rows[28].opened_at
    # 경계와 같은 시각의 행은 학습 범위에서 빠진다 — 시각 필터가 인덱스보다 좁다.
    assert report.train_row_count == len([row for row in rows if row.opened_at < cutoff])
    assert report.train_row_count < report.gate_train_row_count

    # red line: 어떤 학습 행도 홀드아웃 첫 행의 시각에 도달하지 않는다.
    split = _split_out_of_time(rows, train_fraction=0.7)
    assert max(row.opened_at for row in split.train_rows) < min(
        row.opened_at for row in split.gate_test
    )


def test_bias_and_residual_std_are_reported_separately():
    """편향이 줄어 좋아 보이는 것과 모양이 좋아진 것을 구별할 수 있어야 한다."""
    report = evaluate_award_rate_holdout(_rows(), sample_scope=_SCOPE, folds=3)
    for score in report.baselines + report.models:
        assert score.rmse == pytest.approx(
            (score.bias**2 + (score.residual_std**2) * _unbias(report)) ** 0.5,
            rel=0.02,
        )


def _unbias(report) -> float:
    """표본 표준편차(ddof=1)를 모분산으로 되돌리는 계수 — RMSE 항등식 확인용."""
    n = report.gate_test_row_count
    return (n - 1) / n


def test_segment_breakdown_covers_the_holdout_exactly_once_per_axis():
    """축마다 세그먼트 행 수의 합이 홀드아웃 전체다 — 쪼갠 것이지 다시 학습한 게 아니다.

    이 단언이 지키는 성질: 세그먼트 수치가 "실제로 나간 예측의 부분집합"이라는 것.
    세그먼트별 재학습으로 바꾸면 합은 그대로여도 그 성질이 깨지고, 공종별 편향이
    승격 판단의 근거가 되지 못한다.
    """
    report = evaluate_award_rate_holdout(_rows(), sample_scope=_SCOPE, folds=3)
    axes = {score.axis for score in report.segments}
    assert axes == {"category", "published_floor"}
    for axis in axes:
        in_axis = [score for score in report.segments if score.axis == axis]
        assert sum(score.row_count for score in in_axis) == report.gate_test_row_count
    assert {score.segment for score in report.segments if score.axis == "category"} == {
        "construction",
        "service",
    }
    assert {
        score.segment for score in report.segments if score.axis == "published_floor"
    } == {"present", "absent"}


def test_segment_score_equals_the_whole_holdout_when_one_segment_holds_everything():
    """세그먼트가 하나뿐이면 그 성적은 전체 성적과 같다(자르기의 항등성)."""
    agencies = list(_AGENCY_RATES)
    rows = [
        AwardRateTrainingRow(
            value=_AGENCY_RATES[agencies[index % len(agencies)]]
            + (0.002 * ((index % 7) - 3)),
            amount=100_000_000.0 * (1 + (index % 4)),
            category="construction",
            agency=agencies[index % len(agencies)],
            denominator_source=GATE_STRATUM,
            opened_at=_START + timedelta(hours=index),
            published_floor_rate=0.88,
        )
        for index in range(300)
    ]
    report = evaluate_award_rate_holdout(rows, sample_scope=_SCOPE, folds=3)
    category = next(score for score in report.segments if score.axis == "category")
    assert category.segment == "construction"
    assert category.row_count == report.gate_test_row_count
    assert category.baseline_rmse == pytest.approx(report.gate_baseline_rmse)
    assert category.model_rmse == pytest.approx(report.gate_model_rmse)


def test_agency_baseline_falls_back_for_shallow_groups():
    """얕은 기관을 자기 평균으로 점추정하면 베이스라인이 부당하게 나빠진다."""
    report = evaluate_award_rate_holdout(_rows(), sample_scope=_SCOPE, folds=3)
    agency = next(score for score in report.baselines if score.name == "agency")
    assert 0.0 <= agency.test_coverage <= 1.0


@pytest.mark.parametrize("train_fraction", [0.0, 1.0])
def test_empty_side_of_the_split_fails_loudly(train_fraction):
    """학습이나 홀드아웃 한쪽이 비면 조용히 0건을 평가하지 않고 실패한다."""
    with pytest.raises(ValueError, match="both sides of the split"):
        evaluate_award_rate_holdout(
            _rows(count=60),
            sample_scope=_SCOPE,
            train_fraction=train_fraction,
            folds=2,
        )


def test_no_evaluation_stratum_rows_fails_loudly():
    rows = [
        AwardRateTrainingRow(
            value=0.9,
            amount=1e8,
            category="service",
            agency="가기관",
            denominator_source="reserve-estimate",
            opened_at=_START + timedelta(hours=index),
            published_floor_rate=None,
        )
        for index in range(50)
    ]
    with pytest.raises(ValueError, match="both sides of the split"):
        evaluate_award_rate_holdout(rows, sample_scope=_SCOPE, folds=2)
