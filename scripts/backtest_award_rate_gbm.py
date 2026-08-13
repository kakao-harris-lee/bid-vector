#!/usr/bin/env python3
"""낙찰률 GBM(Phase 2) out-of-time 홀드아웃 — 사전 선언 게이트 판정 + 아티팩트 생성.

무엇을 재는가
-------------
``clean-base`` 층의 시간 홀드아웃에서 다섯 그룹 평균 베이스라인(전역·공종·금액대·
공종×금액대·발주기관)과 GBM 두 변형(전 층 학습 / 평가 층만 학습)을 나란히 잰다.
판정식은 사전 선언돼 있다: **GBM 이 ``category × amount band`` 를 유의하게 넘는가**
(:mod:`app.services.ml_training.award_rate_holdout`). 게이트를 사후에 느슨하게 만들지
않기 위해 임계와 비교 대상이 코드의 선언 상수이고 이 스크립트에는 없다.

평가 구간은 **성숙도로 고른 겹치지 않는 주 단위 창**이다 (Phase 2c)
----------------------------------------------------------------
이전에는 rolling origin 이 평가 층 행 수의 **비율**이었다. 이 코퍼스는 행이 3주에 몰려 있어
다섯 비율이 같은 5일 구간으로 붕괴했고(겹침 100%), "모든 origin 통과"는 안정성이 아니라
**같은 행을 다섯 번 잰 것**이었다. 지금은 정산 성숙도가 임계 이상인 KST 주가 곧 평가 창이고
(:mod:`app.services.ml_training.award_rate_windows`), 창끼리 겹치지 않는다.

리포트는 창마다 경계·성숙도·행 수를, 그리고 창 사이 **홀드아웃 겹침 행 수**(설계상 0)와
embargo 로 뺀 구간을 함께 싣는다. 수치를 나중에 다시 읽는 사람이 "이것이 무엇 위에서 나온
수치인지"를 리포트만으로 알 수 있어야 하기 때문이다.

성숙한 창이 하나도 없으면 ``gate_evaluable=false`` 로 끝난다. **그것도 결과다** — "지금
데이터로는 정직하게 평가할 수 없다"는 판정이지, 임계를 낮춰 통과를 만들 사유가 아니다.

표본 정의는 ``--feed-origin-only`` / ``--no-feed-origin-only`` 로 바꿔 나란히 잴 수 있다
(기본은 설정값). 리포트에 그 값과 코퍼스 구성(기간·공종·공시 하한 보유 수)이 함께 실리므로
두 실행의 수치를 나중에 다시 읽을 때 어느 모집단에서 나온 것인지 헷갈리지 않는다.

``--artifact-out`` 을 주면 **전 구간**으로 학습한 서빙 아티팩트를 함께 쓴다. 그 파일을
``PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH`` 로 가리켜야 predictor 가 살아나며, 선호
설정(``PRICE_PREDICTION_PREFERRED_PREDICTOR``)을 바꾸지 않는 한 라이브 경로는 그대로다.

DB read-only(SELECT 전용, write/commit 없음). 외부 API 호출 없음.

사용 예::

    docker compose exec -T training-worker python scripts/backtest_award_rate_gbm.py
    docker compose exec -T training-worker python scripts/backtest_award_rate_gbm.py \\
        --artifact-out models/award-rate-gbm.json
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import Field

from app.core.config import settings
from app.core.constants import award_rate_sample_scope
from app.core.database import SessionLocal
from app.domain.settlement_maturity import MaturityWindow, build_weekly_maturity
from app.schemas._base import StrictModel
from app.services.award_rate_dataset import load_award_rate_rows
from app.services.ml_training.award_rate_gbm import (
    DEFAULT_ENCODING_FOLDS,
    DEFAULT_TRAINING_SEED,
    AwardRateTrainingRow,
    train_award_rate_gbm,
)
from app.services.ml_training.award_rate_holdout import (
    GATE_BASELINE_NAME,
    GATE_PAIRED_T_THRESHOLD,
    GATE_STRATUM,
    AwardRateHoldoutReport,
    evaluate_award_rate_holdout,
)
from app.services.ml_training.award_rate_windows import (
    GATE_MAX_ORIGINS,
    EvaluationPlan,
    HoldoutOverlapSummary,
    WindowPolicy,
    WindowSummary,
    plan_evaluation_windows,
    summarize_exclusions,
    summarize_overlaps,
    summarize_window,
)
from app.services.settlement_maturity import load_settlement_observations


class StratumCount(StrictModel):
    """코퍼스의 분모 출처 층 구성 — 평가 층이 전체의 얼마인지 공시한다."""

    denominator_source: str
    row_count: int


class CategoryCount(StrictModel):
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
    categories: list[CategoryCount] = Field(default_factory=list)
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
    holdout_overlaps: list[HoldoutOverlapSummary] = Field(default_factory=list)
    holdout_overlap_row_count: int = 0
    """창 쌍 중 최대 겹침. 0이 아니면 창 선택이 깨진 것이다."""
    encoding_folds: int
    training_seed: int
    origins: list[AwardRateHoldoutReport] = Field(default_factory=list)
    gate_evaluable: bool
    """성숙한 평가 창이 하나라도 있었는가. false 면 판정 자체가 성립하지 않는다."""
    gate_window_start: str | None = None
    """게이트 판정을 낸 창(가장 최근 성숙 창 — 서빙에 가장 가깝다)."""
    gate_passed: bool
    """:attr:`gate_window_start` 창의 판정. 다른 창은 견고성 확인용이다."""
    gate_passed_at_all_origins: bool
    artifact_out: str | None = None
    artifact_training_row_count: int | None = None


class ConsoleSummary(StrictModel):
    """콘솔 한 줄 요약(리포트 파일 경로 + 게이트 결론)."""

    out: str
    corpus_row_count: int
    feed_origin_only: bool
    evaluation_window_count: int
    holdout_overlap_row_count: int
    gate_evaluable: bool
    gate_window_start: str | None = None
    gate_test_row_count: int | None = None
    gate_baseline_rmse: float | None = None
    gate_model_rmse: float | None = None
    gate_improvement_ratio: float | None = None
    gate_paired_t: float | None = None
    gate_passed: bool
    gate_passed_at_all_origins: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Out-of-time holdout gate for the award-rate GBM predictor."
    )
    parser.add_argument(
        "--max-origins",
        type=int,
        default=GATE_MAX_ORIGINS,
        help=(
            "How many of the most recent mature evaluation windows to score. "
            "The maturity threshold itself is a declared constant, not a flag — "
            "lowering it to manufacture a pass would defeat the embargo."
        ),
    )
    parser.add_argument(
        "--folds", type=int, default=DEFAULT_ENCODING_FOLDS,
        help="Out-of-fold splits for the agency encoding and residual spread.",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_TRAINING_SEED,
        help="Training seed (fold permutation + boosting).",
    )
    parser.add_argument(
        "--artifact-out", default="",
        help="Write a full-corpus serving artifact to this path (optional).",
    )
    parser.add_argument(
        "--feed-origin-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Restrict the corpus to notices seen in the 공고 feed (serving-distribution "
            "alignment). Omit to follow "
            "PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY; --no-feed-origin-only "
            "restores the pre-filter sample definition for a before/after comparison."
        ),
    )
    parser.add_argument("--out", default="", help="Optional JSON report path.")
    return parser


def _category_counts(rows: list[AwardRateTrainingRow]) -> list[CategoryCount]:
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
        CategoryCount(
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
    """평가 창마다 홀드아웃 커널을 돌린다 — 창끼리 겹치지 않으므로 서로 독립이다."""
    sample_scope = award_rate_sample_scope(feed_origin_only=feed_origin_only)
    return [
        evaluate_award_rate_holdout(
            rows, window=window, sample_scope=sample_scope, folds=folds, seed=seed
        )
        for window in plan.selected
    ]


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



def build_report(
    rows: list[AwardRateTrainingRow],
    plan: EvaluationPlan,
    *,
    maturity_observation_count: int,
    folds: int,
    seed: int,
    feed_origin_only: bool,
) -> BacktestReport:
    """선언된 평가 창을 훑어 홀드아웃 결과를 모은다.

    게이트 판정은 **가장 최근 성숙 창**의 성적이다(서빙에 가장 가까운 구간). 성숙한 창이
    없으면 ``gate_evaluable=false`` 이고 ``gate_passed`` 는 False 다 — 잴 수 없었던 것이
    통과로 보이면 안 된다."""
    evaluations = _evaluate_windows(
        rows, plan, folds=folds, seed=seed, feed_origin_only=feed_origin_only
    )
    overlaps = summarize_overlaps(rows, plan.selected)
    first_opened, last_opened = _opened_at_span(rows)
    gate = evaluations[-1] if evaluations else None
    return BacktestReport(
        generated_at=datetime.now(UTC).isoformat(),
        corpus_row_count=len(rows),
        feed_origin_only=feed_origin_only,
        corpus_first_opened_at=first_opened,
        corpus_last_opened_at=last_opened,
        corpus_published_floor_row_count=_published_floor_count(rows),
        strata=_strata_counts(rows),
        categories=_category_counts(rows),
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
        holdout_overlaps=overlaps,
        holdout_overlap_row_count=max((item.row_count for item in overlaps), default=0),
        encoding_folds=folds,
        training_seed=seed,
        origins=evaluations,
        gate_evaluable=gate is not None,
        gate_window_start=gate.window_start if gate else None,
        gate_passed=gate is not None and gate.gate_passed,
        gate_passed_at_all_origins=gate is not None
        and all(evaluation.gate_passed for evaluation in evaluations),
    )


def write_artifact(
    rows: list[AwardRateTrainingRow],
    path: Path,
    *,
    folds: int,
    seed: int,
    feed_origin_only: bool,
) -> int:
    """전 구간으로 학습한 서빙 아티팩트를 저장하고 학습 행 수를 돌려준다.

    학습기는 JSON 페이로드를 내주고, 저장은 **읽기 계약을 한 번 통과시킨 뒤** 한다 —
    디스크에 남는 파일이 서빙이 읽을 수 있는 것임을 파일이 생기는 지점에서 고정한다.
    """
    from app.ai.predictors.artifact_contracts import PersistedAwardRateGbmArtifact

    artifact = train_award_rate_gbm(
        rows,
        sample_scope=award_rate_sample_scope(feed_origin_only=feed_origin_only),
        folds=folds,
        seed=seed,
    )
    validated = PersistedAwardRateGbmArtifact.model_validate(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(validated.model_dump_json(), encoding="utf-8")
    return len(rows)


def load_corpus(
    *, feed_origin_only: bool
) -> tuple[list[AwardRateTrainingRow], tuple[MaturityWindow, ...], int]:
    """코퍼스와 성숙도 구간 표를 한 세션에서 싣는다(SELECT 전용 — write/commit 없음).

    두 로더가 **같은 시점**을 봐야 성숙도와 평가 대상이 어긋나지 않는다. 성숙도의 모집단은
    표본 정의와 무관하게 피드 공고다(``app/services/settlement_maturity`` 모듈 docstring).

    Returns:
        (학습 행, 성숙도 구간 표, 성숙도 관측 수).
    """
    db = SessionLocal()
    try:
        rows = load_award_rate_rows(db, feed_origin_only=feed_origin_only)
        observations = load_settlement_observations(db)
    finally:
        db.close()
    return rows, build_weekly_maturity(observations), len(observations)


def resolve_report_path(raw: str) -> Path:
    """리포트 경로(미지정이면 타임스탬프 파일)를 만들고 상위 디렉터리를 보장한다."""
    path = Path(
        raw
        or "models/reports/award-rate-gbm-backtest-"
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _console_summary(report: BacktestReport, output_path: Path) -> ConsoleSummary:
    """콘솔 요약. 평가 창이 하나도 없으면 성적 칸은 ``null`` 로 남는다 — 잴 수 없었던
    것을 0.0 으로 채우면 "베이스라인과 동률"처럼 읽힌다."""
    gate = report.origins[-1] if report.origins else None
    return ConsoleSummary(
        out=str(output_path),
        corpus_row_count=report.corpus_row_count,
        feed_origin_only=report.feed_origin_only,
        evaluation_window_count=len(report.evaluation_windows),
        holdout_overlap_row_count=report.holdout_overlap_row_count,
        gate_evaluable=report.gate_evaluable,
        gate_window_start=report.gate_window_start,
        gate_test_row_count=gate.gate_test_row_count if gate else None,
        gate_baseline_rmse=gate.gate_baseline_rmse if gate else None,
        gate_model_rmse=gate.gate_model_rmse if gate else None,
        gate_improvement_ratio=gate.gate_improvement_ratio if gate else None,
        gate_paired_t=gate.gate_paired_t if gate else None,
        gate_passed=report.gate_passed,
        gate_passed_at_all_origins=report.gate_passed_at_all_origins,
    )


def main() -> int:
    args = build_parser().parse_args()
    output_path = resolve_report_path(args.out)
    feed_origin_only = (
        bool(settings.PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY)
        if args.feed_origin_only is None
        else bool(args.feed_origin_only)
    )
    rows, maturity_windows, observation_count = load_corpus(
        feed_origin_only=feed_origin_only
    )
    report = build_report(
        rows,
        plan_evaluation_windows(
            rows,
            maturity_windows,
            policy=WindowPolicy(max_origins=args.max_origins),
        ),
        maturity_observation_count=observation_count,
        folds=args.folds,
        seed=args.seed,
        feed_origin_only=feed_origin_only,
    )
    if args.artifact_out:
        artifact_path = Path(args.artifact_out)
        report = report.model_copy(
            update={
                "artifact_out": str(artifact_path),
                "artifact_training_row_count": write_artifact(
                    rows,
                    artifact_path,
                    folds=args.folds,
                    seed=args.seed,
                    feed_origin_only=feed_origin_only,
                ),
            }
        )

    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(_console_summary(report, output_path).model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
