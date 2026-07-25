#!/usr/bin/env python3
"""추천 투찰가 오차의 eligibility 세그먼트 집계 리포트 (읽기 전용).

로드맵 G-3 exit(docs/roadmap.md:247)는 엔지니어링협회 조건 공고 세그먼트에 대해
식별 precision/recall·오탐 원인·**추천 투찰가 오차**·후보 없음 원인을 "별도
세그먼트로" 요구한다. 앞 2종은 ``report_eligibility_precision.py`` 가 담당하지만
**추천 투찰가 오차를 eligibility 세그먼트로 집계하는 리포트가 없었다** — 이 갭을 채운다.

이 스크립트는 개찰완료(award 보유) 공고를, 두 축의 eligibility 세그먼트로 나눠
각 세그먼트별 추천 투찰가 오차(건수·평균 절대오차율·threshold 이내 건수)를 집계한다:
    (a) ``NoticeEligibilityLabel(source="rule").label`` 조인 (positive/negative/ambiguous)
    (b) ``eligibility_labeling.classify_eligibility`` 로 ``Project.eligibility_raw`` 재판정

오차 측정은 ``backtest_latest_award_holdouts.py`` 의 leakage-free 홀드아웃 골격을
그대로 재사용한다(``evaluate_target``·``predict_price``·``PredictionDatasetService``
·추천가 vs 실낙찰가 절대오차율, as-of 시간 누수 방지 규칙 포함). 이 리포트는 그 결과에
eligibility 세그먼트 라벨만 붙여 재집계하는 read-only 소비자이며, app/ 동작을 바꾸지
않는다(predictor/guardrail/config/모델 불변, 마이그레이션 0).

정직 명세(§2): 이 리포트는 **추천 투찰가 오차 전용**이다. 식별 precision/recall 은
``report_eligibility_precision.py`` 소관이며 operator 정답 라벨을 기다린다. positive
settled 표본은 작고 단일 카테고리에 몰려 과적합·일반화가 불가하며, eligibility_raw
커버리지가 낮아 세그먼트가 전체 공고를 대표하지 않는다 — 표본 수·커버리지는 실행 시
실측해 출력한다(하드코딩 금지).

DB read-only. 외부 API(KONEPS/Telegram) 호출 0. 시크릿/개인정보 미출력. 시각은 KST.

사용 예:
    docker compose exec -T api python scripts/report_eligibility_segment_backtest.py
    docker compose exec -T api python scripts/report_eligibility_segment_backtest.py \
        --max-per-segment 50 --include-contaminated
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.constants import OPEN_PROJECT_STATUS as _OPEN_STATUS  # noqa: E402
from app.models.models import (  # noqa: E402
    NoticeEligibilityLabel,
    Project,
    TenderResult,
)
from app.services.base_amount_basis import BASIS_CLEAN  # noqa: E402
from app.services.eligibility_labeling import (  # noqa: E402
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    SOURCE_RULE,
    classify_eligibility,
    extract_eligibility_labels,
)
from app.services.query_predicates import settled_any_signal  # noqa: E402

# --- 매직값은 여기 선언(§4.5.1) ---------------------------------------------

_LOG_PREFIX = "[eligibility-segment-backtest]"
_NO_CATEGORY = "(none)"
_UNLABELED = "(unlabeled)"

# 세그먼트 축 키 — eligibility 세그먼트를 두 방식으로 판정한다.
AXIS_RULE_LABEL = "rule_label"  # 영속 NoticeEligibilityLabel(source=rule)
AXIS_CLASSIFY = "classify"  # eligibility_raw 를 classify_eligibility 로 재판정
AXIS_VALUES = (AXIS_RULE_LABEL, AXIS_CLASSIFY)

# 세그먼트 출력 순서(eligibility_labeling 라벨 어휘의 단일 출처 재사용, 복붙 금지).
SEGMENT_ORDER = (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIGUOUS)

# 오차 측정 홀드아웃과 정렬한 기본값(§4.5.1) — 홀드아웃 스크립트와 같은 축.
DEFAULT_GROUPS = ("construction", "service", "goods")
DEFAULT_THRESHOLDS = (0.001, 0.003, 0.005, 0.01)
DEFAULT_HISTORY_LIMIT = 1000
# 세그먼트당 최신 개찰건 상한(0=무제한). negative 는 수천 건이라 예측 시간이 크므로
# 최신순(leakage-free) 표본을 세그먼트별로 제한한다. positive 는 소수라 전량 포함된다.
DEFAULT_MAX_PER_SEGMENT = 200
# _OPEN_STATUS 는 app/core/constants.py 의 OPEN_PROJECT_STATUS 단일 출처를 소비한다
# ("open" only, re_notice 제외). 위 import 참조.

# 정직 명세(§2) 고정 caveat — 표본 수/커버리지 수치는 실행 시 실측해 별도 라인으로
# 출력하고(하드코딩 금지), 이 상수는 해석/경계만 고정한다.
CAVEAT_HEADER = "정직 명세(§2) — 이 리포트는 추천 투찰가 오차 전용:"
CAVEATS: tuple[str, ...] = (
    "precision/recall 아님: 식별 정확도는 report_eligibility_precision.py 소관이며 "
    "operator 정답 라벨(현재 0)을 기다린다. 이 리포트는 투찰가 오차만 다룬다.",
    "positive settled 표본이 작고(아래 실측 n) 단일 카테고리에 몰릴 수 있어 "
    "과적합·타 카테고리 일반화 불가 — 세그먼트별 카테고리 분포를 함께 출력한다.",
    "eligibility_raw 커버리지가 낮으면(아래 실측 %) 라벨 세그먼트가 전체 공고를 "
    "대표하지 않는다(자격 데이터 부재 공고는 ambiguous).",
    "오차는 개찰완료(award) 공고의 leakage-free as-of 예측 대비 실낙찰가 절대오차율이며 "
    "backtest_latest_award_holdouts 의 홀드아웃 규칙을 그대로 재사용한다.",
)


# --- 결과 스키마 -------------------------------------------------------------


@dataclass(frozen=True)
class SegmentRow:
    """평가된 개찰 공고 1건의 세그먼트 집계 최소 투영(DB/예측과 집계 분리용).

    ``absolute_amount_error_pct``/``rate_error_bp`` 는 홀드아웃 ``evaluate_target``
    의 recommended 시나리오에서 온 추천가 오차이며, 없으면 None(집계 제외)이다.
    """

    notice_number: str | None
    project_id: int | None
    category: str | None
    group: str | None
    basis: str
    rule_label: str
    classify_label: str
    absolute_amount_error_pct: float | None
    rate_error_bp: float | None


@dataclass(frozen=True)
class SegmentSummary:
    """한 세그먼트의 추천 투찰가 오차 요약(§2 정직 — 표본 없으면 지표 None)."""

    sample_count: int
    mean_absolute_amount_error_pct: float | None
    mean_absolute_bid_rate_error_bp: float | None
    within_counts: dict[str, int]


@dataclass(frozen=True)
class SegmentReport:
    """리포트 렌더가 쓰는 집계 결과 전체(순수 계산 산출물)."""

    thresholds: tuple[float, ...]
    include_contaminated: bool
    summaries_by_axis: dict[str, dict[str, SegmentSummary]]
    totals_by_axis: dict[str, dict[str, int]]
    excluded_by_axis: dict[str, int]
    basis_counts: dict[str, dict[str, int]]
    category_counts: dict[str, dict[str, int]]
    evaluated_count: int
    total_rule_labels: int
    skipped_no_award: int
    skipped_unmapped: int
    skipped_predict_error: int
    coverage_open_total: int
    coverage_open_with_raw: int


# --- 세그먼트 판정 + 집계 (순수 — DB/네트워크 접근 없음) ----------------------


def segment_key(row: SegmentRow, axis: str) -> str:
    """한 행의 eligibility 세그먼트를 축(rule_label|classify)에 따라 돌려준다(순수)."""
    label = row.classify_label if axis == AXIS_CLASSIFY else row.rule_label
    return label or _UNLABELED


def _format_threshold(threshold: float) -> str:
    """threshold 비율을 홀드아웃 리포트와 같은 표기(예: 0.3%)로 포맷한다."""
    return f"{threshold * 100:.1f}%"


def build_segment_row(
    evaluated: dict[str, Any], *, rule_label: str, classify_label: str
) -> SegmentRow:
    """홀드아웃 ``evaluate_target`` 산출 dict + 세그먼트 라벨을 SegmentRow 로 매핑(순수)."""
    recommended = evaluated.get("recommended") or {}
    return SegmentRow(
        notice_number=evaluated.get("notice_number"),
        project_id=evaluated.get("project_id"),
        category=evaluated.get("category"),
        group=evaluated.get("group"),
        basis=str(evaluated.get("basis") or ""),
        rule_label=rule_label,
        classify_label=classify_label,
        absolute_amount_error_pct=recommended.get("absolute_amount_error_pct"),
        rate_error_bp=recommended.get("rate_error_bp"),
    )


def evaluate_candidates(
    candidates: Iterable[tuple[Any, str, Any, Any]],
    *,
    max_per_segment: int,
    evaluate_fn: Callable[[Any], dict[str, Any]],
    classify_fn: Callable[[Any], str],
) -> tuple[list[SegmentRow], int]:
    """캡을 적용해 후보를 평가, (SegmentRow 목록, 예측 실패 수)를 낸다(I/O 주입 · 테스트 가능).

    후보는 ``(event_at, rule_label, target, project)`` 튜플이다. ``evaluate_fn`` (홀드아웃
    ``evaluate_target`` 주입)이 예외를 던지면 그 행은 **predict_error 로만** 집계한다 —
    award 없음/미매핑/zero-budget 은 이 함수 호출 이전 단계에서 이미 걸러지므로 여기
    카운터는 오직 예측 실패만 센다(drop 사유 정직성). 실패 시 ``per_segment`` 슬롯을
    되돌리므로 세그먼트가 ``max_per_segment`` 를 예측 실패 수만큼 초과 평가할 수 있다
    (의도된 soft cap — 리포트라 무해). I/O 는 주입된 콜러블에만 있어 순수 단위 테스트가 된다.
    """
    per_segment: Counter[str] = Counter()
    rows: list[SegmentRow] = []
    skipped_predict_error = 0
    for _event_at, rule_label, target, project in candidates:
        if max_per_segment and per_segment[rule_label] >= max_per_segment:
            continue
        per_segment[rule_label] += 1
        try:
            evaluated = evaluate_fn(target)
        except Exception:  # noqa: BLE001 - report resilience: 예측 실패만 별도 집계
            per_segment[rule_label] -= 1  # soft cap: 실패 슬롯 반환
            skipped_predict_error += 1
            continue
        rows.append(
            build_segment_row(
                evaluated,
                rule_label=rule_label,
                classify_label=classify_fn(project),
            )
        )
    return rows, skipped_predict_error


def filter_aggregatable(
    rows: Iterable[SegmentRow], *, include_contaminated: bool
) -> tuple[list[SegmentRow], int]:
    """추천 오차가 있는 행만 남기고, 기본은 clean basis 만 집계에 쓴다(순수).

    홀드아웃과 동일한 오염 가드: derived(예정가 역산/VAT)·suspect base_amount 행은
    오차 통계를 왜곡하므로 기본 clean-only 집계하고, ``include_contaminated`` 로 편입한다.
    """
    usable = [row for row in rows if row.absolute_amount_error_pct is not None]
    if include_contaminated:
        return usable, 0
    clean = [row for row in usable if row.basis == BASIS_CLEAN]
    return clean, len(usable) - len(clean)


def aggregate_segment(
    rows: list[SegmentRow], thresholds: tuple[float, ...]
) -> SegmentSummary:
    """한 세그먼트 행 집합에서 추천 투찰가 오차 요약을 계산한다(순수)."""
    usable = [row for row in rows if row.absolute_amount_error_pct is not None]
    within = {_format_threshold(threshold): 0 for threshold in thresholds}
    if not usable:
        return SegmentSummary(0, None, None, within)
    errors = [row.absolute_amount_error_pct for row in usable]
    bps = [abs(row.rate_error_bp) for row in usable if row.rate_error_bp is not None]
    within = {
        _format_threshold(threshold): sum(
            1 for value in errors if value <= threshold
        )
        for threshold in thresholds
    }
    return SegmentSummary(
        sample_count=len(usable),
        mean_absolute_amount_error_pct=round(mean(errors), 6),
        mean_absolute_bid_rate_error_bp=round(mean(bps), 2) if bps else None,
        within_counts=within,
    )


def aggregate_by_segment(
    rows: Iterable[SegmentRow],
    *,
    axis: str,
    thresholds: tuple[float, ...],
    include_contaminated: bool,
) -> tuple[dict[str, SegmentSummary], int]:
    """행을 eligibility 세그먼트로 나눠 세그먼트별 오차 요약을 낸다(순수).

    반환: (세그먼트→요약, clean-only 로 제외된 오염 표본 수).
    """
    aggregatable, excluded = filter_aggregatable(
        rows, include_contaminated=include_contaminated
    )
    buckets: dict[str, list[SegmentRow]] = defaultdict(list)
    for row in aggregatable:
        buckets[segment_key(row, axis)].append(row)
    summaries = {
        segment: aggregate_segment(bucket, thresholds)
        for segment, bucket in buckets.items()
    }
    return summaries, excluded


def totals_by_segment(rows: Iterable[SegmentRow], axis: str) -> dict[str, int]:
    """축별 세그먼트당 전체 평가건수(clean 필터 이전)를 센다(순수)."""
    counts: Counter[str] = Counter()
    for row in rows:
        counts[segment_key(row, axis)] += 1
    return dict(counts)


def basis_counts_by_segment(
    rows: Iterable[SegmentRow], axis: str
) -> dict[str, dict[str, int]]:
    """축별 세그먼트당 basis 분포(오염 투명성)를 센다(순수)."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        counts[segment_key(row, axis)][row.basis or "unknown"] += 1
    return {segment: dict(counter) for segment, counter in counts.items()}


