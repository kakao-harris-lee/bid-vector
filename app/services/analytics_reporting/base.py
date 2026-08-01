"""Shared constants and stateless leaf helpers for the reporting mixins."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.domain.aggregates import average
from app.services.smoke_failure_taxonomy import SMOKE_FAILURE_CATEGORIES
from app.services.stored_json_payload import load_stored_json_object


class _AnalyticsReportingBase:
    """Shared constants and stateless leaf helpers for reporting mixins.

    The foundation every ``AnalyticsReportingService`` mixin builds on: the
    dashboard status constants plus the leaf helpers (rate/average math, status
    thresholds, payload/JSON loaders, and URL redaction). Method bodies are the
    original ``AnalyticsReportingService`` methods, moved verbatim.
    """

    SUCCESSFUL_CRAWL_STATUSES = {"completed", "fallback_mock"}
    COMPLETED_TASK_STATUSES = {"completed", "fallback_mock", "success"}
    FAILED_TASK_STATUSES = {"failed", "failure"}
    QUEUED_TASK_STATUSES = {"queued", "pending"}
    RUNNING_TASK_STATUSES = {"running", "started"}
    STALE_TASK_SECONDS = 15 * 60
    # Canonical scheduled smoke order (KonepsTelegramSmokeTestService).
    SMOKE_PHASE_NAMES = (
        "koneps_collect",
        "sbert_embedding",
        "predict_price",
        "candidate_generation",
        "telegram_ping",
    )
    # G-0 exit gate: seven consecutive scheduled smoke cycles should be green.
    SMOKE_HEALTHY_STREAK = 7
    SMOKE_EVIDENCE_SCOPE = "g0_scheduled_smoke"
    SMOKE_SOURCE_RUN_TYPE = "smoke_test_run"
    SMOKE_CANONICAL_ONLY_REASON = (
        "G-0 scheduled smoke validates the canonical shared pipeline; "
        "G-2 per-operator evidence is recorded on operator-scoped monitor and experiment runs."
    )
    # Shared with the smoke producer (KonepsTelegramSmokeTestService) via
    # app.services.smoke_failure_taxonomy.
    SMOKE_FAILURE_CATEGORIES = SMOKE_FAILURE_CATEGORIES
    SYNTHETIC_EVIDENCE_SCOPE = "g1_canonical_synthetic_validation"
    SYNTHETIC_CANONICAL_ONLY_REASON = (
        "Canonical G-1 synthetic validation aggregates preset operator slugs; "
        "it is not a target user/operator_id run."
    )

    def _latest_completed_at(self, rows: list[Any]):
        """Return the latest completed timestamp from rows."""
        values = [row.completed_at for row in rows if row.completed_at is not None]
        return max(values) if values else None

    def _reason_breakdown(self, reasons: list[str]) -> dict[str, int]:
        """Count normalized failure reasons."""
        breakdown: dict[str, int] = {}
        for reason in reasons:
            cleaned_reason = reason.strip() or "unknown"
            breakdown[cleaned_reason] = breakdown.get(cleaned_reason, 0) + 1
        return dict(sorted(breakdown.items(), key=lambda item: (-item[1], item[0])))

    def _average(self, values: list[int | float]) -> float | None:
        """Return a rounded average while preserving empty sets."""
        return average(values, digits=4)

    def _rate(self, numerator: int, denominator: int) -> float:
        """Return a dashboard-friendly ratio."""
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)

    def _status_for_rate(self, value: float, *, warning: float, critical: float) -> str:
        """Convert a ratio to a simple status token."""
        if value < critical:
            return "critical"
        if value < warning:
            return "watch"
        return "healthy"

    def _status_for_failure_rate(self, value: float) -> str:
        """Convert a failure ratio to a status token where lower is better."""
        if value >= 0.3:
            return "critical"
        if value >= 0.1:
            return "watch"
        return "healthy"

    def _load_json_object(
        self, raw_payload: Any, *, context: str = ""
    ) -> dict[str, Any]:
        """Restore a stored JSON object, degrading to an empty dict.

        Decoding + the shape check live in the shared restore path
        (``app.services.stored_json_payload``); this reporting layer only declares
        its degrade policy: an unreadable payload renders as an *empty* object so
        the surrounding dashboard row still renders (``None`` would break every
        ``summary.get(...)`` consumer). ``context`` labels the column in the
        degrade warning.
        """
        if isinstance(raw_payload, dict):
            return raw_payload
        return load_stored_json_object(str(raw_payload or "{}"), context=context) or {}

    def _count_payloads_by_key(self, payloads: list[dict[str, Any]], key: str) -> dict[str, int]:
        """Count arbitrary payload values by one key."""
        counts: dict[str, int] = {}
        for payload in payloads:
            value = str(payload.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    def _optional_int(self, value: Any) -> int | None:
        """Return int(value) when available."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _redact_url(self, value: str) -> str:
        """Redact credentials from broker/backend URLs before exposing them to dashboards."""
        raw_value = (value or "").strip()
        if "://" not in raw_value:
            return raw_value
        parsed = urlsplit(raw_value)
        netloc = parsed.netloc
        if "@" in netloc:
            auth, host = netloc.rsplit("@", 1)
            username = auth.split(":", 1)[0]
            netloc = f"{username}:***@{host}" if username else f"***@{host}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def _url_scheme(self, value: str) -> str:
        """Return a stable transport/backend scheme label."""
        raw_value = (value or "").strip()
        if "://" not in raw_value:
            return raw_value or "unknown"
        return raw_value.split("://", 1)[0]

    def _is_production_environment(self) -> bool:
        """Return whether current settings describe production-like execution."""
        return str(settings.ENVIRONMENT or "").strip().lower() in {"prod", "production"}
