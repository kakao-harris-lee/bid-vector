"""KONEPS collector service skeleton."""
from typing import Any

from app.schemas.schemas import CrawlRequest


class KonepsCollectorService:
    """Collect KONEPS notices/opening data."""

    def collect_notices(self, request: CrawlRequest) -> dict[str, Any]:
        """Return a placeholder collection result for future Playwright integration."""
        return {
            "job_status": "queued",
            "source": request.source,
            "collected_count": 0,
            "items": [],
        }
