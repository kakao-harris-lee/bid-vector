"""Operational dashboard reporting across crawl, strategy, and task runtime health."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.domain.aggregates import average
from app.models.models import (
    Analytics,
    CrawlJob,
    DecisionExperimentRun,
    Notification,
    OperatorNotificationChannel,
    OperatorStrategyRun,
    SmokeTestRun,
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
    User,
)
from app.services.ml_release import MLReleasePromotionService
from app.services.synthetic_experiment import (
    SAMPLE_STATUS_SUFFICIENT,
    SYNTHETIC_EXPERIMENT_PRESETS,
    SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
    sample_status_for_settled_count,
)
from app.services.notifications.manager import (
    mask_notification_route_key,
    mask_notification_target,
)
from app.services.notifications.telegram import TelegramNotificationService
from app.services.smoke_failure_taxonomy import (
    SMOKE_FAILURE_CATEGORIES,
    classify_failure,
    guidance_for,
)
from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    OPERATOR_STRATEGY_MONITOR_TASK_NAME,
    build_task_routes,
)


def _resolve_g2_evidence_status(
    *,
    has_evidence: bool,
    has_mixed_scope: bool,
    has_ready: bool,
    missing_gap: str | None = None,
    mixed_scope_gap: str | None = None,
    insufficient_gap: str | None = None,
) -> tuple[str, str | None]:
    """Resolve a G-2 evidence status and its blocking gap by canonical precedence.

    Precedence (highest first): ``missing`` -> ``mixed_scope`` -> ``insufficient``
    -> ``ready``. A component is:

    - ``missing`` when it holds no evidence at all,
    - ``mixed_scope`` when the only (or highest-priority) evidence is out of the
      operator_id scope,
    - ``insufficient`` when operator-scoped evidence exists but has not reached a
      ready/completed threshold,
    - ``ready`` otherwise.

    Only the non-ready branches carry a blocking gap; ``ready`` never blocks.
    Callers whose branch order matches this precedence share this resolver; those
    with a different precedence (e.g. the smoke summary, which prefers ``ready``
    over ``mixed_scope``) must not use it.
    """
    if not has_evidence:
        return "missing", missing_gap
    if has_mixed_scope:
        return "mixed_scope", mixed_scope_gap
    if not has_ready:
        return "insufficient", insufficient_gap
    return "ready", None


class AnalyticsReportingService:
    """Build dashboard-ready cards for operational health and strategy outcomes."""

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

    def build_operations_dashboard(
        self,
        db: Session,
        *,
        days: int = 30,
        recent_limit: int = 5,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return crawl, strategy, and task runtime summaries for one reporting window."""
        if operator is None:
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
        notification_summary = self._build_notification_summary(
            db,
            operator_id=int(operator.id),
            date_from=date_from,
            recent_limit=recent_limit,
        )
        ml_release_summary = self._build_ml_release_summary(recent_limit=recent_limit)
        smoke_test_summary = self._build_smoke_test_summary(
            db,
            days=days,
            recent_limit=recent_limit,
        )
        synthetic_validation_summary = self._build_synthetic_validation_summary(
            db,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        return {
            "operator_id": operator.id,
            "period_days": days,
            "crawl": crawl_summary,
            "strategy": strategy_summary,
            "tasks": task_summary,
            "notifications": notification_summary,
            "ml_release": ml_release_summary,
            "smoke_test": smoke_test_summary,
            "synthetic_validation": synthetic_validation_summary,
            "cards": self._build_cards(
                crawl_summary,
                strategy_summary,
                task_summary,
                notification_summary,
                ml_release_summary,
                smoke_test_summary,
                synthetic_validation_summary,
            ),
        }

    def build_g2_evidence_summary(
        self,
        db: Session,
        *,
        window_days: int = 30,
        recent_limit: int = 5,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return one operator-scoped evidence ledger for the G-2 exit review."""
        if operator is None:
            operator = ensure_operator_account(db)
        operator_id = int(operator.id)
        date_from = utc_now() - timedelta(days=window_days)

        smoke = self._build_g2_smoke_summary(
            db,
            operator_id=operator_id,
            date_from=date_from,
            window_days=window_days,
            recent_limit=recent_limit,
        )
        strategy_monitor = self._build_g2_strategy_monitor_summary(
            db,
            operator_id=operator_id,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        decision_experiments = self._build_g2_decision_experiment_summary(
            db,
            operator_id=operator_id,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        synthetic_experiments = self._build_g2_synthetic_experiment_summary(
            db,
            operator=operator,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        notifications = self._build_g2_notification_evidence_summary(
            db,
            operator_id=operator_id,
            date_from=date_from,
            recent_limit=recent_limit,
        )

        g2_ready_components = [
            strategy_monitor,
            decision_experiments,
            synthetic_experiments,
            notifications,
        ]
        blocking_gaps = [
            str(component["blocking_gap"])
            for component in g2_ready_components
            if component.get("blocking_gap")
        ]
        supporting_gaps = [
            str(smoke["blocking_gap"])
        ] if smoke.get("blocking_gap") else []
        has_any_evidence = any(
            int(component.get("evidence_count") or 0) > 0
            for component in g2_ready_components
        )
        evidence_status, _ = _resolve_g2_evidence_status(
            has_evidence=has_any_evidence,
            has_mixed_scope=any(
                str(component.get("status")) == "mixed_scope"
                for component in g2_ready_components
            ),
            has_ready=not blocking_gaps,
        )

        return {
            "operator_id": operator_id,
            "window_days": window_days,
            "evidence_status": evidence_status,
            "smoke": smoke,
            "strategy_monitor": strategy_monitor,
            "decision_experiments": decision_experiments,
            "synthetic_experiments": synthetic_experiments,
            "notifications": notifications,
            "blocking_gaps": blocking_gaps,
            "supporting_gaps": supporting_gaps,
        }

    def _build_g2_smoke_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from: datetime,
        window_days: int,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Normalize persisted smoke evidence into explicit G-0 vs G-2 scope."""
        runs = (
            db.query(SmokeTestRun)
            .filter(SmokeTestRun.created_at >= date_from)
            .order_by(SmokeTestRun.created_at.desc(), SmokeTestRun.id.desc())
            .all()
        )
        dashboard_summary = self._build_smoke_test_summary(
            db,
            days=window_days,
            recent_limit=recent_limit,
        )
        operator_evidence: list[dict[str, Any]] = []
        canonical_only_count = 0
        other_operator_count = 0
        for run in runs:
            for phase in self._load_smoke_phases(run.phases):
                phase_evidence = self._smoke_phase_evidence(
                    phase,
                    smoke_run_id=int(run.id),
                )
                scoped_operator_id = self._optional_int(phase_evidence.get("operator_id"))
                if scoped_operator_id == operator_id:
                    operator_evidence.append(
                        {
                            "smoke_run_id": int(run.id),
                            "phase": str(phase.get("name") or ""),
                            "started_at": run.started_at,
                            "completed_at": run.completed_at,
                            "passed": bool(phase.get("passed")),
                            "evidence": phase_evidence,
                        }
                    )
                elif scoped_operator_id is None:
                    canonical_only_count += 1
                else:
                    other_operator_count += 1

        if not runs:
            status = "missing"
            blocking_gap = "No smoke evidence exists in the G-2 review window."
        elif operator_evidence:
            status = "ready"
            blocking_gap = None
        elif canonical_only_count > 0 or other_operator_count > 0:
            status = "mixed_scope"
            blocking_gap = (
                "Smoke evidence includes only G-0 canonical-only or another operator's "
                f"scope; rerun smoke evidence fully scoped to operator_id={operator_id}."
            )
        else:
            status = "insufficient"
            blocking_gap = f"No operator-scoped smoke phase evidence for operator_id={operator_id}."

        return {
            "status": status,
            "evidence_scope": self.SMOKE_EVIDENCE_SCOPE,
            "source_run_type": self.SMOKE_SOURCE_RUN_TYPE,
            "operator_scope": "operator" if status == "ready" else "mixed_scope" if status == "mixed_scope" else "missing",
            "counts_toward_g2_ready": status == "ready",
            "canonical_only_reason": self.SMOKE_CANONICAL_ONLY_REASON,
            "evidence_count": len(operator_evidence) + canonical_only_count + other_operator_count,
            "operator_evidence_count": len(operator_evidence),
            "canonical_only_phase_count": canonical_only_count,
            "other_operator_phase_count": other_operator_count,
            "cycle_count": dashboard_summary["cycle_count"],
            "passed_count": dashboard_summary["passed_count"],
            "failed_count": dashboard_summary["failed_count"],
            "current_streak": dashboard_summary["current_streak"],
            "healthy_streak_target": dashboard_summary["healthy_streak_target"],
            "current_streak_meets_target": dashboard_summary["current_streak_meets_target"],
            "latest_operator_evidence": operator_evidence[0] if operator_evidence else None,
            "recent_operator_evidence": operator_evidence[:recent_limit],
            "blocking_gap": blocking_gap,
        }

    def _build_g2_strategy_monitor_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize operator-scoped strategy-monitor runs for G-2 evidence."""
        runs = (
            db.query(OperatorStrategyRun)
            .filter(
                OperatorStrategyRun.operator_id == operator_id,
                OperatorStrategyRun.created_at >= date_from,
            )
            .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
            .all()
        )
        completed_runs = [run for run in runs if str(run.status) == "completed"]
        failed_runs = [run for run in runs if str(run.status) == "failed"]
        status, blocking_gap = _resolve_g2_evidence_status(
            has_evidence=bool(runs),
            has_mixed_scope=False,
            has_ready=bool(completed_runs),
            insufficient_gap=f"Strategy monitor has no completed run for operator_id={operator_id}.",
            missing_gap=f"No strategy monitor evidence for operator_id={operator_id}.",
        )

        return {
            "status": status,
            "evidence_scope": "g2_operator_strategy_monitor",
            "source_run_type": "operator_strategy_monitor",
            "operator_scope": "operator",
            "operator_id": operator_id,
            "counts_toward_g2_ready": status == "ready",
            "evidence_count": len(runs),
            "run_count": len(runs),
            "completed_count": len(completed_runs),
            "failed_count": len(failed_runs),
            "evaluated_project_count": sum(int(run.evaluated_project_count or 0) for run in runs),
            "selected_candidate_count": sum(int(run.selected_candidate_count or 0) for run in runs),
            "persisted_candidate_count": sum(int(run.persisted_candidate_count or 0) for run in runs),
            "notification_count": sum(int(run.notification_count or 0) for run in runs),
            "latest_run": self._g2_strategy_run_evidence(runs[0]) if runs else None,
            "recent_runs": [self._g2_strategy_run_evidence(run) for run in runs[:recent_limit]],
            "blocking_gap": blocking_gap,
        }

    def _build_g2_decision_experiment_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize decision experiment runs with explicit operator_id scope."""
        runs = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.operator_id == operator_id,
                DecisionExperimentRun.created_at >= date_from,
            )
            .order_by(DecisionExperimentRun.created_at.desc(), DecisionExperimentRun.id.desc())
            .all()
        )
        completed_runs = [run for run in runs if str(run.status) == "completed"]
        status, blocking_gap = _resolve_g2_evidence_status(
            has_evidence=bool(runs),
            has_mixed_scope=False,
            has_ready=bool(completed_runs),
            insufficient_gap=f"Decision experiments exist but none completed for operator_id={operator_id}.",
            missing_gap=f"No decision experiment evidence for operator_id={operator_id}.",
        )

        outcome_counts: dict[str, int] = {}
        for run in runs:
            outcome = str(run.outcome or "pending")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

        return {
            "status": status,
            "evidence_scope": "g2_operator_decision_experiment",
            "source_run_type": "decision_experiment_run",
            "operator_scope": "operator",
            "operator_id": operator_id,
            "counts_toward_g2_ready": status == "ready",
            "evidence_count": len(runs),
            "run_count": len(runs),
            "completed_count": len(completed_runs),
            "success_count": sum(1 for run in runs if str(run.outcome or "") == "success"),
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "latest_run": self._g2_decision_experiment_run_evidence(runs[0]) if runs else None,
            "recent_runs": [
                self._g2_decision_experiment_run_evidence(run)
                for run in runs[:recent_limit]
            ],
            "blocking_gap": blocking_gap,
        }

    def _build_g2_synthetic_experiment_summary(
        self,
        db: Session,
        *,
        operator: User,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize synthetic runs, rejecting slug-only evidence for G-2 ready."""
        operator_id = int(operator.id)
        operator_username = str(operator.username or "")
        runs = (
            db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.created_at >= date_from)
            .order_by(SyntheticExperimentRun.created_at.desc(), SyntheticExperimentRun.id.desc())
            .all()
        )
        operator_results: list[dict[str, Any]] = []
        slug_only_results: list[dict[str, Any]] = []
        for run in runs:
            for result in run.results:
                metrics = self._load_json_object(result.metrics_json)
                result_operator_id = self._optional_int(
                    metrics.get("operator_id") or metrics.get("current_operator_id")
                )
                evidence = self._g2_synthetic_result_evidence(
                    run,
                    result,
                    metrics=metrics,
                    result_operator_id=result_operator_id,
                )
                if result_operator_id == operator_id:
                    operator_results.append(evidence)
                elif str(result.operator_slug or "") == operator_username:
                    slug_only_results.append(evidence)

        sufficient_operator_results = [
            item
            for item in operator_results
            if item["run_status"] == "completed"
            and item["sample_status"] == SAMPLE_STATUS_SUFFICIENT
        ]
        status, blocking_gap = _resolve_g2_evidence_status(
            has_evidence=bool(operator_results or slug_only_results),
            has_mixed_scope=bool(slug_only_results),
            has_ready=bool(sufficient_operator_results),
            mixed_scope_gap=(
                "Synthetic experiment evidence for this operator is slug-scoped "
                "without operator_id; rerun or backfill operator_id-scoped metrics."
            ),
            insufficient_gap=(
                f"Synthetic experiment evidence for operator_id={operator_id} exists "
                "but has not reached completed/sufficient sample status."
            ),
            missing_gap=f"No operator_id-scoped synthetic experiment evidence for operator_id={operator_id}.",
        )

        return {
            "status": status,
            "evidence_scope": "g2_operator_synthetic_experiment",
            "source_run_type": "synthetic_experiment_run",
            "operator_scope": "operator" if status == "ready" else "mixed_scope" if status == "mixed_scope" else "missing",
            "operator_id": operator_id,
            "operator_username": operator_username,
            "counts_toward_g2_ready": status == "ready",
            "evidence_count": len(operator_results) + len(slug_only_results),
            "operator_id_scoped_result_count": len(operator_results),
            "slug_only_result_count": len(slug_only_results),
            "sufficient_operator_result_count": len(sufficient_operator_results),
            "latest_operator_result": operator_results[0] if operator_results else None,
            "latest_slug_only_result": slug_only_results[0] if slug_only_results else None,
            "recent_operator_results": operator_results[:recent_limit],
            "blocking_gap": blocking_gap,
        }

    def _build_g2_notification_evidence_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize app and Telegram notification evidence scoped by user_id."""
        summary = self._build_notification_summary(
            db,
            operator_id=operator_id,
            date_from=date_from,
            recent_limit=recent_limit,
        )
        evidence_count = int(summary["notification_count"]) + int(summary["telegram_delivery_attempt_count"])
        dry_run_policy_channels = (
            db.query(OperatorNotificationChannel)
            .filter(
                OperatorNotificationChannel.operator_id == operator_id,
                OperatorNotificationChannel.is_active.is_(True),
                OperatorNotificationChannel.dry_run_only.is_(True),
            )
            .order_by(OperatorNotificationChannel.verified_at.desc().nullslast(), OperatorNotificationChannel.id.desc())
            .all()
        )
        if evidence_count > 0:
            status = "ready"
            blocking_gap = None
            policy_evidence_count = 0
            source_run_type = "notification_or_telegram_delivery"
        elif dry_run_policy_channels:
            status = "ready"
            blocking_gap = None
            policy_evidence_count = len(dry_run_policy_channels)
            source_run_type = "operator_notification_policy"
            evidence_count = policy_evidence_count
        else:
            status = "missing"
            blocking_gap = f"No notification evidence for operator_id={operator_id}."
            policy_evidence_count = 0
            source_run_type = "notification_or_telegram_delivery"
        return {
            **summary,
            "status": status,
            "evidence_scope": "g2_operator_notification",
            "source_run_type": source_run_type,
            "operator_scope": "operator",
            "operator_id": operator_id,
            "counts_toward_g2_ready": status == "ready",
            "evidence_count": evidence_count,
            "dry_run_policy_evidence_count": policy_evidence_count,
            "latest_dry_run_policy": (
                {
                    "channel_id": int(dry_run_policy_channels[0].id),
                    "channel_type": dry_run_policy_channels[0].channel_type,
                    "route_key": mask_notification_route_key(
                        dry_run_policy_channels[0].route_key,
                    ),
                    "target_label": mask_notification_target(
                        dry_run_policy_channels[0].target_label,
                    ),
                    "verified_at": dry_run_policy_channels[0].verified_at,
                    "created_at": dry_run_policy_channels[0].created_at,
                    "updated_at": dry_run_policy_channels[0].updated_at,
                }
                if dry_run_policy_channels
                else None
            ),
            "blocking_gap": blocking_gap,
        }

    def _g2_strategy_run_evidence(self, run: OperatorStrategyRun) -> dict[str, Any]:
        return {
            "run_id": int(run.id),
            "operator_id": int(run.operator_id),
            "source_run_type": "operator_strategy_monitor",
            "source_run_id": int(run.id),
            "trigger_source": run.trigger_source,
            "status": run.status,
            "evaluated_project_count": int(run.evaluated_project_count or 0),
            "selected_candidate_count": int(run.selected_candidate_count or 0),
            "persisted_candidate_count": int(run.persisted_candidate_count or 0),
            "notification_count": int(run.notification_count or 0),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }

    def _g2_decision_experiment_run_evidence(self, run: DecisionExperimentRun) -> dict[str, Any]:
        latest_evaluation = self._load_json_object(run.latest_evaluation)
        return {
            "run_id": int(run.id),
            "operator_id": int(run.operator_id),
            "source_run_type": "decision_experiment_run",
            "source_run_id": int(run.id),
            "experiment_key": run.experiment_key,
            "recommendation_key": run.recommendation_key,
            "status": run.status,
            "outcome": run.outcome,
            "title": run.title,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "last_evaluated_at": run.last_evaluated_at,
            "created_at": run.created_at,
            "latest_evaluation": latest_evaluation or None,
        }

    def _g2_synthetic_result_evidence(
        self,
        run: SyntheticExperimentRun,
        result: SyntheticExperimentResult,
        *,
        metrics: dict[str, Any],
        result_operator_id: int | None,
    ) -> dict[str, Any]:
        sample_status = str(
            metrics.get("sample_status")
            or sample_status_for_settled_count(int(metrics.get("settled_count") or 0))["sample_status"]
        )
        return {
            "run_id": int(run.id),
            "experiment_id": int(run.experiment_id),
            "experiment_name": run.experiment.name if run.experiment else None,
            "source_run_type": "synthetic_experiment_run",
            "source_run_id": int(run.id),
            "result_id": int(result.id),
            "operator_id": result_operator_id,
            "operator_slug": result.operator_slug,
            "run_status": run.status,
            "sample_status": sample_status,
            "settled_count": int(metrics.get("settled_count") or 0),
            "missing_settled_count": int(metrics.get("missing_settled_count") or 0),
            "created_at": run.created_at,
            "finished_at": run.finished_at,
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
                    "operator_id": int(run.operator_id),
                    "source_run_type": "operator_strategy_monitor",
                    "source_run_id": int(run.id),
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

    def _build_notification_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate web notification and Telegram delivery telemetry."""
        notifications = (
            db.query(Notification)
            .filter(
                Notification.user_id == operator_id,
                Notification.created_at >= date_from,
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .all()
        )
        telegram_events = (
            db.query(Analytics)
            .filter(
                Analytics.user_id == operator_id,
                Analytics.event_type == "telegram.delivery",
                Analytics.timestamp >= date_from,
            )
            .order_by(Analytics.timestamp.desc(), Analytics.id.desc())
            .all()
        )
        event_payloads = [
            {
                "event_id": int(event.id),
                "timestamp": event.timestamp,
                **self._load_event_payload(event.event_data),
            }
            for event in telegram_events
        ]
        sent_count = sum(1 for item in event_payloads if bool(item.get("sent")))
        failed_count = sum(1 for item in event_payloads if str(item.get("status") or "") == "failed")
        pending_configuration_count = sum(
            1 for item in event_payloads if str(item.get("status") or "") == "pending_configuration"
        )
        skipped_count = sum(1 for item in event_payloads if str(item.get("status") or "").startswith("skipped"))
        delivery_attempt_count = len(event_payloads)
        success_rate = self._rate(sent_count, delivery_attempt_count)
        telegram_configured = TelegramNotificationService().is_configured()
        status, detail = self._telegram_delivery_status(
            configured=telegram_configured,
            notification_count=len(notifications),
            delivery_attempt_count=delivery_attempt_count,
            sent_count=sent_count,
            failed_count=failed_count,
            pending_configuration_count=pending_configuration_count,
            success_rate=success_rate,
        )
        recent_failures = [
            item for item in event_payloads
            if str(item.get("status") or "") in {"failed", "pending_configuration"}
        ][:recent_limit]
        return {
            "notification_count": len(notifications),
            "unread_count": sum(1 for item in notifications if not bool(item.is_read)),
            "decision_notification_count": sum(1 for item in notifications if str(item.type or "") == "recommendation"),
            "bid_submission_notification_count": sum(1 for item in notifications if str(item.type or "") == "bid_update"),
            "telegram_configured": telegram_configured,
            "telegram_delivery_attempt_count": delivery_attempt_count,
            "telegram_sent_count": sent_count,
            "telegram_failed_count": failed_count,
            "telegram_pending_configuration_count": pending_configuration_count,
            "telegram_skipped_count": skipped_count,
            "telegram_success_rate": success_rate,
            "telegram_status": status,
            "telegram_detail": detail,
            "telegram_status_counts": self._count_payloads_by_key(event_payloads, "status"),
            "telegram_failure_reason_breakdown": self._reason_breakdown(
                [
                    str(item.get("detail") or "")
                    for item in event_payloads
                    if str(item.get("status") or "") in {"failed", "pending_configuration"}
                    and item.get("detail")
                ]
            ),
            "recent_telegram_failures": [
                {
                    "event_id": int(item["event_id"]),
                    "notification_id": self._optional_int(item.get("notification_id")),
                    "source": str(item.get("source") or "unknown"),
                    "status": str(item.get("status") or "unknown"),
                    "detail": str(item.get("detail") or ""),
                    "timestamp": item["timestamp"],
                }
                for item in recent_failures
            ],
        }

    def _build_synthetic_validation_summary(
        self,
        db: Session,
        *,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize G-1 synthetic experiment health for the operations report."""
        preset_names = tuple(SYNTHETIC_EXPERIMENT_PRESETS)
        experiment_by_name = self._synthetic_experiments_by_name(db, preset_names)
        recent_runs = self._synthetic_preset_runs(
            db,
            preset_names,
            date_from=date_from,
        )
        all_preset_runs = self._synthetic_preset_runs(db, preset_names)
        latest_run_by_name = self._latest_synthetic_run_by_name(all_preset_runs)
        latest_run = all_preset_runs[0] if all_preset_runs else None
        preset_rows = [
            self._synthetic_preset_row(
                name,
                experiment=experiment_by_name.get(name),
                latest_preset_run=latest_run_by_name.get(name),
            )
            for name in preset_names
        ]
        counts = self._synthetic_validation_counts(preset_rows, recent_runs)
        status, detail = self._synthetic_validation_status(
            preset_count=len(preset_names),
            saved_preset_count=counts["saved_preset_count"],
            completed_preset_count=counts["completed_preset_count"],
            failed_preset_count=counts["failed_preset_count"],
            sufficient_preset_count=counts["sufficient_preset_count"],
            recent_run_count=counts["recent_run_count"],
        )
        detail = self._with_synthetic_scope_detail(detail)
        return {
            "preset_count": len(preset_names),
            "saved_preset_count": counts["saved_preset_count"],
            "completed_preset_count": counts["completed_preset_count"],
            "failed_preset_count": counts["failed_preset_count"],
            "sufficient_preset_count": counts["sufficient_preset_count"],
            "sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
            "recent_run_count": counts["recent_run_count"],
            "recent_completed_count": counts["recent_completed_count"],
            "recent_failed_count": counts["recent_failed_count"],
            "status": status,
            "detail": detail,
            "latest": self._synthetic_latest_run_summary(latest_run),
            "presets": preset_rows,
        }

    def _synthetic_experiments_by_name(
        self,
        db: Session,
        preset_names: tuple[str, ...],
    ) -> dict[str, SyntheticExperiment]:
        if not preset_names:
            return {}
        experiments = (
            db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name.in_(preset_names))
            .order_by(
                SyntheticExperiment.created_at.desc(),
                SyntheticExperiment.id.desc(),
            )
            .all()
        )
        experiment_by_name: dict[str, SyntheticExperiment] = {}
        for experiment in experiments:
            experiment_by_name.setdefault(str(experiment.name), experiment)
        return experiment_by_name

    def _synthetic_preset_runs(
        self,
        db: Session,
        preset_names: tuple[str, ...],
        *,
        date_from: datetime | None = None,
    ) -> list[SyntheticExperimentRun]:
        if not preset_names:
            return []
        query = (
            db.query(SyntheticExperimentRun)
            .join(SyntheticExperiment)
            .filter(SyntheticExperiment.name.in_(preset_names))
        )
        if date_from is not None:
            query = query.filter(SyntheticExperimentRun.created_at >= date_from)
        return query.order_by(
            SyntheticExperimentRun.created_at.desc(),
            SyntheticExperimentRun.id.desc(),
        ).all()

    @staticmethod
    def _latest_synthetic_run_by_name(
        runs: list[SyntheticExperimentRun],
    ) -> dict[str, SyntheticExperimentRun]:
        latest_run_by_name: dict[str, SyntheticExperimentRun] = {}
        for run in runs:
            if run.experiment is None:
                continue
            latest_run_by_name.setdefault(str(run.experiment.name), run)
        return latest_run_by_name

    def _synthetic_preset_row(
        self,
        name: str,
        *,
        experiment: SyntheticExperiment | None,
        latest_preset_run: SyntheticExperimentRun | None,
    ) -> dict[str, Any]:
        summary = self._load_json_object(
            latest_preset_run.summary_json if latest_preset_run else None
        )
        row_experiment_id = (
            int(latest_preset_run.experiment_id)
            if latest_preset_run
            else int(experiment.id)
            if experiment
            else None
        )
        return {
            "name": name,
            "experiment_id": row_experiment_id,
            "latest_run_id": int(latest_preset_run.id) if latest_preset_run else None,
            "latest_run_status": latest_preset_run.status if latest_preset_run else None,
            "latest_finished_at": latest_preset_run.finished_at if latest_preset_run else None,
            "sample_status": summary.get("sample_status"),
            "total_settled_count": int(summary.get("total_settled_count") or 0),
            "missing_total_settled_count": int(
                summary.get("missing_total_settled_count") or 0
            ),
            "insufficient_operator_count": len(summary.get("insufficient_operators") or []),
            "evidence_scope": self.SYNTHETIC_EVIDENCE_SCOPE,
            "canonical_only_reason": self.SYNTHETIC_CANONICAL_ONLY_REASON,
        }

    def _synthetic_validation_counts(
        self,
        preset_rows: list[dict[str, Any]],
        recent_runs: list[SyntheticExperimentRun],
    ) -> dict[str, int]:
        return {
            "saved_preset_count": sum(
                1 for item in preset_rows if item["experiment_id"] is not None
            ),
            "completed_preset_count": sum(
                1 for item in preset_rows if item["latest_run_status"] == "completed"
            ),
            "failed_preset_count": sum(
                1 for item in preset_rows if item["latest_run_status"] == "failed"
            ),
            "sufficient_preset_count": sum(
                1 for item in preset_rows if item["sample_status"] == SAMPLE_STATUS_SUFFICIENT
            ),
            "recent_run_count": len(recent_runs),
            "recent_completed_count": sum(
                1 for run in recent_runs if str(run.status) == "completed"
            ),
            "recent_failed_count": sum(
                1 for run in recent_runs if str(run.status) == "failed"
            ),
        }

    def _synthetic_latest_run_summary(
        self,
        latest_run: SyntheticExperimentRun | None,
    ) -> dict[str, Any] | None:
        if latest_run is None:
            return None
        latest_summary = self._load_json_object(latest_run.summary_json)
        return {
            "experiment_id": int(latest_run.experiment_id),
            "experiment_name": latest_run.experiment.name if latest_run.experiment else None,
            "run_id": int(latest_run.id),
            "status": latest_run.status,
            "created_at": latest_run.created_at,
            "finished_at": latest_run.finished_at,
            "sample_status": latest_summary.get("sample_status"),
            "total_settled_count": int(latest_summary.get("total_settled_count") or 0),
            "missing_total_settled_count": int(
                latest_summary.get("missing_total_settled_count") or 0
            ),
            "evidence_scope": self.SYNTHETIC_EVIDENCE_SCOPE,
            "canonical_only_reason": self.SYNTHETIC_CANONICAL_ONLY_REASON,
        }

    def _synthetic_validation_status(
        self,
        *,
        preset_count: int,
        saved_preset_count: int,
        completed_preset_count: int,
        failed_preset_count: int,
        sufficient_preset_count: int,
        recent_run_count: int,
    ) -> tuple[str, str]:
        """Convert G-1 synthetic run state into dashboard status/detail."""
        if preset_count == 0:
            return "info", "G-1 synthetic preset is not configured."
        if failed_preset_count > 0:
            return "critical", f"{failed_preset_count} G-1 preset run(s) failed."
        if saved_preset_count == 0:
            return "info", "No G-1 synthetic preset has been saved yet."
        if sufficient_preset_count >= preset_count:
            return "healthy", "All G-1 presets have sufficient settled samples."
        if completed_preset_count > 0:
            return (
                "watch",
                f"{sufficient_preset_count}/{preset_count} G-1 preset(s) reached the sample target.",
            )
        if recent_run_count > 0:
            return "watch", "G-1 synthetic runs exist, but no preset has completed yet."
        return (
            "watch",
            f"{saved_preset_count}/{preset_count} G-1 preset(s) saved; run experiments to collect samples.",
        )

    def _build_smoke_test_summary(
        self,
        db: Session,
        *,
        days: int,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate persisted daily smoke cycles into PASS/FAIL + green-streak signals.

        ``pass_rate`` = passed cycles / total cycles in the window.
        ``current_streak`` = number of consecutive most-recent cycles that passed
        overall (the roadmap "N일 연속 green" signal). Honest empty-window output:
        zero counts, ``latest=None``, no crash. ``schedule_enabled`` surfaces
        ``SMOKE_TEST_SCHEDULE_ENABLED`` so the UI can say "스케줄 비활성 / 데이터
        없음" instead of implying failure when there are simply no runs.
        """
        date_from = utc_now() - timedelta(days=days)
        runs = self._load_recent_smoke_runs(db, date_from=date_from)
        cycle_count = len(runs)
        passed_count = sum(1 for run in runs if bool(run.overall_passed))
        failed_count = cycle_count - passed_count
        current_streak = self._current_smoke_pass_streak(runs)
        per_phase, failure_categories = self._build_smoke_phase_summary(runs)

        return {
            "cycle_count": cycle_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "pass_rate": self._rate(passed_count, cycle_count),
            "current_streak": current_streak,
            "healthy_streak_target": self.SMOKE_HEALTHY_STREAK,
            "current_streak_meets_target": current_streak >= self.SMOKE_HEALTHY_STREAK,
            "schedule_enabled": bool(settings.SMOKE_TEST_SCHEDULE_ENABLED),
            "failure_category_breakdown": {
                key: value for key, value in failure_categories.items() if value > 0
            },
            "per_phase": per_phase,
            "latest": self._build_latest_smoke_summary(runs[0]) if runs else None,
            "recent_failures": self._build_recent_smoke_failures(
                runs,
                recent_limit=recent_limit,
            ),
        }

    def _load_recent_smoke_runs(
        self,
        db: Session,
        *,
        date_from: datetime,
    ) -> list[SmokeTestRun]:
        return (
            db.query(SmokeTestRun)
            .filter(SmokeTestRun.created_at >= date_from)
            .order_by(SmokeTestRun.created_at.desc(), SmokeTestRun.id.desc())
            .all()
        )

    def _current_smoke_pass_streak(self, runs: list[SmokeTestRun]) -> int:
        current_streak = 0
        for run in runs:
            if not bool(run.overall_passed):
                break
            current_streak += 1
        return current_streak

    def _build_smoke_phase_summary(
        self,
        runs: list[SmokeTestRun],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        phase_passed: dict[str, int] = {name: 0 for name in self.SMOKE_PHASE_NAMES}
        phase_attempted: dict[str, int] = {name: 0 for name in self.SMOKE_PHASE_NAMES}
        failure_categories: dict[str, int] = {
            name: 0 for name in self.SMOKE_FAILURE_CATEGORIES
        }
        for run in runs:
            for phase in self._load_smoke_phases(run.phases):
                name = str(phase.get("name") or "")
                if name not in phase_attempted:
                    continue
                if self._is_skipped_phase(phase):
                    category = self._smoke_failure_category(phase)
                    if category == "no_candidate":
                        failure_categories[category] = failure_categories.get(category, 0) + 1
                    continue
                phase_attempted[name] += 1
                if bool(phase.get("passed")):
                    phase_passed[name] += 1
                else:
                    category = self._smoke_failure_category(phase)
                    failure_categories[category] = failure_categories.get(category, 0) + 1
        per_phase = [
            {
                "name": name,
                "pass_rate": self._rate(phase_passed[name], phase_attempted[name]),
                "evaluated_count": phase_attempted[name],
            }
            for name in self.SMOKE_PHASE_NAMES
        ]
        return per_phase, failure_categories

    def _build_latest_smoke_summary(self, latest_run: SmokeTestRun) -> dict[str, Any]:
        return {
            "started_at": latest_run.started_at,
            "overall_passed": bool(latest_run.overall_passed),
            "phases": [
                self._serialize_smoke_phase(
                    phase,
                    smoke_run_id=int(latest_run.id),
                    include_passed_nulls=True,
                )
                for phase in self._load_smoke_phases(latest_run.phases)
            ],
        }

    def _build_recent_smoke_failures(
        self,
        runs: list[SmokeTestRun],
        *,
        recent_limit: int,
    ) -> list[dict[str, Any]]:
        recent_failures = []
        for run in runs:
            if bool(run.overall_passed):
                continue
            failed_phases = [
                phase
                for phase in self._load_smoke_phases(run.phases)
                if not bool(phase.get("passed"))
            ]
            run_categories: dict[str, int] = {}
            for phase in failed_phases:
                category = self._smoke_failure_category(phase)
                run_categories[category] = run_categories.get(category, 0) + 1
            phase_details = [
                self._serialize_smoke_phase(phase, smoke_run_id=int(run.id))
                for phase in failed_phases
            ]
            recent_failures.append(
                {
                    "started_at": run.started_at,
                    "failed_phases": [
                        str(phase.get("name") or "") for phase in failed_phases
                    ],
                    "failure_categories": sorted(run_categories),
                    "failure_category_breakdown": dict(
                        sorted(run_categories.items(), key=lambda item: (-item[1], item[0]))
                    ),
                    "failure_actions": sorted(
                        {
                            str(item["action_required"])
                            for item in phase_details
                            if item["action_required"]
                        }
                    ),
                    "retry_methods": sorted(
                        {
                            str(item["retry_method"])
                            for item in phase_details
                            if item["retry_method"]
                        }
                    ),
                    "phase_details": phase_details,
                }
            )
            if len(recent_failures) >= recent_limit:
                break
        return recent_failures

    def _serialize_smoke_phase(
        self,
        phase: dict[str, Any],
        *,
        smoke_run_id: int,
        include_passed_nulls: bool = False,
    ) -> dict[str, Any]:
        passed = bool(phase.get("passed"))
        if include_passed_nulls and passed:
            failure_category = None
            action_required = None
            retry_method = None
        else:
            failure_category = self._smoke_failure_category(phase)
            action_required = self._smoke_failure_action(phase)
            retry_method = self._smoke_retry_method(phase)
        return {
            "name": str(phase.get("name") or ""),
            "passed": passed,
            "detail": str(phase.get("detail") or ""),
            "failure_category": failure_category,
            "action_required": action_required,
            "retry_method": retry_method,
            "skip_reason": self._smoke_skip_reason(phase),
            "evidence": self._smoke_phase_evidence(
                phase,
                smoke_run_id=smoke_run_id,
            ),
        }

    def _with_synthetic_scope_detail(self, detail: str) -> str:
        return f"{detail} {self.SYNTHETIC_CANONICAL_ONLY_REASON}"

    def _load_smoke_phases(self, raw_phases: Any) -> list[dict[str, Any]]:
        """Parse a SmokeTestRun.phases JSON string into a list of phase dicts."""
        if isinstance(raw_phases, list):
            return [phase for phase in raw_phases if isinstance(phase, dict)]
        try:
            parsed = json.loads(str(raw_phases or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [phase for phase in parsed if isinstance(phase, dict)]

    def _is_skipped_phase(self, phase: dict[str, Any]) -> bool:
        """Return whether a phase record marks a *skipped* (never-run) occurrence.

        A skipped phase is persisted with ``passed=False`` and a ``detail`` that
        starts with ``"skipped"`` (e.g. ``"skipped — no eligible project"``). A
        genuinely-attempted-but-failed phase has a non-skipped detail and still
        counts as attempted+fail.
        """
        if bool(phase.get("passed")):
            return False
        detail = str(phase.get("detail") or "").strip().lower()
        return detail.startswith("skipped")

    def _smoke_failure_category(self, phase: dict[str, Any]) -> str:
        """Classify a failed smoke phase into the roadmap's fixed buckets.

        Trusts the producer-stored ``failure_category`` when it is one of the
        canonical buckets, otherwise re-derives it via the shared classifier
        (guidance-less / legacy rows).
        """
        stored = str(phase.get("failure_category") or "").strip().lower()
        if stored in self.SMOKE_FAILURE_CATEGORIES:
            return stored
        return classify_failure(
            str(phase.get("name") or ""),
            str(phase.get("detail") or ""),
        )

    def _smoke_failure_action(self, phase: dict[str, Any]) -> str:
        stored = str(phase.get("action_required") or "").strip()
        if stored:
            return stored
        return self._smoke_failure_guidance(self._smoke_failure_category(phase))["action_required"]

    def _smoke_retry_method(self, phase: dict[str, Any]) -> str:
        stored = str(phase.get("retry_method") or "").strip()
        if stored:
            return stored
        return self._smoke_failure_guidance(self._smoke_failure_category(phase))["retry_method"]

    def _smoke_skip_reason(self, phase: dict[str, Any]) -> str | None:
        stored = str(phase.get("skip_reason") or "").strip()
        if stored:
            return stored
        if not self._is_skipped_phase(phase):
            return None
        detail = str(phase.get("detail") or "").strip()
        if detail.lower().startswith("skipped"):
            return detail.replace("skipped", "", 1).strip(" -—")
        return detail or None

    def _smoke_phase_evidence(
        self,
        phase: dict[str, Any],
        *,
        smoke_run_id: int | None = None,
    ) -> dict[str, Any]:
        evidence = phase.get("evidence")
        scoped = dict(evidence) if isinstance(evidence, dict) else {}
        scoped.setdefault("evidence_scope", self.SMOKE_EVIDENCE_SCOPE)
        phase_name = str(phase.get("name") or "")
        monitor_run_id = self._optional_int(scoped.get("monitor_run_id"))
        if phase_name == "candidate_generation" and monitor_run_id is not None:
            scoped.setdefault("source_run_type", "operator_strategy_monitor")
            scoped.setdefault("source_run_id", monitor_run_id)
        else:
            scoped.setdefault("source_run_type", self.SMOKE_SOURCE_RUN_TYPE)
            if smoke_run_id is not None:
                scoped.setdefault("source_run_id", int(smoke_run_id))
        if smoke_run_id is not None:
            scoped.setdefault("source_smoke_run_id", int(smoke_run_id))
        operator_id = self._optional_int(scoped.get("operator_id") or scoped.get("current_operator_id"))
        if operator_id is not None:
            scoped["operator_id"] = operator_id
            scoped.setdefault("operator_scope", "operator")
            return scoped
        scoped.setdefault("operator_scope", "canonical_only")
        scoped.setdefault("canonical_only_reason", self.SMOKE_CANONICAL_ONLY_REASON)
        return scoped

    @staticmethod
    def _smoke_failure_guidance(category: str) -> dict[str, str]:
        """Return shared remediation guidance for a smoke failure category."""
        return guidance_for(category)

    def _smoke_test_status(self, summary: dict[str, Any]) -> tuple[str, str]:
        """Convert smoke cycle telemetry into a dashboard status + detail."""
        cycle_count = int(summary.get("cycle_count") or 0)
        schedule_enabled = bool(summary.get("schedule_enabled"))
        if cycle_count == 0:
            if not schedule_enabled:
                return "info", "스모크 스케줄 비활성 / 데이터 없음."
            return "watch", "스모크 스케줄은 켜져 있으나 기록된 사이클이 없습니다."
        streak = int(summary.get("current_streak") or 0)
        pass_rate = float(summary.get("pass_rate") or 0.0)
        if streak >= self.SMOKE_HEALTHY_STREAK:
            return "healthy", f"G-0 기준 충족: 최근 {streak}회 연속 통과 ({cycle_count}회)."
        if pass_rate >= 0.9:
            return "watch", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."
        if pass_rate >= 0.65:
            return "watch", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."
        return "critical", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."

    def _build_ml_release_summary(self, *, recent_limit: int) -> dict[str, Any]:
        """Summarize local ML release manifests and predictor promotion gates."""
        manifest_dir = self._ml_manifest_dir()
        manifest_paths = list(manifest_dir.glob("*.json") if manifest_dir.exists() else [])
        manifest_summaries = [self._read_manifest_summary(path) for path in manifest_paths]
        manifest_summaries.sort(key=self._manifest_recency_key, reverse=True)
        recent_manifests = manifest_summaries[:recent_limit]
        latest = recent_manifests[0] if recent_manifests else None
        status, detail = self._ml_release_status(latest, manifest_count=len(manifest_paths))
        backtest_status, backtest_detail = self._ml_backtest_status(latest)
        return {
            "manifest_dir": str(manifest_dir),
            "manifest_count": len(manifest_paths),
            "remote_storage_configured": bool(settings.ML_RELEASE_OBJECT_STORAGE_URL),
            "remote_auto_publish": bool(settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH),
            "retention_limit": int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0),
            "status": status,
            "detail": detail,
            "latest_release_tag": latest.get("release_tag") if latest else None,
            "latest_manifest_path": latest.get("manifest_path") if latest else None,
            "latest_validated_on": latest.get("validated_on") if latest else None,
            "latest_signature_status": latest.get("signature_status") if latest else "missing",
            "latest_gate_status": latest.get("gate_status") if latest else "missing",
            "latest_gate_passed": latest.get("gate_passed") if latest else None,
            "latest_gate_policy": latest.get("gate_policy") if latest else None,
            "latest_best_predictor_key": latest.get("best_predictor_key") if latest else None,
            "latest_dataset_quality_status": latest.get("dataset_quality_status") if latest else None,
            "latest_backtest_sample_count": int(latest.get("backtest_sample_count") or 0) if latest else 0,
            "latest_backtest_average_absolute_error_rate": (
                latest.get("backtest_average_absolute_error_rate") if latest else None
            ),
            "backtest_status": backtest_status,
            "backtest_detail": backtest_detail,
            "recent_manifests": recent_manifests,
        }

    def _build_cards(
        self,
        crawl_summary: dict[str, Any],
        strategy_summary: dict[str, Any],
        task_summary: dict[str, Any],
        notification_summary: dict[str, Any],
        ml_release_summary: dict[str, Any],
        smoke_test_summary: dict[str, Any],
        synthetic_validation_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build dashboard card payloads from detailed summaries."""
        return [
            *self._crawl_dashboard_cards(crawl_summary),
            *self._strategy_dashboard_cards(strategy_summary),
            *self._task_dashboard_cards(task_summary),
            self._telegram_delivery_card(notification_summary),
            *self._ml_release_dashboard_cards(ml_release_summary),
            *self._smoke_test_dashboard_cards(smoke_test_summary),
            *self._synthetic_validation_dashboard_cards(synthetic_validation_summary),
        ]

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

    def _strategy_dashboard_cards(
        self,
        strategy_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        completion_status = (
            "info"
            if strategy_summary["run_count"] == 0
            else self._status_for_rate(
                strategy_summary["completion_rate"],
                warning=0.85,
                critical=0.65,
            )
        )
        selection_status = (
            "info"
            if strategy_summary["evaluated_project_count"] == 0
            else "healthy"
            if strategy_summary["selected_candidate_count"] > 0
            else "watch"
        )
        return [
            {
                "key": "strategy_completion_rate",
                "label": "Strategy run completion",
                "value": strategy_summary["completion_rate"],
                "unit": "ratio",
                "status": completion_status,
                "detail": f"{strategy_summary['run_count']} run(s), {strategy_summary['failed_count']} failed.",
            },
            {
                "key": "strategy_selection_rate",
                "label": "Candidate selection rate",
                "value": strategy_summary["selection_rate"],
                "unit": "ratio",
                "status": selection_status,
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

    def _task_dashboard_cards(
        self,
        task_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
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
                "detail": (
                    f"{task_summary['failed_count']} failed from "
                    f"{task_summary['tracked_task_count']} tracked task(s)."
                ),
            },
        ]

    @staticmethod
    def _telegram_delivery_card(
        notification_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "key": "telegram_delivery_rate",
            "label": "Telegram delivery rate",
            "value": notification_summary["telegram_success_rate"],
            "unit": "ratio",
            "status": notification_summary["telegram_status"],
            "detail": notification_summary["telegram_detail"],
        }

    @staticmethod
    def _ml_release_dashboard_cards(
        ml_release_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": "ml_release_gate",
                "label": "ML release gate",
                "value": 1 if ml_release_summary["status"] == "healthy" else 0,
                "unit": "count",
                "status": ml_release_summary["status"],
                "detail": ml_release_summary["detail"],
            },
            {
                "key": "ml_backtest_samples",
                "label": "Backtest samples",
                "value": ml_release_summary["latest_backtest_sample_count"],
                "unit": "count",
                "status": ml_release_summary["backtest_status"],
                "detail": ml_release_summary["backtest_detail"],
            },
        ]

    def _smoke_test_dashboard_cards(
        self,
        smoke_test_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        smoke_status, smoke_detail = self._smoke_test_status(smoke_test_summary)
        return [
            {
                "key": "smoke_test_streak",
                "label": "G-0 scheduled smoke streak",
                "value": smoke_test_summary["current_streak"],
                "unit": "count",
                "status": smoke_status,
                "detail": smoke_detail,
            },
            {
                "key": "smoke_test_pass_rate",
                "label": "G-0 scheduled smoke pass rate",
                "value": smoke_test_summary["pass_rate"],
                "unit": "ratio",
                "status": smoke_status,
                "detail": (
                    f"{smoke_test_summary['passed_count']}/{smoke_test_summary['cycle_count']} "
                    "G-0 scheduled smoke cycle(s) passed; per-operator G-2 evidence lives on monitor/experiment runs."
                ),
            },
        ]

    @staticmethod
    def _synthetic_validation_dashboard_cards(
        synthetic_validation_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": "synthetic_g1_presets",
                "label": "G-1 preset 준비",
                "value": synthetic_validation_summary["saved_preset_count"],
                "unit": "count",
                "status": synthetic_validation_summary["status"],
                "detail": (
                    f"{synthetic_validation_summary['saved_preset_count']}/"
                    f"{synthetic_validation_summary['preset_count']} preset saved."
                ),
            },
            {
                "key": "synthetic_g1_samples",
                "label": "G-1 충분 표본 preset",
                "value": synthetic_validation_summary["sufficient_preset_count"],
                "unit": "count",
                "status": synthetic_validation_summary["status"],
                "detail": synthetic_validation_summary["detail"],
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

    def _telegram_delivery_status(
        self,
        *,
        configured: bool,
        notification_count: int,
        delivery_attempt_count: int,
        sent_count: int,
        failed_count: int,
        pending_configuration_count: int,
        success_rate: float,
    ) -> tuple[str, str]:
        """Convert Telegram delivery telemetry into a dashboard status."""
        if not configured:
            if notification_count > 0 or pending_configuration_count > 0:
                return "watch", "Telegram is not configured while operator notifications are being created."
            return "info", "Telegram is not configured and no delivery attempts were recorded."
        if delivery_attempt_count == 0:
            return "info", "Telegram is configured, but no eligible delivery attempts were recorded in this window."
        if failed_count > 0 or success_rate < 0.9:
            return (
                "critical",
                f"{sent_count}/{delivery_attempt_count} Telegram delivery attempt(s) succeeded.",
            )
        if success_rate < 1.0:
            return "watch", f"{sent_count}/{delivery_attempt_count} Telegram delivery attempt(s) succeeded."
        return "healthy", f"All {delivery_attempt_count} Telegram delivery attempt(s) succeeded."

    def _load_event_payload(self, raw_payload: Any) -> dict[str, Any]:
        """Parse analytics event payloads stored as JSON text."""
        if isinstance(raw_payload, dict):
            return raw_payload
        try:
            payload = json.loads(str(raw_payload or "{}"))
        except json.JSONDecodeError:
            return {"detail": str(raw_payload or "")}
        return payload if isinstance(payload, dict) else {}

    def _load_json_object(self, raw_payload: Any) -> dict[str, Any]:
        """Parse a JSON object payload, returning an empty dict for invalid data."""
        if isinstance(raw_payload, dict):
            return raw_payload
        try:
            payload = json.loads(str(raw_payload or "{}"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

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

    def _ml_manifest_dir(self) -> Path:
        """Resolve the local release manifest directory."""
        raw_path = Path(settings.ML_RELEASE_MANIFEST_DIR)
        if raw_path.is_absolute():
            return raw_path
        return Path(__file__).resolve().parents[2] / raw_path

    def _manifest_recency_key(self, summary: dict[str, Any]) -> tuple[float, str]:
        """Sort manifests by validation timestamp, falling back to the release tag."""
        validated_on = summary.get("validated_on")
        timestamp = 0.0
        if validated_on:
            try:
                parsed = datetime.fromisoformat(str(validated_on).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp = parsed.timestamp()
            except ValueError:
                timestamp = 0.0
        return timestamp, str(summary.get("release_tag") or summary.get("manifest_path") or "")

    def _read_manifest_summary(self, path: Path) -> dict[str, Any]:
        """Read one release manifest into a compact operations summary."""
        summary: dict[str, Any] = {
            "manifest_path": str(path),
            "release_tag": path.stem,
            "validated_on": None,
            "signature_status": "missing",
            "gate_status": "missing",
            "gate_passed": None,
            "gate_policy": None,
            "backtest_sample_count": 0,
            "backtest_average_absolute_error_rate": None,
            "dataset_quality_status": None,
            "best_predictor_key": None,
            "best_predictor_name": None,
            "detail": "",
        }
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            summary.update({
                "signature_status": "invalid",
                "gate_status": "invalid",
                "gate_passed": False,
                "detail": f"Manifest could not be read: {exc}",
            })
            return summary
        if not isinstance(manifest, dict):
            summary.update({
                "signature_status": "invalid",
                "gate_status": "invalid",
                "gate_passed": False,
                "detail": "Manifest JSON is not an object.",
            })
            return summary

        summary["release_tag"] = str(manifest.get("release_tag") or path.stem)
        summary["validated_on"] = manifest.get("validated_on")
        summary["recommended_docker_target"] = manifest.get("recommended_docker_target")
        summary["remote_storage_enabled"] = bool((manifest.get("remote_storage") or {}).get("enabled"))
        summary["signature_status"] = self._manifest_signature_status(manifest, path)

        gate_container = manifest.get("promotion_gate") if isinstance(manifest.get("promotion_gate"), dict) else {}
        gate = gate_container.get("predictor_backtest") if isinstance(gate_container, dict) else {}
        gate = gate if isinstance(gate, dict) else {}
        metrics = gate.get("metrics") if isinstance(gate.get("metrics"), dict) else {}
        summary.update({
            "gate_status": str(gate.get("status") or "missing"),
            "gate_passed": bool(gate.get("passed")) if "passed" in gate else None,
            "gate_policy": (gate.get("thresholds") or {}).get("policy") if isinstance(gate.get("thresholds"), dict) else None,
            "backtest_sample_count": int(metrics.get("sample_count") or 0),
            "backtest_average_absolute_error_rate": metrics.get("average_absolute_error_rate"),
            "dataset_quality_status": metrics.get("dataset_quality_status"),
            "best_predictor_key": gate.get("best_predictor_key") or metrics.get("best_predictor_key"),
            "best_predictor_name": gate.get("best_predictor_name") or metrics.get("best_predictor_name"),
            "detail": "; ".join(str(reason) for reason in gate.get("reasons", []) if reason) if gate else "",
        })
        return summary

    def _manifest_signature_status(self, manifest: dict[str, Any], path: Path) -> str:
        """Return verified/missing/invalid for one release manifest signature."""
        if not isinstance(manifest.get("signature"), dict):
            return "invalid" if settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE else "missing"
        try:
            MLReleasePromotionService().verify_release_manifest(manifest, manifest_path=path)
        except ValueError:
            return "invalid"
        return "verified"

    def _ml_release_status(self, latest: dict[str, Any] | None, *, manifest_count: int) -> tuple[str, str]:
        """Convert release manifest state into a dashboard status."""
        if manifest_count == 0 or latest is None:
            return "watch", "No ML release manifest was found."
        if latest.get("signature_status") == "invalid":
            if settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE:
                return "critical", f"Latest manifest {latest.get('release_tag')} has an invalid signature."
            return "watch", f"Latest manifest {latest.get('release_tag')} has an invalid optional signature."
        if latest.get("gate_passed") is False:
            return "critical", f"Latest manifest {latest.get('release_tag')} failed the predictor promotion gate."
        if latest.get("signature_status") == "missing":
            return "watch", f"Latest manifest {latest.get('release_tag')} is not signed."
        return "healthy", f"Latest manifest {latest.get('release_tag')} is signed and passed its release gate."

    def _ml_backtest_status(self, latest: dict[str, Any] | None) -> tuple[str, str]:
        """Convert latest predictor backtest metadata into a dashboard status."""
        if latest is None:
            return "info", "No predictor backtest metadata is available."
        sample_count = int(latest.get("backtest_sample_count") or 0)
        gate_passed = latest.get("gate_passed")
        if gate_passed is False:
            return "critical", "Latest predictor promotion gate failed."
        if sample_count <= 0:
            return "info", "Latest manifest has no predictor backtest samples."
        required = int(settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 0)
        if sample_count < required:
            return "watch", f"Latest backtest has {sample_count} sample(s), below required {required}."
        error_rate = latest.get("backtest_average_absolute_error_rate")
        if error_rate is None:
            return "watch", f"Latest backtest has {sample_count} sample(s), but no error-rate metric."
        return "healthy", f"Latest backtest has {sample_count} sample(s) at {float(error_rate):.4f} average error."

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
                "task_id": getattr(job, "celery_task_id", None),
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
                "operator_id": int(run.operator_id),
                "source_run_type": "operator_strategy_monitor",
                "source_run_id": int(run.id),
                "task_id": run.task_id,
                "task_name": OPERATOR_STRATEGY_MONITOR_TASK_NAME,
                "queue": settings.CELERY_OPS_QUEUE,
                "status": str(run.status or "queued"),
                "normalized_status": self._normalize_task_status(str(run.status or "queued")),
                "detail": f"operator_id={int(run.operator_id)} trigger={run.trigger_source or 'strategy_monitor'}",
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
