"""리포트 조립 — 주입된 값 행만으로 (DB 없이) 판정·진단·정직 신고를 고정한다.

여기서 지키는 계약은 세 가지다.

1. **얕은 셀은 "못 쟀다"로 남는다** — 0 이나 빈 값이 아니라 사유가 붙는다(Phase 2c).
2. **α 는 추천하지 않는다** — 격자 위 트레이드오프만, 그리고 제약이 걸린 좌표는
   ``shape_irrelevant`` 로 공시한다(NEW-3).
3. **격자는 잘리지 않는다** — 범위가 넓으면 간격이 넓어질 뿐, 낙찰자가 사는 구간이
   격자 밖으로 밀려나지 않는다(실측에서 용역 코퍼스가 이 경로를 탔다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.award_landing_curve import (
    WIN_PROXY_TIE_TOLERANCE,
    WinProxyCurve,
)
from app.domain.award_margin_distribution import MarginDistribution
from app.services.ml_training import (
    award_landing_curves,
    award_landing_report,
)
from app.services.ml_training.award_landing_cells import (
    dominant_floor_subgroup,
    floor_rate_distribution,
    group_by_cell,
    split_as_of,
)
from app.services.ml_training.award_landing_curves import alpha_tradeoff
from app.services.ml_training.award_landing_dataset import (
    AwardLandingCorpus,
    regime_text,
)
from app.services.ml_training.award_landing_ladder import (
    LadderStage,
    LandingRow,
    NegativeMarginAudit,
)
from app.services.ml_training.award_landing_diagnostics import (
    build_negative_margin_summary,
    pearson,
)
from app.services.ml_training.award_landing_report import build_award_landing_report

START = datetime(2026, 6, 1, tzinfo=UTC)


def landing_row(
    index: int,
    *,
    floor_rate: float = 0.89745,
    margin: float = 0.005,
    assessment_ratio: float = 0.995,
    category: str = "construction",
    amount_band: str = "30m_100m",
) -> LandingRow:
    winning_rate = floor_rate + margin
    base_amount = 50_000_000.0
    omega = winning_rate * assessment_ratio
    return LandingRow(
        project_id=index,
        opened_at=START + timedelta(days=index),
        category=category,
        era_tier="post_2026_01_30",
        amount_band=amount_band,
        agency="울산광역시",
        regime_label="floor_bound",
        base_amount=base_amount,
        winning_amount=base_amount * omega,
        floor_rate=floor_rate,
        winning_rate=winning_rate,
        assessment_ratio=assessment_ratio,
        omega_direct=omega,
    )


def corpus_of(
    rows: list[LandingRow], *, negatives: tuple[NegativeMarginAudit, ...] = ()
) -> AwardLandingCorpus:
    return AwardLandingCorpus(
        rows=tuple(rows),
        candidate_count=len(rows) + 7,
        ladder_counts={stage.value: 0 for stage in LadderStage},
        regime_histogram={"floor_bound": len(rows)},
        negative_margins=negatives,
        regime_gate="floor_bound",
        as_of=None,
    )


def build(rows: list[LandingRow], **overrides: object):  # type: ignore[no-untyped-def]
    kwargs: dict[str, object] = {
        "prior_strength": 40.0,
        "stored_floor_rate": {"construction": 0.87, "service": 0.85},
        "grid_step": 1e-4,
        "min_cell_rows": 10,
    }
    kwargs.update(overrides)
    return build_award_landing_report(corpus_of(rows), **kwargs)  # type: ignore[arg-type]


def spread_rows(count: int, **overrides: float | str) -> list[LandingRow]:
    """마진·사정률을 결정적으로 흩뿌린 행 — 곡선이 한 점으로 붕괴하지 않게."""
    return [
        landing_row(
            index,
            margin=0.001 + (index % 11) * 0.0007,
            assessment_ratio=0.99 + (index % 7) * 0.003,
            **overrides,  # type: ignore[arg-type]
        )
        for index in range(count)
    ]


def test_shallow_cell_reports_why_it_could_not_be_measured() -> None:
    """G3 미달 셀은 빈 칸이 아니라 **사유**를 남긴다 — 0 은 "못 쟀다"가 아니다."""
    report = build(spread_rows(6), min_cell_rows=10)

    cell = report.cells[0]
    assert cell.meets_depth_gate is False
    assert cell.unmeasurable_reason is not None
    assert "6" in cell.unmeasurable_reason and "10" in cell.unmeasurable_reason
    assert report.measurable_cell_count == 0


def test_deep_cell_passes_the_declared_depth_gate() -> None:
    report = build(spread_rows(20), min_cell_rows=10)

    assert report.cells[0].meets_depth_gate is True
    assert report.cells[0].unmeasurable_reason is None
    assert report.measurable_cell_count == 1


def test_alpha_tradeoff_reports_every_declared_alpha_without_picking_one() -> None:
    """격자의 모든 α 가 결과에 남는다 — 실패도 3-status 로 남지 조용히 사라지지 않는다."""
    report = build(spread_rows(20))

    points = report.cells[0].alpha_tradeoff
    assert [point.alpha for point in points] == report.parameters.alpha_grid
    assert all(
        point.status
        in {"optimal", "budget_infeasible", "degenerate_win_proxy"}
        for point in points
    )
    # 어떤 필드도 "권고 α" 를 담지 않는다.
    assert not hasattr(report, "recommended_alpha")


def test_alpha_constraint_pushes_the_landing_point_up() -> None:
    """α 가 조이면 착지점이 **위로** 간다(설계 §7 방향 경고의 회귀 가드)."""
    report = build(spread_rows(30))

    points = {
        point.alpha: point
        for point in report.cells[0].alpha_tradeoff
        if point.optimal_bid_rate is not None
    }
    tight, loose = points[0.01], points[0.50]
    assert tight.optimal_bid_rate is not None and loose.optimal_bid_rate is not None
    assert tight.optimal_bid_rate >= loose.optimal_bid_rate
    assert tight.feasible_point_count <= loose.feasible_point_count


def test_binding_point_marks_the_curve_shape_as_irrelevant() -> None:
    """``b* = b_min(α)`` 면 곡선 모양이 선택에 관여하지 않았음을 공시한다."""
    report = build(spread_rows(30))

    for point in report.cells[0].alpha_tradeoff:
        if point.binding and point.status == "optimal":
            assert point.shape_irrelevant is True
            assert point.distance_above_feasible_lower == pytest.approx(0.0)
        if point.distance_above_feasible_lower is not None:
            assert point.distance_above_feasible_lower >= 0.0


def test_layer_c_matches_layer_a_when_the_assessment_is_degenerate() -> None:
    """사정률이 한 값뿐이면 ``a ⊥ Δ`` 가 자명하게 참이라 두 층이 거의 붙는다.

    이 극한이 성립하지 않으면 대조 수치는 가정의 크기가 아니라 조립 버그를 재고 있다.
    """
    rows = [
        landing_row(index, margin=0.001 + (index % 9) * 0.0006, assessment_ratio=0.995)
        for index in range(24)
    ]
    report = build(rows)

    calibration = report.cells[0].calibration_in_sample
    assert calibration is not None
    assert calibration.max_abs_difference < 0.05
    assert calibration.omega_boundary_lost_atom_count == 0


def test_calibration_uses_the_dominant_floor_subgroup_only() -> None:
    """층 C 는 단일 f 를 받는다 — 대조 스코프도 그 f 의 부분군이어야 축이 맞는다."""
    rows = spread_rows(20) + [
        landing_row(100 + index, floor_rate=0.87745, margin=0.002)
        for index in range(6)
    ]
    report = build(rows)

    cell = report.cells[0]
    assert cell.dominant_floor_rate == pytest.approx(0.89745)
    assert cell.dominant_floor_row_count == 20
    assert cell.calibration_in_sample is not None
    assert cell.calibration_in_sample.floor_rate == pytest.approx(0.89745)
    assert cell.calibration_in_sample.score_row_count == 20


def test_as_of_calibration_never_scores_on_its_own_fit_rows() -> None:
    report = build(spread_rows(20))

    calibration = report.cells[0].calibration_as_of
    assert calibration is not None
    assert calibration.fit_row_count + calibration.score_row_count == 20
    assert calibration.score_row_count > 0
    # 두 창의 **경계**가 겹치지 않는다(행 수만 보면 straddle 이 통과한다 — B1).
    assert calibration.fit_window_end is not None
    assert calibration.score_window_start is not None
    assert calibration.fit_window_end < calibration.score_window_start
    assert calibration.fit_distinct_open_days > 0
    assert calibration.score_distinct_open_days > 0


def tied_rows(count: int, *, group_size: int) -> list[LandingRow]:
    """``group_size`` 행씩 개찰 시각이 동률인 행 — 실코퍼스의 뭉침을 재현한다."""
    return [
        replace(
            landing_row(index, margin=0.001 + (index % 7) * 0.0006),
            opened_at=START + timedelta(days=index // group_size),
        )
        for index in range(count)
    ]


def test_as_of_split_never_straddles_a_tied_open_timestamp() -> None:
    """B1 회귀 — 동률 시각 그룹은 통째로 한쪽에 간다.

    실코퍼스는 개찰 시각이 심하게 뭉쳐 있어(distinct 107개, 최대 동률 232행) 인덱스
    경계가 동률 그룹 **내부**에 떨어졌다. 그러면 채점 행과 **완전히 동시에** 관측된
    형제 행으로 적합한 분포가 그 행을 채점한다 — G2 "겹치지 않는 창" 위반이고 방향은
    층 C 에 유리하다(누수는 항상 낙관).
    """
    rows = tied_rows(20, group_size=5)

    fit, score = split_as_of(rows, fit_share=0.7)

    assert fit and score
    assert {row.opened_at for row in fit} & {row.opened_at for row in score} == set()
    assert max(row.opened_at for row in fit) < min(row.opened_at for row in score)
    # 동률 그룹이 쪼개지지 않았으므로 두 창의 행 수는 그룹 크기의 배수다.
    assert len(fit) % 5 == 0 and len(score) % 5 == 0


def test_parent_margin_distribution_boundary_is_strict() -> None:
    """B1 회귀(부모 축) — 경계 시각의 행은 부모에 **들어가지 않는다**.

    ``<=`` 면 채점 창의 첫 시각을 공유하는 형제 행이 수축 부모로 새어 들어가고, 그
    행들은 자기 자신을 채점하는 셈이 된다.
    """
    rows = tied_rows(10, group_size=5)
    boundary = rows[5].opened_at

    parent = award_landing_curves.parent_margin_distribution(rows, before=boundary)
    unbounded = award_landing_curves.parent_margin_distribution(rows, before=None)

    assert parent is not None and unbounded is not None
    # 경계 이전 5행만 들어갔다 — 경계 시각 행 5개가 빠진다.
    assert parent.cumulative_probability(max(row.margin for row in rows[:5])) == 1.0
    assert unbounded.cumulative_probability(
        max(row.margin for row in rows[:5])
    ) < 1.0


def test_as_of_parent_boundary_is_the_first_scoring_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1 회귀(배선 축) — 부모 경계로 **첫 채점 시각**을 넘긴다(적합 마지막 시각이 아니라).

    ``fit+score == n`` 만 보는 테스트는 이 누수를 통과시킨다: 부모 분포는 셀 분할과
    다른 경로로 만들어지므로, 어떤 경계가 넘어가는지를 직접 잡아야 한다.
    """
    seen: list[datetime | None] = []
    original = award_landing_report.parent_margin_distribution

    def spy(
        rows: Sequence[LandingRow], *, before: datetime | None
    ) -> MarginDistribution | None:
        seen.append(before)
        return original(rows, before=before)

    monkeypatch.setattr(award_landing_report, "parent_margin_distribution", spy)
    rows = tied_rows(20, group_size=5)
    build(rows)

    _fit, score = split_as_of(rows, fit_share=0.7)
    boundaries = [before for before in seen if before is not None]
    assert boundaries == [score[0].opened_at]


