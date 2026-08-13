"""홀드아웃 채점 도구 — 베이스라인 표·세그먼트 표·오차 지표 (순수 계산).

여기 있는 것은 **무엇으로 비교하는가**(선언 표)와 **어떻게 점수를 매기는가**(순수 함수)
뿐이다. 어느 구간에서 재는가(창 선택)는
:mod:`app.services.ml_training.award_rate_windows`, 판정식과 조립은
:mod:`app.services.ml_training.award_rate_holdout` 이다. 세 축을 나눠 둔 이유는 하나를
고칠 때 나머지가 따라 흔들리지 않게 하기 위해서다.

RMSE 하나만 내지 않는다. 편향과 잔차 표준편차를 **같은 잔차에서** 나눠 내야, 좋아 보이는
수치가 "치우침이 줄어서"인지 "모양이 좋아져서"인지 구별된다 — 층 구성비나 시간 드리프트가
바뀌기만 해도 전자는 움직인다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.synthetic_experiment import _budget_band_key

__all__ = [
    "AGENCY_BASELINE_MIN_COUNT",
    "BASELINE_SPECS",
    "GATE_BASELINE_NAME",
    "SEGMENT_SPECS",
    "BaselineSpec",
    "ModelScore",
    "SegmentScore",
    "group_mean_predictions",
    "paired_t",
    "rmse_bias_std",
    "segment_scores",
]

# 게이트의 비교 대상 베이스라인 이름(선언 — 리포트와 판정이 같은 문자열을 본다).
GATE_BASELINE_NAME: Final[str] = "category_x_band"

# 발주기관 그룹 평균 베이스라인의 최소 표본. 이보다 얕은 기관은 전역 평균으로 떨어진다
# — 얕은 기관을 자기 평균으로 점추정하면 베이스라인이 부당하게 나빠져 GBM 이 이기기
# 쉬워진다(비교를 유리하게 만들지 않기 위한 값).
AGENCY_BASELINE_MIN_COUNT: Final[int] = 10


@dataclass(frozen=True)
class BaselineSpec:
    """그룹 평균 베이스라인 하나 — 이름, 그룹 키, 최소 표본(§4.5-2 룩업 선언)."""

    name: str
    key: Callable[[AwardRateTrainingRow], str]
    min_count: int = 1


# 베이스라인 표. 새 축을 비교하려면 코드 분기가 아니라 여기 한 줄을 추가한다.
BASELINE_SPECS: Final[tuple[BaselineSpec, ...]] = (
    BaselineSpec(name="global_mean", key=lambda row: ""),
    BaselineSpec(name="category", key=lambda row: row.category),
    BaselineSpec(name="amount_band", key=lambda row: _budget_band_key(row.amount)),
    BaselineSpec(
        name=GATE_BASELINE_NAME,
        key=lambda row: f"{row.category}|{_budget_band_key(row.amount)}",
    ),
    BaselineSpec(
        name="agency",
        key=lambda row: row.agency,
        min_count=AGENCY_BASELINE_MIN_COUNT,
    ),
)


# 홀드아웃을 쪼개 볼 축 표(§4.5-2 선언). 새 축은 코드 분기가 아니라 여기 한 줄이다.
# 공종이 첫 줄인 이유: 승격을 막은 지표가 전체 RMSE 가 아니라 **공종별 편향**이었다.
#
# ``published_floor`` 은 피처가 아니라 **진단 축**이다. 이 축을 리포트에 싣는 목적은 두
# 층(하한 공시/미공시)의 학습·홀드아웃 평균이 같은 것을 가리키는지 보는 것이고, 지금은
# 어긋나 있다(미공시 층 학습 0.9089 vs 홀드아웃 0.9310 — 백필 커버리지가 시간에 기울어
# 있기 때문이다. 상세는 ``AwardRateTrainingRow.published_floor_rate`` docstring).
# 이 계측이 그 어긋남을 잡아냈고, 평평해졌는지 확인하는 것도 같은 계측이다.
SEGMENT_SPECS: Final[tuple[tuple[str, Callable[[AwardRateTrainingRow], str]], ...]] = (
    ("category", lambda row: row.category or "unknown"),
    (
        "published_floor",
        lambda row: "present" if row.published_floor_rate is not None else "absent",
    ),
)


class ModelScore(StrictModel):
    """한 모델의 홀드아웃 성적. RMSE 만 보면 편향과 모양이 구별되지 않는다."""

    name: str
    rmse: float
    bias: float
    residual_std: float
    test_coverage: float = 1.0
    """그룹 평균이 자기 그룹으로 예측한 홀드아웃 비율(나머지는 전역 평균 폴백)."""


class SegmentScore(StrictModel):
    """홀드아웃 한 세그먼트에서 베이스라인과 게이트 모델을 나란히 잰 성적.

    전체 RMSE 하나로는 "어디가 나빠서 못 쓰는가"에 답할 수 없다. 승격 판단을 막은 신호가
    바로 공종별 **편향**이었으므로(전체 개선과 공존한다), 그 축을 리포트가 직접 싣는다.
    세그먼트 예측은 전체 예측 벡터를 **자르기만** 한 것이다 — 세그먼트별로 다시 학습하지
    않으므로 여기 수치는 실제로 나간 예측의 부분집합이다.
    """

    axis: str
    segment: str
    row_count: int
    baseline_rmse: float
    baseline_bias: float
    model_rmse: float
    model_bias: float
    model_residual_std: float


def rmse_bias_std(
    predictions: np.ndarray, targets: np.ndarray
) -> tuple[float, float, float]:
    """(RMSE, 편향, 잔차 표준편차) 한 벌 — 세 지표가 같은 잔차에서 나오게 한다."""
    residuals = predictions - targets
    return (
        float(np.sqrt(np.mean(residuals**2))),
        float(np.mean(residuals)),
        float(np.std(residuals, ddof=1)) if residuals.size > 1 else 0.0,
    )


def paired_t(a: np.ndarray, b: np.ndarray, targets: np.ndarray) -> float:
    """제곱오차 차이(a − b)의 대응 t. 음수면 a 가 낫다. 차이가 0이면 0."""
    differences = ((a - targets) ** 2) - ((b - targets) ** 2)
    deviation = float(np.std(differences, ddof=1)) if differences.size > 1 else 0.0
    if deviation <= 0.0:
        return 0.0
    return float(np.mean(differences) / (deviation / np.sqrt(differences.size)))


def group_mean_predictions(
    spec: BaselineSpec,
    train_rows: Sequence[AwardRateTrainingRow],
    test_rows: Sequence[AwardRateTrainingRow],
    *,
    global_mean: float,
) -> tuple[np.ndarray, float]:
    """그룹 평균 예측과 그 그룹으로 실제 예측된 홀드아웃 비율."""
    totals: dict[str, tuple[float, int]] = {}
    for row in train_rows:
        total, count = totals.get(spec.key(row), (0.0, 0))
        totals[spec.key(row)] = (total + row.value, count + 1)

    predictions: list[float] = []
    covered = 0
    for row in test_rows:
        total, count = totals.get(spec.key(row), (0.0, 0))
        if count >= spec.min_count:
            predictions.append(total / count)
            covered += 1
        else:
            predictions.append(global_mean)
    return np.array(predictions, dtype=float), covered / len(test_rows)


def segment_scores(
    rows: Sequence[AwardRateTrainingRow],
    targets: np.ndarray,
    *,
    baseline_predictions: np.ndarray,
    model_predictions: np.ndarray,
) -> list[SegmentScore]:
    """선언된 축마다 홀드아웃을 쪼개 두 예측을 나란히 채점한다(재학습 없음)."""
    scores: list[SegmentScore] = []
    for axis, key in SEGMENT_SPECS:
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            groups.setdefault(key(row), []).append(index)
        scores.extend(
            _one_segment_score(axis, segment, np.array(indices, dtype=int), targets,
                               baseline_predictions, model_predictions)
            for segment, indices in sorted(groups.items())
        )
    return scores


def _one_segment_score(
    axis: str,
    segment: str,
    selector: np.ndarray,
    targets: np.ndarray,
    baseline_predictions: np.ndarray,
    model_predictions: np.ndarray,
) -> SegmentScore:
    """세그먼트 하나의 성적 — 전체 예측 벡터를 **자르기만** 한다."""
    segment_targets = targets[selector]
    baseline_rmse, baseline_bias, _ = rmse_bias_std(
        baseline_predictions[selector], segment_targets
    )
    model_rmse, model_bias, model_std = rmse_bias_std(
        model_predictions[selector], segment_targets
    )
    return SegmentScore(
        axis=axis,
        segment=segment,
        row_count=int(selector.size),
        baseline_rmse=baseline_rmse,
        baseline_bias=baseline_bias,
        model_rmse=model_rmse,
        model_bias=model_bias,
        model_residual_std=model_std,
    )
