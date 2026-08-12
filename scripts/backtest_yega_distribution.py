#!/usr/bin/env python3
"""예정가 분포 엔진(Phase 1) 캘리브레이션 홀드아웃 — 커버리지 검정 + 점추정 비교.

코어 표본(15개 복수예비가격 ∩ clean ∩ 정산완료)에 두 축을 검증한다(개선 강제 게이트
아님 — 수치는 그대로 보고): ①점추정 — rolling holdout 에 historical/distribution 을
나란히 태워 |예측 사정률 − 실측| 비교, ②커버리지(PIT) — 각 홀드아웃 공고 **이전**
행만으로 수축 사후분포를 만들고(시간 누수 차단) 실현 사정률의 PIT 균등성과 중앙
50/80/90% 커버리지를 측정. 메커니즘 축(공고 자신의 완전 열거 분포에서 실현 추첨
평균의 PIT — 4/15 균등 복권 가정 자체의 검정)도 따로 잰다.

읽기 전용(SELECT)이며 DB 에 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel  # noqa: E402
from sqlalchemy import and_, exists  # noqa: E402
from sqlalchemy.orm import Query, Session  # noqa: E402

from app.ai.predictor_backtest import build_predictor_backtest_report  # noqa: E402
from app.ai.predictors import (  # noqa: E402
    BasePricePredictor,
    HistoricalStatisticalPredictor,
    PricePredictionContext,
    ReserveDrawDistributionPredictor,
)
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.domain.assessment_shrinkage import (  # noqa: E402
    AGENCY_PRIOR_STRENGTH,
    CATEGORY_PRIOR_STRENGTH,
    MIN_PREDICTIVE_STD,
)
from app.domain.reserve_draw_distribution import (  # noqa: E402
    EXPECTED_RESERVE_PRICE_COUNT,
)
from app.models.models import HistoricalData, TenderResult  # noqa: E402
from app.services.base_amount_basis import BASIS_CLEAN  # noqa: E402
from app.utils.sequence_coercion import (  # noqa: E402
    coerce_numeric_list,
)
from scripts._common import parse_datetime  # noqa: E402
from scripts._yega_coverage import (  # noqa: E402
    MIN_PRIOR_ROWS_FOR_COVERAGE,
    CoverageLevelStat,
    CoverageReport,
    run_coverage_backtest,
)


class HoldoutReport(BaseModel):
    generated_at: str
    category: str | None
    core_row_count: int
    holdout_size: int
    min_training_samples: int
    min_prior_rows_for_coverage: int
    # 수축 모수 프로버넌스(L4-4) — Phase 2 κ 재추정 후 아티팩트를 형태로 구별한다(#357 축).
    prior_strength_agency: float
    prior_strength_category: float
    min_predictive_std: float
    coverage: CoverageReport
    point_error: dict[str, Any]


class ConsoleSummary(BaseModel):
    out: str
    core_row_count: int
    prior_coverage: dict[str, CoverageLevelStat]
    mechanism_coverage: dict[str, CoverageLevelStat]
    # best = "자동 승격 제외 arm 을 뺀 최선" — 방법론 메타를 콘솔에도 노출(K5).
    best_predictor_key: str | None
    excluded_predictor_arms: list[str]
    report_version: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibration holdout for the reserve-draw distribution engine."
    )
    parser.add_argument("--category", default="", help="Optional category filter.")
    parser.add_argument("--start-date", help="opened_at/created_at window start.")
    parser.add_argument("--end-date", help="opened_at/created_at window end.")
    parser.add_argument(
        "--limit", type=int, default=8000,
        help=(
            "Maximum core rows loaded. 정렬이 오래된 순이라 코어가 limit 보다 크면 "
            "가장 오래된 행부터 잡힌다 — 최근 구간을 보려면 --start-date 를 함께 써라."
        ),
    )
    parser.add_argument(
        "--holdout-size", type=int, default=200,
        help="Rolling point-error holdout rows (build_predictor_backtest_report).",
    )
    parser.add_argument(
        "--min-training-samples", type=int, default=None,
        help="Override PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES.",
    )
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    return parser


def _window_filtered(
    query: Query, start_at: datetime | None, end_at: datetime | None
) -> Query:
    """opened_at 우선(폴백 created_at) 시간창 — 기존 백테스트 스크립트와 동일 규칙."""
    if start_at is not None:
        query = query.filter(
            (HistoricalData.opened_at >= start_at)
            | ((HistoricalData.opened_at.is_(None)) & (HistoricalData.created_at >= start_at))
        )
    if end_at is not None:
        query = query.filter(
            (HistoricalData.opened_at <= end_at)
            | ((HistoricalData.opened_at.is_(None)) & (HistoricalData.created_at <= end_at))
        )
    return query


def load_core_rows(
    db: Session,
    *,
    category: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
    limit: int,
) -> list[HistoricalData]:
    """코어: clean ∩ 정산완료 ∩ 예비가 15개(Text JSON 이라 15개는 적재 후 확정)."""
    settled = exists().where(
        and_(
            TenderResult.project_id == HistoricalData.project_id,
            TenderResult.winning_amount > 0,
        )
    )
    query = db.query(HistoricalData).filter(
        HistoricalData.base_amount_basis == BASIS_CLEAN,
        HistoricalData.bid_rate > 0,
        HistoricalData.base_amount > 0,
        HistoricalData.reserve_prices.isnot(None),
        HistoricalData.reserve_prices != "[]",
        settled,
    )
    if category:
        query = query.filter(HistoricalData.category == category)
    rows = (
        _window_filtered(query, start_at, end_at)
        .order_by(
            HistoricalData.opened_at.asc(),
            HistoricalData.created_at.asc(),
            HistoricalData.id.asc(),
        )
        .limit(max(1, int(limit or 1)))
        .all()
    )
    return [
        row
        for row in rows
        if len(coerce_numeric_list(row.reserve_prices)) == EXPECTED_RESERVE_PRICE_COUNT
    ]


def build_point_error_registry() -> dict[str, BasePricePredictor]:
    """historical vs distribution 을 나란히 태우는 rolling holdout 레지스트리."""
    return {
        "historical": HistoricalStatisticalPredictor(),
        "distribution": ReserveDrawDistributionPredictor(),
    }


def build_report(rows: list[HistoricalData], *, category: str) -> HoldoutReport:
    """커버리지 + 점추정 축을 하나의 보고서로 조립한다."""
    context = PricePredictionContext(
        budget=float(rows[-1].base_amount or 0.0) if rows else 0.0,
        category=category or "all",
        description="Reserve-draw distribution calibration holdout",
        historical_records=tuple(rows),
        agency_name=None,
    )
    return HoldoutReport(
        generated_at=datetime.now(UTC).isoformat(),
        category=category or None,
        core_row_count=len(rows),
        holdout_size=int(settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE),
        min_training_samples=int(settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES),
        min_prior_rows_for_coverage=MIN_PRIOR_ROWS_FOR_COVERAGE,
        prior_strength_agency=AGENCY_PRIOR_STRENGTH,
        prior_strength_category=CATEGORY_PRIOR_STRENGTH,
        min_predictive_std=MIN_PREDICTIVE_STD,
        coverage=run_coverage_backtest(rows),
        # use_record_context=True: 홀드아웃 각 행을 **그 행의** 기관·공종으로 평가한다.
        # 이것을 끄면 계층 predictor 의 agency/category 관측이 비어 사후분포가 전역
        # 평균으로 붕괴하고, 비교는 "전역 평균 모델 vs historical"이 된다(리뷰 J2).
        point_error=build_predictor_backtest_report(
            context, build_point_error_registry(), use_record_context=True
        ),
    )


def main() -> int:
    args = build_parser().parse_args()
    settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE = max(1, int(args.holdout_size))
    if args.min_training_samples is not None:
        settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES = max(
            1, int(args.min_training_samples)
        )
    # distribution predictor 는 실험 플래그 뒤에 있다 — 이 스크립트 프로세스 안에서만 연다.
    settings.PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS = True

    category = args.category.strip()
    output_path = Path(
        args.out
        or f"models/reports/yega-distribution-backtest-{category or 'all'}-"
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        rows = load_core_rows(
            db,
            category=category or None,
            start_at=parse_datetime(args.start_date),
            end_at=parse_datetime(args.end_date, end_of_day=True),
            limit=args.limit,
        )
    finally:
        db.close()

    report = build_report(rows, category=category)
    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        ConsoleSummary(
            out=str(output_path),
            core_row_count=len(rows),
            prior_coverage=report.coverage.prior_predictive.coverage,
            mechanism_coverage=report.coverage.mechanism_exact_draw.coverage,
            best_predictor_key=report.point_error.get("best_predictor_key"),
            excluded_predictor_arms=list(
                report.point_error.get("excluded_predictor_arms") or []
            ),
            report_version=report.point_error.get("report_version"),
        ).model_dump_json()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
