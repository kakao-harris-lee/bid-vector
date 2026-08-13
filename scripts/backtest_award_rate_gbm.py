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
from app.services.ml_training.award_rate_backtest_report import (
    BacktestReport,
    build_backtest_report,
)
from app.services.ml_training.award_rate_gbm import (
    DEFAULT_ENCODING_FOLDS,
    DEFAULT_TRAINING_SEED,
    AwardRateTrainingRow,
    train_award_rate_gbm,
)
from app.services.ml_training.award_rate_holdout import (
    GATE_BASELINE_NAME,
    AwardRateHoldoutReport,
)
from app.services.ml_training.award_rate_windows import (
    GATE_MAX_ORIGINS,
    WindowPolicy,
    plan_evaluation_windows,
)
from app.services.settlement_maturity import load_settlement_observations


class ConsoleSummary(StrictModel):
    """콘솔 한 줄 요약(리포트 파일 경로 + 게이트 결론).

    **JSON 이 정직해도 인용되는 것은 이 줄이다.** 그래서 "모델이 못 이겼다"와 "이 창은
    아무것도 재지 못했다"를 가르는 두 신호가 여기에도 실려야 한다 — 그러지 않으면
    ``gate_passed_at_all_origins=false`` 한 줄이 전자로만 읽힌다(이 요약이 실제로 그
    오독을 생산한 적이 있다).
    """

    out: str
    corpus_row_count: int
    feed_origin_only: bool
    evaluation_window_count: int
    holdout_overlap_pair_count: int
    holdout_overlap_row_count: int
    unaccounted_row_count: int
    stability_seed_count: int = 0
    """안정성을 잰 seed 수. 0이면 아래 두 목록의 빈 값은 "없다"가 아니라 **"재지
    않았다"** 이다 — 두 상태가 같은 ``[]`` 로 보이면 안 된다."""
    gate_evaluable: bool
    gate_window_start: str | None = None
    gate_test_row_count: int | None = None
    gate_baseline_rmse: float | None = None
    gate_model_rmse: float | None = None
    gate_improvement_ratio: float | None = None
    gate_paired_t: float | None = None
    gate_min_detectable_improvement: float | None = None
    gate_baseline_coverage: float | None = None
    gate_regressed_segments: list[str] = Field(default_factory=list)
    """게이트 창에서 **모델이 베이스라인보다 나쁜** 세그먼트(행 수 내림차순). 전체 개선만
    인용되는 것을 막으려고 요약 줄에 올린다."""
    seed_unstable_windows: list[str] = Field(default_factory=list)
    """판정이나 **개선률 부호**가 seed 에 흔들린 창.

    부호만 뒤집히고 판정은 일관된 창(5회 전부 실패)이 실재하므로 ``verdict_consistent``
    하나로 거르면 그 창이 목록에서 사라진다 — 검정력 없는 창은 뒤집힐 힘이 없어 판정
    일관성을 **공짜로** 만족하기 때문이다. 두 축을 함께 봐야 "재지 못했다"가 드러난다."""
    underpowered_windows: list[str] = Field(default_factory=list)
    """관측 개선이 그 창의 검출 한계(MDE)보다 작은 창 — **"못 이겼다"가 아니라 "못 쟀다"**.

    게이트 창의 MDE 만 실으면 실패한 창의 검정력은 콘솔에서 볼 수 없고, 그러면 이 요약만
    읽고는 "못 쟀다"에 도달할 경로가 없다."""
    gate_passed_at_latest_window: bool
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
        "--stability",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Re-score every window across the declared seeds so the report can say "
            "whether a verdict survives a seed change. Costs one extra pass per seed."
        ),
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


def _regressed_segments(gate: AwardRateHoldoutReport | None) -> list[str]:
    """게이트 창에서 모델이 베이스라인보다 나쁜 세그먼트("축/세그먼트 n=… −x.x%").

    1행 세그먼트는 뺀다. 대응 t 는 차이의 표본 표준편차를 나누므로 **한 행에서는 존재할 수
    없고**(계산상 0으로 떨어진다), 그런 줄이 헤드라인에 오르면 진짜 신호를 희석한다. 행 수
    내림차순으로 실어 큰 세그먼트가 먼저 읽히게 한다.
    """
    if gate is None:
        return []
    return [
        f"{score.axis}/{score.segment} n={score.row_count} "
        f"{score.improvement_ratio * 100:+.1f}%"
        for score in sorted(gate.segments, key=lambda item: -item.row_count)
        if score.improvement_ratio < 0 and score.row_count > 1
    ]