def test_alpha_rollup_separates_deep_cells_from_unmeasurable_ones() -> None:
    """N1 — 판정 불가 셀을 같은 평균에 섞지 않는다(Phase 2c: 판정 ≠ 못 쟀다).

    얕은 셀은 격자가 좁아 α 좌표가 ``b_min`` 에 붙기 쉽고, 그 0 들이 깊은 셀의 값을
    끌어내려 헤드라인을 희석한다(실측: 전-셀 median gain 0.0 인 게이트도 깊은 셀만은
    0 이 아니었다). 두 스코프를 이름으로 구별해 병기해야 어느 모집단의 수치를 인용하는지가
    분명해진다.
    """
    shallow = [
        replace(
            landing_row(300 + index, margin=0.001 + index * 0.0009),
            amount_band="lt_10m",
        )
        for index in range(4)
    ]

    report = build(spread_rows(20) + shallow, min_cell_rows=10)

    all_cells = report.constrained_all_cells
    deep_cells = report.constrained_deep_cells
    assert all_cells.scope == "all_cells" and deep_cells.scope == "deep_cells"
    assert all_cells.cell_count == 2 and deep_cells.cell_count == 1
    assert deep_cells.alpha_point_count < all_cells.alpha_point_count
    assert sum(all_cells.status_histogram.values()) == all_cells.alpha_point_count
    assert sum(deep_cells.status_histogram.values()) == deep_cells.alpha_point_count
    assert report.measurable_cell_count == deep_cells.cell_count


