"""Operational dashboard reporting across crawl and strategy runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import CrawlJob, OperatorStrategyRun


class AnalyticsReportingService:
    """Build dashboard-ready cards for operational health and strategy outcomes."""

    SUCCESSFUL_CRAWL_STATUSES = {"completed", "fallback_mock"}

    def build_operations_dashboard(self, db: Session, *, days: int = 30, recent_limit: int = 5) -> dict[str, Any]:
        """Return crawl and strategy monitoring summaries for one reporting window."""
        operator = ensure_operator_account(db)
        date_from = utc_now() - timedelta(days=days)
        crawl_jobs = (
            db.query(CrawlJob)
            .filter(CrawlJob.created_at >= date_from)
            .order_by(CrawlJob.created_at.desc(), CrawlJob.id.desc())
            .all()
        )
        strategy_runs = (
            db.query(OperatorStrategyRun)
            .filter(
                OperatorStrategyRun.operator_id == operator.id,
                OperatorStrategyRun.created_at >= date_from,
            )
            .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
            .all()
        )
        crawl_summary = self._build_crawl_summary(crawl_jobs, recent_limit=recent_limit)
        strategy_summary = self._build_strategy_summary(strategy_runs, recent_limit=recent_limit)
        return {
            "operator_id": operator.id,
            "period_days": days,
            "crawl": crawl_summary,
            "strategy": strategy_summary,
            "cards": self._build_cards(crawl_summary, strategy_summary),
        }

    def _build_crawl_summary(self, crawl_jobs: list[CrawlJob], *, recent_limit: int) -> dict[str, Any]:
        """Aggregate crawl job health metrics."""
        total_count = len(crawl_jobs)
        completed_count = sum(1 for job in crawl_jobs if str(job.status) == "completed")
        fallback_count = sum(1 for job in crawl_jobs if "fallback" in str(job.status or ""))
        failed_count = sum(1 for job in crawl_jobs if str(job.status) == "failed")
        successful_count = sum(1 for job in crawl_jobs if str(job.status) in self.SUCCESSFUL_CRAWL_STATUSES)
        result_counts = [int(job.result_count or 0) for job in crawl_jobs]
        failure_reason_breakdown = self._reason_breakdown(
            [str(job.error_message or "") for job in crawl_jobs if job.error_message]
        )
        completed_jobs = [job for job in crawl_jobs if str(job.status) in self.SUCCESSFUL_CRAWL_STATUSES]
        failed_jobs = [job for job in crawl_jobs if str(job.status) == "failed"]
        return {
            "job_count": total_count,
            "completed_count": completed_count,
            "fallback_count": fallback_count,
            "failed_count": failed_count,
            "success_rate": self._rate(successful_count, total_count),
            "failure_rate": self._rate(failed_count, total_count),
            "average_result_count": self._average(result_counts),
            "total_result_count": sum(result_counts),
            "last_success_at": self._latest_completed_at(completed_jobs),
            "last_failure_at": self._latest_completed_at(failed_jobs),
            "failure_reason_breakdown": failure_reason_breakdown,
            "recent_failures": [
                {
                    "crawl_job_id": int(job.id),
                    "source": job.source,
                    "target_date": job.target_date,
                    "status": job.status,
                    "error_message": job.error_message,
                    "created_at": job.created_at,
                    "completed_at": job.completed_at,
                }
                for job in failed_jobs[:recent_limit]
            ],
        }

    def _build_strategy_summary(self, strategy_runs: list[OperatorStrategyRun], *, recent_limit: int) -> dict[str, Any]:
        """Aggregate strategy monitoring performance metrics."""
        total_count = len(strategy_runs)
        completed_count = sum(1 for run in strategy_runs if str(run.status) == "completed")
        failed_count = sum(1 for run in strategy_runs if str(run.status) == "failed")
        running_count = sum(1 for run in strategy_runs if str(run.status) in {"queued", "running"})
        evaluated_count = sum(int(run.evaluated_project_count or 0) for run in strategy_runs)
        selected_count = sum(int(run.selected_candidate_count or 0) for run in strategy_runs)
        persisted_count = sum(int(run.persisted_candidate_count or 0) for run in strategy_runs)
        notification_count = sum(int(run.notification_count or 0) for run in strategy_runs)
        failed_runs = [run for run in strategy_runs if str(run.status) == "failed"]
        completed_runs = [run for run in strategy_runs if str(run.status) == "completed"]
        return {
            "run_count": total_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "running_count": running_count,
            "completion_rate": self._rate(completed_count, total_count),
            "failure_rate": self._rate(failed_count, total_count),
            "evaluated_project_count": evaluated_count,
            "selected_candidate_count": selected_count,
            "persisted_candidate_count": persisted_count,
            "notification_count": notification_count,
            "selection_rate": self._rate(selected_count, evaluated_count),
            "persistence_rate": self._rate(persisted_count, selected_count),
            "notification_rate": self._rate(notification_count, persisted_count),
            "average_selected_candidates": self._average([int(run.selected_candidate_count or 0) for run in completed_runs]),
            "last_completed_at": self._latest_completed_at(completed_runs),
            "last_failure_at": self._latest_completed_at(failed_runs),
            "failure_reason_breakdown": self._reason_breakdown(
                [str(run.error_message or "") for run in failed_runs if run.error_message]
            ),
            "recent_failures": [
                {
                    "run_id": int(run.id),
                    "trigger_source": run.trigger_source,
                    "status": run.status,
                    "error_message": run.error_message,
                    "created_at": run.created_at,
                    "completed_at": run.completed_at,
                }
                for run in failed_runs[:recent_limit]
            ],
        }

    def _build_cards(self, crawl_summary: dict[str, Any], strategy_summary: dict[str, Any]) -> list[dict[str, Any]]:
        """Build dashboard card payloads from detailed summaries."""
        return [
            {
                "key": "crawl_success_rate",
                "label": "Crawl success rate",
                "value": crawl_summary["success_rate"],
                "unit": "ratio",
                "status": self._status_for_rate(crawl_summary["success_rate"], warning=0.85, critical=0.65),
                "detail": f"{crawl_summary['job_count']} crawl job(s), {crawl_summary['failed_count']} failed.",
            },
            {
                "key": "crawl_result_count",
                "label": "Collected notices",
                "value": crawl_summary["total_result_count"],
                "unit": "count",
                "status": "healthy" if crawl_summary["total_result_count"] > 0 else "watch",
                "detail": f"Average {crawl_summary['average_result_count'] or 0} item(s) per crawl.",
            },
            {
                "key": "strategy_completion_rate",
                "label": "Strategy run completion",
                "value": strategy_summary["completion_rate"],
                "unit": "ratio",
                "status": self._status_for_rate(strategy_summary["completion_rate"], warning=0.85, critical=0.65),
                "detail": f"{strategy_summary['run_count']} run(s), {strategy_summary['failed_count']} failed.",
            },
            {
                "key": "strategy_selection_rate",
                "label": "Candidate selection rate",
                "value": strategy_summary["selection_rate"],
                "unit": "ratio",
                "status": "healthy" if strategy_summary["selected_candidate_count"] > 0 else "watch",
                "detail": (
                    f"{strategy_summary['selected_candidate_count']} selected from "
                    f"{strategy_summary['evaluated_project_count']} evaluated project(s)."
                ),
            },
            {
                "key": "strategy_notifications",
                "label": "Notifications created",
                "value": strategy_summary["notification_count"],
                "unit": "count",
                "status": "healthy" if strategy_summary["notification_count"] > 0 else "info",
                "detail": f"{strategy_summary['persisted_candidate_count']} persisted candidate(s).",
            },
        ]

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
        if not values:
            return None
        return round(sum(float(value) for value in values) / len(values), 4)

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
