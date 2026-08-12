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
from math import erf, sqrt
from pathlib import Path
from statistics import fmean, pvariance
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
from app.ai.predictors.distribution_extraction import (  # noqa: E402
    ReserveDrawObservation,
    observe_reserve_draw,
)
from app.ai.predictors.historical import (  # noqa: E402
    normalize_agency_name,
    normalize_category_key,
)
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.domain.assessment_shrinkage import (  # noqa: E402
    AGENCY_PRIOR_STRENGTH,
    CATEGORY_PRIOR_STRENGTH,
    MIN_PREDICTIVE_STD,
    LevelObservation,
    resolve_assessment_posterior,
)
from app.domain.reserve_draw_distribution import (  # noqa: E402
    EXPECTED_RESERVE_PRICE_COUNT,
    exact_draw_mean_distribution,
)
from app.models.models import HistoricalData, TenderResult  # noqa: E402
from app.services.base_amount_basis import BASIS_CLEAN  # noqa: E402
from app.utils.sequence_coercion import (  # noqa: E402
    coerce_integer_list,
    coerce_numeric_list,
)
from scripts._common import parse_datetime  # noqa: E402

# 명목 커버리지 수준(선언 데이터 §4.5)과, prior 축 평가를 시작할 최소 선행 이력.
COVERAGE_LEVELS = (0.5, 0.8, 0.9)
MIN_PRIOR_ROWS_FOR_COVERAGE = 50


class CoverageLevelStat(BaseModel):
    nominal: float
    empirical: float


class PitSummary(BaseModel):
    """PIT 균등성 요약 — 균등이면 mean 0.5, std ≈ 0.2887(√(1/12))."""

    sample_count: int
    pit_mean: float | None = None
    pit_std: float | None = None
    coverage: dict[str, CoverageLevelStat] = {}


class CoverageReport(BaseModel):
    prior_predictive: PitSummary
    prior_mean_absolute_center_error: float | None
    # 표준화 잔차 z 모양 진단(균등이면 0/1/0): mean≠0=계통 편향, 첨도≫0=정규근사가
    # 못 잡는 뾰족한 중심+두꺼운 꼬리(과커버의 실제 원인).
    prior_standardized_residual_mean: float | None
    prior_standardized_residual_std: float | None
    prior_excess_kurtosis: float | None
    mechanism_exact_draw: PitSummary
    skipped_no_selected_numbers: int
    # 엔진과 같은 관측 관문(15개·center 밴드)에서 걸러진 행 수 — 침묵 스킵 금지(L4-1).
    skipped_unobservable: int
    agency_count: int
    category_count: int


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
        # 정렬이 오래된 순이라 코어가 limit 초과면 최신 구간은 --start-date 로 잡아라.
        help="Maximum core rows loaded (oldest-first; use --start-date for recent windows).",
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


class _RunningLevel:
    """한 계층의 (count, mean, 모분산) 을 온라인으로 유지한다 (Welford)."""

    __slots__ = ("count", "mean", "_m2")

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)

    def observation(self) -> LevelObservation | None:
        if self.count == 0:
            return None
        return LevelObservation(
            sample_count=self.count,
            mean=self.mean,
            variance=(self._m2 / self.count) if self.count >= 2 else 0.0,
        )


def normal_cdf(value: float, *, mean: float, std: float) -> float:
    """예측분포 정규근사의 CDF — PIT 계산용."""
    if std <= 0:
        return 0.5
    return 0.5 * (1.0 + erf((value - mean) / (std * sqrt(2.0))))


def summarize_pit(pit_values: list[float]) -> PitSummary:
    """PIT 균등성 요약 — 명목 vs 실측 중앙 구간 커버리지."""
    if not pit_values:
        return PitSummary(sample_count=0)
    coverage: dict[str, CoverageLevelStat] = {}
    for level in COVERAGE_LEVELS:
        tail = (1.0 - level) / 2.0
        inside = sum(1 for pit in pit_values if tail <= pit <= 1.0 - tail)
        coverage[f"central_{int(level * 100)}"] = CoverageLevelStat(
            nominal=level, empirical=round(inside / len(pit_values), 4)
        )
    return PitSummary(
        sample_count=len(pit_values),
        pit_mean=round(fmean(pit_values), 4),
        pit_std=(
            round(sqrt(pvariance(pit_values)), 4) if len(pit_values) >= 2 else 0.0
        ),
        coverage=coverage,
    )


def standardized_shape_stats(
    values: list[float],
) -> tuple[float | None, float | None, float | None]:
    """표준화 잔차의 (mean, std, 초과 첨도) — 정규근사 모양 진단(균등이면 0/1/0)."""
    if len(values) < 2:
        return None, None, None
    mean_value = fmean(values)
    second_moment = pvariance(values)
    if second_moment <= 0:
        return round(mean_value, 4), 0.0, None
    fourth_moment = fmean([(value - mean_value) ** 4 for value in values])
    return (
        round(mean_value, 4),
        round(sqrt(second_moment), 4),
        round((fourth_moment / (second_moment**2)) - 3.0, 2),
    )