def category_counts_by_segment(
    rows: Iterable[SegmentRow], axis: str
) -> dict[str, dict[str, int]]:
    """축별 세그먼트당 카테고리 분포(단일 카테고리 과적합 caveat 증거)를 센다(순수)."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        counts[segment_key(row, axis)][row.category or _NO_CATEGORY] += 1
    return {segment: dict(counter) for segment, counter in counts.items()}


def build_report(
    rows: list[SegmentRow],
    *,
    thresholds: tuple[float, ...],
    include_contaminated: bool,
    total_rule_labels: int,
    skipped_no_award: int,
    skipped_unmapped: int,
    skipped_predict_error: int,
    coverage_open_total: int,
    coverage_open_with_raw: int,
) -> SegmentReport:
    """평가된 행에서 두 축의 세그먼트 요약·분포·caveat 지표를 조립한다(순수)."""
    summaries_by_axis: dict[str, dict[str, SegmentSummary]] = {}
    totals_by_axis: dict[str, dict[str, int]] = {}
    excluded_by_axis: dict[str, int] = {}
    for axis in AXIS_VALUES:
        summaries, excluded = aggregate_by_segment(
            rows,
            axis=axis,
            thresholds=thresholds,
            include_contaminated=include_contaminated,
        )
        summaries_by_axis[axis] = summaries
        totals_by_axis[axis] = totals_by_segment(rows, axis)
        excluded_by_axis[axis] = excluded
    return SegmentReport(
        thresholds=thresholds,
        include_contaminated=include_contaminated,
        summaries_by_axis=summaries_by_axis,
        totals_by_axis=totals_by_axis,
        excluded_by_axis=excluded_by_axis,
        basis_counts=basis_counts_by_segment(rows, AXIS_RULE_LABEL),
        category_counts=category_counts_by_segment(rows, AXIS_RULE_LABEL),
        evaluated_count=len(rows),
        total_rule_labels=total_rule_labels,
        skipped_no_award=skipped_no_award,
        skipped_unmapped=skipped_unmapped,
        skipped_predict_error=skipped_predict_error,
        coverage_open_total=coverage_open_total,
        coverage_open_with_raw=coverage_open_with_raw,
    )


# --- 렌더 (순수) -------------------------------------------------------------


def _ordered_segments(segments: Iterable[str]) -> list[str]:
    """SEGMENT_ORDER 를 우선하고 나머지는 사전순으로 세그먼트를 정렬한다."""
    present = set(segments)
    ordered = [seg for seg in SEGMENT_ORDER if seg in present]
    ordered.extend(sorted(present - set(SEGMENT_ORDER)))
    return ordered


def _summary_lines(
    axis: str, report: SegmentReport
) -> list[str]:
    summaries = report.summaries_by_axis[axis]
    totals = report.totals_by_axis[axis]
    excluded = report.excluded_by_axis[axis]
    basis_note = "all" if report.include_contaminated else "clean_only"
    lines = [
        f"{_LOG_PREFIX} 축[{axis}] 세그먼트별 추천 투찰가 오차 "
        f"(집계 basis={basis_note}, 오염 제외={excluded}):"
    ]
    all_segments = _ordered_segments(set(summaries) | set(totals))
    if not all_segments:
        return lines + ["    (표본 없음)"]
    for segment in all_segments:
        total = totals.get(segment, 0)
        summary = summaries.get(segment)
        if summary is None or summary.sample_count == 0:
            lines.append(
                f"    {segment}: 평가 {total} · 집계표본 0 (오차 지표 없음)"
            )
            continue
        within = " ".join(
            f"{key}={value}" for key, value in summary.within_counts.items()
        )
        rate_bp = (
            "n/a"
            if summary.mean_absolute_bid_rate_error_bp is None
            else f"{summary.mean_absolute_bid_rate_error_bp:.2f}bp"
        )
        lines.append(
            f"    {segment}: 평가 {total} · 집계표본 {summary.sample_count} · "
            f"평균절대오차율 {summary.mean_absolute_amount_error_pct:.6f} · "
            f"평균절대사정률오차 {rate_bp} · within[{within}]"
        )
    return lines


def _distribution_lines(title: str, dist: dict[str, dict[str, int]]) -> list[str]:
    lines = [f"{_LOG_PREFIX} {title}:"]
    if not dist:
        return lines + ["    (none)"]
    for segment in _ordered_segments(dist):
        parts = ", ".join(
            f"{key}={value}" for key, value in sorted(dist[segment].items())
        )
        lines.append(f"    {segment}: {parts}")
    return lines


def _coverage_lines(report: SegmentReport) -> list[str]:
    total = report.coverage_open_total
    with_raw = report.coverage_open_with_raw
    pct = (with_raw / total * 100.0) if total else 0.0
    positive_total = report.totals_by_axis[AXIS_RULE_LABEL].get(LABEL_POSITIVE, 0)
    positive_cats = report.category_counts.get(LABEL_POSITIVE, {})
    cats = ", ".join(f"{k}={v}" for k, v in sorted(positive_cats.items())) or "(none)"
    return [
        f"{_LOG_PREFIX} 실측 표본/커버리지 (하드코딩 아님):",
        f"    rule 라벨 총 {report.total_rule_labels} · 평가된 개찰건 "
        f"{report.evaluated_count} (제외 사유별: "
        f"no_award={report.skipped_no_award} unmapped={report.skipped_unmapped} "
        f"predict_error={report.skipped_predict_error})",
        f"    positive settled 평가 n={positive_total} · positive 카테고리 분포: {cats}",
        f"    eligibility_raw 커버리지(open 공고): {with_raw}/{total} = {pct:.1f}%",
    ]


def render_report(report: SegmentReport) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST · 순수)."""
    from app.core.time import kst_now

    stamp = kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    lines = [f"{_LOG_PREFIX} {stamp} READ-ONLY · 외부 호출 0"]
    for axis in AXIS_VALUES:
        lines.extend(_summary_lines(axis, report))
    lines.extend(
        _distribution_lines(
            "rule 세그먼트별 카테고리 분포", report.category_counts
        )
    )
    lines.extend(
        _distribution_lines("rule 세그먼트별 basis 분포", report.basis_counts)
    )
    lines.extend(_coverage_lines(report))
    lines.append(f"{_LOG_PREFIX} {CAVEAT_HEADER}")
    lines.extend(f"    - {caveat}" for caveat in CAVEATS)
    return "\n".join(lines)


