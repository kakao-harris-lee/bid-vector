#!/usr/bin/env python3
"""라이브 공고의 '후보 없음 원인'을 eligibility 세그먼트로 분해하는 리포트 (읽기 전용).

로드맵 G-3 exit(docs/roadmap.md:247)은 엔지니어링협회 조건 공고 세그먼트에 대해
식별 precision/recall·오탐 원인·추천 투찰가 오차·**후보 없음 원인**을 "별도 세그먼트로"
요구한다. 앞 3종은 각각 ``report_eligibility_precision.py`` /
``report_eligibility_segment_backtest.py`` (증분 A, #252)가 담당하지만 **세그먼트별
no-candidate 원인 분류가 없었다** — 파이프라인 전역용 ``smoke_failure_taxonomy`` 는
있어도 세그먼트별 후보 탈락 원인은 없다. 이 갭을 채운다.

라이브(open·미마감) 공고를 eligibility 라벨(positive/negative/ambiguous)로 세그먼트한
뒤, canonical operator 프로필/전략 대비 각 공고가 **왜 후보가 안 되는지**를 실제 추천
파이프라인과 같은 순서의 원인 캐스케이드로 집계한다:

    ① strategy_watch_filtered — 전략 watch 룰(``matches_strategy_watch_rules``)
       미통과라 애초에 게이트/ML 에 도달하지 못함
    ② license_gate_ineligible — 면허 자격 게이트(``assess_license_eligibility``)가
       ineligible 로 제외(``LICENSE_ELIGIBILITY_GATE_ENABLED`` 활성 시 라이브 제외)
    ③ classifier_unmatched — 룰 분류기 ``classify().score_breakdown.blocking_axes`` 가
       비지 않아 매칭 실패 (축별 blocking 집계: business_type/license/association/
       tech_field/region/budget/capability)
    ④ candidate — 위 어느 것에도 걸리지 않아 후보가 됨

②(게이트 제외)는 §요구대로 "**matched 지만 게이트로 제외**"(would_match)와 "**애초
unmatched**"(also_unmatched)를 나눠 센다. blocking_axes 는 ③(classifier_unmatched)
공고에 대해 세그먼트×축으로 집계한다.

성능 레드라인: ``classify()`` 의 semantic 축은 384d 임베딩을 재계산하므로 리포트가
느려질 수 있다. 이 스크립트는 semantic 축을 **집계에서 제외**한다 — classifier 의
``_compute_semantic_similarity`` 를 통과 중립값으로 고정해 임베딩 모델을 **로드하지
않고**(레드라인 준수), blocking_axes 에서 semantic 을 방어적으로 제거한다. 따라서
candidate 는 상한, unmatched 는 하한이다(§2 정직 caveat 로 명시).

정직 명세(§2): "후보 없음"은 **매칭 실패**이지 낙찰 예측/확률이 아니다(확률 조작 없음).
세그먼트 커버리지·대상 수는 런타임 실측이며 하드코딩하지 않는다(빈 세그먼트/원인 0 은
0 그대로). unknown 은 자격/보유면허 정보 부재이며 ineligible 이 아니다. 세그먼트 판정은
eligibility_raw 룰 해석이며 operator 정답 라벨이 아니다. 시크릿/사업자번호 미출력.

app/ 동작 변경 0 — 기존 classifier/eligibility_labeling/license_eligibility 를 read-only
소비만 한다(predictor/guardrail/config/모델 불변, 마이그레이션 0). DB read-only.
외부 API(KONEPS/Telegram) 호출 0. 시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/report_no_candidate_cause.py
    docker exec bid_vector_api python scripts/report_no_candidate_cause.py \
        --limit 4000 --samples 20
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.constants import OPEN_PROJECT_STATUS as _OPEN_STATUS  # noqa: E402
from app.services.eligibility_labeling import (  # noqa: E402
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    classify_eligibility,
    extract_eligibility_labels,
)
from app.services.license_eligibility import (  # noqa: E402
    VERDICT_INELIGIBLE,
    VERDICT_VALUES,
)

# --- 매직값은 여기 선언(§4.5.1) ---------------------------------------------

_LOG_PREFIX = "[no-candidate-cause]"
# _OPEN_STATUS 는 app/core/constants.py 의 OPEN_PROJECT_STATUS 단일 출처를 소비한다
# ("open" only, re_notice 제외). 위 import 참조.
_NO_CATEGORY = "(none)"
_UNLABELED = "(unlabeled)"
_ALL_SEGMENTS = "(all)"

# 세그먼트 출력 순서 — eligibility_labeling 라벨 어휘 단일 출처 재사용(복붙 금지).
SEGMENT_ORDER = (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIGUOUS)

# 후보 탈락 원인(실제 추천 파이프라인 순서의 캐스케이드, first-match-wins).
# 매직값 금지(§4.5.1) — 캐스케이드·렌더·테스트가 이 상수를 공유한다.
CAUSE_STRATEGY_WATCH = "strategy_watch_filtered"
CAUSE_LICENSE_GATE = "license_gate_ineligible"
CAUSE_UNMATCHED = "classifier_unmatched"
CAUSE_CANDIDATE = "candidate"
# 렌더 순서(candidate 먼저 = "후보 있음", 그다음 원인들).
CAUSE_ORDER = (
    CAUSE_CANDIDATE,
    CAUSE_STRATEGY_WATCH,
    CAUSE_LICENSE_GATE,
    CAUSE_UNMATCHED,
)
# 원인 라벨(§4.5.2 룩업) — 렌더 전용 한글 설명. 캐스케이드 로직과 어휘 단일 출처.
CAUSE_LABELS: dict[str, str] = {
    CAUSE_CANDIDATE: "후보(탈락 아님)",
    CAUSE_STRATEGY_WATCH: "전략 watch 룰 미통과(게이트 미도달)",
    CAUSE_LICENSE_GATE: "면허 자격 게이트 ineligible 제외",
    CAUSE_UNMATCHED: "분류기 blocking(축 미통과)",
}

# classifier ``classify()`` axis_assessments 의 semantic 축 키(classifier 단일 출처
# 미러). 성능 레드라인상 이 축만 blocking 집계에서 제외한다 — 다른 축 어휘는
# classify() 산출에서 동적으로 받으므로 나열/복붙하지 않는다.
SEMANTIC_AXIS = "semantic_similarity"

# semantic 중립값 — 강한 통과 유사도로 고정해 semantic 축이 절대 blocking 되지 않게
# 하고(모델 미로드), candidate 가 상한이 되게 한다. source 는 provenance 마커.
_NEUTRAL_SEMANTIC_SIMILARITY = 1.0
_NEUTRAL_SEMANTIC_SOURCE = "report-neutralized"

# 대상 상한 기본값(보수적) — open·미마감 공고를 최신 id 순으로 이만큼만 스캔한다.
# classify(semantic 제외)+게이트+watch 는 공고당 <1ms 라 상한을 풀어도 수 초지만,
# §요구대로 기본은 보수적이며 0/음수면 전량 스캔한다.
DEFAULT_LIMIT = 1500
DEFAULT_SAMPLES = 15
_TITLE_CLIP_CHARS = 40

CAVEAT_HEADER = "정직 명세(§2) — 후보 없음 원인 리포트 한계:"
CAVEATS: tuple[str, ...] = (
    "'후보 없음'은 프로필/전략 대비 매칭 실패이지 낙찰 예측·확률이 아니다(확률 조작 없음).",
    "semantic 축은 집계에서 제외했다(성능 레드라인: 384d 임베딩 재계산 회피, 모델 "
    "미로드). 따라서 candidate 는 상한, classifier_unmatched 는 하한이다 — 라이브 "
    "임베딩 경로는 semantic 으로 추가 탈락시킬 수 있다.",
    "세그먼트는 eligibility_raw 룰 해석(positive/negative/ambiguous)이며 operator "
    "정답 라벨이 아니다. ambiguous=자격 데이터 부재, positive/negative=자격 데이터 보유.",
    "unknown 은 자격/보유면허 정보 부재이며 ineligible(부적격)이 아니다 — 게이트는 "
    "unknown 을 억제하지 않는다.",
    "대상 수·세그먼트 커버리지는 런타임 실측이며 하드코딩하지 않는다(빈 세그먼트/원인 0).",
)


# --- 결과 스키마 -------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRow:
    """라이브 공고 1건의 후보 판정 최소 투영(DB/분류기와 집계 분리용).

    ``blocking_axes`` 는 semantic 을 제외한 결정적 blocking 축이며, 비면 결정적으로는
    매칭이다(candidate 상한). ``passes_watch``/``gate_verdict`` 는 캐스케이드 상위 단계.
    """

    notice_number: str | None
    project_id: int | None
    title: str | None
    category: str | None
    segment: str
    passes_watch: bool
    gate_verdict: str
    blocking_axes: tuple[str, ...]


@dataclass(frozen=True)
class SegmentCauseSummary:
    """한 세그먼트(또는 전체)의 후보 탈락 원인 집계(§2 정직 — 빈 값은 0)."""

    total: int
    cause_counts: dict[str, int]
    # 분류기 독립 판정(watch/게이트 무관): 세그먼트 전체에서 결정적 매칭/미매칭 수.
    classifier_matched: int
    classifier_unmatched: int
    # 분류기 matched=False 공고 전체에 대한 축별 blocking 카운트(§요구 핵심 산출).
    # 상위 watch/게이트가 가리지 않는 분류기 자체의 축별 탈락 분포다.
    blocking_axis_counts: dict[str, int]
    gate_verdict_counts: dict[str, int]
    # 게이트 제외(license_gate_ineligible) 세분: matched 지만 제외 vs 애초 unmatched.
    gate_would_match: int
    gate_also_unmatched: int
    category_counts: dict[str, int]


@dataclass(frozen=True)
class CauseReport:
    """리포트 렌더가 쓰는 집계 결과 전체(순수 계산 산출물)."""

    gate_enabled: bool
    has_watch_gate: bool
    profile_present: bool
    summaries: dict[str, SegmentCauseSummary]
    overall: SegmentCauseSummary
    samples: dict[str, list[CandidateRow]]
    scanned: int
    open_live_total: int
    open_live_with_raw: int


# --- 원인 캐스케이드 + 집계 (순수 — DB/네트워크 접근 없음) --------------------


def is_deterministic_match(blocking_axes: Iterable[str]) -> bool:
    """semantic 제외 결정적 blocking 이 없으면 결정적 매칭이다(순수)."""
    return not tuple(blocking_axes)


def primary_cause(row: CandidateRow, *, gate_enabled: bool) -> str:
    """공고 1건의 후보 탈락 1차 원인을 파이프라인 순서로 판정한다(순수, first-match-wins).

    실제 추천 순서(전략 watch → 면허 게이트 → 룰 분류)와 같게 캐스케이드한다.
    ``gate_enabled`` 이 False 면 게이트가 라이브 제외를 안 하므로 ineligible 도
    게이트 원인으로 세지 않고 분류기 단계로 흘려보낸다(억제 금지·정직).
    """
    if not row.passes_watch:
        return CAUSE_STRATEGY_WATCH
    if gate_enabled and row.gate_verdict == VERDICT_INELIGIBLE:
        return CAUSE_LICENSE_GATE
    if row.blocking_axes:
        return CAUSE_UNMATCHED
    return CAUSE_CANDIDATE


def aggregate_segment(
    rows: Iterable[CandidateRow], *, gate_enabled: bool
) -> SegmentCauseSummary:
    """행 집합에서 원인 분포·blocking 축·게이트 세분·카테고리 분포를 집계한다(순수)."""
    cause_counts: Counter[str] = Counter()
    blocking: Counter[str] = Counter()
    gate_verdicts: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    gate_would_match = 0
    gate_also_unmatched = 0
    classifier_unmatched = 0
    total = 0
    for row in rows:
        total += 1
        cause = primary_cause(row, gate_enabled=gate_enabled)
        cause_counts[cause] += 1
        gate_verdicts[row.gate_verdict] += 1
        categories[row.category or _NO_CATEGORY] += 1
        # 분류기 독립 판정(watch/게이트 무관): matched=False 전체에서 축별 blocking 집계.
        if row.blocking_axes:
            classifier_unmatched += 1
            for axis in row.blocking_axes:
                blocking[axis] += 1
        if cause == CAUSE_LICENSE_GATE:
            if row.blocking_axes:
                gate_also_unmatched += 1
            else:
                gate_would_match += 1
    return SegmentCauseSummary(
        total=total,
        cause_counts=dict(cause_counts),
        classifier_matched=total - classifier_unmatched,
        classifier_unmatched=classifier_unmatched,
        blocking_axis_counts=dict(blocking),
        gate_verdict_counts=dict(gate_verdicts),
        gate_would_match=gate_would_match,
        gate_also_unmatched=gate_also_unmatched,
        category_counts=dict(categories),
    )


def group_by_segment(
    rows: Iterable[CandidateRow],
) -> dict[str, list[CandidateRow]]:
    """행을 eligibility 세그먼트로 묶는다(순수). 빈 라벨은 ``(unlabeled)``."""
    buckets: dict[str, list[CandidateRow]] = defaultdict(list)
    for row in rows:
        buckets[row.segment or _UNLABELED].append(row)
    return dict(buckets)


def collect_samples(
    rows: Iterable[CandidateRow], *, gate_enabled: bool, samples: int
) -> dict[str, list[CandidateRow]]:
    """원인별 대표 샘플 공고를 상한까지 모은다(순수). candidate 는 샘플에서 제외."""
    collected: dict[str, list[CandidateRow]] = defaultdict(list)
    if samples <= 0:
        return {}
    for row in rows:
        cause = primary_cause(row, gate_enabled=gate_enabled)
        if cause == CAUSE_CANDIDATE:
            continue
        if len(collected[cause]) < samples:
            collected[cause].append(row)
    return dict(collected)


def build_report(
    rows: list[CandidateRow],
    *,
    gate_enabled: bool,
    has_watch_gate: bool,
    profile_present: bool,
    open_live_total: int,
    open_live_with_raw: int,
    samples: int = DEFAULT_SAMPLES,
) -> CauseReport:
    """평가된 행에서 세그먼트별·전체 원인 집계와 샘플을 조립한다(순수)."""
    grouped = group_by_segment(rows)
    summaries = {
        segment: aggregate_segment(bucket, gate_enabled=gate_enabled)
        for segment, bucket in grouped.items()
    }
    overall = aggregate_segment(rows, gate_enabled=gate_enabled)
    return CauseReport(
        gate_enabled=gate_enabled,
        has_watch_gate=has_watch_gate,
        profile_present=profile_present,
        summaries=summaries,
        overall=overall,
        samples=collect_samples(rows, gate_enabled=gate_enabled, samples=samples),
        scanned=len(rows),
        open_live_total=open_live_total,
        open_live_with_raw=open_live_with_raw,
    )


# --- 렌더 (순수) -------------------------------------------------------------


def _ordered_segments(segments: Iterable[str]) -> list[str]:
    """SEGMENT_ORDER 를 우선하고 나머지는 사전순으로 정렬한다."""
    present = set(segments)
    ordered = [seg for seg in SEGMENT_ORDER if seg in present]
    ordered.extend(sorted(present - set(SEGMENT_ORDER)))
    return ordered


def _cause_line(summary: SegmentCauseSummary) -> str:
    """원인 분포를 고정 순서로 렌더한다(0건도 표시)."""
    parts = [
        f"{cause}={summary.cause_counts.get(cause, 0)}" for cause in CAUSE_ORDER
    ]
    extras = sorted(set(summary.cause_counts) - set(CAUSE_ORDER))
    parts.extend(f"{cause}={summary.cause_counts[cause]}" for cause in extras)
    return " ".join(parts)


def _sorted_counts(counts: dict[str, int]) -> str:
    """카운트 dict 를 (count desc, key asc) 로 렌더한다(비면 ``(none)``)."""
    if not counts:
        return "(none)"
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{key}={value}" for key, value in items)


def _segment_block(segment: str, summary: SegmentCauseSummary) -> list[str]:
    lines = [
        f"{_LOG_PREFIX} 세그먼트[{segment}] 대상 {summary.total} · "
        f"원인분포: {_cause_line(summary)}"
    ]
    gate_count = summary.cause_counts.get(CAUSE_LICENSE_GATE, 0)
    if gate_count:
        lines.append(
            f"    게이트 제외 세분: would_match(matched 지만 제외)="
            f"{summary.gate_would_match} · also_unmatched(애초 unmatched)="
            f"{summary.gate_also_unmatched}"
        )
    lines.append(
        f"    분류기 독립판정(watch/게이트 무관·semantic 제외): "
        f"matched={summary.classifier_matched} unmatched={summary.classifier_unmatched}"
    )
    lines.append(
        f"    분류기 matched=False blocking 축(세그먼트×축): "
        f"{_sorted_counts(summary.blocking_axis_counts)}"
    )
    lines.append(
        "    게이트 verdict 분포: "
        + " ".join(
            f"{verdict}={summary.gate_verdict_counts.get(verdict, 0)}"
            for verdict in VERDICT_VALUES
        )
    )
    return lines


def _sample_lines(report: CauseReport) -> list[str]:
    lines = [f"{_LOG_PREFIX} 원인별 샘플 공고 (공고번호 | 세그먼트 | 공고명 | 세부):"]
    if not report.samples:
        return lines + ["    (none)"]
    for cause in CAUSE_ORDER:
        bucket = report.samples.get(cause)
        if not bucket:
            continue
        lines.append(f"    [{cause}]")
        for row in bucket:
            detail = (
                ",".join(row.blocking_axes) or "(no det. block)"
                if cause == CAUSE_UNMATCHED
                else f"gate={row.gate_verdict}"
            )
            lines.append(
                f"      {row.notice_number or '(번호없음)'} | {row.segment} | "
                f"{row.title or '(제목없음)'} | {detail}"
            )
    return lines


def _coverage_lines(report: CauseReport) -> list[str]:
    total = report.open_live_total
    with_raw = report.open_live_with_raw
    pct = (with_raw / total * 100.0) if total else 0.0
    positive_total = report.summaries.get(LABEL_POSITIVE)
    positive_n = positive_total.total if positive_total else 0
    return [
        f"{_LOG_PREFIX} 실측 대상/커버리지 (하드코딩 아님):",
        f"    스캔한 open·미마감 공고 {report.scanned} / open·미마감 총 {total}",
        f"    eligibility_raw 커버리지(open·미마감): {with_raw}/{total} = {pct:.1f}%",
        f"    positive 세그먼트 대상 n={positive_n} (스캔분 기준 · 자격 데이터 보유·"
        f"협회/엔지니어링/기술부문 요건 명시분)",
    ]


def render_report(report: CauseReport) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST · 순수)."""
    from app.core.time import kst_now

    stamp = kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    gate_state = "ON(라이브 제외 반영)" if report.gate_enabled else "OFF(라이브 미제외)"
    watch_state = "설정됨" if report.has_watch_gate else "없음(모든 공고 게이트 도달)"
    lines = [
        f"{_LOG_PREFIX} {stamp} READ-ONLY · 외부 호출 0 · semantic 축 제외(성능 레드라인)",
        f"{_LOG_PREFIX} 면허 자격 게이트: {gate_state} · 전략 watch 게이트: {watch_state}",
    ]
    if not report.profile_present:
        lines.append(
            f"{_LOG_PREFIX} canonical operator 프로필/전략이 없어 원인 판정 불가."
        )
        return "\n".join(lines)
    lines.extend(_segment_block(_ALL_SEGMENTS, report.overall))
    for segment in _ordered_segments(report.summaries):
        lines.extend(_segment_block(segment, report.summaries[segment]))
    lines.extend(_sample_lines(report))
    lines.extend(_coverage_lines(report))
    # 원인/축 라벨 범례(§4.5.2 룩업 렌더).
    lines.append(f"{_LOG_PREFIX} 원인 라벨:")
    lines.extend(f"    - {cause}: {CAUSE_LABELS[cause]}" for cause in CAUSE_ORDER)
    lines.append(f"{_LOG_PREFIX} {CAVEAT_HEADER}")
    lines.extend(f"    - {caveat}" for caveat in CAVEATS)
    return "\n".join(lines)


