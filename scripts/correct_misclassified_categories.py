#!/usr/bin/env python
"""One-off correction of Project.category misclassifications.

When SBERT prototype reclassification was active without the title-keyword
fast path, some rows landed in goods/software/technical-service for the wrong
reason (e.g. embedding cosine to a goods prototype confused by '수리시설').

This script applies the current title-keyword regex to all non-base
categories and updates rows whose keyword-match disagrees with the current
category. SBERT-only decisions are NOT overwritten — only the high-precision
title regex.

Usage:
    python scripts/correct_misclassified_categories.py            # dry-run
    python scripts/correct_misclassified_categories.py --apply    # commit
    python scripts/correct_misclassified_categories.py --apply --limit 100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.models.models import Project
from app.services.category_classifier import CategoryClassifierService


TARGET_CATEGORIES = ("general", "other", "goods", "software", "technical-service")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Cap rows considered (0 = all)")
    args = parser.parse_args()

    svc = CategoryClassifierService(model=None)
    db = SessionLocal()
    try:
        query = (
            db.query(Project)
            .filter(Project.category.in_(TARGET_CATEGORIES))
            .order_by(Project.id.asc())
        )
        if args.limit > 0:
            query = query.limit(args.limit)

        candidates = query.all()
        diffs: list[tuple[int, str, str, str]] = []
        updated = 0

        for project in candidates:
            predicted = svc.match_title_keyword(project.title)
            if predicted is None or predicted == project.category:
                continue
            diffs.append((int(project.id), project.category or "", predicted, project.title or ""))
            if args.apply:
                project.category = predicted
                db.add(project)
                updated += 1

        if args.apply and updated:
            db.commit()
            print(f"[apply] {updated} rows updated")
        else:
            print(f"[dry-run] {len(diffs)} mismatches found (use --apply to commit)")

        for project_id, current, predicted, title in diffs[:50]:
            short = title[:60].replace("\n", " ")
            print(f"  id={project_id}  {current:<20} -> {predicted:<20}  {short}")
        if len(diffs) > 50:
            print(f"  ... and {len(diffs) - 50} more")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
