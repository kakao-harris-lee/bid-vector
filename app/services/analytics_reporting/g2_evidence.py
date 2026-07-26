"""G-2 exit evidence ledger mixin (operator-scoped)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import (
    DecisionExperimentRun,
    OperatorNotificationChannel,
    OperatorStrategyRun,
    SmokeTestRun,
    SyntheticExperimentRun,
    User,
)
from app.services.synthetic_experiment import SAMPLE_STATUS_SUFFICIENT
from app.services.notifications.manager import (
    mask_notification_route_key,
    mask_notification_target,
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


class _G2EvidenceMixin:
    """Operator-scoped G-2 exit evidence ledger builders."""

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
