"""후보 없음 원인 세그먼트 리포트 순수 함수 테스트.

원인 캐스케이드(watch→게이트→분류)·blocking 축 세그먼트×축 카운트·게이트 제외
would_match vs also_unmatched 세분·빈 세그먼트·커버리지 0 엣지를 값 테이블로 검증한다.
DB/네트워크 없음 — 스크립트를 경로로 로드해 순수 함수만 부르고, classifier/게이트는
fake 콜러블로 주입한다(§4.7 seam · 무거운 실경로 호출 금지).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.services.eligibility_labeling import (
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
)
from app.services.license_eligibility import (
    VERDICT_ELIGIBLE,
    VERDICT_INELIGIBLE,
    VERDICT_UNKNOWN,
)

# scripts/ 는 import 가능한 패키지가 아니므로 경로로 로드한다(기존 테스트 패턴).
_SPEC = importlib.util.spec_from_file_location(
    "report_no_candidate_cause",
    Path(__file__).resolve().parents[1] / "scripts" / "report_no_candidate_cause.py",
)
nc = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = nc
_SPEC.loader.exec_module(nc)


def _row(
    *,
    segment: str = LABEL_POSITIVE,
    passes_watch: bool = True,
    gate_verdict: str = VERDICT_ELIGIBLE,
    blocking_axes: tuple[str, ...] = (),
    category: str = "service",
    notice: str = "N",
    title: str = "t",
):
    return nc.CandidateRow(
        notice_number=notice,
        project_id=1,
        title=title,
        category=category,
        segment=segment,
        passes_watch=passes_watch,
        gate_verdict=gate_verdict,
        blocking_axes=blocking_axes,
    )


# --- is_deterministic_match / primary_cause (캐스케이드) ----------------------


def test_deterministic_match_when_no_blocking():
    assert nc.is_deterministic_match(()) is True
    assert nc.is_deterministic_match(("license",)) is False


def test_primary_cause_watch_filtered_takes_precedence():
    """watch 미통과는 게이트/분류보다 먼저 — 게이트에 도달하지 못한다."""
    row = _row(
        passes_watch=False,
        gate_verdict=VERDICT_INELIGIBLE,
        blocking_axes=("license",),
    )
    assert nc.primary_cause(row, gate_enabled=True) == nc.CAUSE_STRATEGY_WATCH


def test_primary_cause_license_gate_before_unmatched():
    """게이트 ineligible 은 분류기 blocking 보다 먼저(파이프라인 순서)."""
    row = _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=("business_type",))
    assert nc.primary_cause(row, gate_enabled=True) == nc.CAUSE_LICENSE_GATE


def test_primary_cause_gate_disabled_falls_through_to_classifier():
    """게이트 OFF 면 ineligible 도 게이트 원인으로 세지 않고 분류 단계로 흐른다."""
    row = _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=("license",))
    assert nc.primary_cause(row, gate_enabled=False) == nc.CAUSE_UNMATCHED


def test_primary_cause_gate_disabled_ineligible_but_no_block_is_candidate():
    """게이트 OFF + blocking 없음이면 candidate(억제 금지·정직)."""
    row = _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=())
    assert nc.primary_cause(row, gate_enabled=False) == nc.CAUSE_CANDIDATE


def test_primary_cause_unmatched_when_blocking():
    row = _row(gate_verdict=VERDICT_ELIGIBLE, blocking_axes=("region", "budget"))
    assert nc.primary_cause(row, gate_enabled=True) == nc.CAUSE_UNMATCHED


def test_primary_cause_candidate_when_clean():
    row = _row(gate_verdict=VERDICT_UNKNOWN, blocking_axes=())
    assert nc.primary_cause(row, gate_enabled=True) == nc.CAUSE_CANDIDATE


# --- aggregate_segment (원인 분포 · blocking 축 · 게이트 세분) ----------------


def test_aggregate_counts_causes_and_blocking_axes():
    rows = [
        _row(blocking_axes=()),  # candidate
        _row(passes_watch=False),  # strategy_watch
        _row(blocking_axes=("license", "region")),  # unmatched -> 2 axes
        _row(blocking_axes=("license",)),  # unmatched -> 1 axis
    ]
    summary = nc.aggregate_segment(rows, gate_enabled=True)
    assert summary.total == 4
    assert summary.cause_counts[nc.CAUSE_CANDIDATE] == 1
    assert summary.cause_counts[nc.CAUSE_STRATEGY_WATCH] == 1
    assert summary.cause_counts[nc.CAUSE_UNMATCHED] == 2
    # 분류기 독립판정: matched=2(candidate+watch, blocking 없음)·unmatched=2.
    assert summary.classifier_matched == 2
    assert summary.classifier_unmatched == 2
    # 세그먼트×축(matched=False 전체): license 는 2건, region 은 1건.
    assert summary.blocking_axis_counts == {"license": 2, "region": 1}


def test_aggregate_gate_split_would_match_vs_also_unmatched():
    """게이트 제외를 matched(would_match) vs 애초 unmatched 로 나눠 센다(§요구)."""
    rows = [
        _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=()),  # would_match
        _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=("business_type",)),  # also
        _row(gate_verdict=VERDICT_INELIGIBLE, blocking_axes=("license",)),  # also
    ]
    summary = nc.aggregate_segment(rows, gate_enabled=True)
    assert summary.cause_counts[nc.CAUSE_LICENSE_GATE] == 3
    assert summary.gate_would_match == 1
    assert summary.gate_also_unmatched == 2
    # blocking 축 히스토그램은 분류기 독립판정(watch/게이트 무관·matched=False 전체)이라
    # 게이트가 1차 원인이어도 축을 집계한다 — 상위 게이트가 축 신호를 가리지 않는다.
    assert summary.classifier_unmatched == 2
    assert summary.blocking_axis_counts == {"business_type": 1, "license": 1}


def test_aggregate_gate_verdict_distribution_counts_all():
    rows = [
        _row(gate_verdict=VERDICT_ELIGIBLE),
        _row(gate_verdict=VERDICT_UNKNOWN),
        _row(gate_verdict=VERDICT_UNKNOWN),
    ]
    summary = nc.aggregate_segment(rows, gate_enabled=True)
    assert summary.gate_verdict_counts[VERDICT_UNKNOWN] == 2
    assert summary.gate_verdict_counts[VERDICT_ELIGIBLE] == 1


def test_aggregate_empty_yields_zeroed_summary():
    summary = nc.aggregate_segment([], gate_enabled=True)
    assert summary.total == 0
    assert summary.cause_counts == {}
    assert summary.blocking_axis_counts == {}
    assert summary.classifier_matched == 0
    assert summary.classifier_unmatched == 0
    assert summary.gate_would_match == 0


# --- group_by_segment / build_report -----------------------------------------


def test_group_by_segment_splits_labels():
    rows = [
        _row(segment=LABEL_POSITIVE),
        _row(segment=LABEL_NEGATIVE),
        _row(segment=LABEL_AMBIGUOUS),
    ]
    grouped = nc.group_by_segment(rows)
    assert set(grouped) == {LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIGUOUS}


def test_group_by_segment_blank_is_unlabeled():
    grouped = nc.group_by_segment([_row(segment="")])
    assert nc._UNLABELED in grouped


def test_build_report_carries_measured_counts_not_hardcoded():
    rows = [
        _row(segment=LABEL_POSITIVE, blocking_axes=()),
        _row(segment=LABEL_POSITIVE, blocking_axes=("license",)),
        _row(segment=LABEL_NEGATIVE, passes_watch=False),
        _row(segment=LABEL_AMBIGUOUS, gate_verdict=VERDICT_INELIGIBLE, blocking_axes=()),
    ]
    report = nc.build_report(
        rows,
        gate_enabled=True,
        has_watch_gate=True,
        profile_present=True,
        open_live_total=4481,
        open_live_with_raw=742,
        samples=5,
    )
    assert report.scanned == 4
    assert report.open_live_total == 4481
    assert report.open_live_with_raw == 742
    assert report.overall.total == 4
    assert report.summaries[LABEL_POSITIVE].cause_counts[nc.CAUSE_UNMATCHED] == 1
    assert report.summaries[LABEL_POSITIVE].cause_counts[nc.CAUSE_CANDIDATE] == 1
    assert report.summaries[LABEL_NEGATIVE].cause_counts[nc.CAUSE_STRATEGY_WATCH] == 1
    assert report.summaries[LABEL_AMBIGUOUS].gate_would_match == 1


def test_collect_samples_excludes_candidates_and_caps():
    rows = [
        _row(blocking_axes=(), notice="cand"),
        _row(blocking_axes=("license",), notice="u1"),
        _row(blocking_axes=("license",), notice="u2"),
        _row(blocking_axes=("license",), notice="u3"),
    ]
    samples = nc.collect_samples(rows, gate_enabled=True, samples=2)
    assert nc.CAUSE_CANDIDATE not in samples
    assert len(samples[nc.CAUSE_UNMATCHED]) == 2  # cap 적용


def test_collect_samples_zero_returns_empty():
    rows = [_row(blocking_axes=("license",))]
    assert nc.collect_samples(rows, gate_enabled=True, samples=0) == {}


# --- evaluate_project (I/O 주입 seam) ----------------------------------------


class _FakeProject:
    def __init__(self, notice, title, category):
        self.notice_number = notice
        self.title = title
        self.category = category
        self.id = 7


def test_evaluate_project_maps_injected_signals():
    """주입된 fake 판정 콜러블로 CandidateRow 가 조립된다(무거운 실경로 없음)."""
    project = _FakeProject("2026-1", "항만 준설 설계", "construction")
    row = nc.evaluate_project(
        project,
        segment_fn=lambda _p: LABEL_POSITIVE,
        watch_fn=lambda _p: True,
        gate_fn=lambda _p: VERDICT_INELIGIBLE,
        blocking_axes_fn=lambda _p: ("license", nc.SEMANTIC_AXIS),
    )
    assert row.notice_number == "2026-1"
    assert row.project_id == 7
    assert row.segment == LABEL_POSITIVE
    assert row.gate_verdict == VERDICT_INELIGIBLE
    # blocking_axes_fn 산출을 그대로 담는다(제외는 실경로 fn 책임).
    assert row.blocking_axes == ("license", nc.SEMANTIC_AXIS)


class _FakeClassifier:
    """classify()가 semantic 축을 포함한 blocking_axes를 내는 가짜(실경로 미호출)."""

    def __init__(self, blocking_axes):
        self._blocking_axes = list(blocking_axes)

    def classify(self, _project, _profile):
        return {"score_breakdown": {"blocking_axes": list(self._blocking_axes)}}


def test_build_blocking_axes_fn_strips_semantic_axis():
    """실경로 콜러블이 blocking_axes에서 semantic 축을 제거하고, semantic을 통과
    중립값으로 고정(모델 미로드)하는지 회귀 가드 — fake classifier 주입."""
    fake = _FakeClassifier(["license", nc.SEMANTIC_AXIS, "budget"])
    fn = nc._build_blocking_axes_fn(fake, profile=object())
    # 중립화 대입이 인스턴스 속성으로 설정됨(임베딩 진입점 미호출).
    assert fake._compute_semantic_similarity("a", "b") == (
        nc._NEUTRAL_SEMANTIC_SIMILARITY,
        nc._NEUTRAL_SEMANTIC_SOURCE,
    )
    axes = fn(_FakeProject("n", "t", "service"))
    assert nc.SEMANTIC_AXIS not in axes
    assert axes == ("license", "budget")


def test_build_blocking_axes_fn_empty_when_no_blocking():
    """blocking 없으면 빈 튜플 — 결정적 매칭(candidate 상한)."""
    fn = nc._build_blocking_axes_fn(_FakeClassifier([]), profile=object())
    assert fn(_FakeProject("n", "t", "service")) == ()


def test_clip_title_truncates_long_and_keeps_short():
    assert nc._clip_title(None) is None
    assert nc._clip_title("  short  ") == "short"
    long = "x" * (nc._TITLE_CLIP_CHARS + 10)
    clipped = nc._clip_title(long)
    assert clipped.endswith("…") and len(clipped) == nc._TITLE_CLIP_CHARS + 1


# --- render (통합 순수) ------------------------------------------------------


def _sample_report(**overrides):
    rows = overrides.pop(
        "rows",
        [
            _row(segment=LABEL_POSITIVE, blocking_axes=()),
            _row(segment=LABEL_POSITIVE, blocking_axes=("license",)),
            _row(segment=LABEL_NEGATIVE, gate_verdict=VERDICT_INELIGIBLE, blocking_axes=()),
        ],
    )
    kwargs = dict(
        gate_enabled=True,
        has_watch_gate=True,
        profile_present=True,
        open_live_total=4481,
        open_live_with_raw=742,
        samples=5,
    )
    kwargs.update(overrides)
    return nc.build_report(rows, **kwargs)


def test_render_includes_causes_coverage_and_caveats():
    text = nc.render_report(_sample_report())
    assert nc.CAVEAT_HEADER in text
    assert nc.CAUSE_UNMATCHED in text
    assert nc.CAUSE_LICENSE_GATE in text
    # 커버리지 실측 렌더(742/4481 = 16.6%).
    assert "742/4481" in text
    assert "16.6%" in text
    # semantic 제외·매칭≠낙찰예측 caveat 이 출력된다.
    assert "semantic" in text
    assert "낙찰 예측" in text
    # 게이트 제외 세분이 렌더된다(would_match/also_unmatched).
    assert "would_match" in text
    # 분류기 독립판정(세그먼트×축 blocking) 라인이 렌더된다.
    assert "분류기 독립판정" in text
    assert "blocking 축" in text


def test_render_zero_coverage_edge_case():
    """open·미마감 0 엣지 — 나눗셈 오류 없이 0.0% 렌더."""
    report = _sample_report(rows=[], open_live_total=0, open_live_with_raw=0)
    text = nc.render_report(report)
    assert "0/0 = 0.0%" in text


def test_render_profile_absent_short_circuits():
    report = nc.build_report(
        [],
        gate_enabled=True,
        has_watch_gate=False,
        profile_present=False,
        open_live_total=0,
        open_live_with_raw=0,
    )
    text = nc.render_report(report)
    assert "원인 판정 불가" in text
    # 프로필 부재면 세그먼트/원인 블록은 렌더하지 않는다.
    assert nc.CAVEAT_HEADER not in text


def test_render_gate_off_state_labeled():
    report = _sample_report(gate_enabled=False)
    text = nc.render_report(report)
    assert "OFF(라이브 미제외)" in text