# --- 공고 평가 (I/O 주입 · 테스트 가능) --------------------------------------


def evaluate_project(
    project: Any,
    *,
    segment_fn: Callable[[Any], str],
    watch_fn: Callable[[Any], bool],
    gate_fn: Callable[[Any], str],
    blocking_axes_fn: Callable[[Any], tuple[str, ...]],
) -> CandidateRow:
    """공고 1건을 주입된 판정 콜러블로 평가해 CandidateRow 로 매핑한다(I/O 주입).

    협력자(세그먼트/watch/게이트/분류 blocking)는 전부 인자로 주입되므로(§4.7 seam)
    테스트는 무거운 실경로 없이 fake 를 넘겨 캐스케이드/집계를 검증한다.
    """
    notice = str(getattr(project, "notice_number", "") or "") or None
    title = getattr(project, "title", None)
    return CandidateRow(
        notice_number=notice,
        project_id=getattr(project, "id", None),
        title=_clip_title(title),
        category=getattr(project, "category", None),
        segment=segment_fn(project),
        passes_watch=watch_fn(project),
        gate_verdict=gate_fn(project),
        blocking_axes=tuple(blocking_axes_fn(project)),
    )


def _clip_title(title: str | None) -> str | None:
    """샘플 출력용 공고명 클립(순수)."""
    if not title:
        return None
    text = title.strip()
    if len(text) <= _TITLE_CLIP_CHARS:
        return text
    return text[:_TITLE_CLIP_CHARS] + "…"


