"""Operational dashboard reporting across crawl, strategy, and task runtime health."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import CrawlJob, OperatorStrategyRun
from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    build_task_routes,
)


class AnalyticsReportingService:
    """Build dashboard-ready cards for operational health and strategy outcomes."""

    SUCCESSFUL_CRAWL_STATUSES = {"completed", "fallback_mock"}
    COMPLETED_TASK_STATUSES = {"completed", "fallback_mock", "success"}
    FAILED_TASK_STATUSES = {"failed", "failure"}
    QUEUED_TASK_STATUSES = {"queued", "pending"}
    RUNNING_TASK_STATUSES = {"running", "started"}
    STALE_TASK_SECONDS = 15 * 60

    def build_operations_dashboard(self, db: Session, *, days: int = 30, recent_limit: int = 5) -> dict[str, Any]:
        """Return crawl, strategy, and task runtime summaries for one reporting window."""
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
        task_summary = self._build_task_summary(crawl_jobs, strategy_runs, recent_limit=recent_limit)
        return {
            "operator_id": operator.id,
            "period_days": days,
            "crawl": crawl_summary,
            "strategy": strategy_summary,
            "tasks": task_summary,
            "cards": self._build_cards(crawl_summary, strategy_summary, task_summary),
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

    def _build_task_summary(
        self,
        crawl_jobs: list[CrawlJob],
        strategy_runs: list[OperatorStrategyRun],
        *,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate task history and Celery runtime diagnostics without touching the broker."""
        task_records = self._tracked_task_records(crawl_jobs, strategy_runs)
        tracked_task_count = len(task_records)
        queued_count = sum(1 for item in task_records if item["normalized_status"] in self.QUEUED_TASK_STATUSES)
        running_count = sum(1 for item in task_records if item["normalized_status"] in self.RUNNING_TASK_STATUSES)
        completed_count = sum(1 for item in task_records if item["normalized_status"] in self.COMPLETED_TASK_STATUSES)
        failed_count = sum(1 for item in task_records if item["normalized_status"] in self.FAILED_TASK_STATUSES)
        active_count = queued_count + running_count
        now = utc_now()
        delayed_tasks = [
            {
                **item,
                "age_seconds": self._age_seconds(item, now=now),
            }
            for item in task_records
            if item["normalized_status"] in self.QUEUED_TASK_STATUSES | self.RUNNING_TASK_STATUSES
            and self._age_seconds(item, now=now) >= self.STALE_TASK_SECONDS
        ]
        delayed_tasks.sort(key=lambda item: (-int(item["age_seconds"]), item["source"], int(item["record_id"])))
        failed_tasks = [item for item in task_records if item["normalized_status"] in self.FAILED_TASK_STATUSES]
        failed_tasks.sort(key=lambda item: self._datetime_sort_key(item["completed_at"] or item["created_at"]), reverse=True)
        retry_tasks = [item for item in task_records if str(item["status"]).strip().lower() == "retry"]
        retry_tasks.sort(key=lambda item: self._datetime_sort_key(item["created_at"]), reverse=True)
        queue_wait_values = [
            self._duration_seconds(item["created_at"], item["started_at"])
            for item in task_records
            if item.get("created_at") is not None and item.get("started_at") is not None
        ]
        runtime_values = [
            self._duration_seconds(item["started_at"] or item["created_at"], item["completed_at"])
            for item in task_records
            if item.get("completed_at") is not None and (item.get("started_at") is not None or item.get("created_at") is not None)
        ]
        broker_status, broker_detail = self._broker_health()
        result_status, result_detail = self._result_backend_health()
        worker_status, worker_detail = self._worker_separation_health()
        backlog_status = self._task_backlog_status(delayed_count=len(delayed_tasks), active_count=active_count)
        failure_status = self._status_for_failure_rate(self._rate(failed_count, tracked_task_count))
        risk_flags = self._task_risk_flags(
            broker_status=broker_status,
            result_status=result_status,
            worker_status=worker_status,
            delayed_count=len(delayed_tasks),
            failed_count=failed_count,
            retry_count=len(retry_tasks),
        )
        return {
            "broker": {
                "url": self._redact_url(settings.CELERY_BROKER_URL),
                "transport": self._url_scheme(settings.CELERY_BROKER_URL),
                "health_status": broker_status,
                "detail": broker_detail,
            },
            "result_backend": {
                "url": self._redact_url(settings.CELERY_RESULT_BACKEND),
                "transport": self._url_scheme(settings.CELERY_RESULT_BACKEND),
                "health_status": result_status,
                "detail": result_detail,
            },
            "runtime": {
                "eager_mode": bool(settings.uses_in_memory_celery),
                "inline_ml_tasks_allowed": bool(settings.CELERY_ALLOW_INLINE_ML_TASKS),
                "worker_concurrency": int(settings.CELERY_WORKER_CONCURRENCY or 0),
                "worker_prefetch_multiplier": int(settings.CELERY_WORKER_PREFETCH_MULTIPLIER or 0),
                "worker_max_tasks_per_child": int(settings.CELERY_WORKER_MAX_TASKS_PER_CHILD or 0),
                "task_time_limit_seconds": int(settings.CELERY_TASK_TIME_LIMIT_SECONDS or 0),
                "task_soft_time_limit_seconds": int(settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS or 0),
                "result_expires_seconds": int(settings.CELERY_RESULT_EXPIRES_SECONDS or 0),
                "task_track_started": bool(settings.CELERY_TASK_TRACK_STARTED),
                "worker_send_task_events": bool(settings.CELERY_WORKER_SEND_TASK_EVENTS),
                "task_send_sent_event": bool(settings.CELERY_TASK_SEND_SENT_EVENT),
                "broker_connection_retry_on_startup": bool(settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP),
                "broker_connection_max_retries": int(settings.CELERY_BROKER_CONNECTION_MAX_RETRIES or 0),
                "broker_publish_max_retries": int(settings.CELERY_BROKER_PUBLISH_MAX_RETRIES or 0),
                "health_status": worker_status,
                "detail": worker_detail,
            },
            "queues": self._queue_diagnostics(),
            "tracked_task_count": tracked_task_count,
            "queued_count": queued_count,
            "running_count": running_count,
            "active_count": active_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "retry_count": len(retry_tasks),
            "failure_rate": self._rate(failed_count, tracked_task_count),
            "stale_task_threshold_seconds": self.STALE_TASK_SECONDS,
            "stale_task_count": len(delayed_tasks),
            "average_queue_wait_seconds": self._average([value for value in queue_wait_values if value is not None]),
            "average_runtime_seconds": self._average([value for value in runtime_values if value is not None]),
            "backlog_status": backlog_status,
            "failure_status": failure_status,
            "risk_flags": risk_flags,
            "recent_delayed_tasks": [
                self._serialize_task_item(item, include_age=True) for item in delayed_tasks[:recent_limit]
            ],
            "recent_failures": [
                self._serialize_task_item(item, include_age=False) for item in failed_tasks[:recent_limit]
            ],
            "recent_retries": [
                self._serialize_task_item(item, include_age=False) for item in retry_tasks[:recent_limit]
            ],
        }

    def _build_cards(
        self,
        crawl_summary: dict[str, Any],
        strategy_summary: dict[str, Any],
        task_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
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
            {
                "key": "task_broker_health",
                "label": "Task broker health",
                "value": 1 if task_summary["broker"]["health_status"] == "healthy" else 0,
                "unit": "count",
                "status": task_summary["broker"]["health_status"],
                "detail": task_summary["broker"]["detail"],
            },
            {
                "key": "task_stale_queue",
                "label": "Stale queued tasks",
                "value": task_summary["stale_task_count"],
                "unit": "count",
                "status": task_summary["backlog_status"],
                "detail": (
                    f"{task_summary['active_count']} active task(s), "
                    f"{task_summary['stale_task_count']} stale over "
                    f"{task_summary['stale_task_threshold_seconds']}s."
                ),
            },
            {
                "key": "task_failure_rate",
                "label": "Task failure rate",
                "value": task_summary["failure_rate"],
                "unit": "ratio",
                "status": task_summary["failure_status"],
                "detail": f"{task_summary['failed_count']} failed from {task_summary['tracked_task_count']} tracked task(s).",
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

    def _status_for_failure_rate(self, value: float) -> str:
        """Convert a failure ratio to a status token where lower is better."""
        if value >= 0.3:
            return "critical"
        if value >= 0.1:
            return "watch"
        return "healthy"

    def _tracked_task_records(
        self,
        crawl_jobs: list[CrawlJob],
        strategy_runs: list[OperatorStrategyRun],
    ) -> list[dict[str, Any]]:
        """Map persisted operational histories into a common task record shape."""
        records: list[dict[str, Any]] = []
        for job in crawl_jobs:
            records.append({
                "source": "crawl",
                "record_id": int(job.id),
                "task_id": None,
                "task_name": COLLECT_KONEPS_NOTICES_TASK_NAME,
                "queue": settings.CELERY_OPS_QUEUE,
                "status": str(job.status or "queued"),
                "normalized_status": self._normalize_task_status(str(job.status or "queued")),
                "detail": f"{job.source or 'crawl'} {job.target_date or 'latest'}",
                "error_message": job.error_message,
                "created_at": job.created_at,
                "started_at": None,
                "completed_at": job.completed_at,
            })
        for run in strategy_runs:
            records.append({
                "source": "strategy_monitor",
                "record_id": int(run.id),
                "task_id": run.task_id,
                "task_name": OPERATOR_STRATEGY_MONITOR_TASK_NAME,
                "queue": settings.CELERY_OPS_QUEUE,
                "status": str(run.status or "queued"),
                "normalized_status": self._normalize_task_status(str(run.status or "queued")),
                "detail": str(run.trigger_source or "strategy_monitor"),
                "error_message": run.error_message,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            })
        return records

    def _queue_diagnostics(self) -> list[dict[str, Any]]:
        """Return queue-to-task routing metadata for dashboard diagnostics."""
        queues: dict[str, list[str]] = {
            settings.CELERY_TASK_DEFAULT_QUEUE: [],
            settings.CELERY_OPS_QUEUE: [],
            settings.CELERY_ML_BACKFILL_QUEUE: [],
            settings.CELERY_ML_TRAINING_QUEUE: [],
            settings.CELERY_ML_REEVALUATION_QUEUE: [],
        }
        for task_name, route in build_task_routes().items():
            queue_name = str(route.get("queue") or settings.CELERY_TASK_DEFAULT_QUEUE)
            queues.setdefault(queue_name, []).append(task_name)
        return [
            {
                "queue": queue_name,
                "task_count": len(task_names),
                "task_names": sorted(task_names),
            }
            for queue_name, task_names in sorted(queues.items())
        ]

    def _broker_health(self) -> tuple[str, str]:
        """Evaluate broker configuration for operations visibility."""
        if settings.uses_in_memory_celery:
            if self._is_production_environment():
                return "critical", "memory:// broker is configured in a production environment."
            return "watch", "memory:// broker runs tasks in-process and does not provide worker queue durability."
        if not settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP:
            return "watch", "External broker is configured, but startup retry is disabled."
        return "healthy", "External broker is configured with startup retry enabled."

    def _result_backend_health(self) -> tuple[str, str]:
        """Evaluate task result backend configuration."""
        backend = (settings.CELERY_RESULT_BACKEND or "").strip().lower()
        if not backend:
            return "critical", "No Celery result backend is configured for task polling."
        if backend.startswith("cache+memory://") and not settings.uses_in_memory_celery:
            return "critical", "External broker is using the volatile in-memory result backend."
        if backend.startswith("cache+memory://"):
            return "watch", "In-memory result backend is suitable only for local eager execution."
        return "healthy", "Result backend is persistent or broker-managed for task polling."

    def _worker_separation_health(self) -> tuple[str, str]:
        """Evaluate whether heavy work is separated from the API process."""
        if settings.CELERY_ALLOW_INLINE_ML_TASKS:
            status = "critical" if self._is_production_environment() else "watch"
            return status, "Inline ML task execution is enabled; heavy jobs can run in the API process."
        if settings.uses_in_memory_celery:
            return "watch", "ML jobs stay queued with memory:// until an external worker broker is configured."
        return "healthy", "ML task execution is restricted to broker-backed workers."

    def _task_backlog_status(self, *, delayed_count: int, active_count: int) -> str:
        """Convert active/stale task counts into a dashboard status."""
        if delayed_count > 0:
            return "critical"
        if active_count > 0:
            return "watch"
        return "healthy"

    def _task_risk_flags(
        self,
        *,
        broker_status: str,
        result_status: str,
        worker_status: str,
        delayed_count: int,
        failed_count: int,
        retry_count: int,
    ) -> list[str]:
        """Return compact risk tokens for UI filtering."""
        flags: list[str] = []
        if broker_status != "healthy":
            flags.append("broker_not_production_ready")
        if result_status != "healthy":
            flags.append("result_backend_not_production_ready")
        if worker_status != "healthy":
            flags.append("worker_separation_risk")
        if delayed_count > 0:
            flags.append("stale_tasks_detected")
        if failed_count > 0:
            flags.append("task_failures_detected")
        if retry_count > 0:
            flags.append("task_retries_detected")
        return flags

    def _normalize_task_status(self, value: str) -> str:
        """Normalize status strings from app histories and Celery names."""
        normalized = value.strip().lower()
        if normalized in {"success", "completed", "fallback_mock"}:
            return "completed"
        if normalized in {"failure", "failed"}:
            return "failed"
        if normalized in {"started", "running"}:
            return "running"
        if normalized in {"pending", "queued", "received", "retry"}:
            return "queued"
        if normalized == "revoked":
            return "cancelled"
        return normalized or "unknown"

    def _serialize_task_item(self, item: dict[str, Any], *, include_age: bool) -> dict[str, Any]:
        """Serialize a common task record into the public dashboard shape."""
        payload = {
            "source": item["source"],
            "record_id": item["record_id"],
            "task_id": item["task_id"],
            "task_name": item["task_name"],
            "queue": item["queue"],
            "status": item["status"],
            "detail": item["detail"],
            "error_message": item["error_message"],
            "created_at": item["created_at"],
            "started_at": item["started_at"],
            "completed_at": item["completed_at"],
        }
        if include_age:
            payload["age_seconds"] = int(item.get("age_seconds") or 0)
        return payload

    def _age_seconds(self, item: dict[str, Any], *, now) -> int:
        """Return active task age in seconds."""
        started_at = item.get("started_at")
        created_at = item.get("created_at")
        anchor = started_at if item["normalized_status"] in self.RUNNING_TASK_STATUSES and started_at else created_at
        duration = self._duration_seconds(anchor, now)
        return int(duration or 0)

    def _duration_seconds(self, start, end) -> int | None:
        """Return a non-negative duration while tolerating naive/aware datetime mixes."""
        if start is None or end is None:
            return None
        if getattr(start, "tzinfo", None) is not None and getattr(end, "tzinfo", None) is None:
            end = end.replace(tzinfo=start.tzinfo)
        elif getattr(start, "tzinfo", None) is None and getattr(end, "tzinfo", None) is not None:
            start = start.replace(tzinfo=end.tzinfo)
        return max(0, int((end - start).total_seconds()))

    def _datetime_sort_key(self, value) -> float:
        """Return a comparable timestamp for optional naive or aware datetimes."""
        if value is None:
            return 0.0
        return float(value.timestamp())

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
