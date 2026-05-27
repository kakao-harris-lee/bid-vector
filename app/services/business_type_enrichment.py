"""Background enrichment of Project.business_type_code via detail HTML."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.models import Project
from app.services.koneps.collector import KonepsCollectorService


DetailFetcher = Callable[[str], dict[str, Any] | None]


@dataclass
class EnrichmentStats:
    """Counters reported per enrichment run."""

    candidates: int = 0
    updated_from_detail: int = 0
    failed: int = 0
    skipped_no_url: int = 0


class BusinessTypeEnrichmentService:
    """Fill business_type_code/label on recently-collected Project rows.

    Targets rows where business_type_code IS NULL AND source_url IS NOT NULL.
    Best-effort: any detail-fetch failure is counted and the row is skipped
    so the task can keep going.
    """

    def __init__(self, fetcher: DetailFetcher | None = None) -> None:
        self._fetcher = fetcher

    def _resolve_fetcher(self) -> DetailFetcher:
        if self._fetcher is not None:
            return self._fetcher
        # Lazy-construct the default fetcher so tests can substitute via __init__
        collector = KonepsCollectorService()
        return collector.fetch_detail_html_payload

    def enrich_pending(self, db: Session, *, limit: int = 50) -> dict[str, Any]:
        """Process up to `limit` Project rows missing business_type_code."""
        stats = EnrichmentStats()
        fetcher = self._resolve_fetcher()

        candidates = (
            db.query(Project)
            .filter(Project.business_type_code.is_(None))
            .filter(Project.source_url.isnot(None))
            .order_by(Project.id.desc())
            .limit(limit)
            .all()
        )
        stats.candidates = len(candidates)

        for project in candidates:
            url = (project.source_url or "").strip()
            if not url:
                stats.skipped_no_url += 1
                continue
            try:
                payload = fetcher(url) or {}
            except Exception:
                stats.failed += 1
                continue

            code = payload.get("business_type_code")
            label = payload.get("business_type_label")
            if not code and not label:
                stats.failed += 1
                continue

            if code:
                project.business_type_code = str(code).strip() or None
            if label:
                project.business_type_label = str(label).strip() or None
            db.add(project)
            stats.updated_from_detail += 1

        if stats.updated_from_detail:
            db.commit()

        return asdict(stats)