def test_alpha_rollup_is_named_but_empty_when_no_cell_is_measurable() -> None:
    """깊은 셀이 없으면 값이 0 이 아니라 ``None`` 이다 — "못 쟀다"를 0 으로 쓰지 않는다."""
    report = build(spread_rows(6), min_cell_rows=10)

    deep_cells = report.constrained_deep_cells
    assert deep_cells.cell_count == 0
    assert deep_cells.alpha_point_count == 0
    assert deep_cells.binding_share is None
    assert deep_cells.median_distance_above_feasible_lower is None
    assert deep_cells.median_alpha_gain_over_feasible_lower is None
    assert report.constrained_all_cells.cell_count == 1


def test_wide_range_widens_the_step_instead_of_dropping_the_award_region() -> None:
    """``f=0.47995`` 같은 실값이 섞여도 낙찰자 구간이 격자 밖으로 밀려나지 않는다."""
    rows = spread_rows(20) + [
        landing_row(200 + index, floor_rate=0.47995, margin=0.01)
        for index in range(4)
    ]
    report = build(rows, grid_step=1e-5)

    summary = report.cells[0].layer_a
    assert summary is not None
    assert summary.grid_step_widened is True
    assert summary.grid_step_effective > 1e-5
    # 지배 하한(0.89745) 근방의 실제 착지가 격자 안에 있다.
    assert summary.grid_lower_rate < 0.47995
    assert summary.grid_upper_rate > 0.90


