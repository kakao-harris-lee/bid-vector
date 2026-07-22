#!/usr/bin/env python3
"""참가자격 식별 라벨(rule source) 멱등 생성 스크립트 (DB read + 라벨 write만).

``Project.eligibility_raw`` 가 있는 공고를 순회하며
``app.services.eligibility_labeling`` 의 순수 해석기로 라벨을 계산하고
``NoticeEligibilityLabel`` 에 소스별 1라벨(``source="rule"``)로 upsert 한다.
같은 ``(project_id, source)`` 는 갱신되므로 재실행해도 중복이 생기지 않고
``labeler_version`` 이 최신 규칙 버전으로 기록된다.

외부 API 호출은 없다(KONEPS/Telegram 등). 라벨은 이 단계에선 데이터로만 존재하며
classifier/수집/추천 동작에 영향이 없다. 시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/generate_eligibility_labels.py --dry-run
    docker exec bid_vector_api python scripts/generate_eligibility_labels.py --limit 2000
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.time import kst_now  # noqa: E402
from app.models.models import Project  # noqa: E402
from app.services.eligibility_feedback import upsert_eligibility_label  # noqa: E402
from app.services.eligibility_labeling import (  # noqa: E402
    LABELER_VERSION,
    SOURCE_RULE,
    classify_eligibility,
    extract_eligibility_labels,
)

# 매직값은 여기 선언(§4.5.1). 라벨/소스/버전 값은 eligibility_labeling 단일 출처.
DEFAULT_LIMIT: int | None = None

_LOG_PREFIX = "[generate-eligibility-labels]"

_OUTCOME_INSERTED = "inserted"
_OUTCOME_UPDATED = "updated"


@dataclass
class GenerateStats:
    """생성 실행 요약 — 후보 수 · insert/update · 라벨 분포."""

    candidates: int = 0
    by_outcome: Counter = field(default_factory=Counter)
    by_label: Counter = field(default_factory=Counter)


# --- 라벨 생성 (upsert 핵심은 eligibility_feedback 서비스와 공유) --------------


def generate_labels(
    db: Session, *, limit: int | None, stats: GenerateStats
) -> None:
    """eligibility_raw 보유 공고를 순회해 rule 라벨을 upsert 한다(flush 만).

    upsert 는 엔드포인트(operator 피드백)와 공유하는 ``upsert_eligibility_label``
    을 쓴다(복붙 금지). ``created`` 플래그를 inserted/updated 통계로 매핑한다.
    """
    query = (
        db.query(Project.id, Project.title, Project.eligibility_raw)
        .filter(Project.eligibility_raw.isnot(None))
        .order_by(Project.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    for project_id, title, eligibility_raw in query.all():
        stats.candidates += 1
        labels = extract_eligibility_labels(eligibility_raw, title=title)
        verdict = classify_eligibility(labels)
        _record, created = upsert_eligibility_label(
            db,
            project_id=project_id,
            label=verdict.label,
            source=SOURCE_RULE,
            rationale=verdict.rationale,
            labeler_version=LABELER_VERSION,
        )
        stats.by_outcome[_OUTCOME_INSERTED if created else _OUTCOME_UPDATED] += 1
        stats.by_label[verdict.label] += 1
    db.flush()


def run_generation(
    db: Session, *, limit: int | None = None, dry_run: bool = False
) -> GenerateStats:
    """generate_labels 실행 후 dry-run 이면 rollback, 아니면 commit(주입된 세션)."""
    stats = GenerateStats()
    generate_labels(db, limit=limit, stats=stats)
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats


# --- 렌더/CLI ----------------------------------------------------------------


def render_stats(stats: GenerateStats, *, dry_run: bool) -> str:
    """실행 요약을 사람이 읽는 텍스트로 렌더한다(시각 KST)."""
    mode = "DRY-RUN (no write)" if dry_run else "WRITE"
    stamp = kst_now().strftime("%Y-%m-%d %H:%M:%S KST")
    outcome = stats.by_outcome
    label = stats.by_label
    return "\n".join(
        [
            f"{_LOG_PREFIX} {stamp} {mode} source={SOURCE_RULE} "
            f"version={LABELER_VERSION}",
            (
                f"{_LOG_PREFIX} candidates={stats.candidates} "
                f"inserted={outcome[_OUTCOME_INSERTED]} "
                f"updated={outcome[_OUTCOME_UPDATED]}"
            ),
            (
                f"{_LOG_PREFIX} labels positive={label['positive']} "
                f"negative={label['negative']} ambiguous={label['ambiguous']}"
            ),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Label at most N eligibility rows (default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute + report without writing (rolls back).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        stats = run_generation(db, limit=args.limit, dry_run=args.dry_run)
    finally:
        db.close()

    print(render_stats(stats, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
