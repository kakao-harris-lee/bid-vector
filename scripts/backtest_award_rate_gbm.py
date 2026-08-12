#!/usr/bin/env python3
"""낙찰률 GBM(Phase 2) out-of-time 홀드아웃 — 사전 선언 게이트 판정 + 아티팩트 생성.

무엇을 재는가
-------------
``clean-base`` 층의 시간 홀드아웃에서 다섯 그룹 평균 베이스라인(전역·공종·금액대·
공종×금액대·발주기관)과 GBM 두 변형(전 층 학습 / 평가 층만 학습)을 나란히 잰다.
판정식은 사전 선언돼 있다: **GBM 이 ``category × amount band`` 를 유의하게 넘는가**
(:mod:`app.services.ml_training.award_rate_holdout`). 게이트를 사후에 느슨하게 만들지
않기 위해 임계와 비교 대상이 코드의 선언 상수이고 이 스크립트에는 없다.

``--rolling-origins`` 로 여러 cutoff 를 훑을 수 있다. 한 분할의 우연을 배제하려는
것이므로, 한 지점만 좋고 나머지가 나쁘면 그것도 결과다.

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
        --rolling-origins 0.60,0.65,0.70,0.75,0.80 \\
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
from app.core.database import SessionLocal
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

# 기본 rolling origin. 사전 선언된 베이스라인 표가 0.70 에서 측정됐으므로 그 지점이
# 반드시 포함된다.
_DEFAULT_ORIGINS = "0.60,0.65,0.70,0.75,0.80"
_GATE_ORIGIN = 0.70


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
    """rolling origin 전체의 홀드아웃 결과 + 게이트 요약."""

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
    encoding_folds: int
    training_seed: int
    origins: list[AwardRateHoldoutReport] = Field(default_factory=list)
    gate_origin: float
    gate_passed: bool
    """사전 선언 지점(0.70)의 판정. 다른 origin 은 견고성 확인용이다."""
    gate_passed_at_all_origins: bool
    artifact_out: str | None = None
    artifact_training_row_count: int | None = None


class ConsoleSummary(StrictModel):
    """콘솔 한 줄 요약(리포트 파일 경로 + 게이트 결론)."""

    out: str
    corpus_row_count: int
    feed_origin_only: bool
    gate_baseline_rmse: float
    gate_model_rmse: float
    gate_improvement_ratio: float
    gate_paired_t: float
    gate_passed: bool
    gate_passed_at_all_origins: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Out-of-time holdout gate for the award-rate GBM predictor."
    )
    parser.add_argument(
        "--rolling-origins",
        default=_DEFAULT_ORIGINS,
        help=(
            "Comma-separated train fractions of the evaluation stratum. "
            f"The declared gate origin ({_GATE_ORIGIN}) is always evaluated."
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


def parse_origins(raw: str) -> list[float]:
    """쉼표 목록 → 정렬된 분할 비율. 게이트 지점은 빠질 수 없다."""
    origins = {
        value
        for value in (float(part) for part in raw.split(",") if part.strip())
        if 0.0 < value < 1.0
    }
    origins.add(_GATE_ORIGIN)
    return sorted(origins)


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


def build_report(
    rows: list[AwardRateTrainingRow],
    *,
    origins: list[float],
    folds: int,
    seed: int,
    feed_origin_only: bool,
) -> BacktestReport:
    """rolling origin 을 훑어 홀드아웃 결과를 모은다."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.denominator_source] = counts.get(row.denominator_source, 0) + 1
    evaluations = [
        evaluate_award_rate_holdout(rows, train_fraction=origin, folds=folds, seed=seed)
        for origin in origins
    ]
    gate_index = origins.index(_GATE_ORIGIN)
    return BacktestReport(
        generated_at=datetime.now(UTC).isoformat(),
        corpus_row_count=len(rows),
        feed_origin_only=feed_origin_only,
        corpus_first_opened_at=(
            min(row.opened_at for row in rows).isoformat() if rows else None
        ),
        corpus_last_opened_at=(
            max(row.opened_at for row in rows).isoformat() if rows else None
        ),
        corpus_published_floor_row_count=sum(
            1 for row in rows if row.published_floor_rate is not None
        ),
        strata=[
            StratumCount(denominator_source=source, row_count=count)
            for source, count in sorted(counts.items())
        ],
        categories=_category_counts(rows),
        gate_stratum=GATE_STRATUM,
        gate_baseline_name=GATE_BASELINE_NAME,
        gate_paired_t_threshold=GATE_PAIRED_T_THRESHOLD,
        encoding_folds=folds,
        training_seed=seed,
        origins=evaluations,
        gate_origin=_GATE_ORIGIN,
        gate_passed=evaluations[gate_index].gate_passed,
        gate_passed_at_all_origins=all(
            evaluation.gate_passed for evaluation in evaluations
        ),
    )


def write_artifact(
    rows: list[AwardRateTrainingRow], path: Path, *, folds: int, seed: int
) -> int:
    """전 구간으로 학습한 서빙 아티팩트를 저장하고 학습 행 수를 돌려준다.

    학습기는 JSON 페이로드를 내주고, 저장은 **읽기 계약을 한 번 통과시킨 뒤** 한다 —
    디스크에 남는 파일이 서빙이 읽을 수 있는 것임을 파일이 생기는 지점에서 고정한다.
    """
    from app.ai.predictors.artifact_contracts import PersistedAwardRateGbmArtifact

    artifact = train_award_rate_gbm(rows, folds=folds, seed=seed)
    validated = PersistedAwardRateGbmArtifact.model_validate(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(validated.model_dump_json(), encoding="utf-8")
    return len(rows)


def load_corpus(*, feed_origin_only: bool) -> list[AwardRateTrainingRow]:
    """표본 정의 한 벌로 코퍼스를 싣는다(SELECT 전용 — write/commit 없음)."""
    db = SessionLocal()
    try:
        return load_award_rate_rows(db, feed_origin_only=feed_origin_only)
    finally:
        db.close()


def resolve_report_path(raw: str) -> Path:
    """리포트 경로(미지정이면 타임스탬프 파일)를 만들고 상위 디렉터리를 보장한다."""
    path = Path(
        raw
        or "models/reports/award-rate-gbm-backtest-"
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    args = build_parser().parse_args()
    origins = parse_origins(args.rolling_origins)
    output_path = resolve_report_path(args.out)
    feed_origin_only = (
        bool(settings.PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY)
        if args.feed_origin_only is None
        else bool(args.feed_origin_only)
    )
    rows = load_corpus(feed_origin_only=feed_origin_only)
    report = build_report(
        rows,
        origins=origins,
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
                    rows, artifact_path, folds=args.folds, seed=args.seed
                ),
            }
        )

    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    gate = report.origins[origins.index(_GATE_ORIGIN)]
    print(
        ConsoleSummary(
            out=str(output_path),
            corpus_row_count=report.corpus_row_count,
            feed_origin_only=report.feed_origin_only,
            gate_baseline_rmse=gate.gate_baseline_rmse,
            gate_model_rmse=gate.gate_model_rmse,
            gate_improvement_ratio=gate.gate_improvement_ratio,
            gate_paired_t=gate.gate_paired_t,
            gate_passed=report.gate_passed,
            gate_passed_at_all_origins=report.gate_passed_at_all_origins,
        ).model_dump_json()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
