"""백테스트 리포트 조립 — 게이트 실행 결과를 **읽을 수 있는 사실**로 만든다.

여기 있는 것은 계산이 아니라 **공시**다. 판정식은 :mod:`.award_rate_holdout`, 창 선택은
:mod:`.award_rate_windows`, 진단 계산은 :mod:`.award_rate_diagnostics` 이고, 이 모듈은 그
결과들을 한 문서로 묶어 "이 수치가 무엇 위에서 나왔는가"에 리포트만으로 답하게 한다.

리포트가 반드시 말해야 하는 것 (전부 실측에서 나온 요구다)
---------------------------------------------------------
- 창마다 경계·성숙도·행 수, 그리고 창 사이 **홀드아웃 겹침**(설계상 0, 잰 쌍 수와 함께).
- embargo 로 뺀 구간과 사유·행 수, 그리고 **어느 창에도 잡히지 않은 행 수**(회계 불변식).
- 창별 **판정 안정성**: seed 를 바꾸면 부호가 뒤집히는 창의 "판정"은 판정이 아니다.
- 게이트 창의 **베이스라인 커버리지**와 **세그먼트 역행** — 전체 개선 한 줄만 인용되면
  "홀드아웃의 59% 에서 더 나쁘다"가 보이지 않는다.
- 최상위 판정 이름에 한정이 담겨야 한다(``gate_passed_at_latest_window``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import Field

from app.core.constants import award_rate_sample_scope
from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_diagnostics import (
    GATE_STABILITY_SEEDS,
    StabilityTrial,
    summarize_stability,
)
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow
from app.services.ml_training.award_rate_holdout import (
    GATE_BASELINE_NAME,
    GATE_PAIRED_T_THRESHOLD,
    GATE_STRATUM,
    AwardRateHoldoutReport,
    evaluate_award_rate_holdout,
)
from app.domain.settlement_maturity import MaturityWindow
from app.services.ml_training.award_rate_windows import (
    EvaluationPlan,
    HoldoutOverlapSummary,
    WindowSummary,
    indices_in_window,
    summarize_exclusions,
    summarize_overlaps,
    summarize_window,
)

__all__ = [
    "BacktestReport",
    "CorpusCategoryCount",
    "StratumCount",
    "WindowStabilitySummary",
    "build_backtest_report",
]


class StratumCount(StrictModel):
    """코퍼스의 분모 출처 층 구성 — 평가 층이 전체의 얼마인지 공시한다."""

    denominator_source: str
    row_count: int


class CorpusCategoryCount(StrictModel):
    """코퍼스의 공종 구성. 표본 필터가 어느 공종을 통째로 지웠는지 여기서 드러난다."""

    category: str
    row_count: int
    published_floor_row_count: int


class BacktestReport(StrictModel):
    """평가 창 전체의 홀드아웃 결과 + 게이트 요약."""

    generated_at: str
    corpus_row_count: int
    feed_origin_only: bool
    """이 실행의 표본 정의(공고 피드 출처만 실었는가). 수치는 이 값과 함께 읽어야 한다."""
    corpus_first_opened_at: str | None = None
    corpus_last_opened_at: str | None = None
    corpus_published_floor_row_count: int = 0
    strata: list[StratumCount] = Field(default_factory=list)
    categories: list[CorpusCategoryCount] = Field(default_factory=list)
    gate_stratum: str
    gate_baseline_name: str
    gate_paired_t_threshold: float
    maturity_threshold: float
    """embargo 임계. 이 값 미만의 창에서는 재지 않는다."""
    min_evaluation_rows: int
    max_origins: int
    maturity_observation_count: int
    """성숙도 표를 만든 공고 수(피드 모집단). 코퍼스 행 수와 다른 수다 — 분모는 라벨이
    성립하지 않는 공고도 포함한다."""
    evaluation_windows: list[WindowSummary] = Field(default_factory=list)
    excluded_windows: list[WindowSummary] = Field(default_factory=list)
    """embargo·표본 하한·최근 N 으로 뺀 창과 그 사유. 제외는 침묵이 아니라 기록이다."""
    excluded_row_count: int = 0
    """제외된 창에 들어 있던 평가 층 행의 합."""
    unaccounted_row_count: int = 0
    """어느 평가 창에도, 제외 회계에도 잡히지 않은 평가 층 행 수.

    **회계 불변식**: 피드 출처 모드에서는 0이어야 한다(코퍼스와 성숙도 모집단이 같은
    공고 집합이므로 모든 행이 어떤 주에는 속한다). ``--no-feed-origin-only`` 는 코퍼스만
    넓히고 성숙도 모집단은 피드로 고정되므로(미정산 분모가 없는 층은 셀 수 없다) 이 수가
    커진다 — 실측 3,450행(clean-base 16,848 중 20%). 그 모드의 수치는 이 값과 함께 읽어야
    한다."""
    holdout_overlaps: list[HoldoutOverlapSummary] = Field(default_factory=list)
    holdout_overlap_pair_count: int = 0
    """겹침을 잰 창 쌍의 수. 0이면 :attr:`holdout_overlap_row_count` 의 0은 **재서 나온
    0이 아니라 잴 쌍이 없었다는 뜻**이다."""
    holdout_overlap_row_count: int = 0
    """창 쌍 중 최대 겹침. 0이 아니면 창 선택이 깨진 것이다."""
    stability_seeds: list[int] = Field(default_factory=list)
    """판정 안정성을 잰 seed 목록(비었으면 안정성을 재지 않은 실행)."""
    window_stability: list[WindowStabilitySummary] = Field(default_factory=list)
    """창별 seed 민감도 — **아무것도 재지 못한 창**을 드러내는 축이다.

    "판정이 뒤집히는 창"만 찾으면 놓친다. 검정력 없는 창은 뒤집힐 힘이 없어 판정 일관성을
    공짜로 만족하면서 개선률 **부호만** 흔들린다(실측 2026-06-28 창 — 5회 전부 실패,
    개선률 −3.77% ~ +7.12%). ``verdict_consistent`` 와 ``sign_consistent`` 는 **함께**
    읽어야 한다."""
    encoding_folds: int
    training_seed: int
    origins: list[AwardRateHoldoutReport] = Field(default_factory=list)
    gate_evaluable: bool
    """성숙한 평가 창이 하나라도 있었는가. false 면 판정 자체가 성립하지 않는다."""
    gate_window_start: str | None = None
    """게이트 판정을 낸 창(가장 최근 성숙 창 — 서빙에 가장 가깝다)."""
    gate_passed_at_latest_window: bool
    """**최근 성숙 창 하나**의 판정이다. 이름이 그 한정을 담는 이유: ``gate_passed`` 로
    두면 최상위의 ``true`` 가 "게이트 통과"로 인용되고, 그 인용은 창 하나의 성적을 모델
    전체의 사실로 바꿔 놓는다. 승격 판단은 :attr:`gate_passed_at_all_origins` 와 창별
    안정성·커버리지 분해를 함께 봐야 한다."""
    gate_passed_at_all_origins: bool
    artifact_out: str | None = None
    artifact_training_row_count: int | None = None


class WindowStabilitySummary(StrictModel):
    """창 하나의 seed 민감도(리포트 행)."""

    window_start: str
    row_count: int
    passed_count: int
    trial_count: int
    sign_consistent: bool
    verdict_consistent: bool
    min_improvement_ratio: float
    max_improvement_ratio: float
    min_abs_paired_t: float
    max_abs_paired_t: float
    trials: list[StabilityTrial] = Field(default_factory=list)


def _category_counts(rows: list[AwardRateTrainingRow]) -> list[CorpusCategoryCount]:
    """공종별 (행 수, 공시 하한 보유 행 수). 필터가 지운 공종이 0 행으로 사라지므로,
    구성은 **리포트에 남는 것**이라야 나중에 수치를 다시 읽을 때 오해가 없다."""
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        key = row.category or "unknown"
        count, with_floor = totals.get(key, (0, 0))
        totals[key] = (
            count + 1,
            with_floor + (1 if row.published_floor_rate is not None else 0),
        )
    return [
        CorpusCategoryCount(
            category=category, row_count=count, published_floor_row_count=with_floor
        )
        for category, (count, with_floor) in sorted(
            totals.items(), key=lambda item: (-item[1][0], item[0])
        )
    ]


def _strata_counts(rows: list[AwardRateTrainingRow]) -> list[StratumCount]:
    """분모 출처 층별 행 수 — 평가 층이 코퍼스의 얼마인지."""
    totals: dict[str, int] = {}
    for row in rows:
        totals[row.denominator_source] = totals.get(row.denominator_source, 0) + 1
    return [
        StratumCount(denominator_source=source, row_count=count)
        for source, count in sorted(totals.items())
    ]


def _evaluate_windows(
    rows: list[AwardRateTrainingRow],
    plan: EvaluationPlan,
    *,
    folds: int,
    seed: int,
    feed_origin_only: bool,
) -> list[AwardRateHoldoutReport]:
    """평가 창마다 홀드아웃 커널을 돌린다.

    홀드아웃끼리는 **배타적**이지만(창이 겹치지 않는다) 판정끼리 독립은 아니다: 뒤 창의
    학습 구간이 앞 창의 홀드아웃을 통째로 포함하고(실측 07-05 창 학습 332행 ⊇ 06-28 창
    홀드아웃 145행), 인접 주라 모집단도 이어져 있다. 여러 창의 판정이 같은 방향으로 틀릴
    수 있다는 뜻이므로 "모든 창 통과"를 독립 시행의 곱처럼 읽지 않는다.
    """
    sample_scope = award_rate_sample_scope(feed_origin_only=feed_origin_only)
    return [
        evaluate_award_rate_holdout(
            rows, window=window, sample_scope=sample_scope, folds=folds, seed=seed
        )
        for window in plan.selected
    ]


def _stability_seeds(seed: int, *, enabled: bool) -> list[int]:
    """안정성 seed 목록. 헤드라인 판정을 낸 seed 가 **반드시 포함**되도록 앞에 둔다."""
    if not enabled:
        return []
    return [seed, *(item for item in GATE_STABILITY_SEEDS if item != seed)]


def _one_window_stability(
    rows: list[AwardRateTrainingRow],
    window: MaturityWindow,
    *,
    seeds: list[int],
    folds: int,
    sample_scope: str,
) -> WindowStabilitySummary:
    """창 하나를 seed 마다 다시 재고 요약한다.

    베이스라인은 그룹 평균이라 seed 와 무관하다 — 바뀌는 것은 GBM 뿐이므로, 여기서 흔들리는
    수치는 전부 **모델 쪽 분산**이다.
    """
    trials = []
    for seed in seeds:
        report = evaluate_award_rate_holdout(
            rows, window=window, sample_scope=sample_scope, folds=folds, seed=seed
        )
        trials.append(
            StabilityTrial(
                seed=seed,
                improvement_ratio=report.gate_improvement_ratio,
                paired_t=report.gate_paired_t,
                passed=report.gate_passed,
            )
        )
    summary = summarize_stability(trials)
    return WindowStabilitySummary(
        window_start=window.start.isoformat(),
        row_count=len(indices_in_window(rows, window)),
        passed_count=summary.passed_count,
        trial_count=len(trials),
        sign_consistent=summary.sign_consistent,
        verdict_consistent=summary.verdict_consistent,
        min_improvement_ratio=summary.min_improvement_ratio,
        max_improvement_ratio=summary.max_improvement_ratio,
        min_abs_paired_t=summary.min_abs_paired_t,
        max_abs_paired_t=summary.max_abs_paired_t,
        trials=summary.trials,
    )


def _window_stability(
    rows: list[AwardRateTrainingRow],
    plan: EvaluationPlan,
    *,
    seeds: list[int],
    folds: int,
    feed_origin_only: bool,
) -> list[WindowStabilitySummary]:
    """평가 창마다 seed 민감도를 잰다(seed 가 없으면 재지 않은 실행이다)."""
    if not seeds:
        return []
    sample_scope = award_rate_sample_scope(feed_origin_only=feed_origin_only)
    return [
        _one_window_stability(
            rows, window, seeds=seeds, folds=folds, sample_scope=sample_scope
        )
        for window in plan.selected
    ]


def _unaccounted_row_count(
    rows: list[AwardRateTrainingRow], plan: EvaluationPlan
) -> int:
    """어느 창에도 귀속되지 않은 평가 층 행 수 — **회계 불변식의 계측기**.

    성숙도 모집단은 항상 피드 공고인데 코퍼스는 표본 정의에 따라 넓어질 수 있다. 그 차이가
    조용히 사라지면 "제외 회계"가 전수를 설명한다는 인상만 남는다.
    """
    accounted = sum(
        len(indices_in_window(rows, window))
        for window in (
            *plan.selected,
            *(item.window for item in plan.excluded),
        )
    )
    stratum_rows = sum(1 for row in rows if row.denominator_source == GATE_STRATUM)
    return max(stratum_rows - accounted, 0)


def _published_floor_count(rows: list[AwardRateTrainingRow]) -> int:
    """공시 하한을 가진 행 수 — 백필 커버리지가 코퍼스의 얼마나 닿았는지."""
    return sum(1 for row in rows if row.published_floor_rate is not None)


def _selected_summaries(
    plan: EvaluationPlan, evaluations: list[AwardRateHoldoutReport]
) -> list[WindowSummary]:
    """평가에 쓴 창의 리포트 행. 행 수는 홀드아웃이 실제로 잰 수를 그대로 싣는다."""
    return [
        summarize_window(
            window,
            evaluation_row_count=report.gate_test_row_count,
            excluded_reason=None,
        )
        for window, report in zip(plan.selected, evaluations, strict=True)
    ]


def _opened_at_span(rows: list[AwardRateTrainingRow]) -> tuple[str | None, str | None]:
    """코퍼스의 (첫, 마지막) 개찰 시각. 표본 필터가 구간을 얼마나 좁혔는지 드러난다."""
    if not rows:
        return None, None
    times = [row.opened_at for row in rows]
    return min(times).isoformat(), max(times).isoformat()



@dataclass(frozen=True)
class _CorpusProfile:
    """코퍼스 구성 한 벌 — 표본 필터가 무엇을 남겼는지."""

    first_opened_at: str | None
    last_opened_at: str | None
    published_floor_row_count: int
    strata: list[StratumCount]
    categories: list[CorpusCategoryCount]


def _corpus_profile(rows: list[AwardRateTrainingRow]) -> _CorpusProfile:
    """구간·층·공종·공시 하한 보유 수를 한 번에 — 리포트가 모집단을 밝히게 한다."""
    first_opened, last_opened = _opened_at_span(rows)
    return _CorpusProfile(
        first_opened_at=first_opened,
        last_opened_at=last_opened,
        published_floor_row_count=_published_floor_count(rows),
        strata=_strata_counts(rows),
        categories=_category_counts(rows),
    )


def build_backtest_report(
    rows: list[AwardRateTrainingRow],
    plan: EvaluationPlan,
    *,
    maturity_observation_count: int,
    folds: int,
    seed: int,
    feed_origin_only: bool,
    stability: bool = True,
) -> BacktestReport:
    """선언된 평가 창을 훑어 홀드아웃 결과를 모은다.

    ``gate_passed_at_latest_window`` 는 **창 하나**의 성적이다. 승격 판단은 그 한 줄이
    아니라 창별 안정성·커버리지 분해·공종 구성을 함께 봐야 한다."""
    evaluations = _evaluate_windows(
        rows, plan, folds=folds, seed=seed, feed_origin_only=feed_origin_only
    )
    overlaps = summarize_overlaps(rows, plan.selected)
    seeds = _stability_seeds(seed, enabled=stability)
    return _assemble(
        rows,
        plan,
        evaluations,
        corpus=_corpus_profile(rows),
        overlaps=overlaps,
        seeds=seeds,
        stability=_window_stability(
            rows, plan, seeds=seeds, folds=folds, feed_origin_only=feed_origin_only
        ),
        maturity_observation_count=maturity_observation_count,
        folds=folds,
        seed=seed,
        feed_origin_only=feed_origin_only,
    )


def _assemble(
    rows: list[AwardRateTrainingRow],
    plan: EvaluationPlan,
    evaluations: list[AwardRateHoldoutReport],
    *,
    corpus: _CorpusProfile,
    overlaps: list[HoldoutOverlapSummary],
    seeds: list[int],
    stability: list[WindowStabilitySummary],
    maturity_observation_count: int,
    folds: int,
    seed: int,
    feed_origin_only: bool,
) -> BacktestReport:
    """계산된 조각들을 한 문서로 편다 — 여기서 새로 재지 않는다(공시만 한다)."""
    gate = evaluations[-1] if evaluations else None
    return BacktestReport(
        generated_at=datetime.now(UTC).isoformat(),
        corpus_row_count=len(rows),
        feed_origin_only=feed_origin_only,
        corpus_first_opened_at=corpus.first_opened_at,
        corpus_last_opened_at=corpus.last_opened_at,
        corpus_published_floor_row_count=corpus.published_floor_row_count,
        strata=corpus.strata,
        categories=corpus.categories,
        gate_stratum=GATE_STRATUM,
        gate_baseline_name=GATE_BASELINE_NAME,
        gate_paired_t_threshold=GATE_PAIRED_T_THRESHOLD,
        maturity_threshold=plan.policy.maturity_threshold,
        min_evaluation_rows=plan.policy.min_evaluation_rows,
        max_origins=plan.policy.max_origins,
        maturity_observation_count=maturity_observation_count,
        evaluation_windows=_selected_summaries(plan, evaluations),
        excluded_windows=summarize_exclusions(plan),
        excluded_row_count=sum(item.evaluation_row_count for item in plan.excluded),
        unaccounted_row_count=_unaccounted_row_count(rows, plan),
        holdout_overlaps=overlaps,
        holdout_overlap_pair_count=len(overlaps),
        holdout_overlap_row_count=max((item.row_count for item in overlaps), default=0),
        stability_seeds=seeds,
        window_stability=stability,
        encoding_folds=folds,
        training_seed=seed,
        origins=evaluations,
        gate_evaluable=gate is not None,
        gate_window_start=gate.window_start if gate else None,
        gate_passed_at_latest_window=gate is not None and gate.gate_passed,
        gate_passed_at_all_origins=gate is not None
        and all(evaluation.gate_passed for evaluation in evaluations),
    )
