#!/usr/bin/env python3
"""Backfill Project.business_type_code/label for existing rows.

Usage:
    python scripts/backfill_business_type.py --dry-run
    python scripts/backfill_business_type.py --limit 500
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import Project


@dataclass
class BackfillStats:
    candidates: int = 0
    updated_from_detail: int = 0
    updated_from_title_rule: int = 0
    failed: int = 0
    skipped_already_set: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


DetailFetcher = Callable[[str], dict[str, str | None]]


def backfill_from_detail_html(
    db: Session,
    *,
    fetcher: DetailFetcher,
    limit: int,
    stats: BackfillStats,
) -> None:
    """Update rows whose source_url is populated by re-fetching the detail page."""
    query = (
        db.query(Project)
        .filter(Project.business_type_code.is_(None))
        .filter(Project.source_url.isnot(None))
        .order_by(Project.id.asc())
        .limit(limit)
    )
    for project in query.all():
        stats.candidates += 1
        try:
            payload = fetcher(project.source_url)
        except Exception as exc:  # noqa: BLE001  -- best-effort backfill
            stats.failed += 1
            stats.failures.append({"id": str(project.id), "reason": str(exc)})
            continue
        code = (payload.get("business_type_code") or "").strip() or None
        label = (payload.get("business_type_label") or "").strip() or None
        if not code and not label:
            stats.failed += 1
            stats.failures.append({"id": str(project.id), "reason": "empty payload"})
            continue
        if code:
            project.business_type_code = code
        if label:
            project.business_type_label = label
        stats.updated_from_detail += 1
    db.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Project.business_type_*")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("reports/business-type-backfill/run.json"),
    )
    args = parser.parse_args()

    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()

    def fetcher(url: str) -> dict[str, str | None]:
        return service.fetch_detail_html_payload(url)

    stats = BackfillStats()
    db = SessionLocal()
    try:
        backfill_from_detail_html(db, fetcher=fetcher, limit=args.limit, stats=stats)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()

    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
