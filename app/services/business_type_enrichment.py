"""Background enrichment of Project.business_type_code via detail HTML."""

from __future__ import annotations

import re
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
    updated_from_title_rule: int = 0
    failed: int = 0
    skipped_no_url: int = 0


class BusinessTypeEnrichmentService:
    """Fill business_type_code/label on recently-collected Project rows.

    Targets rows where business_type_code IS NULL AND source_url IS NOT NULL.
    Enrichment order:
      1. detail HTML fetch  → if code returned, use it.
      2. title-rule fallback (settings.BUSINESS_TYPE_TITLE_RULES) → regex match
         against title + description + demand_agency.
      3. If neither succeeds, count as failed (row stays NULL).
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
        from app.core.config import settings

        stats = EnrichmentStats()
        fetcher = self._resolve_fetcher()
        compiled_rules = self._compile_title_rules(settings.BUSINESS_TYPE_TITLE_RULES or [])

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
            if self._apply_detail_fetch(project, fetcher, stats):
                continue
            if self._apply_title_rules(project, compiled_rules, stats):
                continue
            stats.failed += 1

        if stats.updated_from_detail or stats.updated_from_title_rule:
            db.commit()

        return asdict(stats)

    @staticmethod
    def _compile_title_rules(rules: list) -> list:
        """Compile raw rule dicts into (pattern, code, label) tuples.

        Rules missing ``pattern`` or ``code``, or with invalid regex, are
        silently skipped so a bad config entry doesn't crash the whole run.
        """
        compiled = []
        for rule in rules or []:
            pattern = rule.get("pattern") if isinstance(rule, dict) else None
            code = rule.get("code") if isinstance(rule, dict) else None
            if not pattern or not code:
                continue
            try:
                compiled.append((re.compile(pattern), str(code), rule.get("label")))
            except re.error:
                continue
        return compiled

    def _apply_detail_fetch(self, project: Project, fetcher: DetailFetcher, stats: EnrichmentStats) -> bool:
        """Attempt detail HTML fetch.

        Returns True if the project was consumed (either enriched or skipped_no_url),
        False if caller should try title-rule fallback.
        """
        url = (project.source_url or "").strip()
        if not url:
            stats.skipped_no_url += 1
            return True  # consumed — no further attempt needed
        try:
            payload = fetcher(url) or {}
        except Exception:
            return False  # fall through to title-rule

        code = payload.get("business_type_code")
        label = payload.get("business_type_label")
        if not code:
            return False  # fall through to title-rule

        project.business_type_code = str(code).strip() or None
        if label:
            project.business_type_label = str(label).strip() or None
        stats.updated_from_detail += 1
        return True

    @staticmethod
    def _apply_title_rules(project: Project, compiled_rules: list, stats: EnrichmentStats) -> bool:
        """Try each compiled title rule against project text fields.

        Returns True if a rule matched (project enriched), False otherwise.
        """
        if not compiled_rules:
            return False
        text = " ".join(filter(None, [
            project.title,
            project.description,
            project.demand_agency or "",
        ]))
        if not text.strip():
            return False
        for pattern, code, label in compiled_rules:
            if pattern.search(text):
                project.business_type_code = code
                if label:
                    project.business_type_label = label
                stats.updated_from_title_rule += 1
                return True
        return False
