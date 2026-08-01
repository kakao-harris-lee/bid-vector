#!/usr/bin/env python3
"""설계 래칫의 데이터 계약 + 순수 비교 — AST 도 파일 I/O 도 없다.

``scripts/_design_ratchet_scan.py`` (AST 스캔) 과 ``scripts/design_ratchet.py``
(CLI · baseline 영속화 · 리포트 출력) 가 함께 쓰는 지표 모델이다. 스캐너 자신도 파일
크기 한도(CLAUDE.md §4.5-4)를 지켜야 하므로 "무엇을 세는가"(스캔)와 "센 것을 어떤
모양으로 담고 비교하는가"(여기)를 책임 단위로 나눴다.

여기에는 baseline 이라는 개념도, 소스 텍스트도 없다. 비교·집계는 모두 순수 함수라 값
테이블로 테스트한다(§4.7-4).
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import Field  # noqa: E402

from app.schemas._base import StrictModel  # noqa: E402

# --- 선언적 구성 (§4.5-1) ---------------------------------------------------------
FILE_LOC_SOFT_LIMIT = 500
# 500줄 초과 파일은 LOC 자체가 아니라 25줄 밴드로 센다. 한 줄 추가하는 버그픽스가
# baseline 상승을 요구하지 않게 하되(잡음 제거), 파일이 계속 자라면 밴드가 올라간다.
FILE_LOC_BAND_LINES = 25
FILE_LOC_METRIC = "file_loc_band"
# 밴드 숫자만 보면 무슨 단위인지 알 수 없으므로 위반 메시지에 환산을 붙인다.
FILE_LOC_BAND_HINT = (
    f"(밴드≈{FILE_LOC_BAND_LINES}줄, {FILE_LOC_SOFT_LIMIT}줄 초과 진입 시"
    f" {FILE_LOC_SOFT_LIMIT // FILE_LOC_BAND_LINES + 1}부터)"
)
METRIC_DESCRIBE_SUFFIXES = {FILE_LOC_METRIC: FILE_LOC_BAND_HINT}


# --- 데이터 계약 (§4.7: 원시 dict 대신 검증되는 모델) -----------------------------
class FileMetrics(StrictModel):
    """한 파일의 규율 위반 카운트. 0 은 "해당 위반 없음"을 뜻한다."""

    functions_over_soft_limit: int = 0
    functions_over_hard_limit: int = 0
    file_loc_band: int = 0
    json_direct_calls: int = 0
    dict_boundary_functions: int = 0
    env_test_sniff: int = 0
    unvalidated_dict_tasks: int = 0
    duplicate_mechanical_helpers: int = 0
    duplicate_mechanical_helpers_local: int = 0

    def count(self, metric: str) -> int:
        return int(getattr(self, metric))

    def is_clean(self) -> bool:
        return all(self.count(metric) == 0 for metric in METRIC_NAMES)


METRIC_NAMES: tuple[str, ...] = tuple(FileMetrics.model_fields)


class RatchetReport(StrictModel):
    """파일 경로(posix 상대경로) → 위반 카운트."""

    files: dict[str, FileMetrics] = Field(default_factory=dict)

    def metrics_for(self, path: str) -> FileMetrics:
        return self.files.get(path) or FileMetrics()

    def totals(self) -> FileMetrics:
        summed = {
            metric: sum(metrics.count(metric) for metrics in self.files.values())
            for metric in METRIC_NAMES
        }
        return FileMetrics(**summed)


class RatchetViolation(StrictModel):
    """baseline 대비 악화된 (파일, 지표) 한 건."""

    metric: str
    file: str
    baseline: int
    current: int

    def describe(self) -> str:
        line = f"  {self.metric}: {self.file} {self.baseline} -> {self.current}"
        suffix = METRIC_DESCRIBE_SUFFIXES.get(self.metric, "")
        return f"{line} {suffix}" if suffix else line


# --- 순수 비교 (I/O 없음) ---------------------------------------------------------
def compare_reports(
    baseline: RatchetReport, current: RatchetReport
) -> list[RatchetViolation]:
    """current 가 baseline 을 초과한 (파일, 지표) 목록. 감소·삭제는 통과."""
    violations: list[RatchetViolation] = []
    for path in sorted(current.files):
        before = baseline.metrics_for(path)
        after = current.metrics_for(path)
        violations.extend(_violations_for_file(path, before, after))
    return violations


def _violations_for_file(
    path: str, baseline: FileMetrics, current: FileMetrics
) -> list[RatchetViolation]:
    return [
        RatchetViolation(
            metric=metric,
            file=path,
            baseline=baseline.count(metric),
            current=current.count(metric),
        )
        for metric in METRIC_NAMES
        if current.count(metric) > baseline.count(metric)
    ]


def count_improvements(baseline: RatchetReport, current: RatchetReport) -> FileMetrics:
    """지표별 감소 총량(양수로). 파일 삭제로 사라진 카운트도 포함하는 순수 함수."""
    reduced = dict.fromkeys(METRIC_NAMES, 0)
    for path in set(baseline.files) | set(current.files):
        before = baseline.metrics_for(path)
        after = current.metrics_for(path)
        for metric in METRIC_NAMES:
            delta = before.count(metric) - after.count(metric)
            if delta > 0:
                reduced[metric] += delta
    return FileMetrics(**reduced)


def without_paths(report: RatchetReport, paths: Sequence[str]) -> RatchetReport:
    """지정 경로를 뺀 리포트(삭제된 파일의 allowance 를 집계에서 제외할 때 쓴다)."""
    excluded = set(paths)
    return RatchetReport(
        files={
            path: metrics
            for path, metrics in report.files.items()
            if path not in excluded
        }
    )