def test_stored_label_floor_mismatch_is_measured_not_assumed() -> None:
    """M4 — 저장 라벨 하한(카테고리 상수)과 게시 f 의 불일치를 실측으로 신고한다."""
    report = build(spread_rows(12))

    mismatch = report.corpus.stored_label_floor_mismatch
    assert mismatch["comparable_row_count"] == 12.0
    assert mismatch["mismatch_share"] == 1.0
    assert mismatch["median_gap"] == pytest.approx(0.89745 - 0.87)


def test_quoted_comparison_reports_both_numbers_and_never_verdicts() -> None:
    report = build(spread_rows(20))

    by_name = {row.name: row for row in report.quoted_comparisons}
    assert by_name["corr_assessment_margin"].quoted == pytest.approx(-0.0045)
    # 재산출 불가(용역 행 0)는 0 이 아니라 None 이다.
    assert by_name["corr_floor_margin_service"].recomputed is None
    assert by_name["corr_floor_margin_service"].difference is None
    assert all(
        not hasattr(row, "passed") for row in report.quoted_comparisons
    )


def test_negative_margin_summary_separates_quantisation_from_pollution() -> None:
    """ε 재조정(백로그 ⑥)의 판단 재료 — 크기 구간과 ``f == 1.0`` 점유."""
    audits = (
        NegativeMarginAudit(1, 1.0, 0.9, -0.1),
        NegativeMarginAudit(2, 0.89745, 0.897445, -5e-6),
    )
    summary = build_negative_margin_summary(audits, unity_floor_rate=1.0)

    assert summary.count == 2
    assert summary.unity_floor_count == 1
    assert summary.unity_floor_share == pytest.approx(0.5)
    assert summary.magnitude_buckets == {"1e-06~1e-05": 1, ">=0.01": 1}
    assert summary.distinct_floor_rates == [0.89745, 1.0]


