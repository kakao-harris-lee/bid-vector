"""Crawl-job health reporting mixin."""

from __future__ import annotations

from typing import Any

from app.models.models import CrawlJob


class _CrawlMixin:
    """Crawl-job health summary and dashboard cards."""

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

    def _crawl_dashboard_cards(
        self,
        crawl_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": "crawl_success_rate",
                "label": "Crawl success rate",
                "value": crawl_summary["success_rate"],
                "unit": "ratio",
                "status": self._status_for_rate(
                    crawl_summary["success_rate"],
                    warning=0.85,
                    critical=0.65,
                ),
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
        ]
