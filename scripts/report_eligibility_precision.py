#!/usr/bin/env python3
"""참가자격 식별 라벨 분포 + rule↔operator precision/recall 리포트 (읽기 전용).

``NoticeEligibilityLabel`` 을 읽어 rule 라벨 분포(positive/negative/ambiguous),
positive 목록 요약(공고번호+근거), 카테고리별 분해를 stdout 으로 낸다.

precision/recall 은 **operator 소스 정답 라벨이 있는 공고 교집합에서만** rule 을
예측, operator 를 정답으로 두고 계산한다(positive 클래스 기준). 현재 operator
라벨이 0이면 교집합이 비어 "operator 라벨 대기"로 출력하며 지표를 지어내지 않는다
(§2 정직). operator 정답은 G-3 실사용 중 축적된다.

DB read-only. 외부 API 호출 없음. 시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/report_eligibility_precision.py
    docker exec bid_vector_api python scripts/report_eligibility_precision.py --samples 30
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.core.time import kst_now  # noqa: E402
from app.models.models import NoticeEligibilityLabel, Project  # noqa: E402
from app.services.eligibility_labeling import (  # noqa: E402
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    SOURCE_OPERATOR,
    SOURCE_RULE,
)

# 매직값은 여기 선언(§4.5.1).
DEFAULT_POSITIVE_SAMPLES = 20
_NO_CATEGORY = "(none)"
_NO_NUMBER = "(no-number)"
_LOG_PREFIX = "[eligibility-precision]"


@dataclass(frozen=True)
class LabelRow:
    """리포트가 쓰는 라벨 최소 투영(DB 접근과 집계 로직 분리용)."""

    project_id: int
    notice_number: str | None
    category: str | None
    label: str
    source: str
    rationale: str | None


@dataclass(frozen=True)
class PrecisionRecall:
    """positive 클래스 기준 rule↔operator 대조 지표(교집합에서만)."""

    tp: int
    fp: int
    fn: int
    support: int  # rule·operator 라벨을 모두 가진 공고 수
    precision: float | None  # 교집합이 없거나 예측 positive 0이면 None
    recall: float | None


@dataclass
class ReportData:
    """집계 결과 — 분포·positive 샘플·카테고리 분해·대조 지표."""

    rule_total: int = 0
    distribution: Counter = field(default_factory=Counter)
    positive_samples: list[tuple[str, str]] = field(default_factory=list)
    category_breakdown: dict[str, Counter] = field(default_factory=dict)
    precision_recall: PrecisionRecall = field(
        default_factory=lambda: PrecisionRecall(0, 0, 0, 0, None, None)
    )


# --- 집계 (순수 — DB 접근 없음) ----------------------------------------------


def compute_precision_recall(
    pairs: Iterable[tuple[str, str]],
) -> PrecisionRecall:
    """(rule_label, operator_label) 쌍에서 positive 클래스 지표를 계산한다(순수).

    operator 를 정답, rule 을 예측으로 본다. 예측 positive 가 하나도 없으면
    precision 은 정의 불가(None), 정답 positive 가 없으면 recall 은 None.
    """
    tp = fp = fn = support = 0
    for rule_label, operator_label in pairs:
        support += 1
        rule_pos = rule_label == LABEL_POSITIVE
        op_pos = operator_label == LABEL_POSITIVE
        if rule_pos and op_pos:
            tp += 1
        elif rule_pos and not op_pos:
            fp += 1
        elif not rule_pos and op_pos:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return PrecisionRecall(tp, fp, fn, support, precision, recall)


def aggregate_report(
    rows: Iterable[LabelRow], *, positive_samples: int = DEFAULT_POSITIVE_SAMPLES
) -> ReportData:
    """라벨 행에서 분포·positive 샘플·카테고리 분해·대조 지표를 집계한다(순수).

    rule 라벨은 분포/카테고리/샘플에 반영하고, rule·operator 를 project_id 로
    짝지어 교집합에서만 precision/recall 을 낸다.
    """
    data = ReportData()
    category_breakdown: dict[str, Counter] = defaultdict(Counter)
    rule_by_project: dict[int, str] = {}
    operator_by_project: dict[int, str] = {}

    for row in rows:
        if row.source == SOURCE_RULE:
            data.distribution[row.label] += 1
            category_breakdown[row.category or _NO_CATEGORY][row.label] += 1
            rule_by_project[row.project_id] = row.label
            if (
                row.label == LABEL_POSITIVE
                and len(data.positive_samples) < positive_samples
            ):
                data.positive_samples.append(
                    (row.notice_number or _NO_NUMBER, row.rationale or "")
                )
        elif row.source == SOURCE_OPERATOR:
            operator_by_project[row.project_id] = row.label

    data.rule_total = sum(data.distribution.values())
    data.category_breakdown = dict(category_breakdown)
    pairs = [
        (rule_label, operator_by_project[project_id])
        for project_id, rule_label in rule_by_project.items()
        if project_id in operator_by_project
    ]
    data.precision_recall = compute_precision_recall(pairs)
    return data


def render_report(data: ReportData) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST)."""
    stamp = kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    dist = data.distribution
    lines = [
        f"{_LOG_PREFIX} {stamp} READ-ONLY",
        (
            f"{_LOG_PREFIX} rule_labels={data.rule_total} "
            f"positive={dist[LABEL_POSITIVE]} negative={dist[LABEL_NEGATIVE]} "
            f"ambiguous={dist[LABEL_AMBIGUOUS]}"
        ),
        f"{_LOG_PREFIX} positive 목록(공고번호 · 근거):",
        *_positive_lines(data.positive_samples),
        f"{_LOG_PREFIX} 카테고리별 라벨 분포:",
        *_category_lines(data.category_breakdown),
        f"{_LOG_PREFIX} rule↔operator precision/recall (positive 클래스):",
        *_precision_lines(data.precision_recall),
    ]
    return "\n".join(lines)