def test_pearson_returns_none_rather_than_zero_when_unmeasurable() -> None:
    """상수 축의 상관은 "0" 이 아니라 "못 쟀다" 다."""
    assert pearson([(1.0, 2.0)]) is None
    assert pearson([(1.0, 2.0), (1.0, 3.0)]) is None
    assert pearson([(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]) == pytest.approx(1.0)


def test_floor_distribution_and_dominant_subgroup_are_deterministic() -> None:
    rows = [landing_row(index, floor_rate=0.88) for index in range(3)] + [
        landing_row(10 + index, floor_rate=0.90) for index in range(3)
    ]

    distribution = floor_rate_distribution(rows)
    dominant_rate, dominant_rows = dominant_floor_subgroup(rows)

    # 동률이면 낮은 f 를 고른다(실행마다 스코프가 흔들리면 수치가 재현되지 않는다).
    assert [share.floor_rate for share in distribution] == [0.88, 0.90]
    assert dominant_rate == pytest.approx(0.88)
    assert len(dominant_rows) == 3


def test_split_as_of_refuses_a_one_sided_split() -> None:
    assert split_as_of([landing_row(0)]) == ((), ())
    fit, score = split_as_of([landing_row(i) for i in range(10)], fit_share=0.7)
    assert len(fit) == 7 and len(score) == 3
    assert fit[-1].opened_at < score[0].opened_at


def test_cells_are_keyed_on_three_axes_and_ordered_by_time() -> None:
    """셀 키 3축 + 셀 내부 시각 오름차순(as-of 분할이 그 순서에 기댄다)."""
    rows = [
        landing_row(2, category="service", amount_band="10m_30m"),
        landing_row(1, category="service", amount_band="10m_30m"),
        landing_row(3, category="construction", amount_band="10m_30m"),
    ]

    grouped = group_by_cell(rows)

    assert sorted(cell.key for cell in grouped) == [
        "construction|post_2026_01_30|10m_30m",
        "service|post_2026_01_30|10m_30m",
    ]
    service_cell = next(cell for cell in grouped if cell.category == "service")
    assert [row.project_id for row in grouped[service_cell]] == [1, 2]


def test_cell_reports_the_share_it_could_not_confirm_as_floor_bound() -> None:
    """M1 — ``ambiguous`` 점유는 셀 단위로 신고한다(게이트 이름 한 줄로는 안 보인다)."""
    rows = spread_rows(10) + [
        replace(landing_row(50 + index), regime_label="ambiguous")
        for index in range(10)
    ]

    report = build(rows)

    assert report.cells[0].ambiguous_share == pytest.approx(0.5)
    # 확정된 행만 있는 코퍼스는 0 이다.
    assert build(spread_rows(10)).cells[0].ambiguous_share == pytest.approx(0.0)


def test_f_mixed_cell_carries_the_scope_caveat() -> None:
    """M5 — 셀 층 A·α 곡선은 f-혼합 위에서 만든다는 사실을 필드로 남긴다."""
    mixed = build(
        spread_rows(20)
        + [landing_row(100 + index, floor_rate=0.87745) for index in range(4)]
    )
    homogeneous = build(spread_rows(20))

    assert mixed.cells[0].floor_mixture_caveat is not None
    assert "최빈 점유" in mixed.cells[0].floor_mixture_caveat
    assert homogeneous.cells[0].floor_mixture_caveat is None


def test_missing_as_of_calibration_carries_a_reason() -> None:
    """"못 쟀으면 사유" 계약을 as-of 자리에도 적용한다 — 빈칸으로 두지 않는다."""
    single_timestamp = [
        replace(landing_row(index, margin=0.001 + index * 0.0004), opened_at=START)
        for index in range(6)
    ]

    report = build(single_timestamp)

    cell = report.cells[0]
    assert cell.calibration_as_of is None
    assert cell.calibration_as_of_unmeasurable_reason is not None
    assert "못 쟀다" in cell.calibration_as_of_unmeasurable_reason


def test_alpha_point_reports_the_win_proxy_at_the_feasible_lower_bound() -> None:
    """거리만으로는 신호/잡음이 안 갈린다 — ``b_min(α)`` 에서의 ``WW`` 를 함께 싣는다."""
    report = build(spread_rows(30))

    for point in report.cells[0].alpha_tradeoff:
        if point.status != "optimal":
            continue
        assert point.win_proxy is not None
        assert point.win_proxy_at_feasible_lower is not None
        # 제약 안에서 고른 값이므로 하단보다 낮을 수 없다.
        assert point.win_proxy >= point.win_proxy_at_feasible_lower
    assert (
        report.constrained_deep_cells.median_alpha_gain_over_feasible_lower is not None
    )


def test_tradeoff_tolerates_a_disqualification_curve_within_the_kernel_tolerance() -> (
    None
):
    """합성 경로의 부분합은 1 ulp 역전이 가능하다 — 허용오차 안이면 판정이 선다.

    엄격 비교로 되돌리면 층 C 곡선이 입구에서 죽고, 그 실패는 "판정 불가"가 아니라
    예외로 나타난다.
    """
    curve = WinProxyCurve(
        bid_rates=(0.89, 0.90, 0.91),
        values=(0.1, 0.3, 0.2),
        # 가운데가 직전보다 허용오차 **안에서** 크다(부동소수 잡음 규모).
        disqualification_probabilities=(0.30, 0.30 + WIN_PROXY_TIE_TOLERANCE / 2, 0.05),
    )

    points = alpha_tradeoff(curve)

    assert [point.alpha for point in points]
    assert any(point.status == "optimal" for point in points)


def test_regime_text_joins_title_body_and_requirements() -> None:
    """레짐 텍스트는 라이브 추천과 같은 조합이다(#239 title 정렬).

    백테스트만 다른 텍스트를 보면 여기서 잰 레짐 분포가 서빙이 마주할 분포가 아니게 된다.
    """
    assert regime_text("항만 공사", "적격심사", None) == "항만 공사 적격심사"
    assert regime_text(None, None, None) == ""
