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

유의성은 제곱오차 차이의 대응 t 통계량이다(음수면 앞 모델이 낫다). 같은 홀드아웃 행을 두
모델이 공유하므로 대응 검정이 맞고, 정규근사가 성립할 만큼의 표본은 창 선택 정책
(:data:`~app.services.ml_training.award_rate_windows.GATE_MIN_EVALUATION_ROWS`)이 보장한다.

평가 구간은 **선언된 창**이다 (Phase 2c)
----------------------------------------
이 커널은 창 하나(``[start, end)``)를 받아 그 안에서만 잰다. 창을 어떻게 고르는지는
:mod:`app.services.ml_training.award_rate_windows` 의 정책이고(성숙도 embargo + 표본 하한),
여기서는 **그 창이 곧 cutoff** 다: 학습은 ``opened_at < window.start``, 평가는 창 안.
비율 분할이 시간에 몰린 코퍼스에서 같은 구간으로 붕괴하던 문제와, 경계 동시각 행이 GBM
학습에만 새던 문제가 이 규칙 하나로 함께 닫힌다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np
from pydantic import Field

from app.domain.settlement_maturity import MaturityWindow
from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_gbm import (
    AwardRateTrainingRow,
    DEFAULT_ENCODING_FOLDS,
    DEFAULT_TRAINING_SEED,
    train_award_rate_gbm,
)
from app.services.ml_training.award_rate_scoring import (
    BASELINE_SPECS,
    GATE_BASELINE_NAME,
    ModelScore,
    SegmentScore,
    group_mean_predictions,
    paired_t,
    rmse_bias_std,
    segment_scores,
)
from app.services.ml_training.award_rate_windows import GATE_STRATUM, rows_in_window

__all__ = [
    "GATE_BASELINE_NAME",
    "GATE_MODEL_NAME",
    "GATE_STRATUM",
    "AwardRateHoldoutReport",
    "ModelScore",
    "SegmentScore",
    "evaluate_award_rate_holdout",
]


# 게이트에 오르는 GBM 변형 이름. 나란히 재는 보수 변형(``gbm_gate_stratum_only``)은
# 참고용이고, 판정은 이 이름의 성적으로만 한다.
GATE_MODEL_NAME: Final[str] = "gbm_all_strata"

# 게이트의 유의성 임계(대응 t 의 절대값). 2.58 은 양측 1% 수준이다. 사후에 느슨하게
# 만들지 않는다.
GATE_PAIRED_T_THRESHOLD: Final[float] = 2.58


class AwardRateHoldoutReport(StrictModel):
    """한 평가 창에서의 전체 비교 + 게이트 판정.

    창의 경계와 성숙도를 함께 싣는 이유: 이 수치를 나중에 다시 읽는 사람이 **무엇 위에서
    나온 수치인지**를 리포트만으로 알 수 있어야 한다. 이번 트랙 전체가 그게 없어서 생긴
    문제다(비율 origin 다섯이 같은 5일 구간으로 붕괴한 것을 리포트가 드러내지 못했다).
    """

    window_start: str
    """평가 창의 시작(포함). 학습 범위의 상한이기도 하다."""
    window_end: str
    """평가 창의 끝(제외)."""
    window_maturity: float
    """이 창의 정산 비율 — 그 구간에 개찰된 공고 중 결과를 아는 비율."""
    window_opened_count: int
    """창의 성숙도 분모(개찰된 공고 수). 비율만으로는 4건과 4,000건이 구별되지 않는다."""
    window_settled_count: int
    """창의 성숙도 분자(결과를 아는 공고 수)."""
    cutoff_at: str
    """학습 범위의 상한 = ``window_start``. 학습은 이 시각 **미만**이고 평가는 이 시각
    이상이라, 경계 동시각 행이 양쪽에 동시에 들어가지 않는다."""
    train_row_count: int
    gate_train_row_count: int
    gate_test_row_count: int
    train_mean: float
    test_mean: float
    test_std: float
    baselines: list[ModelScore] = Field(default_factory=list)
    models: list[ModelScore] = Field(default_factory=list)
    segments: list[SegmentScore] = Field(default_factory=list)
    """게이트 베이스라인 vs 게이트 모델을 축별로 쪼갠 성적(:data:`~app.services.ml_training.award_rate_scoring.SEGMENT_SPECS`)."""
    gate_baseline_rmse: float
    gate_model_rmse: float
    gate_improvement_ratio: float
    """(베이스라인 − 모델) ÷ 베이스라인. 양수면 모델이 낫다."""
    gate_paired_t: float
    """제곱오차 차이(모델 − 베이스라인)의 대응 t. 음수면 모델이 낫다."""
    gate_passed: bool


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
    """평가 창 하나에 대한 out-of-time 분할 한 벌.

    ``gate_train``/``gate_test`` 는 평가 층(``clean-base``)만이고, ``train_rows`` 는
    ``window.start`` **미만**의 모든 층이다. 두 범위를 한 값으로 묶어 두면 이후 단계가 어느
    범위를 쓰는지 인자에서 드러나고, 베이스라인이 실수로 전 층 학습 구간을 보는 일이 없다.
    """

    window: MaturityWindow
    gate_train: list[AwardRateTrainingRow]
    gate_test: list[AwardRateTrainingRow]
    train_rows: list[AwardRateTrainingRow]

    @property
    def cutoff_at(self) -> datetime:
        """학습 범위의 상한 = 평가 창의 시작. **선언된 시각이지 관측값이 아니다.**"""
        return self.window.start


