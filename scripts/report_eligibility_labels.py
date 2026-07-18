#!/usr/bin/env python3
"""참가자격 라벨 분포·미매칭 원문 검증 리포트 (읽기 전용, 외부 호출 0).

``Project.eligibility_raw`` 가 있는 공고를 순회하며
``app.services.eligibility_labeling.extract_eligibility_labels`` 로 자격 라벨을
계산하고, 분포(협회/엔지니어링 요구 건수 · 기술부문 상위 N · 미매칭 건수)와
**규칙이 잡지 못한 ``lcnsLmtNm``/``indstrytyNm`` 원문 상위 샘플**(규칙 캘리브레이션
대상)을 stdout 으로 요약한다.

DB read-only 다. KONEPS 등 외부 API 는 호출하지 않는다. 라벨은 여기서 저장하지
않고 on-demand 계산만 한다(저장/추천 반영은 후속 PR의 의도적 결정). 시각은 KST.
현재 DB 에 eligibility_raw 실데이터는 0건이며(내일부터 1,500건/일 적재), 이
스크립트가 그때부터의 캘리브레이션 루프를 담당한다.

사용 예:
    docker exec bid_vector_api python scripts/report_eligibility_labels.py
    docker exec bid_vector_api python scripts/report_eligibility_labels.py \
        --limit 2000 --samples 20
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.core.time import kst_now  # noqa: E402
from app.models.models import Project  # noqa: E402
from app.services.eligibility_labeling import (  # noqa: E402
    LABEL_SOURCE_FIELDS,
    extract_eligibility_labels,
)

# 리포트 기본값 (매직값은 여기 선언, 함수 안 리터럴 금지 — §4.5.1).
DEFAULT_LIMIT: int | None = None
DEFAULT_SAMPLES = 15
DEFAULT_TOP_TECH_FIELDS = 15

_LOG_PREFIX = "[eligibility-labels]"


@dataclass(frozen=True)
class ReportRow:
    """리포트가 필요로 하는 공고 최소 투영 (DB 접근과 집계 로직 분리용)."""

    notice_number: str
    title: str | None
    eligibility_raw: dict | None


@dataclass
class ReportSummary:
    """라벨 분포 + 미매칭 원문 빈도 집계 결과."""

    total: int = 0
    association_required: int = 0
    engineering_business_required: int = 0
    tech_field_counts: Counter = field(default_factory=Counter)
    unmatched_total: int = 0
    unmatched_license: Counter = field(default_factory=Counter)
    unmatched_industry: Counter = field(default_factory=Counter)


# --- 집계 (순수 — DB 접근 없음) ----------------------------------------------


def aggregate_labels(rows: Iterable[ReportRow]) -> ReportSummary:
    """공고 행들을 순회하며 라벨 분포와 미매칭 원문 빈도를 집계한다(순수 함수).

    미매칭 = 참가자격 데이터는 있으나 규칙이 협회/엔지니어링/기술부문 라벨을
    하나도 못 붙인 공고(eligibility 소스 매칭 0건). 이 경우의 ``lcnsLmtNm``/
    ``indstrytyNm`` 원문을 빈도 집계해 규칙 캘리브레이션 대상으로 노출한다.
    """
    summary = ReportSummary()
    for row in rows:
        labels = extract_eligibility_labels(row.eligibility_raw, title=row.title)
        if not labels.has_eligibility_data:
            continue
        summary.total += 1
        if labels.association_required:
            summary.association_required += 1
        if labels.engineering_business_required:
            summary.engineering_business_required += 1
        for name in labels.tech_fields:
            summary.tech_field_counts[name] += 1

        # eligibility 소스(면허/업종)에서 잡힌 매칭이 하나도 없으면 미매칭.
        # title 참고 매칭은 라벨 근거가 아니므로 미매칭 판정에서 제외한다.
        eligibility_hit = any(
            match.source_field in LABEL_SOURCE_FIELDS for match in labels.matches
        )
        if eligibility_hit:
            continue
        summary.unmatched_total += 1
        raw = row.eligibility_raw or {}
        license_text = str(raw.get("lcnsLmtNm") or "").strip()
        industry_text = str(raw.get("indstrytyNm") or "").strip()
        if license_text:
            summary.unmatched_license[license_text] += 1
        if industry_text:
            summary.unmatched_industry[industry_text] += 1
    return summary


def render_summary(
    summary: ReportSummary,
    *,
    samples: int,
    top_tech_fields: int,
) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST)."""
    lines = [
        f"{_LOG_PREFIX} {_kst_stamp()} READ-ONLY",
        (
            f"{_LOG_PREFIX} eligibility_rows={summary.total} "
            f"association_required={summary.association_required} "
            f"engineering_business_required={summary.engineering_business_required} "
            f"unmatched={summary.unmatched_total}"
        ),
        f"{_LOG_PREFIX} tech_fields(top):",
        *_count_lines(summary.tech_field_counts, top_tech_fields),
        f"{_LOG_PREFIX} unmatched lcnsLmtNm(top) — 규칙 캘리브레이션 대상:",
        *_count_lines(summary.unmatched_license, samples),
        f"{_LOG_PREFIX} unmatched indstrytyNm(top) — 규칙 캘리브레이션 대상:",
        *_count_lines(summary.unmatched_industry, samples),
    ]
    return "\n".join(lines)


def _count_lines(counter: Counter, top_n: int) -> list[str]:
    """Counter 상위 N 을 들여쓴 목록 줄로 만든다(비면 ``(none)``)."""
    if not counter or top_n <= 0:
        return ["    (none)"]
    return [f"    {value}: {count}" for value, count in counter.most_common(top_n)]


def _kst_stamp() -> str:
    """리포트 헤더용 현재 KST 타임스탬프."""
    return kst_now().strftime("%Y-%m-%d %H:%M:%S KST")


# --- DB read (얇은 경계) ------------------------------------------------------


def load_rows(db, *, limit: int | None = None) -> list[ReportRow]:
    """eligibility_raw 가 있는 공고를 최신 id 순으로 투영해 돌려준다(read-only)."""
    query = (
        db.query(Project.notice_number, Project.title, Project.eligibility_raw)
        .filter(Project.eligibility_raw.isnot(None))
        .order_by(Project.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return [
        ReportRow(notice_number=row[0], title=row[1], eligibility_raw=row[2])
        for row in query.all()
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Aggregate at most N eligibility rows (default: all).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="How many top unmatched raw strings to show per field.",
    )
    parser.add_argument(
        "--top-tech-fields",
        type=int,
        default=DEFAULT_TOP_TECH_FIELDS,
        help="How many top tech fields to show.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        rows = load_rows(db, limit=args.limit)
    finally:
        db.close()

    summary = aggregate_labels(rows)
    print(
        render_summary(
            summary,
            samples=max(0, args.samples),
            top_tech_fields=max(0, args.top_tech_fields),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
