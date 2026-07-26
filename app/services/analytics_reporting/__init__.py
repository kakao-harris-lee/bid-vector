"""Operational dashboard reporting across crawl, strategy, and task runtime health.

Public surface. ``AnalyticsReportingService`` and the module-level
``_resolve_g2_evidence_status`` keep their historical import path
(``from app.services.analytics_reporting import ...``). The service body was
decomposed into responsibility mixins (base / dashboard / g2_evidence /
g2_run_evidence / crawl / strategy / task_runtime / notification / synthetic /
smoke / ml_release_report); this ``__init__`` composes them. The split is a pure
move -- every method body is the original ``AnalyticsReportingService`` method,
relocated verbatim, so behaviour is unchanged.
"""

from __future__ import annotations

from app.services.analytics_reporting.base import _AnalyticsReportingBase
from app.services.analytics_reporting.crawl import _CrawlMixin
from app.services.analytics_reporting.dashboard import _DashboardMixin
from app.services.analytics_reporting.g2_evidence import (
    _G2EvidenceMixin,
    _resolve_g2_evidence_status,
)
from app.services.analytics_reporting.g2_run_evidence import _G2RunEvidenceMixin
from app.services.analytics_reporting.ml_release_report import _MLReleaseReportMixin
from app.services.analytics_reporting.notification import _NotificationMixin
from app.services.analytics_reporting.smoke import _SmokeTestMixin
from app.services.analytics_reporting.strategy import _StrategyMixin
from app.services.analytics_reporting.synthetic import _SyntheticValidationMixin
from app.services.analytics_reporting.task_runtime import _TaskRuntimeMixin

__all__ = ["AnalyticsReportingService", "_resolve_g2_evidence_status"]


class AnalyticsReportingService(
    _DashboardMixin,
    _G2EvidenceMixin,
    _G2RunEvidenceMixin,
    _CrawlMixin,
    _StrategyMixin,
    _TaskRuntimeMixin,
    _NotificationMixin,
    _SyntheticValidationMixin,
    _SmokeTestMixin,
    _MLReleaseReportMixin,
    _AnalyticsReportingBase,
):
    """Build dashboard-ready cards for operational health and strategy outcomes."""