def _split_at_window(
    rows: Sequence[AwardRateTrainingRow], window: MaturityWindow
) -> _HoldoutSplit:
    """평가 창 ``[start, end)`` 를 경계로 out-of-time 분할을 만든다.

    경계 동시각(tie) 규칙
    ---------------------
    학습은 ``opened_at < window.start``, 평가는 ``window.start <= opened_at < window.end``
    다. 두 범위가 같은 시각을 공유하지 않으므로 경계와 같은 ``opened_at`` 을 가진 홀드아웃
    행이 학습에 새지 않는다. 이 누수가 위험한 이유는 크기가 아니라 **비대칭**이다:
    베이스라인은 ``gate_train`` 만 보므로 누수는 GBM 에만 붙는 이득이 되고, 그러면 게이트가
    재는 것이 실력인지 누수인지 구별되지 않는다.

    이전 구현은 분할을 **인덱스**로 하고 학습 범위만 시각으로 걸렀기 때문에 이 성질을
    별도 규칙으로 지켜야 했다. 창 경계에서는 두 범위가 같은 축(시각)이라 규칙이 곧 정의다.

    Raises:
        ValueError: 평가 층의 학습/홀드아웃 어느 한쪽이라도 비었을 때. 조용히 0건을
            평가하면 게이트가 "통과"로 보일 수 있으므로 크게 실패한다.
    """
    ordered = sorted(rows, key=lambda row: row.opened_at)
    gate_test = rows_in_window(ordered, window)
    gate_train = [
        row
        for row in ordered
        if row.denominator_source == GATE_STRATUM and row.opened_at < window.start
    ]
    if not gate_train or not gate_test:
        raise ValueError(
            f"Award-rate holdout needs rows on both sides of the split "
            f"(window={window.start.isoformat()}, train={len(gate_train)}, "
            f"test={len(gate_test)})."
        )
    return _HoldoutSplit(
        window=window,
        gate_train=gate_train,
        gate_test=gate_test,
        train_rows=[row for row in ordered if row.opened_at < window.start],
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
    for spec in BASELINE_SPECS:
        predictions, coverage = group_mean_predictions(
            spec, split.gate_train, split.gate_test, global_mean=global_mean
        )
        rmse, bias, residual_std = rmse_bias_std(predictions, targets)
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
        rmse, bias, residual_std = rmse_bias_std(predictions, targets)
        scores.append(
            ModelScore(name=name, rmse=rmse, bias=bias, residual_std=residual_std)
        )
        if name == GATE_MODEL_NAME:
            gate_predictions = predictions
    if gate_predictions is None:  # pragma: no cover - 위 루프가 보장한다
        raise ValueError("Gate model produced no predictions.")
    return scores, gate_predictions



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
    gate_paired_t = paired_t(
        gate_model_predictions, gate_baseline_predictions, targets
    )
    return AwardRateHoldoutReport(
        window_start=split.window.start.isoformat(),
        window_end=split.window.end.isoformat(),
        window_maturity=split.window.maturity,
        window_opened_count=split.window.opened_count,
        window_settled_count=split.window.settled_count,
        cutoff_at=split.cutoff_at.isoformat(),
        train_row_count=len(split.train_rows),
        gate_train_row_count=len(split.gate_train),
        gate_test_row_count=len(split.gate_test),
        train_mean=gate_train_mean,
        test_mean=float(np.mean(targets)),
        test_std=float(np.std(targets, ddof=1)) if targets.size > 1 else 0.0,
        baselines=baselines,
        models=models,
        segments=segment_scores(
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
        gate_paired_t=gate_paired_t,
        gate_passed=gate_model_rmse < gate_baseline_rmse
        and gate_paired_t < -GATE_PAIRED_T_THRESHOLD,
    )


def evaluate_award_rate_holdout(
    rows: Sequence[AwardRateTrainingRow],
    *,
    window: MaturityWindow,
    sample_scope: str,
    folds: int = DEFAULT_ENCODING_FOLDS,
    seed: int = DEFAULT_TRAINING_SEED,
) -> AwardRateHoldoutReport:
    """선언된 평가 창에서 베이스라인들과 GBM 두 변형을 비교한다.

    홀드아웃은 **평가 층**(``clean-base``)의 행 중 창 안에 개찰된 것이고, 학습에는 창 시작
    **미만**인 모든 ``ok`` 행을 쓴다(두 층 모두 — 층 구분은 통제 변수로 들어간다).

    Args:
        rows: 개찰 시각 오름차순 학습 행(로더가 이미 정렬해 준다).
        window: 평가 창과 그 성숙도. 창 선택은 이 커널의 책임이 아니라 정책이다
            (:func:`~app.services.ml_training.award_rate_windows.plan_evaluation_windows`)
            — 여기서 고르면 "무엇을 재는가"와 "어디서 재는가"가 한 함수에 섞여, 창을
            바꾸면 판정식도 같이 흔들린다.
        sample_scope: 이 행들의 표본 정의. 중간 아티팩트가 자기 코퍼스를 정직하게 신고하게
            하려고 받는다 — 이 경로의 아티팩트는 디스크에 남지 않지만, 계약을 통과해야
            하는 것은 서빙과 같으므로 여기서만 예외를 두면 그 round-trip 이 느슨해진다.
        folds: out-of-fold 인코딩·잔차의 분할 수.
        seed: 재현성 seed.

    Returns:
        :class:`AwardRateHoldoutReport`.

    Raises:
        ValueError: 평가 층의 학습/홀드아웃 어느 한쪽이라도 비었을 때.
    """
    return _build_report(
        _split_at_window(rows, window),
        folds=folds,
        seed=seed,
        sample_scope=sample_scope,
    )