def _seed_unstable_windows(report: BacktestReport) -> list[str]:
    """판정이나 개선률 **부호**가 seed 에 흔들린 창.

    ``verdict_consistent`` 만 보면 안 되는 이유: 검정력 없는 창은 애초에 뒤집힐 힘이 없어
    판정 일관성을 공짜로 만족한다(실측 2026-06-28 창 — 5회 전부 실패인데 개선률은
    −3.77% ~ +7.12%).
    """
    return [
        f"{item.window_start[:10]} sign {item.min_improvement_ratio * 100:+.2f}%.."
        f"{item.max_improvement_ratio * 100:+.2f}% passed={item.passed_count}"
        f"/{item.trial_count}"
        for item in report.window_stability
        if not (item.verdict_consistent and item.sign_consistent)
    ]


def _underpowered_windows(report: BacktestReport) -> list[str]:
    """관측 개선이 그 창의 검출 한계(MDE)보다 작은 창 — "못 이겼다"가 아니라 "못 쟀다"."""
    return [
        f"{origin.window_start[:10]} n={origin.gate_test_row_count} "
        f"impr={origin.gate_improvement_ratio * 100:+.2f}% "
        f"< MDE={origin.gate_min_detectable_improvement * 100:.2f}%"
        for origin in report.origins
        if origin.gate_improvement_ratio < origin.gate_min_detectable_improvement
    ]


def _baseline_coverage(gate: AwardRateHoldoutReport | None) -> float | None:
    """게이트 창에서 베이스라인이 자기 셀로 예측한 비율. 낮으면 비교 대상에 구멍이 있다."""
    if gate is None:
        return None
    return next(
        (
            score.test_coverage
            for score in gate.baselines
            if score.name == GATE_BASELINE_NAME
        ),
        None,
    )


def _console_summary(report: BacktestReport, output_path: Path) -> ConsoleSummary:
    """콘솔 요약. 평가 창이 하나도 없으면 성적 칸은 ``null`` 로 남는다 — 잴 수 없었던
    것을 0.0 으로 채우면 "베이스라인과 동률"처럼 읽힌다."""
    gate = report.origins[-1] if report.origins else None
    return ConsoleSummary(
        out=str(output_path),
        corpus_row_count=report.corpus_row_count,
        feed_origin_only=report.feed_origin_only,
        evaluation_window_count=len(report.evaluation_windows),
        holdout_overlap_pair_count=report.holdout_overlap_pair_count,
        holdout_overlap_row_count=report.holdout_overlap_row_count,
        unaccounted_row_count=report.unaccounted_row_count,
        stability_seed_count=len(report.stability_seeds),
        gate_evaluable=report.gate_evaluable,
        gate_window_start=report.gate_window_start,
        gate_test_row_count=gate.gate_test_row_count if gate else None,
        gate_baseline_rmse=gate.gate_baseline_rmse if gate else None,
        gate_model_rmse=gate.gate_model_rmse if gate else None,
        gate_improvement_ratio=gate.gate_improvement_ratio if gate else None,
        gate_paired_t=gate.gate_paired_t if gate else None,
        gate_min_detectable_improvement=(
            gate.gate_min_detectable_improvement if gate else None
        ),
        gate_baseline_coverage=_baseline_coverage(gate),
        gate_regressed_segments=_regressed_segments(gate),
        seed_unstable_windows=_seed_unstable_windows(report),
        underpowered_windows=_underpowered_windows(report),
        gate_passed_at_latest_window=report.gate_passed_at_latest_window,
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
    report = build_backtest_report(
        rows,
        plan_evaluation_windows(
            rows,
            maturity_windows,
            policy=WindowPolicy(max_origins=args.max_origins),
        ),
        maturity_observation_count=observation_count,
        stability=bool(args.stability),
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
