"""win-proxy 백테스트 리포트 조립 — 셀·공종 신고와 인용 수치 대조 (순수).

곡선 산술은 :mod:`app.services.ml_training.award_landing_curves` 가, 그 아래 통계는
PR1 순수 커널이 한다. 이 모듈은 **무엇을 어떤 순서로 신고하는가**만 정한다. I/O 도
시계도 없다 — 행 목록을 주입받아 값 모델을 돌려주므로 DB 없이 조립 테스트가 선다(§4.7-3).

리포트가 스스로 말해야 하는 것
------------------------------
Phase 2c 의 교훈은 "통과했다"보다 **"잴 수 있었나"** 를 먼저 물으라는 것이었다. 그래서
모든 셀은 세 가지를 함께 신고한다: 표본 깊이와 G3 게이트 충족 여부, 판정의 불안정성
진단(동률 구간 폭·봉우리 수·평탄 여부), 그리고 **못 쟀으면 그 사유**(:attr:`CellReport.
unmeasurable_reason`). 얕은 셀의 빈 칸은 0 이 아니라 ``None`` 이다.

α 는 추천하지 않는다 (NEW-3)
-----------------------------
``α`` 는 채점 코퍼스가 아니라 **운영자 리스크 허용치**다. 이 리포트는 격자 위
트레이드오프만 보고하고 값을 고르지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from app.domain.award_landing_curve import (
    DEFAULT_GRID_STEP,
    ConstrainedOptimumStatus,
)
from app.services.ml_training.award_landing_cells import (
    DEFAULT_AS_OF_FIT_SHARE,
    AwardLandingCell,
    dominant_floor_subgroup,
    floor_rate_distribution,
    group_by_category,
    group_by_cell,
    split_as_of,
)
from app.services.ml_training.award_landing_curves import (
    ALPHA_TRADEOFF_GRID,
    CALIBRATION_GRID_STEP,
    CalibrationComparison,
    alpha_tradeoff,
    build_calibration,
    layer_a_summary,
    parent_margin_distribution,
)
from app.services.ml_training.award_landing_dataset import AwardLandingCorpus
from app.services.ml_training.award_landing_diagnostics import (
    build_assessment_summary,
    build_correlation_summary,
    build_negative_margin_summary,
    compare_to_quoted,
    median_or_none,
)
from app.services.ml_training.award_landing_ladder import (
    AMBIGUOUS_REGIME_LABEL,
    FLOOR_RATE_UNITY,
    LandingRow,
)
from app.services.ml_training.award_landing_schema import (
    AwardLandingReport,
    CategoryReport,
    CellReport,
    ConstrainedRollup,
    CorpusAccounting,
    RunParameters,
)

__all__ = [
    "MIN_CELL_ROWS",
    "AwardLandingReport",
    "CategoryReport",
    "CellReport",
    "build_award_landing_report",
]

# 부분셀 표본 깊이 게이트(G3). candidate-selector 판정문이 못박은 값을 그대로 쓴다 —
# 이 리포트가 새로 정하는 임계가 아니다(사후 완화 방지).
MIN_CELL_ROWS: Final[int] = 150

# 용역 셀 f 분포 인용값(설계 §6 M5)이 가리키는 하한율. 재산출 대조에 쓰는 좌표라
# 리터럴을 코드 흐름에 흩뿌리지 않고 여기 한 곳에 선언한다.
SERVICE_QUOTED_FLOOR_RATE: Final[float] = 0.88


def _floor_shares(rows: Sequence[LandingRow]) -> list[dict[str, float]]:
    return [
        {
            "floor_rate": share.floor_rate,
            "row_count": float(share.row_count),
            "share": share.share,
        }
        for share in floor_rate_distribution(rows)
    ]


def _cell_calibrations(
    dominant_rate: float | None,
    dominant_rows: Sequence[LandingRow],
    *,
    category_rows: Sequence[LandingRow],
    prior_strength: float,
    fit_share: float,
) -> tuple[CalibrationComparison | None, CalibrationComparison | None, str | None]:
    """(in-sample, as-of, as-of 불가 사유) — 못 쟀으면 사유가 함께 나온다.

    둘 다 **최빈 f 부분군**에서만 돈다: 층 C 는 단일 ``floor_rate`` 를 받으므로 f 가
    섞인 모집단에 그대로 대면 두 곡선의 차이에 합성 가정의 효과와 f 이질성이 뒤섞인다.
    """
    if dominant_rate is None or not dominant_rows:
        return None, None, "최빈 하한율 부분군이 비어 있어 층 C 대조를 낼 수 없습니다."
    in_sample = build_calibration(
        scope="in_sample_dominant_floor",
        fit_rows=dominant_rows,
        score_rows=dominant_rows,
        floor_rate=dominant_rate,
        parent_margins=parent_margin_distribution(category_rows, before=None),
        prior_strength=prior_strength,
    )
    fit_rows, score_rows = split_as_of(dominant_rows, fit_share=fit_share)
    if not fit_rows or not score_rows:
        return (
            in_sample,
            None,
            (
                f"최빈 하한율 부분군 {len(dominant_rows)}행의 개찰 시각이 "
                "한 값뿐이라 겹치지 않는 시간 분할이 성립하지 않습니다(못 쟀다)."
            ),
        )
    as_of = build_calibration(
        scope="as_of_dominant_floor",
        fit_rows=fit_rows,
        score_rows=score_rows,
        floor_rate=dominant_rate,
        # 경계는 **첫 채점 시각**이고 부모는 그 **미만**만 본다(B1). 적합 창의 마지막
        # 시각을 경계로 쓰면 그 시각을 공유하는 채점 행이 부모 분포로 새어 들어간다.
        parent_margins=parent_margin_distribution(
            category_rows, before=score_rows[0].opened_at
        ),
        prior_strength=prior_strength,
    )
    return in_sample, as_of, None


def _build_cell_report(
    cell: AwardLandingCell,
    rows: Sequence[LandingRow],
    *,
    category_rows: Sequence[LandingRow],
    grid_step: float,
    prior_strength: float,
    fit_share: float,
    min_cell_rows: int,
) -> CellReport:
    dominant_rate, dominant_rows = dominant_floor_subgroup(rows)
    curve, summary = layer_a_summary(rows, step=grid_step)
    in_sample, as_of, as_of_reason = _cell_calibrations(
        dominant_rate,
        dominant_rows,
        category_rows=category_rows,
        prior_strength=prior_strength,
        fit_share=fit_share,
    )
    dominant_share = (
        len(dominant_rows) / len(rows) if rows and dominant_rows else None
    )
    meets_gate = len(rows) >= min_cell_rows
    return CellReport(
        cell_key=cell.key,
        category=cell.category,
        era_tier=cell.era_tier,
        amount_band=cell.amount_band,
        row_count=len(rows),
        meets_depth_gate=meets_gate,
        unmeasurable_reason=_depth_reason(len(rows), min_cell_rows),
        ambiguous_share=_ambiguous_share(rows),
        layer_a=summary,
        naive_argmax_disqualification=summary.disqualification_at_optimum,
        floor_rate_shares=_floor_shares(rows),
        dominant_floor_rate=dominant_rate,
        dominant_floor_share=dominant_share,
        dominant_floor_row_count=len(dominant_rows),
        floor_mixture_caveat=_floor_mixture_caveat(rows, dominant_share),
        margin_median=median_or_none([row.margin for row in rows]),
        correlations=build_correlation_summary(rows),
        assessment=build_assessment_summary(rows),
        calibration_in_sample=in_sample,
        calibration_as_of=as_of,
        calibration_as_of_unmeasurable_reason=as_of_reason,
        alpha_tradeoff=alpha_tradeoff(curve),
    )


def _depth_reason(row_count: int, min_cell_rows: int) -> str | None:
    """G3 미달 사유. 통과하면 ``None`` — 빈 문자열로 두면 "사유 없음"과 안 갈린다."""
    if row_count >= min_cell_rows:
        return None
    return (
        f"셀 표본 {row_count}건 < G3 게이트 {min_cell_rows}건 — "
        "이 셀의 곡선은 판정이 아니라 관측이다(못 쟀다)."
    )


def _ambiguous_share(rows: Sequence[LandingRow]) -> float:
    """분류기가 ``floor_bound`` 로 확정하지 못한 행의 비율(M1)."""
    if not rows:
        return 0.0
    ambiguous = sum(
        1 for row in rows if row.regime_label == AMBIGUOUS_REGIME_LABEL
    )
    return ambiguous / len(rows)


def _floor_mixture_caveat(
    rows: Sequence[LandingRow], dominant_share: float | None
) -> str | None:
    """f-혼합 셀의 해석 경고(M5). 동질이면 ``None``."""
    if dominant_share is None or dominant_share >= 1.0:
        return None
    return (
        f"셀 {len(rows)}행에 게시 하한율이 섞여 있습니다(최빈 점유 "
        f"{dominant_share:.3f}). layer_a·naive_argmax_disqualification·"
        "alpha_tradeoff 는 셀 전체로 만들므로 한 f 의 함수가 아닙니다 — "
        "층 C 대조(최빈 f 부분군)와 스코프가 다릅니다."
    )


def _build_cell_reports(
    rows: Sequence[LandingRow],
    *,
    by_category: dict[str, tuple[LandingRow, ...]],
    grid_step: float,
    prior_strength: float,
    fit_share: float,
    min_cell_rows: int,
) -> list[CellReport]:
    """셀 리포트 목록(행 수 내림차순 — 깊은 셀이 먼저 읽히게)."""
    return [
        _build_cell_report(
            cell,
            cell_rows,
            category_rows=by_category.get(cell.category, ()),
            grid_step=grid_step,
            prior_strength=prior_strength,
            fit_share=fit_share,
            min_cell_rows=min_cell_rows,
        )
        for cell, cell_rows in sorted(
            group_by_cell(rows).items(), key=lambda item: (-len(item[1]), item[0].key)
        )
    ]


def _build_category_report(
    category: str, rows: Sequence[LandingRow], *, grid_step: float
) -> CategoryReport:
    _curve, summary = layer_a_summary(rows, step=grid_step)
    return CategoryReport(
        category=category,
        row_count=len(rows),
        layer_a=summary,
        naive_argmax_disqualification=summary.disqualification_at_optimum,
        margin_median=median_or_none([row.margin for row in rows]),
        correlations=build_correlation_summary(rows),
        floor_rate_shares=_floor_shares(rows),
    )


def _build_category_reports(
    by_category: dict[str, tuple[LandingRow, ...]], *, grid_step: float
) -> list[CategoryReport]:
    """공종 리포트 목록(이름 오름차순 — 실행마다 순서가 흔들리면 diff 가 안 읽힌다)."""
    return [
        _build_category_report(category, rows, grid_step=grid_step)
        for category, rows in sorted(by_category.items())
        if rows
    ]


def _stored_label_floor_mismatch(
    rows: Sequence[LandingRow], stored_floor_rate: dict[str, float | None]
) -> dict[str, float]:
    """저장 ``_final`` 라벨이 쓰는 하한 vs 게시 ``f`` 의 불일치 실측(M4).

    저장 라벨의 하한은 ``_resolve_floor_bid_rate(category)``(카테고리 상수)라 공고가
    게시한 ``award_floor_rate`` 와 다르다. **라벨 값을 대조하는 것이 아니라 하한 자체를**
    비교한다 — 라벨 대조는 두 다른 하한 위에서 잰 수를 같은 것처럼 놓는 일이다.
    """
    gaps = [
        row.floor_rate - float(stored)
        for row in rows
        if (stored := stored_floor_rate.get(row.category)) is not None
    ]
    if not gaps:
        return {"comparable_row_count": 0.0}
    mismatched = sum(1 for gap in gaps if gap != 0.0)
    return {
        "comparable_row_count": float(len(gaps)),
        "mismatched_row_count": float(mismatched),
        "mismatch_share": mismatched / len(gaps),
        "median_gap": float(median_or_none(gaps) or 0.0),
        "max_abs_gap": max(abs(gap) for gap in gaps),
    }


def _category_quotes(
    report: CategoryReport | None, suffix: str
) -> dict[str, tuple[float | None, int]]:
    """한 공종에서 나오는 인용 대조 항목들(없으면 전부 ``None`` = 못 쟀다)."""
    count = report.row_count if report else 0
    return {
        f"corr_floor_margin_{suffix}": (
            report.correlations.corr_floor_margin if report else None,
            count,
        ),
        f"naive_argmax_disqualification_{suffix}": (
            report.naive_argmax_disqualification if report else None,
            count,
        ),
        f"median_margin_{suffix}": (report.margin_median if report else None, count),
    }


def _service_floor_share(report: CategoryReport | None) -> float | None:
    if report is None:
        return None
    return next(
        (
            share["share"]
            for share in report.floor_rate_shares
            if share["floor_rate"] == SERVICE_QUOTED_FLOOR_RATE
        ),
        None,
    )


def _recomputed_quotes(
    accounting: CorpusAccounting, categories: Sequence[CategoryReport]
) -> dict[str, tuple[float | None, int]]:
    by_category = {report.category: report for report in categories}
    service = by_category.get("service")
    correlations = accounting.correlations
    assessment = accounting.assessment
    sample_count = correlations.sample_count
    quotes: dict[str, tuple[float | None, int]] = {
        "corr_assessment_margin": (correlations.corr_assessment_margin, sample_count),
        "corr_assessment_omega": (correlations.corr_assessment_omega, sample_count),
        "service_floor_share_088": (
            _service_floor_share(service),
            service.row_count if service else 0,
        ),
        "negative_margin_count": (
            float(accounting.negative_margins.count),
            accounting.candidate_count,
        ),
        "assessment_mean": (assessment.mean, assessment.sample_count),
        "assessment_median": (assessment.median_value, assessment.sample_count),
        "assessment_above_one_share": (
            assessment.above_one_share,
            assessment.sample_count,
        ),
    }
    quotes.update(_category_quotes(by_category.get("construction"), "construction"))
    quotes.update(_category_quotes(service, "service"))
    return quotes


def _constrained_rollup(
    cells: Sequence[CellReport], *, scope: str
) -> ConstrainedRollup:
    """α 판정 집계. 거리와 이득을 **함께** 낸다 — 거리만으로는 신호/잡음이 안 갈린다.

    ``cells`` 스코프는 호출부가 정한다(전 셀 / G3 통과 셀). 판정 불가 셀을 같은 평균에
    섞으면 헤드라인이 희석되므로 두 스코프를 각각 돌려 **병기**한다.
    """
    statuses: dict[str, int] = {status.value: 0 for status in ConstrainedOptimumStatus}
    distances: list[float] = []
    gains: list[float] = []
    binding = 0
    total = 0
    for cell in cells:
        for point in cell.alpha_tradeoff:
            statuses[point.status] = statuses.get(point.status, 0) + 1
            total += 1
            binding += 1 if point.binding else 0
            if point.distance_above_feasible_lower is not None:
                distances.append(point.distance_above_feasible_lower)
            if point.win_proxy is not None and (
                point.win_proxy_at_feasible_lower is not None
            ):
                gains.append(point.win_proxy - point.win_proxy_at_feasible_lower)
    return ConstrainedRollup(
        scope=scope,
        cell_count=len(cells),
        alpha_point_count=total,
        status_histogram=statuses,
        binding_share=binding / total if total else None,
        median_distance_above_feasible_lower=median_or_none(distances),
        median_alpha_gain_over_feasible_lower=median_or_none(gains),
    )


def build_award_landing_report(
    corpus: AwardLandingCorpus,
    *,
    prior_strength: float,
    stored_floor_rate: dict[str, float | None],
    grid_step: float = DEFAULT_GRID_STEP,
    fit_share: float = DEFAULT_AS_OF_FIT_SHARE,
    min_cell_rows: int = MIN_CELL_ROWS,
    now: datetime | None = None,
) -> AwardLandingReport:
    """코퍼스 → 리포트. I/O 없음(시계도 주입 — §4.7-3).

    ``prior_strength``(κ)는 **기본값이 없다** — 상수로 박으면 사후 산물이 되므로 릴리스/
    실행 단위로 선언한다(설계 §5.2). ``stored_floor_rate`` 는 공종 → 저장 ``_final``
    라벨이 쓰는 하한율로, M4 불일치 실측의 대조 입력이다.
    """
    by_category = group_by_category(corpus.rows)
    categories = _build_category_reports(by_category, grid_step=grid_step)
    cells = _build_cell_reports(
        corpus.rows,
        by_category=by_category,
        grid_step=grid_step,
        prior_strength=prior_strength,
        fit_share=fit_share,
        min_cell_rows=min_cell_rows,
    )
    accounting = _corpus_accounting(corpus, stored_floor_rate=stored_floor_rate)
    deep_cells = [cell for cell in cells if cell.meets_depth_gate]
    return AwardLandingReport(
        generated_at=(now or datetime.now(UTC)).isoformat(),
        parameters=_run_parameters(
            corpus,
            prior_strength=prior_strength,
            grid_step=grid_step,
            fit_share=fit_share,
            min_cell_rows=min_cell_rows,
        ),
        corpus=accounting,
        cells=cells,
        categories=categories,
        quoted_comparisons=compare_to_quoted(
            _recomputed_quotes(accounting, categories)
        ),
        measurable_cell_count=len(deep_cells),
        constrained_all_cells=_constrained_rollup(cells, scope="all_cells"),
        constrained_deep_cells=_constrained_rollup(deep_cells, scope="deep_cells"),
    )


def _run_parameters(
    corpus: AwardLandingCorpus,
    *,
    prior_strength: float,
    grid_step: float,
    fit_share: float,
    min_cell_rows: int,
) -> RunParameters:
    """실행 선언값 에코 — 코퍼스가 스스로 신고하는 것(게이트·as-of)과 함께 묶는다."""
    return RunParameters(
        regime_gate=corpus.regime_gate,
        as_of=corpus.as_of.isoformat() if corpus.as_of else None,
        prior_strength=prior_strength,
        grid_step=grid_step,
        calibration_grid_step=CALIBRATION_GRID_STEP,
        as_of_fit_share=fit_share,
        min_cell_rows=min_cell_rows,
        alpha_grid=list(ALPHA_TRADEOFF_GRID),
    )


def _corpus_accounting(
    corpus: AwardLandingCorpus, *, stored_floor_rate: dict[str, float | None]
) -> CorpusAccounting:
    """코퍼스 전역 회계·진단(사다리 계수 · 상관 · 사정률 · M4 불일치)."""
    return CorpusAccounting(
        candidate_count=corpus.candidate_count,
        accepted_count=corpus.accepted_count,
        ladder_counts=corpus.ladder_counts,
        regime_histogram=corpus.regime_histogram,
        negative_margins=build_negative_margin_summary(
            corpus.negative_margins, unity_floor_rate=FLOOR_RATE_UNITY
        ),
        correlations=build_correlation_summary(corpus.rows),
        assessment=build_assessment_summary(corpus.rows),
        stored_label_floor_mismatch=_stored_label_floor_mismatch(
            corpus.rows, stored_floor_rate
        ),
    )
