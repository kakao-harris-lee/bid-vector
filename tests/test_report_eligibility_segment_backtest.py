"""추천 투찰가 오차의 eligibility 세그먼트 집계 리포트 순수 함수 테스트.

세그먼트 판정(rule_label/classify 두 축)·오차 집계·오염 가드·caveat 지표를 값
테이블로 검증한다. DB/네트워크 없음 — 스크립트를 경로로 로드해 순수 함수만 부른다.
빈 세그먼트·operator 라벨 부재·커버리지 0 엣지 케이스를 포함한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
)
from app.services.eligibility_labeling import (
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
)

# scripts/ 는 import 가능한 패키지가 아니므로 경로로 로드한다(기존 테스트 패턴).
_SPEC = importlib.util.spec_from_file_location(
    "report_eligibility_segment_backtest",
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "report_eligibility_segment_backtest.py",
)
seg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = seg
_SPEC.loader.exec_module(seg)


def _row(
    *,
    rule_label: str,
    classify_label: str | None = None,
    basis: str = BASIS_CLEAN,
    err_pct: float | None = 0.001,
    rate_bp: float | None = 10.0,
    category: str = "service",
    notice: str = "N",
):
    return seg.SegmentRow(
        notice_number=notice,
        project_id=1,
        category=category,
        group="service",
        basis=basis,
        rule_label=rule_label,
        classify_label=classify_label if classify_label is not None else rule_label,
        absolute_amount_error_pct=err_pct,
        rate_error_bp=rate_bp,
    )


# --- build_segment_row (홀드아웃 dict → SegmentRow 매핑) ----------------------


def test_build_segment_row_maps_recommended_error():
    evaluated = {
        "notice_number": "2026-1",
        "project_id": 42,
        "category": "service",
        "group": "service",
        "basis": BASIS_CLEAN,
        "recommended": {"absolute_amount_error_pct": 0.004, "rate_error_bp": -12.0},
    }
    row = seg.build_segment_row(
        evaluated, rule_label=LABEL_POSITIVE, classify_label=LABEL_NEGATIVE
    )
    assert row.rule_label == LABEL_POSITIVE
    assert row.classify_label == LABEL_NEGATIVE
    assert row.basis == BASIS_CLEAN
    assert row.absolute_amount_error_pct == 0.004
    assert row.rate_error_bp == -12.0


def test_build_segment_row_missing_recommended_is_none():
    """추천 시나리오가 없으면 오차가 None(집계 제외 대상)이어야 한다."""
    evaluated = {"notice_number": "x", "project_id": 1, "basis": BASIS_CLEAN}
    row = seg.build_segment_row(
        evaluated, rule_label=LABEL_NEGATIVE, classify_label=LABEL_NEGATIVE
    )
    assert row.absolute_amount_error_pct is None
    assert row.rate_error_bp is None


# --- segment_key (두 축) -----------------------------------------------------


def test_segment_key_uses_axis():
    row = _row(rule_label=LABEL_POSITIVE, classify_label=LABEL_NEGATIVE)
    assert seg.segment_key(row, seg.AXIS_RULE_LABEL) == LABEL_POSITIVE
    assert seg.segment_key(row, seg.AXIS_CLASSIFY) == LABEL_NEGATIVE


def test_segment_key_blank_label_is_unlabeled():
    row = _row(rule_label="", classify_label="")
    assert seg.segment_key(row, seg.AXIS_RULE_LABEL) == seg._UNLABELED


# --- filter_aggregatable (오염 가드) -----------------------------------------


def test_filter_aggregatable_excludes_contaminated_by_default():
    rows = [
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_CLEAN, err_pct=0.001),
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_DERIVED_YEGA, err_pct=0.5),
        _row(rule_label=LABEL_NEGATIVE, basis=BASIS_SUSPECT_FRACTIONAL, err_pct=0.9),
    ]
    kept, excluded = seg.filter_aggregatable(rows, include_contaminated=False)
    assert [r.basis for r in kept] == [BASIS_CLEAN]
    assert excluded == 2


def test_filter_aggregatable_include_folds_all_in():
    rows = [
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_CLEAN, err_pct=0.001),
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_DERIVED_YEGA, err_pct=0.5),
    ]
    kept, excluded = seg.filter_aggregatable(rows, include_contaminated=True)
    assert len(kept) == 2
    assert excluded == 0


def test_filter_aggregatable_drops_rows_without_error():
    rows = [
        _row(rule_label=LABEL_POSITIVE, err_pct=None),
        _row(rule_label=LABEL_POSITIVE, err_pct=0.002),
    ]
    kept, excluded = seg.filter_aggregatable(rows, include_contaminated=True)
    assert len(kept) == 1
    assert excluded == 0


# --- aggregate_segment (오차 요약) -------------------------------------------


def test_aggregate_segment_computes_mean_and_within():
    rows = [
        _row(rule_label=LABEL_POSITIVE, err_pct=0.001, rate_bp=10.0),
        _row(rule_label=LABEL_POSITIVE, err_pct=0.003, rate_bp=-30.0),
    ]
    summary = seg.aggregate_segment(rows, (0.001, 0.005))
    assert summary.sample_count == 2
    assert summary.mean_absolute_amount_error_pct == 0.002
    assert summary.mean_absolute_bid_rate_error_bp == 20.0
    assert summary.within_counts == {"0.1%": 1, "0.5%": 2}


def test_aggregate_segment_empty_yields_none_metrics():
    summary = seg.aggregate_segment([], (0.003,))
    assert summary.sample_count == 0
    assert summary.mean_absolute_amount_error_pct is None
    assert summary.mean_absolute_bid_rate_error_bp is None
    assert summary.within_counts == {"0.3%": 0}


def test_aggregate_segment_missing_rate_bp_is_none():
    rows = [_row(rule_label=LABEL_POSITIVE, err_pct=0.002, rate_bp=None)]
    summary = seg.aggregate_segment(rows, (0.003,))
    assert summary.sample_count == 1
    assert summary.mean_absolute_bid_rate_error_bp is None


# --- aggregate_by_segment (세그먼트 분해) ------------------------------------


def test_aggregate_by_segment_splits_positive_and_negative():
    rows = [
        _row(rule_label=LABEL_POSITIVE, err_pct=0.001),
        _row(rule_label=LABEL_NEGATIVE, err_pct=0.02),
        _row(rule_label=LABEL_NEGATIVE, err_pct=0.04),
    ]
    summaries, excluded = seg.aggregate_by_segment(
        rows, axis=seg.AXIS_RULE_LABEL, thresholds=(0.003,), include_contaminated=False
    )
    assert excluded == 0
    assert summaries[LABEL_POSITIVE].sample_count == 1
    assert summaries[LABEL_NEGATIVE].sample_count == 2
    assert summaries[LABEL_POSITIVE].mean_absolute_amount_error_pct == 0.001
    assert summaries[LABEL_NEGATIVE].within_counts["0.3%"] == 0


def test_aggregate_by_segment_classify_axis_differs_from_rule():
    """같은 행이 rule=positive 이나 classify=negative 로 갈리면 축별로 다르게 묶인다."""
    rows = [_row(rule_label=LABEL_POSITIVE, classify_label=LABEL_NEGATIVE, err_pct=0.002)]
    rule_summaries, _ = seg.aggregate_by_segment(
        rows, axis=seg.AXIS_RULE_LABEL, thresholds=(0.003,), include_contaminated=False
    )
    classify_summaries, _ = seg.aggregate_by_segment(
        rows, axis=seg.AXIS_CLASSIFY, thresholds=(0.003,), include_contaminated=False
    )
    assert LABEL_POSITIVE in rule_summaries and LABEL_NEGATIVE not in rule_summaries
    assert LABEL_NEGATIVE in classify_summaries and LABEL_POSITIVE not in classify_summaries


def test_aggregate_by_segment_empty_rows_yield_empty():
    summaries, excluded = seg.aggregate_by_segment(
        [], axis=seg.AXIS_RULE_LABEL, thresholds=(0.003,), include_contaminated=False
    )
    assert summaries == {}
    assert excluded == 0


# --- 분포/총계 ---------------------------------------------------------------


def test_totals_by_segment_counts_all_rows_pre_clean_filter():
    rows = [
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_DERIVED_YEGA),
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_CLEAN),
        _row(rule_label=LABEL_NEGATIVE, err_pct=None),
    ]
    totals = seg.totals_by_segment(rows, seg.AXIS_RULE_LABEL)
    assert totals == {LABEL_POSITIVE: 2, LABEL_NEGATIVE: 1}


def test_category_counts_surface_single_category_overfit_evidence():
    rows = [
        _row(rule_label=LABEL_POSITIVE, category="service"),
        _row(rule_label=LABEL_POSITIVE, category="service"),
    ]
    counts = seg.category_counts_by_segment(rows, seg.AXIS_RULE_LABEL)
    assert counts[LABEL_POSITIVE] == {"service": 2}


def test_basis_counts_report_contamination_mix():
    rows = [
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_CLEAN),
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_DERIVED_YEGA),
    ]
    counts = seg.basis_counts_by_segment(rows, seg.AXIS_RULE_LABEL)
    assert counts[LABEL_POSITIVE] == {BASIS_CLEAN: 1, BASIS_DERIVED_YEGA: 1}


# --- build_report + render (통합 순수 조립) ----------------------------------


def _sample_rows():
    return [
        _row(rule_label=LABEL_POSITIVE, err_pct=0.001, category="service"),
        _row(rule_label=LABEL_POSITIVE, basis=BASIS_DERIVED_YEGA, err_pct=0.5),
        _row(rule_label=LABEL_NEGATIVE, err_pct=0.02, category="construction"),
    ]


def test_build_report_carries_measured_counts_not_hardcoded():
    report = seg.build_report(
        _sample_rows(),
        thresholds=(0.003,),
        include_contaminated=False,
        total_rule_labels=3958,
        skipped_no_award=10,
        skipped_unmapped=2,
        coverage_open_total=5436,
        coverage_open_with_raw=745,
    )
    assert report.evaluated_count == 3
    assert report.total_rule_labels == 3958
    # clean-only: positive 오염 1건 제외
    assert report.excluded_by_axis[seg.AXIS_RULE_LABEL] == 1
    assert report.summaries_by_axis[seg.AXIS_RULE_LABEL][LABEL_POSITIVE].sample_count == 1
    assert report.totals_by_axis[seg.AXIS_RULE_LABEL][LABEL_POSITIVE] == 2
    assert report.coverage_open_total == 5436


def test_render_report_includes_caveats_and_measured_coverage():
    report = seg.build_report(
        _sample_rows(),
        thresholds=(0.003,),
        include_contaminated=False,
        total_rule_labels=3958,
        skipped_no_award=0,
        skipped_unmapped=0,
        coverage_open_total=5436,
        coverage_open_with_raw=745,
    )
    text = seg.render_report(report)
    assert seg.CAVEAT_HEADER in text
    # 커버리지는 실측 수치로 렌더(745/5436 = 13.7%)
    assert "745/5436" in text
    assert "13.7%" in text
    # 두 축이 모두 렌더된다
    assert f"축[{seg.AXIS_RULE_LABEL}]" in text
    assert f"축[{seg.AXIS_CLASSIFY}]" in text
    # precision/recall 은 이 리포트 소관이 아님을 명시
    assert "precision/recall" in text


def test_render_report_zero_coverage_edge_case():
    """커버리지 0(open 공고 0) 엣지 케이스 — 나눗셈 오류 없이 0.0% 렌더."""
    report = seg.build_report(
        [],
        thresholds=(0.003,),
        include_contaminated=False,
        total_rule_labels=0,
        skipped_no_award=0,
        skipped_unmapped=0,
        coverage_open_total=0,
        coverage_open_with_raw=0,
    )
    text = seg.render_report(report)
    assert "0/0 = 0.0%" in text
    assert "(표본 없음)" in text


def test_render_report_operator_labels_absent_positive_still_reported():
    """operator 라벨 부재(축 rule/classify 만) 여도 positive 세그먼트가 집계된다."""
    rows = [_row(rule_label=LABEL_POSITIVE, classify_label=LABEL_POSITIVE, err_pct=0.002)]
    report = seg.build_report(
        rows,
        thresholds=(0.003,),
        include_contaminated=False,
        total_rule_labels=1,
        skipped_no_award=0,
        skipped_unmapped=0,
        coverage_open_total=100,
        coverage_open_with_raw=13,
    )
    text = seg.render_report(report)
    assert LABEL_POSITIVE in text
    assert "operator 정답 라벨(현재 0)" in text


def test_ambiguous_segment_supported():
    rows = [_row(rule_label=LABEL_AMBIGUOUS, err_pct=0.01)]
    summaries, _ = seg.aggregate_by_segment(
        rows, axis=seg.AXIS_RULE_LABEL, thresholds=(0.003,), include_contaminated=False
    )
    assert summaries[LABEL_AMBIGUOUS].sample_count == 1
