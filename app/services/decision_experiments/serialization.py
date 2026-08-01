"""Run serialization, application-state resolution, and dashboard sorting.

Turns a persisted run row into the API response shape (``_serialize_run``) and
derives the dashboard-facing metadata around it: supported/applied apply types,
compact application status, review buckets/priorities/reasons, next-action
descriptors, and the list sort/count helpers. Method bodies are the original
``DecisionExperimentService`` methods, moved verbatim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time import ensure_utc
from app.models.models import DecisionExperimentRun
from app.services.decision_experiments.base import (
    LATEST_EVALUATION_COLUMN,
    _DecisionExperimentBase,
)


class _SerializationMixin(_DecisionExperimentBase):
    """Serialize runs and derive dashboard review/apply metadata."""

    def _serialize_run(self, run: DecisionExperimentRun) -> dict[str, Any]:
        """Serialize one run row into the API response shape."""
        latest_evaluation = self._load_json(
            run.latest_evaluation, fallback=None, context=LATEST_EVALUATION_COLUMN
        )
        if latest_evaluation == {}:
            latest_evaluation = None
        notes = str(run.notes or "").strip() or None
        supported_apply_types = self._supported_apply_types(run)
        applied_apply_types = self._applied_apply_types(run)
        application_status, application_detail = self._resolve_application_state(
            run,
            supported_apply_types=supported_apply_types,
            applied_apply_types=applied_apply_types,
        )
        return {
            "id": int(run.id),
            "operator_id": int(run.operator_id),
            "experiment_key": str(run.experiment_key),
            "recommendation_key": str(run.recommendation_key),
            "status": str(run.status or "planned"),
            "outcome": str(run.outcome) if run.outcome else None,
            "priority_rank": int(run.priority_rank or 1),
            "title": str(run.title or ""),
            "hypothesis": str(run.hypothesis or ""),
            "suggested_change": str(run.suggested_change or ""),
            "target_metric": str(run.target_metric or ""),
            "expected_direction": str(run.expected_direction or "increase"),
            "success_criteria": str(run.success_criteria or ""),
            "guardrail_metric": str(run.guardrail_metric or ""),
            "minimum_decision_sample": int(run.minimum_decision_sample or 1),
            "duration_days": int(run.duration_days or 14),
            "baseline_days": int(run.baseline_days or 14),
            "rollback_trigger": str(run.rollback_trigger or ""),
            "notes": notes,
            "started_at": ensure_utc(run.started_at),
            "ended_at": ensure_utc(run.ended_at) if run.ended_at is not None else None,
            "last_evaluated_at": ensure_utc(run.last_evaluated_at) if run.last_evaluated_at is not None else None,
            "created_at": ensure_utc(run.created_at),
            "updated_at": ensure_utc(run.updated_at),
            "latest_evaluation": latest_evaluation,
            "supported_apply_types": supported_apply_types,
            "applied_apply_types": applied_apply_types,
            "application_status": application_status,
            "application_detail": application_detail,
            "application_history": self._application_history(run),
            "review_bucket": self._review_bucket(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "review_priority": self._review_priority(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "review_reason": self._review_reason(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "next_actions": self._build_run_actions(
                run,
                application_status=application_status,
                supported_apply_types=supported_apply_types,
                applied_apply_types=applied_apply_types,
            ),
        }

    def _supported_apply_types(self, run: DecisionExperimentRun) -> list[str]:
        """Return apply mechanisms supported by this experiment key."""
        experiment_key = str(run.experiment_key or "")
        supported: list[str] = []
        if experiment_key in self.THRESHOLD_EXPERIMENT_KEYS:
            supported.append("thresholds")
        if experiment_key in self.STRATEGY_EXPERIMENT_KEYS:
            supported.append("strategy")
        return supported

    def _applied_apply_types(self, run: DecisionExperimentRun) -> list[str]:
        """Return apply mechanisms already written into the run notes."""
        applied: list[str] = []
        if self._run_has_applied_thresholds(run):
            applied.append("thresholds")
        if self._run_has_applied_strategy(run):
            applied.append("strategy")
        return applied

    def _resolve_application_state(
        self,
        run: DecisionExperimentRun,
        *,
        supported_apply_types: list[str],
        applied_apply_types: list[str],
    ) -> tuple[str, str]:
        """Resolve a compact dashboard status for applying experiment output."""
        if not supported_apply_types:
            return "not_supported", "이 실험 유형은 아직 자동 적용 대상이 아닙니다."

        supported_set = set(supported_apply_types)
        applied_set = set(applied_apply_types)
        if supported_set and supported_set.issubset(applied_set):
            return "applied", "지원되는 적용 작업이 모두 완료되었습니다."
        if applied_set:
            return "partially_applied", "일부 적용 작업은 완료되었고 남은 적용 작업이 있습니다."

        outcome = str(run.outcome or "")
        status = str(run.status or "planned")
        if outcome == "success":
            return "ready", "성공한 실험으로 운영 전략에 적용할 수 있습니다."
        if status == "rolled_back" or outcome in {"rollback", "inconclusive"}:
            return "blocked", "롤백 또는 결론 없음 상태라 기본 적용은 차단됩니다. 필요한 경우 force 적용만 가능합니다."
        return "not_ready", "성공 outcome이 확정되기 전까지 운영 전략 적용은 대기 상태입니다."

    def _application_history(self, run: DecisionExperimentRun) -> list[dict[str, str]]:
        """Extract audit-friendly application notes from the free-form run notes."""
        history: list[dict[str, str]] = []
        for note_line in str(run.notes or "").splitlines():
            note = note_line.strip()
            if not note:
                continue
            if self.THRESHOLD_APPLICATION_PREFIX in note:
                history.append({"apply_type": "thresholds", "note": note})
            if self.STRATEGY_APPLICATION_PREFIX in note:
                history.append({"apply_type": "strategy", "note": note})
        return history

    def _build_run_actions(
        self,
        run: DecisionExperimentRun,
        *,
        application_status: str,
        supported_apply_types: list[str],
        applied_apply_types: list[str],
    ) -> list[dict[str, Any]]:
        """Return dashboard-ready action metadata for one experiment run."""
        run_id = int(run.id)
        status = str(run.status or "planned")
        applied_set = set(applied_apply_types)
        actions = [
            {
                "action": "evaluate",
                "label": "Re-evaluate experiment",
                "method": "POST",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}/evaluate",
                "enabled": status != "rolled_back",
                "reason": "현재 baseline 대비 실험 성과를 다시 계산합니다." if status != "rolled_back" else "롤백된 실험은 재평가보다 새 실험 생성이 안전합니다.",
                "payload": {},
                "dry_run_supported": False,
                "force_supported": False,
            },
            {
                "action": "mark_success",
                "label": "Mark as successful",
                "method": "PATCH",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}",
                "enabled": status != "rolled_back" and str(run.outcome or "") != "success",
                "reason": "운영자가 실험 성공을 확정합니다." if status != "rolled_back" else "롤백된 실험은 성공 처리할 수 없습니다.",
                "payload": {"status": "completed", "outcome": "success"},
                "dry_run_supported": False,
                "force_supported": False,
            },
            {
                "action": "rollback",
                "label": "Rollback experiment",
                "method": "PATCH",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}",
                "enabled": status != "rolled_back",
                "reason": "실험을 롤백 상태로 전환합니다." if status != "rolled_back" else "이미 롤백된 실험입니다.",
                "payload": {"status": "rolled_back"},
                "dry_run_supported": False,
                "force_supported": False,
            },
        ]

        if "thresholds" in supported_apply_types:
            already_applied = "thresholds" in applied_set
            enabled = application_status == "ready" and not already_applied
            actions.append(
                {
                    "action": "apply_thresholds",
                    "label": "Apply threshold adjustment",
                    "method": "POST",
                    "path": f"/api/v1/analytics/decision-experiments/{run_id}/apply-thresholds",
                    "enabled": enabled,
                    "reason": self._apply_action_reason(run, already_applied=already_applied, enabled=enabled),
                    "payload": {"dry_run": False, "force": False},
                    "dry_run_supported": True,
                    "force_supported": True,
                }
            )

        if "strategy" in supported_apply_types:
            already_applied = "strategy" in applied_set
            enabled = application_status == "ready" and not already_applied
            actions.append(
                {
                    "action": "apply_strategy",
                    "label": "Apply strategy tuning",
                    "method": "POST",
                    "path": f"/api/v1/analytics/decision-experiments/{run_id}/apply-strategy",
                    "enabled": enabled,
                    "reason": self._apply_action_reason(run, already_applied=already_applied, enabled=enabled),
                    "payload": {"dry_run": False, "force": False},
                    "dry_run_supported": True,
                    "force_supported": True,
                }
            )

        return actions

    def _sort_serialized_runs(self, runs: list[dict[str, Any]], *, sort: str) -> list[dict[str, Any]]:
        """Sort runs for dashboard review queues."""
        normalized_sort = self._normalize_run_sort(sort)
        if normalized_sort == "created_desc":
            return sorted(runs, key=lambda item: (self._datetime_sort_key(item.get("created_at")), int(item["id"])), reverse=True)
        if normalized_sort == "created_asc":
            return sorted(runs, key=lambda item: (self._datetime_sort_key(item.get("created_at")), int(item["id"])))
        if normalized_sort == "priority":
            return sorted(
                runs,
                key=lambda item: (
                    int(item.get("priority_rank") or 999),
                    -self._datetime_sort_key(item.get("created_at")),
                    -int(item["id"]),
                ),
            )
        if normalized_sort == "last_evaluated_desc":
            return sorted(
                runs,
                key=lambda item: (
                    self._datetime_sort_key(item.get("last_evaluated_at")),
                    self._datetime_sort_key(item.get("updated_at")),
                    int(item["id"]),
                ),
                reverse=True,
            )
        if normalized_sort == "application":
            return sorted(
                runs,
                key=lambda item: (
                    self._application_status_rank(str(item.get("application_status") or "")),
                    int(item.get("priority_rank") or 999),
                    -self._datetime_sort_key(item.get("updated_at")),
                    -int(item["id"]),
                ),
            )
        return sorted(
            runs,
            key=lambda item: (
                int(item.get("review_priority") or 999),
                int(item.get("priority_rank") or 999),
                -self._datetime_sort_key(item.get("updated_at")),
                -int(item["id"]),
            ),
        )

    def _normalize_run_sort(self, sort: str) -> str:
        """Normalize supported experiment list sort modes."""
        normalized = str(sort or "needs_attention").strip().lower()
        supported = {
            "needs_attention",
            "created_desc",
            "created_asc",
            "priority",
            "last_evaluated_desc",
            "application",
        }
        return normalized if normalized in supported else "needs_attention"

    def _review_bucket(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> str:
        """Resolve a coarse dashboard queue bucket for one run."""
        if application_status == "ready":
            return "ready_to_apply"
        if application_status == "partially_applied":
            return "partially_applied"
        if application_status == "applied":
            return "applied"
        if application_status == "blocked":
            return "blocked"
        if application_status == "not_supported":
            return "unsupported"

        status = str(run.status or "planned")
        outcome = str(run.outcome or "")
        recommended_action = str((latest_evaluation or {}).get("recommended_action") or "")
        if status == "failed":
            return "failed"
        if outcome in {"insufficient_data", "watch"} or recommended_action in {"collect_more_data", "continue"}:
            return "collecting_data"
        if status in {"running", "completed"}:
            return "needs_evaluation"
        return "scheduled"

    def _review_priority(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> int:
        """Return a stable low-is-urgent priority for dashboard sorting."""
        bucket = self._review_bucket(run, latest_evaluation=latest_evaluation, application_status=application_status)
        bucket_rank = {
            "ready_to_apply": 10,
            "blocked": 20,
            "failed": 25,
            "needs_evaluation": 30,
            "collecting_data": 40,
            "partially_applied": 50,
            "scheduled": 60,
            "applied": 70,
            "unsupported": 80,
        }
        return bucket_rank.get(bucket, 90)

    def _review_reason(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> str:
        """Explain why a run is in its dashboard review bucket."""
        bucket = self._review_bucket(run, latest_evaluation=latest_evaluation, application_status=application_status)
        if bucket == "ready_to_apply":
            return "성공 outcome이 확정되어 운영 설정 적용 검토가 필요합니다."
        if bucket == "blocked":
            return "롤백 또는 결론 없음 상태라 적용이 차단되었습니다."
        if bucket == "failed":
            return "실험 실행 또는 평가가 실패했습니다. 오류 원인 확인이 필요합니다."
        if bucket == "needs_evaluation":
            return "실험이 진행 또는 완료 상태지만 최신 평가가 없습니다."
        if bucket == "collecting_data":
            sample_size = int((latest_evaluation or {}).get("sample_size") or 0)
            minimum_sample = int(run.minimum_decision_sample or 1)
            return f"현재 표본 {sample_size}건으로 최소 기준 {minimum_sample}건까지 더 관찰해야 합니다."
        if bucket == "partially_applied":
            return "일부 적용은 끝났고 남은 적용 작업 검토가 필요합니다."
        if bucket == "scheduled":
            return "아직 시작 예정인 실험입니다."
        if bucket == "applied":
            return "지원되는 적용 작업이 완료되어 감사 이력만 확인하면 됩니다."
        return "자동 적용 대상이 아니므로 수동 검토 대상입니다."

    def _application_status_rank(self, application_status: str) -> int:
        """Return stable ordering for application-oriented sorting."""
        ranks = {
            "ready": 10,
            "partially_applied": 20,
            "blocked": 30,
            "not_ready": 40,
            "applied": 50,
            "not_supported": 60,
        }
        return ranks.get(application_status, 90)

    def _count_by_key(self, runs: list[dict[str, Any]], key: str, *, missing: str = "unknown") -> dict[str, int]:
        """Count runs by one serialized field for dashboard chips."""
        counts: dict[str, int] = {}
        for run in runs:
            value = run.get(key)
            label = str(value if value is not None else missing)
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    def _datetime_sort_key(self, value: Any) -> float:
        """Return a comparable timestamp for optional datetime-like values."""
        if value is None:
            return 0.0
        if isinstance(value, datetime):
            return float(value.timestamp())
        try:
            return float(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return 0.0

    def _apply_action_reason(self, run: DecisionExperimentRun, *, already_applied: bool, enabled: bool) -> str:
        """Return a concise explanation for an apply action's enabled state."""
        if already_applied:
            return "이미 적용된 실험입니다. 재적용하려면 force가 필요합니다."
        if enabled:
            return "성공 outcome이 확정되어 적용할 수 있습니다."
        if str(run.outcome or "") != "success":
            return "성공 outcome이 확정되기 전까지 적용할 수 없습니다."
        return "현재 상태에서는 적용할 수 없습니다."
