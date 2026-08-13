"""게이트 판정의 **자기 진단** — 검정력·베이스라인 커버리지·판정 안정성 (순수 계산).

왜 이것들이 리포트에 있어야 하는가
---------------------------------
승격 판정을 막거나 여는 것은 RMSE 한 줄이 아니라 "그 한 줄을 믿어도 되는가"다. 이 트랙에서
실측된 세 가지가 그것을 보여준다.

1. **무측정과 실패는 다르다.** 145행 창의 판정은 seed 를 바꾸면 개선률이 +7.12% ~ −3.77% 로
   **부호가 뒤집힌다**. "모델이 못 이겼다"가 아니라 "그 창은 아무것도 재지 못했다"가 사실
   이므로, 리포트는 판정 옆에 그 판정의 **안정성**을 함께 실어야 한다.
2. **유의성이 소수 행에 걸릴 수 있다.** 357행 창의 통과는 그룹 평균이 자기 셀을 못 찾은
   21행(폴백)이 지고 있고, 셀이 찬 336행만 보면 t=−1.37 로 통과하지 못한다. 그 구멍은
   학습 구간이 얇아서 생긴 것이므로 **전 구간 학습으로 서빙되는 모델의 성질이 아니다**.
3. **검정력은 표본 수가 아니라 효과 대비로 봐야 한다.** "몇 행이면 되는가"는 관측된 행당
   효과에서 역산해야 의미가 있고, "이 창이 검출할 수 있었던 최소 개선"이 그 짝이다.

여기 있는 것은 전부 순수 함수다. 창 선택은 :mod:`.award_rate_windows`, 판정식은
:mod:`.award_rate_holdout`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_scoring import (
    BaselineSpec,
    SegmentScore,
    improvement_ratio,
    paired_t,
    rmse_bias_std,
    segment_scores,
)

__all__ = [
    "GATE_STABILITY_SEEDS",
    "CategoryCount",
    "COVERAGE_COVERED",
    "COVERAGE_FALLBACK",
    "CoverageSplit",
    "HoldoutDiagnostics",
    "Verdict",
    "StabilitySummary",
    "StabilityTrial",
    "UnlearnedCell",
    "build_diagnostics",
    "build_verdict",
    "category_counts",
    "coverage_splits",
    "minimum_detectable_improvement",
    "required_row_count",
    "summarize_stability",
    "unlearned_cells",
]

# 판정 안정성을 재는 seed 목록(선언). 다섯인 이유: 부호가 뒤집히는 창을 드러내는 데는
# 몇 벌이면 충분하고, 각 seed 가 GBM 두 변형을 다시 학습하므로 비용이 선형으로 는다.
# 이 값은 판정식이 아니라 **판정에 대한 계측**이므로 게이트 임계와 성격이 다르다.
GATE_STABILITY_SEEDS: Final[tuple[int, ...]] = (20260812, 1, 7, 42, 2026)

# 베이스라인 커버리지 분해의 어휘(§4.5-3 선언).
COVERAGE_COVERED: Final[str] = "baseline-covered"
COVERAGE_FALLBACK: Final[str] = "baseline-fallback"


class CoverageSplit(StrictModel):
    """베이스라인이 자기 그룹으로 예측한 행 / 전역 평균으로 떨어진 행의 성적.

    게이트가 **무엇 위에서 통과했는지**를 리포트가 스스로 말하게 하는 분해다. 폴백 행에서만
    크게 이겨 전체가 통과했다면 그것은 모델의 우위가 아니라 **베이스라인의 구멍**이고, 그
    구멍은 학습 구간이 얇을 때만 생긴다.
    """

    segment: str
    row_count: int
    baseline_rmse: float
    model_rmse: float
    improvement_ratio: float
    paired_t: float


class UnlearnedCell(StrictModel):
    """홀드아웃에는 있는데 학습 구간에 없던 베이스라인 셀(그 행 수)."""

    key: str
    row_count: int


class StabilityTrial(StrictModel):
    """seed 하나에서 나온 게이트 성적 — 안정성 집계의 입력."""

    seed: int
    improvement_ratio: float
    paired_t: float
    passed: bool


class StabilitySummary(StrictModel):
    """seed 를 바꿔가며 같은 창을 잰 결과. **판정을 믿어도 되는가**의 답."""

    trials: list[StabilityTrial]
    passed_count: int
    sign_consistent: bool
    """모든 seed 에서 개선률 부호가 같은가. False 면 이 창은 방향조차 재지 못했다."""
    verdict_consistent: bool
    """모든 seed 에서 통과/실패가 같은가."""
    min_improvement_ratio: float
    max_improvement_ratio: float
    min_abs_paired_t: float
    max_abs_paired_t: float


def summarize_stability(trials: Sequence[StabilityTrial]) -> StabilitySummary:
    """seed 별 성적을 안정성 요약으로. 빈 입력은 "일관"이 아니라 중립으로 둔다."""
    improvements = [trial.improvement_ratio for trial in trials]
    absolute_ts = [abs(trial.paired_t) for trial in trials]
    signs = {improvement > 0 for improvement in improvements}
    verdicts = {trial.passed for trial in trials}
    return StabilitySummary(
        trials=list(trials),
        passed_count=sum(1 for trial in trials if trial.passed),
        sign_consistent=len(signs) <= 1,
        verdict_consistent=len(verdicts) <= 1,
        min_improvement_ratio=min(improvements, default=0.0),
        max_improvement_ratio=max(improvements, default=0.0),
        min_abs_paired_t=min(absolute_ts, default=0.0),
        max_abs_paired_t=max(absolute_ts, default=0.0),
    )


def required_row_count(
    observed_paired_t: float, row_count: int, *, threshold: float
) -> int | None:
    """관측된 **행당 효과가 유지된다면** 유의성에 필요한 행 수.

    ``t`` 는 표본 수의 제곱근에 비례하므로 ``n × (임계 ÷ |t|)²`` 이다. 모델이 베이스라인
    보다 나쁘면(``t ≥ 0``) 이 수는 의미가 없으므로 ``None`` 이다 — 0으로 채우면 "표본이
    충분했다"로 읽힌다.
    """
    if observed_paired_t >= 0 or row_count <= 0:
        return None
    return int(np.ceil(row_count * (threshold / abs(observed_paired_t)) ** 2))


def minimum_detectable_improvement(
    model_predictions: np.ndarray,
    baseline_predictions: np.ndarray,
    targets: np.ndarray,
    *,
    threshold: float,
) -> float:
    """이 창이 유의하게 검출할 수 있었던 **최소 RMSE 개선률**(MDE).

    관측된 개선이 이 값보다 작으면 그 창은 "모델이 못 이겼다"가 아니라 "이만큼 작은 차이는
    애초에 못 잰다"이다. 잔차 산포와 표본 수가 함께 들어가므로 행 수만 보는 것보다 정직하다.
    """
    differences = ((model_predictions - targets) ** 2) - (
        (baseline_predictions - targets) ** 2
    )
    baseline_mse = float(np.mean((baseline_predictions - targets) ** 2))
    if differences.size <= 1 or baseline_mse <= 0:
        return 0.0
    deviation = float(np.std(differences, ddof=1))
    detectable_mse_gap = threshold * deviation / np.sqrt(differences.size)
    detectable_model_mse = max(baseline_mse - detectable_mse_gap, 0.0)
    return improvement_ratio(
        np.sqrt(baseline_mse).item(), np.sqrt(detectable_model_mse).item()
    )


@dataclass(frozen=True)
class _CoverageSlice:
    """분해 한 조각 — 이름과 그 행을 고르는 마스크."""

    name: str
    mask: np.ndarray


def coverage_splits(
    covered: np.ndarray,
    model_predictions: np.ndarray,
    baseline_predictions: np.ndarray,
    targets: np.ndarray,
) -> list[CoverageSplit]:
    """커버리지 마스크로 홀드아웃을 갈라 각각을 채점한다(재학습 없음).

    조각의 t 는 판정식과 같은 식이므로 게이트 임계(2.58)와 직접 비교해 읽을 수 있다.
    """
    slices = (
        _CoverageSlice(COVERAGE_COVERED, covered),
        _CoverageSlice(COVERAGE_FALLBACK, ~covered),
    )
    scores: list[CoverageSplit] = []
    for item in slices:
        if not item.mask.any():
            continue
        segment_targets = targets[item.mask]
        baseline_rmse, _, _ = rmse_bias_std(
            baseline_predictions[item.mask], segment_targets
        )
        model_rmse, _, _ = rmse_bias_std(model_predictions[item.mask], segment_targets)
        scores.append(
            CoverageSplit(
                segment=item.name,
                row_count=int(item.mask.sum()),
                baseline_rmse=baseline_rmse,
                model_rmse=model_rmse,
                improvement_ratio=improvement_ratio(baseline_rmse, model_rmse),
                paired_t=paired_t(
                    model_predictions[item.mask],
                    baseline_predictions[item.mask],
                    segment_targets,
                ),
            )
        )
    return scores


def unlearned_cells(
    spec: BaselineSpec,
    train_rows: Sequence[AwardRateTrainingRow],
    test_rows: Sequence[AwardRateTrainingRow],
) -> list[UnlearnedCell]:
    """홀드아웃에는 있는데 학습 구간에 없던 베이스라인 셀과 그 행 수(행 수 내림차순).

    이 목록이 비어 있지 않다는 것은 비교 대상 베이스라인이 그만큼 **무력했다**는 뜻이고,
    그 상태에서 얻은 우위는 학습 구간이 두꺼워지면 사라질 수 있다.
    """
    learned: dict[str, int] = {}
    for row in train_rows:
        learned[spec.key(row)] = learned.get(spec.key(row), 0) + 1

    missing: dict[str, int] = {}
    for row in test_rows:
        key = spec.key(row)
        if learned.get(key, 0) < spec.min_count:
            missing[key] = missing.get(key, 0) + 1
    return [
        UnlearnedCell(key=key, row_count=count)
        for key, count in sorted(missing.items(), key=lambda item: (-item[1], item[0]))
    ]


class CategoryCount(StrictModel):
    """한 구간의 공종 구성. 창별 학습/평가 구성을 나란히 실어 **레짐 단절**을 드러낸다.

    공사 수집은 2026-06-30 에 켜졌다(PR #150). 그 앞뒤 창은 out-of-time 이 아니라
    **out-of-regime** 분할이므로, 창을 시간 축으로만 보면 비교가 성립하는 것처럼 보인다 —
    실측(2026-08-13) 2026-06-28 창은 학습 187행 중 공사 ~4건, 2026-07-05 창은 332행 중 35건,
    2026-07-19 창은 홀드아웃 2,013행 중 공사 1,106건이다.
    """

    category: str
    row_count: int


def category_counts(rows: Sequence[AwardRateTrainingRow]) -> list[CategoryCount]:
    """공종 구성(행 수 내림차순) — 창별 레짐 단절이 리포트에서 보이게 한다."""
    totals: dict[str, int] = {}
    for row in rows:
        key = row.category or "unknown"
        totals[key] = totals.get(key, 0) + 1
    return [
        CategoryCount(category=category, row_count=count)
        for category, count in sorted(
            totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]


@dataclass(frozen=True)
class HoldoutDiagnostics:
    """판정을 읽는 데 필요한 분해 한 벌 — 세그먼트·커버리지·미학습 셀."""

    segments: list[SegmentScore]
    coverage: list[CoverageSplit]
    unlearned_cells: list[UnlearnedCell]


def build_diagnostics(
    train_rows: Sequence[AwardRateTrainingRow],
    test_rows: Sequence[AwardRateTrainingRow],
    targets: np.ndarray,
    *,
    model_predictions: np.ndarray,
    baseline_predictions: np.ndarray,
    covered: np.ndarray,
    baseline_spec: BaselineSpec,
) -> HoldoutDiagnostics:
    """전체 예측 벡터를 **자르기만** 해서 분해한다(어디서도 재학습하지 않는다)."""
    return HoldoutDiagnostics(
        segments=segment_scores(
            test_rows,
            targets,
            baseline_predictions=baseline_predictions,
            model_predictions=model_predictions,
        ),
        coverage=coverage_splits(
            covered, model_predictions, baseline_predictions, targets
        ),
        unlearned_cells=unlearned_cells(baseline_spec, train_rows, test_rows),
    )


@dataclass(frozen=True)
class Verdict:
    """게이트 판정 한 벌 — 판정과 **그 판정의 검정력**을 같이 들고 다닌다.

    MDE 와 필요 표본 수를 판정 옆에 붙여 두는 이유: 그 둘이 없으면 ``passed=False`` 가
    "못 이겼다"인지 "못 쟀다"인지 구별되지 않는다.
    """

    baseline_rmse: float
    model_rmse: float
    improvement_ratio: float
    paired_t: float
    min_detectable_improvement: float
    required_row_count: int | None
    passed: bool


def build_verdict(
    *,
    baseline_rmse: float,
    model_rmse: float,
    model_predictions: np.ndarray,
    baseline_predictions: np.ndarray,
    targets: np.ndarray,
    threshold: float,
) -> Verdict:
    """판정식을 한 곳에서 적용한다 — 임계는 **호출부가 선언한 값**을 그대로 받는다.

    임계를 여기서 정하지 않는 이유: 판정식의 선언은 게이트 커널의 몫이고, 이 모듈은 그
    선언을 해석해 "얼마나 믿을 수 있는가"를 붙일 뿐이다.
    """
    statistic = paired_t(model_predictions, baseline_predictions, targets)
    return Verdict(
        baseline_rmse=baseline_rmse,
        model_rmse=model_rmse,
        improvement_ratio=improvement_ratio(baseline_rmse, model_rmse),
        paired_t=statistic,
        min_detectable_improvement=minimum_detectable_improvement(
            model_predictions, baseline_predictions, targets, threshold=threshold
        ),
        required_row_count=required_row_count(
            statistic, int(targets.size), threshold=threshold
        ),
        passed=model_rmse < baseline_rmse and statistic < -threshold,
    )
