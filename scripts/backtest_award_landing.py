#!/usr/bin/env python3
"""낙찰자 착지 win-proxy 백테스트 (Phase 3 PR2) — 층 A 접지 진리 + 층 C 캘리브레이션.

무엇을 재는가
-------------
반사실 삽입 win-proxy ``WW(b) = P(φ ≤ b ≤ ω)`` 를 ``공종 × era tier × 금액대`` 셀마다
두 방식으로 낸다.

* **층 A** — 관측 쌍을 직접 세는 결합 경험추정. 가정 0개이고, 이 코퍼스의 **접지 진리**다.
* **층 C** — 사정률 지지집합 × 마진 ECDF 합성(``a ⊥ Δ`` 가정 하나). 층 A 와 나란히 놓아
  그 가정이 이 데이터에서 얼마나 값을 움직이는지 **실측**한다.

**게이트가 아니라 측정이다.** 통과/실패를 찍지 않는다 — 셀마다 표본 깊이와 G3 충족
여부를 신고하고, 얕은 셀은 "못 쟀다"로 남긴다(Phase 2c 원칙: "못 이겼다"와 "못 쟀다"를
구별하라).

α 는 추천하지 않는다
--------------------
실격 예산 ``α`` 는 채점 코퍼스가 아니라 **운영자 리스크 허용치**다(설계 NEW-3). 이
스크립트는 α↔WW 트레이드오프 곡선만 보고하고, 제약이 걸린 좌표에서 ``b* = b_min(α)``
라 곡선 모양이 선택에 무관하다는 사실을 함께 공시한다.

κ(``--prior-strength``)는 **기본값이 없다**. 마진 분포 수축 강도를 상수로 박으면 사후
산물이 되므로(설계 §5.2·``GATE_MIN_EVALUATION_ROWS`` 교훈) 실행마다 선언한다.

DB read-only(SELECT 전용, write/commit 없음). 외부 API 호출 없음.

사용 예::

    python scripts/backtest_award_landing.py --prior-strength 40
    python scripts/backtest_award_landing.py --prior-strength 40 \\
        --regime-gate not_negotiated --out reports/award-landing-loose.json
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

from app.ai.price_prediction import _resolve_floor_bid_rate
from app.core.database import SessionLocal
from app.domain.award_landing_curve import DEFAULT_GRID_STEP
from app.schemas._base import StrictModel
from app.services.ml_training.award_landing_cells import DEFAULT_AS_OF_FIT_SHARE
from app.services.ml_training.award_landing_dataset import (
    AwardLandingCorpus,
    load_award_landing_corpus,
)
from app.services.ml_training.award_landing_ladder import (
    DEFAULT_REGIME_GATE,
    REGIME_GATE_POLICIES,
)
from app.services.ml_training.award_landing_report import (
    MIN_CELL_ROWS,
    AwardLandingReport,
    CellReport,
    build_award_landing_report,
)
from app.services.ml_training.award_landing_schema import ConstrainedRollup
from scripts._common import parse_datetime


class ConsoleSummary(StrictModel):
    """콘솔 한 줄 요약.

    **JSON 이 정직해도 인용되는 것은 이 줄이다**(Phase 2c 교훈). 그래서 "판정했다"와
    "잴 수 있는 셀이 없었다"를 가르는 신호가 여기에도 실린다: G3 를 넘긴 셀 수,
    얕은 셀 목록, era tier 가 f 를 동질화했는지, 그리고 층 A↔층 C 최대 격차.
    """

    out: str
    regime_gate: str
    prior_strength: float
    candidate_count: int
    accepted_count: int
    ladder_drops: dict[str, int]
    regime_histogram: dict[str, int]
    negative_margins: str
    """``Δ<0`` 건수와 ``f==1.0`` 점유 — 설계 m1("전부 f==1.0")의 부분 비재현이 JSON
    안에만 남지 않게 한 줄로 올린다."""
    measurable_cell_count: int
    cell_count: int
    deep_cells: list[str]
    """G3 통과 셀. **깊이만으로는 판정이 못 선다**는 것을 같은 줄에서 보여 주려고
    ``ambiguous`` 점유·봉우리 수·동률 폭을 함께 적는다(§11-7)."""
    shallow_cells: list[str]
    """G3(n≥150) 미달 셀 — 이 목록이 비지 않으면 그 셀의 곡선은 판정이 아니라 관측이다."""
    max_calibration_gap: float | None = None
    """층 A↔층 C 최대 |차이| (in-sample, 깊은 셀 기준). 독립 가정의 실측 크기."""
    calibration_windows: list[str]
    """as-of 대조의 적합/채점 창 폭(KST 달력일). 채점 창이 며칠뿐이면 그 격차는
    "가정의 비용"이 아니라 "단기 분포 이동"일 수 있다."""
    as_of_unmeasurable_cells: list[str]
    """as-of 대조를 낼 수 없었던 셀과 그 사유 — 빈칸이 아니라 사유로 남긴다."""
    omega_boundary_flags: list[str]
    """ω 경계 잔여가 1/N 을 넘거나 방향이 뒤집힌 셀 — ``survival_scaled`` 재론 신호."""
    heterogeneous_floor_cells: list[str]
    """최빈 f 점유가 1 미만인 셀 — era tier 가 f 를 **동질화하지 못했다**(F1 재료)."""
    alpha_deep_cells: str
    """**G3 통과 셀만**의 α 집계 — 인용해야 하는 쪽(N1).

    binding 비율 · ``b* − b_min(α)`` 거리 · ``WW(b*) − WW(b_min(α))`` 이득을 함께 적는다.
    거리를 "제약 바로 위라 사실상 binding" 으로 읽으면 안 되고(리뷰 정정), 그 선택이
    신호인지 잡음인지는 이득이 셀별 ``1/N`` 규모인지로 가른다."""
    alpha_all_cells: str
    """전-셀 α 집계 — **판정 불가 셀이 섞여 있다**. 위 줄과 갈리면 그 차이 자체가
    "얕은 셀이 헤드라인을 희석하고 있다"는 신호다(둘을 한 수로 뭉치지 않는다)."""
    widened_grid_cells: list[str]
    """격자 간격이 자동으로 넓어진 셀(범위가 0.1bp 격자의 점 수 상한을 넘었다)."""
    quoted_deltas: list[str]
    """인용값(=floor_bound 게이트 **미적용** 모집단) ↔ 이 코퍼스 재산출값 병기.
    **"재현"이 아니라 다른 모집단에서의 재산출**이므로 자동 판정을 붙이지 않는다."""


def _add_corpus_arguments(parser: argparse.ArgumentParser) -> None:
    """코퍼스 정의를 바꾸는 인자 — 어떤 행을 보는가."""
    parser.add_argument(
        "--regime-gate",
        choices=sorted(REGIME_GATE_POLICIES),
        default=DEFAULT_REGIME_GATE,
        help=(
            "Price-regime hard filter. 'floor_bound' is the design-faithful default; "
            "'not_negotiated' also keeps 'ambiguous' rows so the report can separate "
            "'excluded because negotiated' from 'excluded because the notice text was "
            "empty'."
        ),
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Only score notices opened at or before this timestamp (leak guard).",
    )


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    """추정·판정을 바꾸는 인자 — 본 행을 어떻게 재는가."""
    parser.add_argument(
        "--prior-strength",
        type=float,
        required=True,
        help=(
            "kappa for the margin-distribution shrinkage (cell -> category). Required "
            "on purpose: baking a constant would make it a post-hoc artefact."
        ),
    )
    parser.add_argument(
        "--grid-step",
        type=float,
        default=DEFAULT_GRID_STEP,
        help="Candidate grid resolution for the verdict curves (default 0.1bp).",
    )
    parser.add_argument(
        "--fit-share",
        type=float,
        default=DEFAULT_AS_OF_FIT_SHARE,
        help="Fraction of each cell used to fit the as-of layer C (older rows).",
    )
    parser.add_argument(
        "--min-cell-rows",
        type=int,
        default=MIN_CELL_ROWS,
        help=(
            "Declared subcell depth gate (G3). The default is the selector verdict's "
            "150 — lowering it to manufacture a measurable cell defeats the gate."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Award-landing win-proxy backtest: layer A ground truth, layer C "
            "calibration contrast, and the alpha/WW tradeoff curve (no value picked)."
        )
    )
    _add_corpus_arguments(parser)
    _add_analysis_arguments(parser)
    parser.add_argument("--out", default="", help="Optional JSON report path.")
    return parser


def resolve_report_path(raw: str) -> Path:
    """리포트 경로(미지정이면 타임스탬프 파일)를 만들고 상위 디렉터리를 보장한다."""
    path = Path(
        raw
        or "reports/award-landing-backtest-"
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_corpus(*, as_of: datetime | None, regime_gate: str) -> AwardLandingCorpus:
    """코퍼스를 한 세션에서 싣는다(SELECT 전용 — write/commit 없음)."""
    db = SessionLocal()
    try:
        return load_award_landing_corpus(db, as_of=as_of, regime_gate=regime_gate)
    finally:
        db.close()


def _stored_floor_rates(corpus: AwardLandingCorpus) -> dict[str, float | None]:
    """공종 → 저장 ``_final`` 라벨이 쓰는 하한율(M4 대조 입력).

    라이브 guardrail 이 쓰는 것과 **같은 리졸버**를 부른다 — 여기서 상수를 다시 적으면
    "저장 라벨의 하한"이 두 벌이 되어 불일치 측정 자체가 무의미해진다.
    """
    return {
        category: _resolve_floor_bid_rate(category)
        for category in sorted({row.category for row in corpus.rows})
    }


def _omega_boundary_flags(cells: list[CellReport]) -> list[str]:
    """ω 경계 잔여가 1/N 을 넘거나 방향이 뒤집힌 셀 — 계약 확장 재론 신호."""
    flagged: list[str] = []
    for cell in cells:
        calibration = cell.calibration_in_sample
        if calibration is None:
            continue
        if not (
            calibration.omega_boundary_exceeds_one_over_n
            or calibration.omega_boundary_direction in {"reversed", "mixed"}
        ):
            continue
        flagged.append(
            f"{cell.cell_key} lost={calibration.omega_boundary_lost_atom_count}"
            f"/{calibration.omega_boundary_sample_count} "
            f"dir={calibration.omega_boundary_direction}"
        )
    return flagged


def _heterogeneous_floor_cells(cells: list[CellReport]) -> list[str]:
    """최빈 f 점유가 1 미만인 셀 — era tier 가 f 를 동질화하지 못한 자리(F1)."""
    return [
        f"{cell.cell_key} top_f={cell.dominant_floor_rate} "
        f"share={cell.dominant_floor_share:.3f}"
        for cell in cells
        if cell.dominant_floor_share is not None and cell.dominant_floor_share < 1.0
    ]


def _deep_cell_line(cell: CellReport) -> str:
    """깊은 셀 한 줄 — 깊이 옆에 **판정의 한계**를 나란히 둔다.

    ``ambiguous`` 점유는 그 셀이 포함 가정 위에 있는지(M1), ``modes``·``plateau`` 는
    그 ``b*`` 가 인용해도 되는 좌표인지(§11-7)를 가른다. 셋을 JSON 안에만 두면 콘솔만
    읽는 쪽이 n 하나로 판정을 만든다.
    """
    layer_a = cell.layer_a
    shape = (
        ""
        if layer_a is None
        else (
            f" modes={layer_a.mode_count}"
            f"{'(multimodal)' if layer_a.is_multimodal else ''}"
            f" plateau={layer_a.plateau_width:.1e}"
        )
    )
    return (
        f"{cell.cell_key} n={cell.row_count} "
        f"ambiguous={cell.ambiguous_share:.3f}{shape}"
    )


def _calibration_windows(cells: list[CellReport]) -> list[str]:
    """as-of 대조의 창 폭 — 격차를 "가정"으로 읽기 전에 며칠을 봤는지 보게 한다."""
    return [
        f"{cell.cell_key} fit={cell.calibration_as_of.fit_distinct_open_days}d"
        f"({cell.calibration_as_of.fit_row_count}행) "
        f"score={cell.calibration_as_of.score_distinct_open_days}d"
        f"({cell.calibration_as_of.score_row_count}행)"
        for cell in cells
        if cell.calibration_as_of is not None
    ]


def _negative_margin_line(report: AwardLandingReport) -> str:
    """``Δ<0`` 한 줄 — 건수 옆에 ``f==1.0`` 점유를 붙인다.

    설계 m1 은 "Δ<0 은 전부 f==1.0"이라고 적었는데 실측은 부분 성립이다. 건수만
    올리면 그 부분 비재현이 JSON 안에서만 살고 콘솔을 읽는 쪽에는 안 보인다.
    """
    negative = report.corpus.negative_margins
    unity = (
        "n/a"
        if negative.unity_floor_share is None
        else f"{negative.unity_floor_count}/{negative.count}"
        f"={negative.unity_floor_share:.3f}"
    )
    return (
        f"count={negative.count} f==1.0 {unity} "
        f"buckets={negative.magnitude_buckets}"
    )


def _alpha_rollup_line(rollup: ConstrainedRollup) -> str:
    """α 집계 한 줄 — 스코프·셀 수·binding·거리·이득을 같은 줄에.

    거리(bp)와 이득을 떼어 놓으면 "제약 위 12bp"만 인용되고 그 이동이 승률을 올렸는지는
    안 따라간다. 한 줄에 붙여 둘을 함께 읽게 한다.
    """
    distance = rollup.median_distance_above_feasible_lower
    gain = rollup.median_alpha_gain_over_feasible_lower
    return (
        f"{rollup.scope} cells={rollup.cell_count} points={rollup.alpha_point_count} "
        f"binding={_format_optional(rollup.binding_share)} "
        f"med_dist={'n/a' if distance is None else f'{distance * 1e4:.2f}bp'} "
        f"med_gain={_format_optional(gain)} status={rollup.status_histogram}"
    )


def _format_optional(value: float | None) -> str:
    """``None`` 은 0 이 아니라 "못 쟀다" 로 찍는다."""
    return "n/a" if value is None else f"{value:.5f}"


def _cell_lines(report: AwardLandingReport) -> dict[str, list[str]]:
    """셀 축 목록 넷 — 깊은 셀 · 얕은 셀 · as-of 불가 · 격자 넓힘."""
    deep = [cell for cell in report.cells if cell.meets_depth_gate]
    return {
        "deep_cells": [_deep_cell_line(cell) for cell in deep],
        "calibration_windows": _calibration_windows(deep),
        "as_of_unmeasurable_cells": [
            f"{cell.cell_key}: {cell.calibration_as_of_unmeasurable_reason}"
            for cell in report.cells
            if cell.calibration_as_of_unmeasurable_reason is not None
        ],
        "shallow_cells": [
            f"{cell.cell_key} n={cell.row_count}"
            for cell in report.cells
            if not cell.meets_depth_gate
        ],
        "widened_grid_cells": [
            f"{cell.cell_key} step={cell.layer_a.grid_step_effective:.2e}"
            for cell in report.cells
            if cell.layer_a is not None and cell.layer_a.grid_step_widened
        ],
    }


def _console_summary(report: AwardLandingReport, output_path: Path) -> ConsoleSummary:
    gaps = [
        cell.calibration_in_sample.max_abs_difference
        for cell in report.cells
        if cell.meets_depth_gate and cell.calibration_in_sample is not None
    ]
    return ConsoleSummary(
        out=str(output_path),
        regime_gate=report.parameters.regime_gate,
        prior_strength=report.parameters.prior_strength,
        candidate_count=report.corpus.candidate_count,
        accepted_count=report.corpus.accepted_count,
        ladder_drops={
            stage: count
            for stage, count in report.corpus.ladder_counts.items()
            if count
        },
        regime_histogram=report.corpus.regime_histogram,
        negative_margins=_negative_margin_line(report),
        measurable_cell_count=report.measurable_cell_count,
        cell_count=len(report.cells),
        max_calibration_gap=max(gaps) if gaps else None,
        omega_boundary_flags=_omega_boundary_flags(report.cells),
        heterogeneous_floor_cells=_heterogeneous_floor_cells(report.cells),
        alpha_deep_cells=_alpha_rollup_line(report.constrained_deep_cells),
        alpha_all_cells=_alpha_rollup_line(report.constrained_all_cells),
        quoted_deltas=[
            f"{row.name} quoted={row.quoted:+.4f} recomputed="
            + ("None" if row.recomputed is None else f"{row.recomputed:+.4f}")
            for row in report.quoted_comparisons
        ],
        **_cell_lines(report),  # type: ignore[arg-type]
    )


def main() -> int:
    args = build_parser().parse_args()
    output_path = resolve_report_path(args.out)
    corpus = load_corpus(
        as_of=parse_datetime(args.as_of, end_of_day=True),
        regime_gate=args.regime_gate,
    )
    report = build_award_landing_report(
        corpus,
        prior_strength=args.prior_strength,
        stored_floor_rate=_stored_floor_rates(corpus),
        grid_step=args.grid_step,
        fit_share=args.fit_share,
        min_cell_rows=args.min_cell_rows,
    )
    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(_console_summary(report, output_path).model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
