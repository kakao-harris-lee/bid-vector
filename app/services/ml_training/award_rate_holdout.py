"""낙찰률 GBM 의 out-of-time 홀드아웃 평가 — 승격 게이트의 계산 커널 (Phase 2).

게이트가 무엇을 판정하는가
--------------------------
사전 선언된 판정식은 하나다: **``clean-base`` 층의 out-of-time 홀드아웃에서 GBM 의 RMSE
가 ``category × amount band`` 그룹 평균보다 유의하게 낮은가.** 그 그룹 평균이 비교
대상인 이유는 세 축(공종·금액대·발주기관) 중 상호작용 없이 가장 강한 조합이기 때문이고,
GBM 이 그것을 못 넘으면 부스팅은 값을 더하지 못한 것이다.

평가 층을 ``clean-base`` 로 고정하는 이유
-----------------------------------------
학습 타깃의 ``ok`` 는 동질 집단이 아니다(``clean-base`` = 관측된 기초금액,
``reserve-estimate`` = 복수예비가격 midpoint 로 복구한 추정 기초금액). 두 층은 라벨
평균이 공종별로 갈리고 산포도 다르므로, 섞어서 잰 RMSE 는 층 구성비가 바뀌기만 해도
움직인다. 서빙이 마주하는 것은 관측된 기초금액이므로 **평가도 그 층에서** 한다.

학습 표본은 두 층을 모두 쓰되 ``denominator_source`` 를 통제 변수로 싣고, 서빙은 그
축을 ``clean-base`` 로 고정한다. 층을 통제하지 않고 뭉치면 예측 수준이 두 층의 혼합
평균으로 내려앉는데, 그것이 특정 홀드아웃에서 더 좋아 보일 수 있다 — 층 간 평균 차이가
그 구간의 시간 드리프트와 우연히 같은 방향일 때다. 그래서 이 커널은 RMSE 만이 아니라
**편향과 잔차 표준편차를 나눠** 보고한다: 편향이 줄어 좋아 보이는 것과 모양이 좋아진
것을 구별할 수 있어야 판단이 흔들리지 않는다.

유의성은 제곱오차 차이의 대응 t 통계량이다(음수면 앞 모델이 낫다). 표본이 수천이라
정규근사가 성립하고, 같은 홀드아웃 행을 두 모델이 공유하므로 대응 검정이 맞다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np
from pydantic import Field

from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_gbm import (
    AwardRateTrainingRow,
    DEFAULT_ENCODING_FOLDS,
    DEFAULT_TRAINING_SEED,
    train_award_rate_gbm,
)
from app.services.synthetic_experiment import _budget_band_key

__all__ = [
    "GATE_BASELINE_NAME",
    "GATE_MODEL_NAME",
    "AwardRateHoldoutReport",
    "ModelScore",
    "SegmentScore",
    "evaluate_award_rate_holdout",
]

# 게이트의 비교 대상 베이스라인 이름(선언 — 리포트와 판정이 같은 문자열을 본다).
GATE_BASELINE_NAME: Final[str] = "category_x_band"

# 게이트에 오르는 GBM 변형 이름. 나란히 재는 보수 변형(``gbm_gate_stratum_only``)은
# 참고용이고, 판정은 이 이름의 성적으로만 한다.
GATE_MODEL_NAME: Final[str] = "gbm_all_strata"

# 평가 층. 서빙이 마주하는 축이며, 사전 선언된 베이스라인 표도 이 층에서 측정됐다.
GATE_STRATUM: Final[str] = "clean-base"

# 발주기관 그룹 평균 베이스라인의 최소 표본. 이보다 얕은 기관은 전역 평균으로 떨어진다
# — 얕은 기관을 자기 평균으로 점추정하면 베이스라인이 부당하게 나빠져 GBM 이 이기기
# 쉬워진다(비교를 유리하게 만들지 않기 위한 값).
AGENCY_BASELINE_MIN_COUNT: Final[int] = 10

# 게이트의 유의성 임계(대응 t 의 절대값). 홀드아웃이 수천 행이라 정규근사가 성립하고,
# 2.58 은 양측 1% 수준이다. 사후에 느슨하게 만들지 않는다.
GATE_PAIRED_T_THRESHOLD: Final[float] = 2.58


@dataclass(frozen=True)
class _BaselineSpec:
    """그룹 평균 베이스라인 하나 — 이름, 그룹 키, 최소 표본(§4.5-2 룩업 선언)."""

    name: str
    key: Callable[[AwardRateTrainingRow], str]
    min_count: int = 1


# 베이스라인 표. 새 축을 비교하려면 코드 분기가 아니라 여기 한 줄을 추가한다.
_BASELINE_SPECS: Final[tuple[_BaselineSpec, ...]] = (
    _BaselineSpec(name="global_mean", key=lambda row: ""),
    _BaselineSpec(name="category", key=lambda row: row.category),
    _BaselineSpec(name="amount_band", key=lambda row: _budget_band_key(row.amount)),
    _BaselineSpec(
        name=GATE_BASELINE_NAME,
        key=lambda row: f"{row.category}|{_budget_band_key(row.amount)}",
    ),
    _BaselineSpec(
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
_SEGMENT_SPECS: Final[tuple[tuple[str, Callable[[AwardRateTrainingRow], str]], ...]] = (
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


class AwardRateHoldoutReport(StrictModel):
    """한 cutoff 에서의 전체 비교 + 게이트 판정."""

    cutoff_at: str
    """홀드아웃 **첫 행**의 개찰 시각. 학습 범위는 이 시각 미만이고, 같은 시각의 행은
    학습측이라도 제외된다(경계 동시각 누수 차단 — :func:`_split_out_of_time`)."""
    train_row_count: int
    gate_train_row_count: int
    gate_test_row_count: int
    train_mean: float
    test_mean: float
    test_std: float
    baselines: list[ModelScore] = Field(default_factory=list)
    models: list[ModelScore] = Field(default_factory=list)
    segments: list[SegmentScore] = Field(default_factory=list)
    """게이트 베이스라인 vs 게이트 모델을 축별로 쪼갠 성적(:data:`_SEGMENT_SPECS`)."""
    gate_baseline_rmse: float
    gate_model_rmse: float
    gate_improvement_ratio: float
    """(베이스라인 − 모델) ÷ 베이스라인. 양수면 모델이 낫다."""
    gate_paired_t: float
    """제곱오차 차이(모델 − 베이스라인)의 대응 t. 음수면 모델이 낫다."""
    gate_passed: bool


def _rmse_bias_std(predictions: np.ndarray, targets: np.ndarray) -> tuple[float, float, float]:
    """(RMSE, 편향, 잔차 표준편차) 한 벌 — 세 지표가 같은 잔차에서 나오게 한다."""
    residuals = predictions - targets
    return (
        float(np.sqrt(np.mean(residuals**2))),
        float(np.mean(residuals)),
        float(np.std(residuals, ddof=1)) if residuals.size > 1 else 0.0,
    )


def _paired_t(a: np.ndarray, b: np.ndarray, targets: np.ndarray) -> float:
    """제곱오차 차이(a − b)의 대응 t. 음수면 a 가 낫다. 차이가 0이면 0."""
    differences = ((a - targets) ** 2) - ((b - targets) ** 2)
    deviation = float(np.std(differences, ddof=1)) if differences.size > 1 else 0.0
    if deviation <= 0.0:
        return 0.0
    return float(np.mean(differences) / (deviation / np.sqrt(differences.size)))


def _group_mean_predictions(
    spec: _BaselineSpec,
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


def _gbm_predictions(
    train_rows: Sequence[AwardRateTrainingRow],
    test_rows: Sequence[AwardRateTrainingRow],
    *,
    folds: int,
    seed: int,
    sample_scope: str,
) -> np.ndarray:
    """학습 → 아티팩트 → 복원 → 예측. **서빙과 같은 경로**로 홀드아웃을 예측한다.

    학습기의 부스터를 그대로 쓰지 않고 아티팩트를 거치는 것은 의도된 우회다: 직렬화·
    복원·피처 재조립까지 포함해야 이 수치가 서빙에서 나올 수치와 같다고 말할 수 있다.

    ⚠ "같은 경로"의 알려진 예외 하나 (공시)
    ----------------------------------------
    이 함수는 ``predict_rates`` 를 직접 부르므로 predictor 의 **미학습 공종 가드**
    (:func:`~app.ai.predictors.award_rate_gbm.unlearned_category_reason`)를 지나지 않는다.
    즉 서빙이라면 거부했을 공종의 행도 리포트 수치에 들어갈 수 있다. 현재 영향은 0 이다 —
    실측상 임계 미만 공종(general 6행)은 전부 학습측에 있고 게이트 홀드아웃은
    construction/service 뿐이다. 얕은 공종이 홀드아웃에 들어오는 시점에는 "서빙은 거부하고
    측정은 포함"이 되므로 그때 가드를 이 경로에도 태워야 한다(동작 변경이라 별도 트랙).
    """
    from app.ai.predictors.award_rate_gbm import load_award_rate_gbm_model

    artifact = train_award_rate_gbm(
        list(train_rows), sample_scope=sample_scope, folds=folds, seed=seed
    )
    model = load_award_rate_gbm_model(artifact)
    return np.array(
        model.predict_rates(
            [(row.amount, row.category, row.agency) for row in test_rows]
        ),
        dtype=float,
    )


@dataclass(frozen=True)
class _HoldoutSplit:
    """평가 층 기준 out-of-time 분할 한 벌.

    ``gate_train``/``gate_test`` 는 평가 층(``clean-base``)만이고, ``train_rows`` 는
    ``cutoff_at`` **미만**의 모든 층이다. 두 범위를 한 값으로 묶어 두면 이후 단계가 어느
    범위를 쓰는지 인자에서 드러나고, 베이스라인이 실수로 전 층 학습 구간을 보는 일이 없다.

    ``cutoff_at`` 은 **홀드아웃 첫 행의 개찰 시각**이다(학습 마지막 행의 시각이 아니다) —
    :func:`_split_out_of_time` 의 "경계 동시각" 절 참조.
    """

    gate_train: list[AwardRateTrainingRow]
    gate_test: list[AwardRateTrainingRow]
    train_rows: list[AwardRateTrainingRow]
    cutoff_at: datetime


def _split_out_of_time(
    rows: Sequence[AwardRateTrainingRow], *, train_fraction: float
) -> _HoldoutSplit:
    """평가 층의 ``train_fraction`` 지점에서 잘라 out-of-time 분할을 만든다.

    경계 동시각(tie) 규칙
    ---------------------
    분할 자체는 **인덱스**인데 학습 범위는 **시각**으로 거른다. 이 둘이 어긋나면 경계와
    같은 ``opened_at`` 을 가진 홀드아웃 행이 GBM 학습에도 들어간다(``<= gate_train[-1]``
    규칙의 결함). 베이스라인은 ``gate_train`` 만 보므로 그 누수는 **GBM 에만 붙는 비대칭
    이득**이 되어 게이트가 재는 것이 실력인지 누수인지 구별되지 않는다.

    그래서 학습 범위를 **홀드아웃 첫 행의 시각 미만**으로 자른다. 같은 시각의 학습측 행도
    함께 빠지는데, 그 방향이 안전하다: 후보에게 불리한 쪽이지 유리한 쪽이 아니다.

    Raises:
        ValueError: 평가 층의 학습/홀드아웃 어느 한쪽이라도 비었을 때. 조용히 0건을
            평가하면 게이트가 "통과"로 보일 수 있으므로 크게 실패한다.
    """
    ordered = sorted(rows, key=lambda row: row.opened_at)
    stratum = [row for row in ordered if row.denominator_source == GATE_STRATUM]
    split_index = int(len(stratum) * train_fraction)
    gate_train, gate_test = stratum[:split_index], stratum[split_index:]
    if not gate_train or not gate_test:
        raise ValueError(
            f"Award-rate holdout needs rows on both sides of the split "
            f"(stratum={len(stratum)}, fraction={train_fraction})."
        )
    cutoff_at = gate_test[0].opened_at
    return _HoldoutSplit(
        gate_train=gate_train,
        gate_test=gate_test,
        train_rows=[row for row in ordered if row.opened_at < cutoff_at],
        cutoff_at=cutoff_at,
    )


def _baseline_scores(
    split: _HoldoutSplit, targets: np.ndarray, *, global_mean: float
) -> tuple[list[ModelScore], np.ndarray]:
    """선언된 베이스라인 표를 순서대로 채점하고 게이트 비교용 예측을 함께 낸다.

    그룹 평균 베이스라인은 **평가 층의 학습 구간**으로만 만든다. 사전 선언된 베이스라인
    표가 그렇게 측정됐고, 폴백 평균도 같은 표본에서 나와야 한 표 안의 다섯 줄이 서로
    비교 가능하다.
    """
    scores: list[ModelScore] = []
    gate_predictions: np.ndarray | None = None
    for spec in _BASELINE_SPECS:
        predictions, coverage = _group_mean_predictions(
            spec, split.gate_train, split.gate_test, global_mean=global_mean
        )
        rmse, bias, residual_std = _rmse_bias_std(predictions, targets)
        scores.append(
            ModelScore(
                name=spec.name,
                rmse=rmse,
                bias=bias,
                residual_std=residual_std,
                test_coverage=coverage,
            )
        )
        if spec.name == GATE_BASELINE_NAME:
            gate_predictions = predictions
    if gate_predictions is None:  # pragma: no cover - 표 선언이 보장한다
        raise ValueError(f"Baseline table has no {GATE_BASELINE_NAME!r} entry.")
    return scores, gate_predictions


def _model_scores(
    split: _HoldoutSplit,
    targets: np.ndarray,
    *,
    folds: int,
    seed: int,
    sample_scope: str,
) -> tuple[list[ModelScore], np.ndarray]:
    """GBM 두 변형을 채점하고 게이트 후보(``gbm_all_strata``) 예측을 함께 낸다.

    두 학습 표본을 나란히 잰다: 층을 통제한 전체 표본(승격 후보)과, 평가 층만으로
    학습한 보수 변형. 둘의 차이가 "다른 층을 섞어 얻은 것"의 크기다.
    """
    scores: list[ModelScore] = []
    gate_predictions: np.ndarray | None = None
    for name, fit_rows in (
        (GATE_MODEL_NAME, split.train_rows),
        ("gbm_gate_stratum_only", split.gate_train),
    ):
        predictions = _gbm_predictions(
            fit_rows,
            split.gate_test,
            folds=folds,
            seed=seed,
            sample_scope=sample_scope,
        )
        rmse, bias, residual_std = _rmse_bias_std(predictions, targets)
        scores.append(
            ModelScore(name=name, rmse=rmse, bias=bias, residual_std=residual_std)
        )
        if name == GATE_MODEL_NAME:
            gate_predictions = predictions
    if gate_predictions is None:  # pragma: no cover - 위 루프가 보장한다
        raise ValueError("Gate model produced no predictions.")
    return scores, gate_predictions


def _segment_scores(
    rows: Sequence[AwardRateTrainingRow],
    targets: np.ndarray,
    *,
    baseline_predictions: np.ndarray,
    model_predictions: np.ndarray,
) -> list[SegmentScore]:
    """선언된 축마다 홀드아웃을 쪼개 두 예측을 나란히 채점한다(재학습 없음)."""
    scores: list[SegmentScore] = []
    for axis, key in _SEGMENT_SPECS:
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            groups.setdefault(key(row), []).append(index)
        for segment, indices in sorted(groups.items()):
            selector = np.array(indices, dtype=int)
            segment_targets = targets[selector]
            baseline_rmse, baseline_bias, _ = _rmse_bias_std(
                baseline_predictions[selector], segment_targets
            )
            model_rmse, model_bias, model_std = _rmse_bias_std(
                model_predictions[selector], segment_targets
            )
            scores.append(
                SegmentScore(
                    axis=axis,
                    segment=segment,
                    row_count=len(indices),
                    baseline_rmse=baseline_rmse,
                    baseline_bias=baseline_bias,
                    model_rmse=model_rmse,
                    model_bias=model_bias,
                    model_residual_std=model_std,
                )
            )
    return scores


def _named_rmse(scores: Sequence[ModelScore], name: str) -> float:
    """채점 표에서 이름으로 RMSE 를 꺼낸다 — 게이트의 두 항이 같은 식을 쓰게 한다."""
    return float(next(score.rmse for score in scores if score.name == name))


def _build_report(
    split: _HoldoutSplit, *, folds: int, seed: int, sample_scope: str
) -> AwardRateHoldoutReport:
    """분할 한 벌에서 베이스라인·모델을 채점하고 게이트 판정까지 조립한다."""
    targets = np.array([row.value for row in split.gate_test], dtype=float)
    gate_train_mean = float(np.mean([row.value for row in split.gate_train]))
    baselines, gate_baseline_predictions = _baseline_scores(
        split, targets, global_mean=gate_train_mean
    )
    models, gate_model_predictions = _model_scores(
        split, targets, folds=folds, seed=seed, sample_scope=sample_scope
    )

    gate_baseline_rmse = _named_rmse(baselines, GATE_BASELINE_NAME)
    gate_model_rmse = _named_rmse(models, GATE_MODEL_NAME)
    paired_t = _paired_t(gate_model_predictions, gate_baseline_predictions, targets)
    return AwardRateHoldoutReport(
        cutoff_at=split.cutoff_at.isoformat(),
        train_row_count=len(split.train_rows),
        gate_train_row_count=len(split.gate_train),
        gate_test_row_count=len(split.gate_test),
        train_mean=gate_train_mean,
        test_mean=float(np.mean(targets)),
        test_std=float(np.std(targets, ddof=1)) if targets.size > 1 else 0.0,
        baselines=baselines,
        models=models,
        segments=_segment_scores(
            split.gate_test,
            targets,
            baseline_predictions=gate_baseline_predictions,
            model_predictions=gate_model_predictions,
        ),
        gate_baseline_rmse=gate_baseline_rmse,
        gate_model_rmse=gate_model_rmse,
        gate_improvement_ratio=(
            (gate_baseline_rmse - gate_model_rmse) / gate_baseline_rmse
            if gate_baseline_rmse > 0
            else 0.0
        ),
        gate_paired_t=paired_t,
        gate_passed=gate_model_rmse < gate_baseline_rmse
        and paired_t < -GATE_PAIRED_T_THRESHOLD,
    )


def evaluate_award_rate_holdout(
    rows: Sequence[AwardRateTrainingRow],
    *,
    sample_scope: str,
    train_fraction: float = 0.70,
    folds: int = DEFAULT_ENCODING_FOLDS,
    seed: int = DEFAULT_TRAINING_SEED,
) -> AwardRateHoldoutReport:
    """out-of-time 홀드아웃에서 베이스라인들과 GBM 두 변형을 비교한다.

    분할은 **평가 층**(``clean-base``)의 행 수 기준이다. 그 층의 ``train_fraction``
    지점에서 자르고, 학습에는 **홀드아웃 첫 행의 개찰 시각 미만**인 모든 ``ok`` 행을
    쓴다(두 층 모두 — 층 구분은 통제 변수로 들어간다). 경계와 같은 시각의 행은 학습측
    이라도 제외된다(:func:`_split_out_of_time` 의 tie 규칙).

    Args:
        rows: 개찰 시각 오름차순 학습 행(로더가 이미 정렬해 준다).
        sample_scope: 이 행들의 표본 정의. 중간 아티팩트가 자기 코퍼스를 정직하게 신고하게
            하려고 받는다 — 이 경로의 아티팩트는 디스크에 남지 않지만, 계약을 통과해야
            하는 것은 서빙과 같으므로 여기서만 예외를 두면 그 round-trip 이 느슨해진다.
        train_fraction: 평가 층에서 학습 구간이 차지할 비율.
        folds: out-of-fold 인코딩·잔차의 분할 수.
        seed: 재현성 seed.

    Returns:
        :class:`AwardRateHoldoutReport`.

    Raises:
        ValueError: 평가 층의 학습/홀드아웃 어느 한쪽이라도 비었을 때.
    """
    return _build_report(
        _split_out_of_time(rows, train_fraction=train_fraction),
        folds=folds,
        seed=seed,
        sample_scope=sample_scope,
    )
