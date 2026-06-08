#!/usr/bin/env python3
"""Backfill KONEPS scsbid (개찰결과) awards over a date window.

Drives ``KonepsCollectorService`` through the scsbid OpenAPI branch with the
multi-category, paginated date-window sweep, then persists the collected awards
through the existing crawl-persist path. Matching awards attach
``winning_amount`` to their existing project (forward paper bids settle on the
next sweep); unmatched awards seed new corpus projects.

This issues many external calls — run only after explicit approval. Validate
small first (e.g. 1 category, 2 days) before a full window.

Usage:
    python scripts/backfill_scsbid_awards.py --start 20260501 --end 20260507 --dry-run
    python scripts/backfill_scsbid_awards.py --start 20260501 --end 20260507 \
        --categories construction,service,goods --no-reserve-detail --persist
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import Project
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService


@dataclass
class CategoryStats:
    category: str
    collected: int = 0
    matched_existing: int = 0
    new_projects: int = 0


@dataclass
class BackfillStats:
    start: str = ""
    end: str = ""
    categories: list[str] = field(default_factory=list)
    reserve_detail: bool = True
    persisted: bool = False
    total_collected: int = 0
    total_matched_existing: int = 0
    total_new_projects: int = 0
    api_call_count: int = 0
    per_category: list[dict[str, Any]] = field(default_factory=list)


def _parse_categories(raw: str | None) -> list[str]:
    """Parse a CSV category string into a normalized list."""
    if not raw:
        return []
    return [token.strip().lower() for token in raw.split(",") if token.strip()]


def _existing_notice_numbers(db: Session, notice_numbers: set[str]) -> set[str]:
    """Return the subset of notice numbers that already have a Project row."""
    if not notice_numbers:
        return set()
    rows = (
        db.query(Project.notice_number)
        .filter(Project.notice_number.in_(notice_numbers))
        .all()
    )
    return {str(row[0]) for row in rows if row[0]}


def run_backfill(
    db: Session,
    *,
    start: str,
    end: str,
    categories: list[str],
    page_size: int,
    max_pages: int,
    collect_reserve_detail: bool,
    execution_mode: str,
    persist: bool,
) -> BackfillStats:
    """Collect scsbid awards for the window and (optionally) persist them."""
    service = KonepsCollectorService()
    stats = BackfillStats(
        start=start,
        end=end,
        categories=categories,
        reserve_detail=collect_reserve_detail,
        persisted=persist,
    )

    per_category: dict[str, CategoryStats] = {
        category: CategoryStats(category=category) for category in categories
    }

    for category in categories:
        request = CrawlRequest(
            source="scsbid-openapi",
            category=category,
            categories=[category],
            start_date=start,
            end_date=end,
            execution_mode=execution_mode,
            page_size=page_size,
            max_pages=max_pages,
            collect_reserve_detail=collect_reserve_detail,
        )
        response = service.collect_notices(request)
        items = response.get("items", [])
        metadata = response.get("metadata", {})
        stats.api_call_count += int(metadata.get("scsbid_api_call_count") or 0)

        notice_numbers = {
            str(item.get("notice_number"))
            for item in items
            if item.get("notice_number")
        }
        pre_existing = _existing_notice_numbers(db, notice_numbers)

        cat_stats = per_category[category]
        cat_stats.collected = len(items)
        cat_stats.matched_existing = len(notice_numbers & pre_existing)
        cat_stats.new_projects = len(notice_numbers - pre_existing)

        if persist:
            crawl_job = service.create_crawl_job(db, request)
            service.persist_crawl_results(db, crawl_job, request, response)

    stats.per_category = [asdict(value) for value in per_category.values()]
    stats.total_collected = sum(v.collected for v in per_category.values())
    stats.total_matched_existing = sum(
        v.matched_existing for v in per_category.values()
    )
    stats.total_new_projects = sum(v.new_projects for v in per_category.values())
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill KONEPS scsbid awards over a date window."
    )
    parser.add_argument(
        "--start", required=True, help="Window start (YYYYMMDD or ISO)."
    )
    parser.add_argument("--end", required=True, help="Window end (YYYYMMDD or ISO).")
    parser.add_argument(
        "--categories",
        default="construction,service,goods",
        help="CSV of categories to sweep.",
    )
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument(
        "--no-reserve-detail",
        action="store_true",
        help="Skip per-item reserve-price detail fetches (recommended for bulk).",
    )
    parser.add_argument(
        "--execution-mode",
        default="live",
        choices=["mock", "live", "auto"],
    )
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument("--persist", dest="persist", action="store_true")
    persistence.add_argument("--dry-run", dest="persist", action="store_false")
    parser.set_defaults(persist=False)
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("reports/scsbid-backfill/run.json"),
    )
    args = parser.parse_args()

    categories = _parse_categories(args.categories)
    if not categories:
        parser.error("--categories must contain at least one category")

    db = SessionLocal()
    try:
        stats = run_backfill(
            db,
            start=args.start,
            end=args.end,
            categories=categories,
            page_size=max(1, min(999, args.page_size)),
            max_pages=max(1, args.max_pages),
            collect_reserve_detail=not args.no_reserve_detail,
            execution_mode=args.execution_mode,
            persist=args.persist,
        )
        if args.persist:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