def _segment_label_for(project: Any) -> str:
    """공고 ``eligibility_raw`` 를 룰 해석기로 세그먼트 라벨화한다(순수 재사용)."""
    labels = extract_eligibility_labels(getattr(project, "eligibility_raw", None))
    return classify_eligibility(labels).label


# --- DB read + 분류기 평가 (얇은 경계) ---------------------------------------


def _build_blocking_axes_fn(classifier, profile) -> Callable[[Any], tuple[str, ...]]:
    """classify() 를 호출해 semantic 을 제외한 결정적 blocking 축을 내는 콜러블.

    성능 레드라인: ``_compute_semantic_similarity`` 를 통과 중립값으로 고정해 384d
    임베딩 모델을 로드하지 않고, blocking_axes 에서 ``SEMANTIC_AXIS`` 를 방어적으로
    제거한다(중립화로 애초에 안 잡히지만 이중 가드).
    """
    classifier._compute_semantic_similarity = (
        lambda _pt, _pf: (_NEUTRAL_SEMANTIC_SIMILARITY, _NEUTRAL_SEMANTIC_SOURCE)
    )

    def _blocking(project: Any) -> tuple[str, ...]:
        result = classifier.classify(project, profile)
        axes = result.get("score_breakdown", {}).get("blocking_axes", []) or []
        return tuple(axis for axis in axes if axis != SEMANTIC_AXIS)

    return _blocking