def _positive_lines(samples: list[tuple[str, str]]) -> list[str]:
    if not samples:
        return ["    (none)"]
    return [f"    {number}: {rationale}" for number, rationale in samples]


def _category_lines(breakdown: dict[str, Counter]) -> list[str]:
    if not breakdown:
        return ["    (none)"]
    lines: list[str] = []
    for category in sorted(breakdown):
        counts = breakdown[category]
        lines.append(
            f"    {category}: positive={counts[LABEL_POSITIVE]} "
            f"negative={counts[LABEL_NEGATIVE]} ambiguous={counts[LABEL_AMBIGUOUS]}"
        )
    return lines


def _precision_lines(pr: PrecisionRecall) -> list[str]:
    if pr.support == 0:
        return ["    operator 라벨 대기 (교집합 0) — G-3 실사용 중 축적"]
    precision = "n/a" if pr.precision is None else f"{pr.precision:.3f}"
    recall = "n/a" if pr.recall is None else f"{pr.recall:.3f}"
    return [
        f"    support={pr.support} tp={pr.tp} fp={pr.fp} fn={pr.fn}",
        f"    precision={precision} recall={recall}",
    ]


# --- DB read (얇은 경계) ------------------------------------------------------


def load_label_rows(db) -> list[LabelRow]:
    """라벨을 공고(notice_number/category)와 조인해 투영한다(read-only)."""
    query = (
        db.query(
            NoticeEligibilityLabel.project_id,
            Project.notice_number,
            Project.category,
            NoticeEligibilityLabel.label,
            NoticeEligibilityLabel.source,
            NoticeEligibilityLabel.rationale,
        )
        .join(Project, Project.id == NoticeEligibilityLabel.project_id)
        .order_by(NoticeEligibilityLabel.project_id.asc())
    )
    return [
        LabelRow(
            project_id=row[0],
            notice_number=row[1],
            category=row[2],
            label=row[3],
            source=row[4],
            rationale=row[5],
        )
        for row in query.all()
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_POSITIVE_SAMPLES,
        help="How many positive notices to list.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        rows = load_label_rows(db)
    finally:
        db.close()

    data = aggregate_report(rows, positive_samples=max(0, args.samples))
    print(render_report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