class _CoverageAccumulator:
    """시간순 단일 패스 상태 — 평가는 항상 프리픽스 편입 **전**(시간 누수 차단)."""

    def __init__(self) -> None:
        self.agency_levels: dict[str, _RunningLevel] = {}
        self.category_levels: dict[str, _RunningLevel] = {}
        self.global_level = _RunningLevel()
        self.draw_variance_level = _RunningLevel()
        self.prior_pit: list[float] = []
        self.mechanism_pit: list[float] = []
        self.prior_absolute_errors: list[float] = []
        self.prior_standardized_residuals: list[float] = []
        self.skipped_no_pick = 0
        self.skipped_unobservable = 0

    def prior_pit_for(
        self, *, realized: float, agency_key: str, category_key: str
    ) -> None:
        """선행 이력만으로 만든 예측분포에서 실현 사정률의 PIT.

        추첨 분산은 프리픽스 평균만 쓴다 — 평가 행 자신의 값을 쓰면 누수다.
        """
        agency_level = self.agency_levels.get(agency_key)
        category_level = self.category_levels.get(category_key)
        global_observation = self.global_level.observation()
        if global_observation is None:
            return
        posterior = resolve_assessment_posterior(
            agency=agency_level.observation() if agency_level else None,
            category=category_level.observation() if category_level else None,
            global_level=global_observation,
        )
        predictive_std = sqrt(
            (posterior.std**2) + max(self.draw_variance_level.mean, 0.0)
        )
        self.prior_pit.append(
            normal_cdf(realized, mean=posterior.mean, std=predictive_std)
        )
        self.prior_absolute_errors.append(abs(posterior.mean - realized))
        if predictive_std > 0:
            self.prior_standardized_residuals.append(
                (realized - posterior.mean) / predictive_std
            )

    def evaluate_and_absorb(
        self,
        observed: ReserveDrawObservation,
        *,
        agency_key: str,
        category_key: str,
    ) -> None:
        """평가(선행 프리픽스만 사용) 후에야 관측을 집계에 편입한다(누수 차단)."""
        realized = observed.realized_assessment
        if realized is None:
            self.skipped_no_pick += 1
        else:
            # 메커니즘 축: 공고 자신의 15개 완전 열거 분포에서 실현 추첨 평균의 PIT.
            self.mechanism_pit.append(
                exact_draw_mean_distribution(
                    list(observed.ratios)
                ).cumulative_probability(realized)
            )
            if self.global_level.count >= MIN_PRIOR_ROWS_FOR_COVERAGE:
                self.prior_pit_for(
                    realized=realized,
                    agency_key=agency_key,
                    category_key=category_key,
                )
        self.absorb(
            center=observed.center,
            draw_variance=observed.draw_variance,
            agency_key=agency_key,
            category_key=category_key,
        )

    def absorb(
        self, *, center: float, draw_variance: float, agency_key: str, category_key: str
    ) -> None:
        if agency_key:
            self.agency_levels.setdefault(agency_key, _RunningLevel()).add(center)
        if category_key:
            self.category_levels.setdefault(category_key, _RunningLevel()).add(center)
        self.global_level.add(center)
        self.draw_variance_level.add(draw_variance)


def run_coverage_backtest(rows: list[HistoricalData]) -> CoverageReport:
    """시간순 단일 패스: 선행 행만으로 사후분포를 만들고 실현 사정률의 PIT 를 잰다.

    행→관측 추출은 엔진과 **같은 함수**(``observe_reserve_draw``, 리뷰 L4-1)를
    쓴다 — 관문 밖 행은 크래시 대신 관측 불가로 계수·skip 된다.
    """
    state = _CoverageAccumulator()
    for row in rows:
        observed = observe_reserve_draw(
            reserve_prices=coerce_numeric_list(row.reserve_prices),
            base_amount=float(row.base_amount or 0.0),
            picked_numbers=coerce_integer_list(row.selected_numbers),
            bid_rate=None,
        )
        if observed is None:
            state.skipped_unobservable += 1
            continue
        state.evaluate_and_absorb(
            observed,
            agency_key=normalize_agency_name(row.agency_name),
            category_key=normalize_category_key(row.category),
        )

    residual_mean, residual_std, excess_kurtosis = standardized_shape_stats(
        state.prior_standardized_residuals
    )
    return CoverageReport(
        prior_predictive=summarize_pit(state.prior_pit),
        prior_mean_absolute_center_error=(
            round(fmean(state.prior_absolute_errors), 6)
            if state.prior_absolute_errors
            else None
        ),
        prior_standardized_residual_mean=residual_mean,
        prior_standardized_residual_std=residual_std,
        prior_excess_kurtosis=excess_kurtosis,
        mechanism_exact_draw=summarize_pit(state.mechanism_pit),
        skipped_no_selected_numbers=state.skipped_no_pick,
        skipped_unobservable=state.skipped_unobservable,
        agency_count=len(state.agency_levels),
        category_count=len(state.category_levels),
    )


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