# --- 홀드아웃 모듈 로드 (heavy 예측 import 는 지연) ---------------------------


def _load_holdouts_module():
    """오차 측정 골격을 담은 홀드아웃 스크립트를 경로로 로드해 재사용한다.

    ``scripts/`` 는 import 가능한 패키지가 아니므로(기존 테스트도 동일 방식) importlib
    로 로드한다. predict_price 등 무거운 import 를 이 시점까지 미뤄 순수 함수 단위
    테스트가 예측 스택 없이 돌게 한다.
    """
    name = "backtest_latest_award_holdouts"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- DB read + 홀드아웃 평가 (얇은 경계) -------------------------------------


def _classify_label_for(project: Project) -> str:
    """공고 ``eligibility_raw`` 를 룰 해석기로 재판정한 세그먼트 라벨(순수 재사용)."""
    labels = extract_eligibility_labels(getattr(project, "eligibility_raw", None))
    return classify_eligibility(labels).label


def load_rows(
    db,
    *,
    groups: tuple[str, ...],
    max_per_segment: int,
    history_limit: int,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    """rule 라벨 개찰 공고를 홀드아웃 골격으로 평가해 SegmentRow + 실측 지표를 낸다.

    read-only: NoticeEligibilityLabel(source=rule)↔Project↔HistoricalData↔TenderResult
    를 조인해, 개찰(award) 보유 공고에 홀드아웃 ``evaluate_target`` 을 그대로 적용한다.
    세그먼트당 최신순 상한(max_per_segment)으로 예측 건수를 제한한다.
    """
    holdouts = _load_holdouts_module()
    from app.services.prediction_dataset import PredictionDatasetService  # noqa: E402

    service = PredictionDatasetService()
    group_set = set(groups)

    label_rows = (
        db.query(NoticeEligibilityLabel.project_id, NoticeEligibilityLabel.label, Project)
        .join(Project, Project.id == NoticeEligibilityLabel.project_id)
        .filter(NoticeEligibilityLabel.source == SOURCE_RULE)
        .order_by(NoticeEligibilityLabel.project_id.desc())
        .all()
    )
    rule_label_by_project: dict[int, str] = {pid: label for pid, label, _ in label_rows}
    project_by_id: dict[int, Project] = {proj.id: proj for _, _, proj in label_rows}
    project_ids = list(project_by_id)

    histories = holdouts.latest_history_by_project(db, project_ids)
    results_by_project: dict[int, TenderResult] = {}
    if project_ids:
        result_rows = (
            db.query(TenderResult)
            .filter(
                TenderResult.project_id.in_(project_ids),
                settled_any_signal(),
            )
            .order_by(
                TenderResult.project_id.asc(),
                TenderResult.created_at.desc().nullslast(),
                TenderResult.id.desc(),
            )
            .all()
        )
        for row in result_rows:
            results_by_project.setdefault(row.project_id, row)

    candidates: list[tuple[Any, str, Any, Project]] = []
    skipped_no_award = 0
    skipped_unmapped = 0
    for pid in project_ids:
        project = project_by_id[pid]
        historical = histories.get(pid)
        result = results_by_project.get(pid)
        if historical is None or result is None:
            skipped_no_award += 1
            continue
        basis = holdouts.resolve_base_basis(historical, result)
        budget, _base_source = holdouts.resolve_pricing_base(project, historical, basis)
        actual_amount = holdouts.amount_float(result.winning_amount)
        if not budget or budget <= 0 or not actual_amount or actual_amount <= 0:
            skipped_no_award += 1
            continue
        group, group_source = holdouts.derive_group(service, project, historical, group_set)
        if group not in group_set:
            skipped_unmapped += 1
            continue
        event_at = holdouts.event_time(result, historical)
        available_at = holdouts.available_time(result, historical)
        if event_at is None or available_at is None:
            skipped_no_award += 1
            continue
        target = holdouts.HoldoutTarget(
            group=group,
            group_source=group_source,
            result=result,
            project=project,
            historical=historical,
            event_at=event_at,
            available_at=available_at,
        )
        candidates.append((event_at, rule_label_by_project[pid], target, project))

    # 세그먼트당 최신순 상한 — leakage-free(과거 개찰) 최신 표본 우선.
    candidates.sort(key=lambda item: (item[0], item[2].result.id or 0), reverse=True)

    def _evaluate(target: Any) -> dict[str, Any]:
        return holdouts.evaluate_target(
            db,
            service=service,
            target=target,
            history_limit=history_limit,
            thresholds=thresholds,
        )

    rows, skipped_predict_error = evaluate_candidates(
        candidates,
        max_per_segment=max_per_segment,
        evaluate_fn=_evaluate,
        classify_fn=_classify_label_for,
    )

    coverage_open_total = db.query(Project).filter(Project.status == _OPEN_STATUS).count()
    coverage_open_with_raw = (
        db.query(Project)
        .filter(Project.status == _OPEN_STATUS, Project.eligibility_raw.isnot(None))
        .count()
    )

    return {
        "rows": rows,
        "total_rule_labels": len(label_rows),
        "skipped_no_award": skipped_no_award,
        "skipped_unmapped": skipped_unmapped,
        "skipped_predict_error": skipped_predict_error,
        "coverage_open_total": coverage_open_total,
        "coverage_open_with_raw": coverage_open_with_raw,
    }


# --- CLI ---------------------------------------------------------------------


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def parse_thresholds(value: str) -> tuple[float, ...]:
    thresholds: list[float] = []
    for item in parse_csv(value):
        try:
            threshold = float(item)
        except ValueError:
            continue
        if threshold > 0:
            thresholds.append(threshold)
    return tuple(thresholds) or DEFAULT_THRESHOLDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--groups",
        default=",".join(DEFAULT_GROUPS),
        help="Comma-separated business groups fed to the holdout evaluator.",
    )
    parser.add_argument(
        "--thresholds",
        default=",".join(str(value) for value in DEFAULT_THRESHOLDS),
        help="Comma-separated absolute amount-error thresholds as ratios (e.g. 0.001,0.003).",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help="Maximum group-scoped settled history rows supplied to each prediction.",
    )
    parser.add_argument(
        "--max-per-segment",
        type=int,
        default=DEFAULT_MAX_PER_SEGMENT,
        help="Cap on latest awarded notices evaluated per rule segment (0 = no cap).",
    )
    parser.add_argument(
        "--include-contaminated",
        action="store_true",
        help=(
            "Fold non-clean (derived/suspect base_amount) targets into the segment "
            "error aggregates. Default: clean-only aggregation."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    groups = parse_csv(args.groups) or DEFAULT_GROUPS
    thresholds = parse_thresholds(args.thresholds)

    from app.core.database import SessionLocal  # noqa: E402

    db = SessionLocal()
    try:
        loaded = load_rows(
            db,
            groups=groups,
            max_per_segment=max(0, int(args.max_per_segment or 0)),
            history_limit=max(1, int(args.history_limit or 1)),
            thresholds=thresholds,
        )
    finally:
        db.close()

    report = build_report(
        loaded["rows"],
        thresholds=thresholds,
        include_contaminated=args.include_contaminated,
        total_rule_labels=loaded["total_rule_labels"],
        skipped_no_award=loaded["skipped_no_award"],
        skipped_unmapped=loaded["skipped_unmapped"],
        skipped_predict_error=loaded["skipped_predict_error"],
        coverage_open_total=loaded["coverage_open_total"],
        coverage_open_with_raw=loaded["coverage_open_with_raw"],
    )
    print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
