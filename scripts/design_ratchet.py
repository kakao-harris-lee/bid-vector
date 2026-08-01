#!/usr/bin/env python3
"""설계 래칫 CLI — 규율 위반 카운트가 늘어나는 것만 막는다(감소는 항상 통과).

방어적 DTO 규율 Phase 0. 저장소에는 이미 긴 함수·``json`` 직접 호출·``dict`` 경계가
많아서 한 번에 고칠 수 없다. 그래서 현재값을 ``tests/design_ratchet_baseline.json`` 에
**baseline 으로 고정**하고, pytest 에서 현재 스캔이 baseline 을 초과하면 실패시킨다.

지표는 **파일별**로 센다(전역 합계가 아니다). 합계로 재면 한 파일의 개선이 다른 파일의
악화를 가려서 래칫이 새기 때문이다.

측정(AST 스캔·데이터 계약·순수 비교)은 ``scripts/_design_ratchet_scan.py`` 에 있고, 이
모듈은 baseline 영속화·리포트 출력·CLI 만 담당한다.

사용::

    python scripts/design_ratchet.py                    # 위반 리포트(위반 있으면 exit 1)
    python scripts/design_ratchet.py --update-baseline  # delta 출력 후 baseline 재생성

이 스캐너 자신도 스캔 대상이며 allowlist 없이 위반 0 이어야 한다(``json`` 직접 호출 대신
Pydantic ``model_validate_json``/``model_dump_json`` 만 쓴다).
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from scripts._design_ratchet_contracts import (  # noqa: E402
    FILE_LOC_BAND_LINES,
    FILE_LOC_METRIC,
    FILE_LOC_SOFT_LIMIT,
    METRIC_NAMES,
    FileMetrics,
    RatchetReport,
    RatchetViolation,
    compare_reports,
    count_improvements,
    without_paths,
)
from scripts._design_ratchet_scan import scan_repo  # noqa: E402

# --- 선언적 구성 (§4.5-1) ---------------------------------------------------------
BASELINE_PATH = REPO_ROOT / "tests" / "design_ratchet_baseline.json"
DELTA_PREVIEW_LIMIT = 20

CLI_DESCRIPTION = "설계 래칫 스캐너 — 규율 위반 카운트의 증가만 막는다(감소는 통과)."
BASELINE_UPDATE_COMMAND = "python scripts/design_ratchet.py --update-baseline"
MISSING_BASELINE_MESSAGE = "\n".join(
    (
        f"baseline 이 없습니다: {BASELINE_PATH}",
        f"`{BASELINE_UPDATE_COMMAND}` 으로 먼저 생성하세요.",
    )
)
UPDATE_BASELINE_HINT = f"정당한 경우 `{BASELINE_UPDATE_COMMAND}` 후 사유를 PR 본문에 기재하세요."
# 삭제된 경로가 baseline 에 남아 있으면 **같은 이름의 파일이 다시 생겼을 때 예전
# allowance 를 상속**한다. 위반은 아니지만(삭제는 항상 통과) 조용히 두면 래칫이 샌다.
STALE_BASELINE_HEADER = "\n".join(
    (
        "경고: baseline 에만 있고 디스크에 없는 파일이 있습니다(위반 아님).",
        "  남겨 두면 같은 경로가 다시 생길 때 예전 allowance 를 상속합니다"
        f" — `{BASELINE_UPDATE_COMMAND}` 으로 정리하세요.",
    )
)
# 감소는 항상 통과라 잠그지 않아도 실패하지 않는다. 그래서 조용히 두면 회수한 부채가
# 영영 baseline 에 slack 으로 남아 나중의 재악화를 무료로 허용한다.
BASELINE_SLACK_HEADER = "\n".join(
    (
        "안내: baseline 이 현재 스캔보다 느슨합니다(위반 아님).",
        f"  회수한 감소분을 잠그려면 `{BASELINE_UPDATE_COMMAND}` 을 실행하세요.",
    )
)
# 지표를 추가/개명하면 구 baseline 은 스키마가 맞지 않는다. 검사 경로는 그대로 크게
# 실패하고, 명시적 재생성 경로에서만 delta 를 생략한다.
INCOMPATIBLE_BASELINE_NOTE = (
    "구 baseline 을 현재 지표 스키마로 해석할 수 없어 delta 를 생략합니다(지표 추가·개명 시 정상)."
)
# 파일 손상·머지 컨플릭트 잔여물은 "지표 드리프트"가 아니다. 이 부류는 재생성 경로에서도
# 삼키지 않고 그대로 올려서 운영자가 파일을 먼저 확인하게 한다.
CORRUPT_BASELINE_ERROR_TYPES = frozenset(
    {"json_invalid", "json_type", "model_type", "model_attributes_type"}
)


# --- baseline I/O ----------------------------------------------------------------
def load_baseline(path: Path) -> RatchetReport:
    return RatchetReport.model_validate_json(path.read_text(encoding="utf-8"))


def save_baseline(report: RatchetReport, path: Path) -> None:
    payload = report.model_dump_json(indent=2, exclude_defaults=True)
    path.write_text(f"{payload}\n", encoding="utf-8")


def stale_baseline_files(baseline: RatchetReport, root: Path) -> list[str]:
    """baseline 에 있으나 디스크에 없는 경로(정렬)."""
    return sorted(path for path in baseline.files if not (root / path).exists())


# --- 리포트 출력 -----------------------------------------------------------------
def format_violations(violations: Sequence[RatchetViolation]) -> str:
    header = f"설계 래칫 위반 {len(violations)}건 (baseline -> current):"
    return "\n".join([header, *(violation.describe() for violation in violations)])


def _format_plain_total(metric: str, report: RatchetReport) -> str:
    return f"  {metric}: {report.totals().count(metric)}"


def _format_file_loc_total(metric: str, report: RatchetReport) -> str:
    """밴드 합계는 의미가 없으므로 파일 수 + 최대 규모로 표기한다."""
    bands = [
        metrics.count(metric)
        for metrics in report.files.values()
        if metrics.count(metric)
    ]
    if not bands:
        return f"  {metric}: {FILE_LOC_SOFT_LIMIT}줄 초과 파일 없음"
    largest = max(bands) * FILE_LOC_BAND_LINES
    scope = f"{FILE_LOC_SOFT_LIMIT}줄 초과 파일 {len(bands)}개(최대 ~{largest}줄)"
    return f"  {metric}: {scope}"


METRIC_TOTAL_FORMATTERS = {FILE_LOC_METRIC: _format_file_loc_total}


def format_totals(report: RatchetReport) -> str:
    lines = [f"스캔 대상 파일(위반 보유) {len(report.files)}개 · 지표 총계:"]
    for metric in METRIC_NAMES:
        formatter = METRIC_TOTAL_FORMATTERS.get(metric, _format_plain_total)
        lines.append(formatter(metric, report))
    return "\n".join(lines)


def _format_preview(label: str, entries: Sequence[str]) -> str:
    if not entries:
        return f"  {label} 0개"
    shown = list(entries[:DELTA_PREVIEW_LIMIT])
    hidden = len(entries) - len(shown)
    suffix = f" 외 {hidden}개" if hidden else ""
    return f"  {label} {len(entries)}개: {', '.join(shown)}{suffix}"


def _format_reductions(reduced: FileMetrics) -> str:
    parts = [
        f"{metric} -{reduced.count(metric)}"
        for metric in METRIC_NAMES
        if reduced.count(metric)
    ]
    return ", ".join(parts) if parts else "없음"


def format_stale_baseline_warning(stale: Sequence[str]) -> str:
    return "\n".join([STALE_BASELINE_HEADER, _format_preview("정리 대상", stale)])


def format_baseline_slack(reduced: FileMetrics) -> str:
    detail = f"  미회수 감소: {_format_reductions(reduced)}"
    return "\n".join([BASELINE_SLACK_HEADER, detail])


def baseline_drift_notes(
    baseline: RatchetReport, current: RatchetReport, root: Path
) -> list[str]:
    """실패시키지 않는 안내: 사라진 경로 + **현존 파일의** 미회수 감소분.

    둘 다 래칫 계약상 통과(삭제·감소는 항상 허용)지만 조용히 두면 래칫이 샌다. 사라진
    경로의 감소분은 stale 경고가 이미 지목하므로 slack 집계에서는 제외한다.
    """
    stale = stale_baseline_files(baseline, root)
    notes = [format_stale_baseline_warning(stale)] if stale else []
    slack = count_improvements(without_paths(baseline, stale), current)
    if not slack.is_clean():
        notes.append(format_baseline_slack(slack))
    return notes


def format_baseline_delta(baseline: RatchetReport, current: RatchetReport) -> str:
    """덮어쓰기 전에 무엇이 늘고 줄고 생기고 사라지는지 보여준다."""
    violations = compare_reports(baseline, current)
    lines = [
        f"baseline delta ({BASELINE_PATH.name}):",
        f"  증가(위반) {len(violations)}건",
    ]
    shown = violations[:DELTA_PREVIEW_LIMIT]
    lines.extend(violation.describe() for violation in shown)
    hidden = len(violations) - len(shown)
    if hidden:
        lines.append(f"    ... 외 {hidden}건")
    added = sorted(set(current.files) - set(baseline.files))
    removed = sorted(set(baseline.files) - set(current.files))
    lines.append(_format_preview("신규 등장 파일", added))
    lines.append(_format_preview("사라진 파일", removed))
    reduced = count_improvements(baseline, current)
    lines.append(f"  감소 총량: {_format_reductions(reduced)}")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=f"delta 를 출력한 뒤 {BASELINE_PATH.name} 을 현재 스캔 결과로 재생성한다.",
    )
    return parser


def _is_corrupt_baseline(exc: ValidationError) -> bool:
    """JSON 자체가 깨졌거나 루트 모양이 다른가(= 지표 드리프트가 아님)."""
    return any(error["type"] in CORRUPT_BASELINE_ERROR_TYPES for error in exc.errors())


def _summarize_first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "unknown"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc") or ())
    return f"{first.get('type')} at {location or '<root>'}"


def _previous_baseline() -> RatchetReport | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        return load_baseline(BASELINE_PATH)
    except ValidationError as exc:
        if _is_corrupt_baseline(exc):
            raise
        print(f"{INCOMPATIBLE_BASELINE_NOTE} [{_summarize_first_error(exc)}]")
        return None


def run_baseline_update(report: RatchetReport) -> int:
    """덮어쓰기 전에 delta 를 출력한 뒤 baseline 을 저장한다."""
    previous = _previous_baseline()
    if previous is not None:
        print(format_baseline_delta(previous, report))
    save_baseline(report, BASELINE_PATH)
    print(f"baseline 재생성: {BASELINE_PATH}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = scan_repo(REPO_ROOT)
    print(format_totals(report))
    if args.update_baseline:
        return run_baseline_update(report)
    if not BASELINE_PATH.exists():
        print(MISSING_BASELINE_MESSAGE)
        return 1
    baseline = load_baseline(BASELINE_PATH)
    for note in baseline_drift_notes(baseline, report, REPO_ROOT):
        print(note)
    violations = compare_reports(baseline, report)
    if not violations:
        print("위반 없음 (baseline 이하)")
        return 0
    print(format_violations(violations))
    print(UPDATE_BASELINE_HINT)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