def load_rows(db, *, limit: int | None) -> dict[str, Any]:
    """canonical 프로필/전략 대비 라이브 공고를 평가해 CandidateRow + 실측 지표를 낸다.

    read-only: open·미마감 Project 를 최신 id 순으로(limit) 로드해, 전략 watch·면허
    게이트·룰 분류(semantic 제외)를 적용한다. write/commit·외부 호출 없음.
    """
    from app.core.config import settings
    from app.core.single_user import get_operator_profile, get_operator_strategy
    from app.core.time import utc_now
    from app.models.models import Project
    from app.services.classifier import NoticeClassifierService
    from app.services.license_eligibility import assess_license_eligibility
    from app.services.opportunity_monitoring import (
        has_watch_rules,
        matches_strategy_watch_rules,
    )

    profile = get_operator_profile(db)
    strategy = get_operator_strategy(db)
    open_live_total = (
        db.query(Project)
        .filter(Project.status == _OPEN_STATUS, Project.deadline >= utc_now())
        .count()
    )
    open_live_with_raw = (
        db.query(Project)
        .filter(
            Project.status == _OPEN_STATUS,
            Project.deadline >= utc_now(),
            Project.eligibility_raw.isnot(None),
        )
        .count()
    )

    base: dict[str, Any] = {
        "rows": [],
        "profile_present": profile is not None and strategy is not None,
        "gate_enabled": bool(settings.LICENSE_ELIGIBILITY_GATE_ENABLED),
        "has_watch_gate": has_watch_rules(strategy),
        "open_live_total": open_live_total,
        "open_live_with_raw": open_live_with_raw,
    }
    if profile is None or strategy is None:
        return base

    query = (
        db.query(Project)
        .filter(Project.status == _OPEN_STATUS, Project.deadline >= utc_now())
        .order_by(Project.id.desc())
    )
    if limit:
        query = query.limit(limit)
    projects = query.all()

    classifier = NoticeClassifierService()
    blocking_axes_fn = _build_blocking_axes_fn(classifier, profile)
    profile_license = getattr(profile, "license_codes", None)

    def _watch(project: Any) -> bool:
        return matches_strategy_watch_rules(project, strategy)

    def _gate(project: Any) -> str:
        return assess_license_eligibility(
            getattr(project, "eligibility_raw", None), profile_license
        ).verdict

    rows = [
        evaluate_project(
            project,
            segment_fn=_segment_label_for,
            watch_fn=_watch,
            gate_fn=_gate,
            blocking_axes_fn=blocking_axes_fn,
        )
        for project in projects
    ]
    base["rows"] = rows
    return base


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Scan at most N latest open·unexpired notices "
            f"(default {DEFAULT_LIMIT}; 0 or negative = all)."
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="How many sample notices to show per cause bucket.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    limit = int(args.limit or 0)
    limit_arg = limit if limit > 0 else None

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        loaded = load_rows(db, limit=limit_arg)
    finally:
        db.close()

    report = build_report(
        loaded["rows"],
        gate_enabled=loaded["gate_enabled"],
        has_watch_gate=loaded["has_watch_gate"],
        profile_present=loaded["profile_present"],
        open_live_total=loaded["open_live_total"],
        open_live_with_raw=loaded["open_live_with_raw"],
        samples=max(0, int(args.samples or 0)),
    )
    print(render_report(report))
    return 0 if loaded["profile_present"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
