"""Experiment-history ranking and concrete parameter-delta recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import DecisionExperimentRun
from app.services.decision_analytics.base import _DecisionAnalyticsBase


@dataclass(frozen=True)
class _HistoryAdjustmentCounts:
    """Experiment-history counts consumed by the priority-adjustment rules."""

    success_count: int
    negative_count: int
    pending_count: int
    applied_count: int


@dataclass(frozen=True)
class _HistoryAdjustmentRule:
    """One first-match priority rule: a predicate plus its declarative outcome."""

    predicate: Callable[[_HistoryAdjustmentCounts], bool]
    status: str
    priority_delta: float
    reason: str


_HISTORY_ADJUSTMENT_RULES: tuple[_HistoryAdjustmentRule, ...] = (
    _HistoryAdjustmentRule(
        predicate=lambda c: c.applied_count > 0 and c.success_count > 0,
        status="promoted",
        priority_delta=14.0,
        reason="성공 후 운영 전략에 적용된 실험 이력이 있어 후속 실험 우선순위를 높였습니다.",
    ),
    _HistoryAdjustmentRule(
        predicate=lambda c: c.negative_count >= 2 and c.success_count == 0,
        status="deprioritized",
        priority_delta=-55.0,
        reason="반복 실패 또는 롤백 이력이 있어 같은 유형의 실험을 뒤로 미뤘습니다.",
    ),
    _HistoryAdjustmentRule(
        predicate=lambda c: c.negative_count > c.success_count,
        status="deprioritized",
        priority_delta=-35.0,
        reason="실패/롤백 이력이 성공 이력보다 많아 우선순위를 낮췄습니다.",
    ),
    _HistoryAdjustmentRule(
        predicate=lambda c: c.pending_count >= 2 and c.success_count == 0,
        status="deprioritized",
        priority_delta=-20.0,
        reason="보류 또는 표본 부족 이력이 반복되어 추가 추천 강도를 낮췄습니다.",
    ),
    _HistoryAdjustmentRule(
        predicate=lambda c: c.success_count > 0,
        status="promoted",
        priority_delta=8.0,
        reason="성공한 실험 이력이 있어 같은 계열의 후속 실험 신뢰도를 높였습니다.",
    ),
    _HistoryAdjustmentRule(
        predicate=lambda c: True,
        status="neutral",
        priority_delta=0.0,
        reason="이력상 우선순위를 조정할 충분한 신호가 없습니다.",
    ),
)


class _ExperimentHistoryMixin(_DecisionAnalyticsBase):
    """Prior-run outcome summaries and history-adjusted parameter suggestions."""

    def _build_recommendation_experiment_history(
        self,
        db: Session,
        *,
        operator_id: int,
        days: int,
    ) -> dict[str, Any]:
        """Summarize prior experiment outcomes for recommendation ranking."""
        lookback_days = max(self.EXPERIMENT_HISTORY_MIN_LOOKBACK_DAYS, int(days) * 6)
        lookback_days = min(self.EXPERIMENT_HISTORY_MAX_LOOKBACK_DAYS, lookback_days)
        date_from = utc_now() - timedelta(days=lookback_days)
        runs = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.operator_id == operator_id,
                DecisionExperimentRun.updated_at >= date_from,
            )
            .order_by(DecisionExperimentRun.updated_at.desc(), DecisionExperimentRun.id.desc())
            .all()
        )
        by_recommendation: dict[str, list[DecisionExperimentRun]] = {}
        for run in runs:
            key = str(run.recommendation_key or "").strip()
            if not key:
                continue
            by_recommendation.setdefault(key, []).append(run)

        recommendation_summaries = {
            key: self._summarize_experiment_runs_for_recommendation(key, key_runs)
            for key, key_runs in sorted(by_recommendation.items())
        }
        return {
            "operator_id": operator_id,
            "lookback_days": lookback_days,
            "run_count": len(runs),
            "evaluated_count": sum(1 for run in runs if str(run.outcome or "") in {"success", "rollback", "inconclusive"}),
            "success_count": sum(1 for run in runs if str(run.outcome or "") == "success"),
            "rollback_count": sum(1 for run in runs if str(run.outcome or "") == "rollback"),
            "inconclusive_count": sum(1 for run in runs if str(run.outcome or "") == "inconclusive"),
            "pending_count": sum(1 for run in runs if str(run.outcome or "") in {"", "watch", "insufficient_data"}),
            "failed_count": sum(1 for run in runs if str(run.status or "") == "failed"),
            "applied_count": sum(1 for run in runs if self._experiment_run_applied(run)),
            "recommendation_summaries": recommendation_summaries,
        }

    def _apply_experiment_history_to_recommendations(
        self,
        recommendations: list[dict[str, Any]],
        *,
        experiment_history: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Attach prior-run context and sort recommendations by adjusted priority."""
        summaries = experiment_history.get("recommendation_summaries")
        if not isinstance(summaries, dict):
            summaries = {}

        enriched: list[dict[str, Any]] = []
        for original_index, recommendation in enumerate(recommendations):
            key = str(recommendation.get("key") or "")
            history_summary = summaries.get(key) if isinstance(summaries.get(key), dict) else None
            base_score = self._base_recommendation_priority_score(recommendation, original_index=original_index)
            adjustment = self._build_history_adjustment(history_summary)
            priority_score = max(0.0, round(base_score + float(adjustment["priority_delta"]), 2))
            adjusted_recommendation = {
                **recommendation,
                "severity": self._adjust_recommendation_severity(
                    str(recommendation.get("severity") or "info"),
                    adjustment=adjustment,
                ),
                "priority_score": priority_score,
                "history_adjustment": adjustment,
                "_original_index": original_index,
            }
            adjusted_recommendation["supporting_metrics"] = {
                **(recommendation.get("supporting_metrics") or {}),
                "experiment_history": history_summary or {},
            }
            adjusted_recommendation = self._attach_parameter_recommendation(
                adjusted_recommendation,
                adjustment=adjustment,
                history_summary=history_summary,
            )
            enriched.append(adjusted_recommendation)

        ordered = sorted(
            enriched,
            key=lambda item: (
                -float(item.get("priority_score") or 0.0),
                int(item.get("_original_index", 0)),
                str(item.get("key") or ""),
            ),
        )
        for item in ordered:
            item.pop("_original_index", None)
        return ordered

    def _with_default_recommendation_priority(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        """Attach neutral ranking metadata to fallback recommendations."""
        return {
            **recommendation,
            "priority_score": self._base_recommendation_priority_score(recommendation, original_index=0),
            "history_adjustment": self._build_history_adjustment(None),
            "parameter_recommendation": {},
        }

    def _summarize_experiment_runs_for_recommendation(
        self,
        recommendation_key: str,
        runs: list[DecisionExperimentRun],
    ) -> dict[str, Any]:
        """Build one recommendation-key experiment history summary."""
        success_runs = [run for run in runs if str(run.outcome or "") == "success"]
        rollback_runs = [run for run in runs if str(run.outcome or "") == "rollback"]
        failed_runs = [run for run in runs if str(run.status or "") == "failed"]
        pending_runs = [run for run in runs if str(run.outcome or "") in {"", "watch", "insufficient_data"}]
        applied_runs = [run for run in runs if self._experiment_run_applied(run)]
        evaluated_count = len(success_runs) + len(rollback_runs) + sum(
            1 for run in runs if str(run.outcome or "") == "inconclusive"
        )
        latest_run = max(runs, key=lambda run: (run.updated_at, int(run.id or 0))) if runs else None
        return {
            "recommendation_key": recommendation_key,
            "run_count": len(runs),
            "evaluated_count": evaluated_count,
            "success_count": len(success_runs),
            "rollback_count": len(rollback_runs),
            "failed_count": len(failed_runs),
            "pending_count": len(pending_runs),
            "applied_count": len(applied_runs),
            "success_rate": self._rate(len(success_runs), evaluated_count),
            "latest_run_id": int(latest_run.id) if latest_run is not None else None,
            "latest_status": str(latest_run.status or "") if latest_run is not None else None,
            "latest_outcome": str(latest_run.outcome) if latest_run is not None and latest_run.outcome else None,
            "latest_updated_at": latest_run.updated_at if latest_run is not None else None,
        }

    def _build_history_adjustment(self, history_summary: dict[str, Any] | None) -> dict[str, Any]:
        """Translate experiment history into a priority adjustment."""
        if not history_summary:
            return {
                "status": "neutral",
                "priority_delta": 0.0,
                "reason": "아직 이 추천 유형의 실험 이력이 없습니다.",
                "recent_run_count": 0,
                "success_count": 0,
                "rollback_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "applied_count": 0,
            }

        run_count = int(history_summary.get("run_count") or 0)
        success_count = int(history_summary.get("success_count") or 0)
        rollback_count = int(history_summary.get("rollback_count") or 0)
        failed_count = int(history_summary.get("failed_count") or 0)
        pending_count = int(history_summary.get("pending_count") or 0)
        applied_count = int(history_summary.get("applied_count") or 0)
        negative_count = rollback_count + failed_count
        counts = _HistoryAdjustmentCounts(
            success_count=success_count,
            negative_count=negative_count,
            pending_count=pending_count,
            applied_count=applied_count,
        )
        rule = next(rule for rule in _HISTORY_ADJUSTMENT_RULES if rule.predicate(counts))
        status = rule.status
        priority_delta = rule.priority_delta
        reason = rule.reason

        return {
            "status": status,
            "priority_delta": priority_delta,
            "reason": reason,
            "recent_run_count": run_count,
            "success_count": success_count,
            "rollback_count": rollback_count,
            "failed_count": failed_count,
            "pending_count": pending_count,
            "applied_count": applied_count,
        }

    def _base_recommendation_priority_score(self, recommendation: dict[str, Any], *, original_index: int) -> float:
        """Return a stable base score before historical adjustment."""
        severity_score = {
            "action": 100.0,
            "watch": 70.0,
            "info": 40.0,
        }.get(str(recommendation.get("severity") or "info"), 40.0)
        return severity_score - (float(original_index) * 0.01)

    def _adjust_recommendation_severity(self, severity: str, *, adjustment: dict[str, Any]) -> str:
        """Downgrade visible urgency for heavily penalized recommendations."""
        if adjustment.get("status") != "deprioritized":
            return severity if severity in {"info", "watch", "action"} else "info"
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        if priority_delta <= -45.0 and severity == "action":
            return "watch"
        if priority_delta <= -45.0 and severity == "watch":
            return "info"
        return severity if severity in {"info", "watch", "action"} else "info"

    def _attach_parameter_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        adjustment: dict[str, Any],
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Attach concrete parameter deltas adjusted by long-running experiment history."""
        parameter_recommendation = self._build_parameter_recommendation(
            recommendation,
            adjustment=adjustment,
            history_summary=history_summary,
        )
        if not parameter_recommendation:
            return {
                **recommendation,
                "parameter_recommendation": {},
            }

        supporting_metrics = {
            **(recommendation.get("supporting_metrics") or {}),
            "parameter_recommendation": parameter_recommendation,
        }
        return {
            **recommendation,
            "suggested_adjustment": self._render_parameter_adjustment(
                recommendation,
                parameter_recommendation=parameter_recommendation,
            ),
            "supporting_metrics": supporting_metrics,
            "parameter_recommendation": parameter_recommendation,
        }

    def _build_parameter_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        adjustment: dict[str, Any],
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build concrete threshold/category parameter suggestions from historical outcomes."""
        key = str(recommendation.get("key") or "")
        multiplier = self._parameter_delta_multiplier(adjustment)
        confidence = self._parameter_confidence(adjustment)
        history_counts = self._parameter_history_counts(history_summary)
        history_reason = str(adjustment.get("reason") or "")

        threshold_spec = self.THRESHOLD_PARAMETER_DELTAS.get(key)
        if threshold_spec is not None:
            base_delta = float(threshold_spec["base_delta"])
            recommended_delta = self._bounded_parameter_delta(
                base_delta * multiplier,
                min_delta=float(threshold_spec["min_delta"]),
                max_delta=float(threshold_spec["max_delta"]),
            )
            return {
                "type": "threshold",
                "parameter": threshold_spec["parameter"],
                "label": threshold_spec["label"],
                "direction": threshold_spec["direction"],
                "base_delta": base_delta,
                "recommended_delta": recommended_delta,
                "min_delta": float(threshold_spec["min_delta"]),
                "max_delta": float(threshold_spec["max_delta"]),
                "delta_multiplier": round(multiplier, 4),
                "confidence": confidence,
                "history_status": str(adjustment.get("status") or "neutral"),
                "history_reason": history_reason,
                "history_counts": history_counts,
            }

        if key == "category-focus-shift":
            metrics = recommendation.get("supporting_metrics") or {}
            base_delta = self.CATEGORY_PARAMETER_BASE_DELTA
            recommended_delta = self._bounded_parameter_delta(
                base_delta * multiplier,
                min_delta=self.CATEGORY_PARAMETER_MIN_DELTA,
                max_delta=self.CATEGORY_PARAMETER_MAX_DELTA,
            )
            return {
                "type": "category_priority",
                "parameter": "category_priority_overrides",
                "label": "CATEGORY_PRIORITY_OVERRIDES",
                "direction": "replace",
                "base_delta": base_delta,
                "best_category": metrics.get("best_category"),
                "worst_category": metrics.get("worst_category"),
                "best_category_delta": recommended_delta,
                "worst_category_delta": -recommended_delta,
                "min_delta": self.CATEGORY_PARAMETER_MIN_DELTA,
                "max_delta": self.CATEGORY_PARAMETER_MAX_DELTA,
                "delta_multiplier": round(multiplier, 4),
                "confidence": confidence,
                "history_status": str(adjustment.get("status") or "neutral"),
                "history_reason": history_reason,
                "history_counts": history_counts,
            }

        return {}

    def _parameter_delta_multiplier(self, adjustment: dict[str, Any]) -> float:
        """Return the parameter-change scale implied by historical outcomes."""
        status = str(adjustment.get("status") or "neutral")
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        applied_count = int(adjustment.get("applied_count") or 0)
        success_count = int(adjustment.get("success_count") or 0)
        if status == "promoted" and applied_count > 0:
            return 1.25
        if status == "promoted" and success_count > 0:
            return 1.15
        if status == "deprioritized":
            if priority_delta <= -45.0:
                return 0.5
            if priority_delta <= -30.0:
                return 0.65
            return 0.75
        return 1.0

    def _parameter_confidence(self, adjustment: dict[str, Any]) -> float:
        """Return a compact confidence score for the adjusted parameter suggestion."""
        status = str(adjustment.get("status") or "neutral")
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        applied_count = int(adjustment.get("applied_count") or 0)
        if status == "promoted" and applied_count > 0:
            return 0.78
        if status == "promoted":
            return 0.68
        if status == "deprioritized" and priority_delta <= -45.0:
            return 0.35
        if status == "deprioritized":
            return 0.42
        return 0.55

    def _parameter_history_counts(self, history_summary: dict[str, Any] | None) -> dict[str, int]:
        """Serialize history counts used by parameter recommendation formulas."""
        if not history_summary:
            return {
                "run_count": 0,
                "success_count": 0,
                "rollback_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "applied_count": 0,
            }
        return {
            "run_count": int(history_summary.get("run_count") or 0),
            "success_count": int(history_summary.get("success_count") or 0),
            "rollback_count": int(history_summary.get("rollback_count") or 0),
            "failed_count": int(history_summary.get("failed_count") or 0),
            "pending_count": int(history_summary.get("pending_count") or 0),
            "applied_count": int(history_summary.get("applied_count") or 0),
        }

    def _bounded_parameter_delta(self, value: float, *, min_delta: float, max_delta: float) -> float:
        """Clamp parameter deltas into a small operationally safe range."""
        return round(max(float(min_delta), min(float(value), float(max_delta))), 4)

    def _render_parameter_adjustment(
        self,
        recommendation: dict[str, Any],
        *,
        parameter_recommendation: dict[str, Any],
    ) -> str:
        """Render operator-facing text from the concrete parameter recommendation."""
        parameter_type = str(parameter_recommendation.get("type") or "")
        history_reason = str(parameter_recommendation.get("history_reason") or "").strip()
        if parameter_type == "threshold":
            label = str(parameter_recommendation.get("label") or "")
            direction = str(parameter_recommendation.get("direction") or "increase")
            direction_text = "상향" if direction == "increase" else "하향"
            delta = float(parameter_recommendation.get("recommended_delta") or 0.0)
            return (
                f"`{label}`를 {delta:.3f} {direction_text}하는 실험을 권장합니다. "
                f"{history_reason}"
            ).strip()

        if parameter_type == "category_priority":
            best_category = parameter_recommendation.get("best_category") or "고성과 카테고리"
            worst_category = parameter_recommendation.get("worst_category") or "저성과 카테고리"
            best_delta = float(parameter_recommendation.get("best_category_delta") or 0.0)
            worst_delta = float(parameter_recommendation.get("worst_category_delta") or 0.0)
            return (
                f"`{best_category}` 우선순위 override를 {best_delta:+.3f}, "
                f"`{worst_category}` override를 {worst_delta:+.3f} 조정하는 실험을 권장합니다. "
                f"{history_reason}"
            ).strip()

        return str(recommendation.get("suggested_adjustment") or "")

    def _experiment_run_applied(self, run: DecisionExperimentRun) -> bool:
        """Return whether an experiment run has been applied to operator settings."""
        notes = str(run.notes or "")
        return any(marker in notes for marker in self.EXPERIMENT_APPLICATION_MARKERS)
