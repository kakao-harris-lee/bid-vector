"""Threshold and strategy application: policy scaling, updates, and notes.

Converts a successful experiment into concrete operator-strategy changes:
reads current thresholds/tuning, derives a history-scaled parameter delta
policy, builds the threshold/strategy update proposals, persists them onto the
strategy row, and writes the audit-friendly application notes. Method bodies
are the original ``DecisionExperimentService`` methods, moved verbatim.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import DecisionExperimentRun
from app.services.decision_experiments.base import (
    BASELINE_SUMMARY_COLUMN,
    LATEST_EVALUATION_COLUMN,
    _DecisionExperimentBase,
)
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    clamp_category_priority_override,
    dump_category_priority_overrides,
    get_strategy_auto_workload_penalty_multiplier,
    get_strategy_category_priority_overrides,
)


class _ApplicationMixin(_DecisionExperimentBase):
    """Translate experiment outcomes into persisted strategy threshold/tuning changes."""

    def _current_strategy_thresholds(self, strategy) -> dict[str, float]:
        """Serialize persisted operator decision thresholds into a stable snapshot."""
        bid_now_threshold = max(0.0, min(1.0, float(getattr(strategy, "bid_now_threshold", 0.7) or 0.7)))
        review_threshold = max(0.0, min(1.0, float(getattr(strategy, "review_threshold", 0.45) or 0.45)))
        if review_threshold > bid_now_threshold:
            review_threshold = bid_now_threshold
        return {
            "bid_now_threshold": round(bid_now_threshold, 4),
            "review_threshold": round(review_threshold, 4),
        }

    def _build_run_parameter_policy(self, db: Session, run: DecisionExperimentRun) -> dict[str, Any]:
        """Summarize prior same-recommendation outcomes into a concrete parameter delta policy."""
        recommendation_key = str(run.recommendation_key or "").strip()
        if not recommendation_key:
            adjustment = self.analytics._build_history_adjustment(None)
            return self._parameter_policy_from_adjustment(adjustment, history_summary=None)

        date_from = utc_now() - timedelta(days=self.PARAMETER_HISTORY_LOOKBACK_DAYS)
        prior_runs = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.operator_id == int(run.operator_id),
                DecisionExperimentRun.recommendation_key == recommendation_key,
                DecisionExperimentRun.id != int(run.id),
                DecisionExperimentRun.updated_at >= date_from,
            )
            .order_by(DecisionExperimentRun.updated_at.desc(), DecisionExperimentRun.id.desc())
            .all()
        )
        history_summary = (
            self.analytics._summarize_experiment_runs_for_recommendation(recommendation_key, prior_runs)
            if prior_runs
            else None
        )
        adjustment = self.analytics._build_history_adjustment(history_summary)
        return self._parameter_policy_from_adjustment(adjustment, history_summary=history_summary)

    def _parameter_policy_from_adjustment(
        self,
        adjustment: dict[str, Any],
        *,
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Convert a history adjustment into an apply-time parameter delta scale."""
        multiplier = self.analytics._parameter_delta_multiplier(adjustment)
        return {
            "status": str(adjustment.get("status") or "neutral"),
            "multiplier": round(multiplier, 4),
            "reason": str(adjustment.get("reason") or ""),
            "history_summary": history_summary or {},
            "history_adjustment": adjustment,
        }

    def _policy_delta(
        self,
        experiment_key: str,
        *,
        parameter_policy: dict[str, Any],
        base_delta: float | None = None,
        min_delta: float | None = None,
        max_delta: float | None = None,
    ) -> float:
        """Return the concrete parameter delta for one experiment after history scaling."""
        if base_delta is None or min_delta is None or max_delta is None:
            base_delta, min_delta, max_delta = self.THRESHOLD_PARAMETER_DELTAS.get(
                experiment_key,
                (0.03, 0.015, 0.05),
            )
        multiplier = float(parameter_policy.get("multiplier") or 1.0)
        return round(max(float(min_delta), min(float(base_delta) * multiplier, float(max_delta))), 4)

    def _parameter_rationale(self, base_rationale: str, *, parameter_policy: dict[str, Any]) -> str:
        """Append history-policy context to an apply recommendation rationale."""
        reason = str(parameter_policy.get("reason") or "").strip()
        status = str(parameter_policy.get("status") or "neutral")
        multiplier = float(parameter_policy.get("multiplier") or 1.0)
        return (
            f"{base_rationale} 장기 적용 이력 상태는 {status}이며 "
            f"추천 변화 폭 배율 {multiplier:.2f}를 적용했습니다. {reason}"
        ).strip()

    def _build_threshold_adjustments(
        self,
        run: DecisionExperimentRun,
        *,
        current_thresholds: dict[str, float],
        parameter_policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Translate supported experiment keys into concrete threshold updates."""
        experiment_key = str(run.experiment_key or "")
        bid_now_threshold = float(current_thresholds["bid_now_threshold"])
        review_threshold = float(current_thresholds["review_threshold"])

        if experiment_key == "exp-review-threshold-tighten":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_review_threshold(
                review_threshold + delta,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="increase",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "review 진입 품질을 높여 낮은 전환율을 개선하도록 threshold를 상향합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        if experiment_key == "exp-review-threshold-relax":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_review_threshold(
                review_threshold - delta,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="decrease",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "review 후보 풀을 넓혀 더 많은 탐색 기회를 확보하도록 threshold를 완화합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        if experiment_key == "exp-bid-now-threshold-tighten":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_bid_now_threshold(
                bid_now_threshold + delta,
                review_threshold=review_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="bid_now_threshold",
                    label="BID_NOW_THRESHOLD",
                    direction="increase",
                    previous_value=bid_now_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "즉시 투찰 후보의 질을 높이기 위해 bid_now 승격 기준을 보수적으로 조정합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        return []

    def _threshold_update_item(
        self,
        *,
        parameter: str,
        label: str,
        direction: str,
        previous_value: float,
        suggested_value: float,
        rationale: str,
    ) -> dict[str, Any]:
        """Serialize one threshold update proposal into the API response shape."""
        return {
            "parameter": parameter,
            "label": label,
            "direction": direction,
            "previous_value": round(previous_value, 4),
            "suggested_value": round(suggested_value, 4),
            "delta": round(float(suggested_value) - float(previous_value), 4),
            "rationale": rationale,
        }

    def _apply_threshold_updates(self, strategy, threshold_updates: list[dict[str, Any]]) -> None:
        """Persist the suggested threshold values onto the operator strategy row."""
        updated_values = self._current_strategy_thresholds(strategy)
        for update in threshold_updates:
            updated_values[str(update["parameter"])] = round(float(update["suggested_value"]), 4)

        strategy.bid_now_threshold = float(updated_values["bid_now_threshold"])
        strategy.review_threshold = float(updated_values["review_threshold"])

    def _current_strategy_tuning(self, strategy) -> dict[str, Any]:
        """Serialize workload/category tuning settings into a stable snapshot."""
        return {
            "auto_workload_penalty_multiplier": get_strategy_auto_workload_penalty_multiplier(strategy),
            "category_priority_overrides": get_strategy_category_priority_overrides(strategy),
        }

    def _build_strategy_adjustments(
        self,
        run: DecisionExperimentRun,
        *,
        current_tuning: dict[str, Any],
        parameter_policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Translate supported experiment keys into concrete strategy tuning updates."""
        experiment_key = str(run.experiment_key or "")

        if experiment_key == "exp-workload-auto-calibration":
            previous_value = float(current_tuning["auto_workload_penalty_multiplier"])
            suggested_value = clamp_auto_workload_penalty_multiplier(max(0.5, previous_value - 0.15))
            return [
                {
                    "parameter": "auto_workload_penalty_multiplier",
                    "label": "AUTO_WORKLOAD_PENALTY_MULTIPLIER",
                    "direction": "decrease",
                    "previous_value": round(previous_value, 4),
                    "suggested_value": round(suggested_value, 4),
                    "delta": round(suggested_value - previous_value, 4),
                    "rationale": "자동 산정 업무부하 후보의 제출 전환율이 낮아 감점 배율을 낮추고 후보 탐색 폭을 넓힙니다.",
                }
            ]

        if experiment_key == "exp-category-focus-shift":
            source_summary = self._strategy_source_summary(run)
            best_category = self._clean_category_name(source_summary.get("best_category"))
            worst_category = self._clean_category_name(source_summary.get("worst_category"))
            if best_category is None and worst_category is None:
                return []

            previous_overrides = dict(current_tuning["category_priority_overrides"])
            suggested_overrides = dict(previous_overrides)
            changed_deltas: dict[str, float] = {}
            category_delta = self._policy_delta(
                experiment_key,
                parameter_policy=parameter_policy,
                base_delta=self.CATEGORY_PARAMETER_BASE_DELTA,
                min_delta=self.CATEGORY_PARAMETER_MIN_DELTA,
                max_delta=self.CATEGORY_PARAMETER_MAX_DELTA,
            )

            if best_category is not None:
                previous_best_value = self._category_override_for(previous_overrides, best_category)
                suggested_best_value = clamp_category_priority_override(previous_best_value + category_delta)
                suggested_overrides[best_category] = suggested_best_value
                changed_deltas[best_category] = round(suggested_best_value - previous_best_value, 4)

            if worst_category is not None and worst_category.lower() != str(best_category or "").lower():
                previous_worst_value = self._category_override_for(previous_overrides, worst_category)
                suggested_worst_value = clamp_category_priority_override(previous_worst_value - category_delta)
                suggested_overrides[worst_category] = suggested_worst_value
                changed_deltas[worst_category] = round(suggested_worst_value - previous_worst_value, 4)

            return [
                {
                    "parameter": "category_priority_overrides",
                    "label": "CATEGORY_PRIORITY_OVERRIDES",
                    "direction": "replace",
                    "previous_value": previous_overrides,
                    "suggested_value": suggested_overrides,
                    "delta": changed_deltas,
                    "rationale": self._parameter_rationale(
                        "제출 전환이 좋은 카테고리는 우선순위를 높이고 저조한 카테고리는 보수적으로 평가합니다.",
                        parameter_policy=parameter_policy,
                    ),
                }
            ]

        return []

    def _apply_strategy_updates(self, strategy, strategy_updates: list[dict[str, Any]]) -> None:
        """Persist suggested workload/category tuning values onto the operator strategy row."""
        for update in strategy_updates:
            parameter = str(update["parameter"])
            if parameter == "auto_workload_penalty_multiplier":
                strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(
                    update["suggested_value"]
                )
            elif parameter == "category_priority_overrides":
                strategy.category_priority_overrides = dump_category_priority_overrides(
                    update["suggested_value"]
                )

    def _strategy_source_summary(self, run: DecisionExperimentRun) -> dict[str, Any]:
        """Choose the best available metrics snapshot for strategy application."""
        latest_evaluation = self._load_json(
            run.latest_evaluation, fallback={}, context=LATEST_EVALUATION_COLUMN
        )
        if isinstance(latest_evaluation, dict):
            current_summary = latest_evaluation.get("current_summary")
            if isinstance(current_summary, dict):
                return current_summary

        baseline_summary = self._load_json(
            run.baseline_summary, fallback={}, context=BASELINE_SUMMARY_COLUMN
        )
        return baseline_summary if isinstance(baseline_summary, dict) else {}

    def _clean_category_name(self, raw_value: Any) -> str | None:
        """Normalize category labels from experiment snapshots."""
        category = str(raw_value or "").strip()
        return category or None

    def _category_override_for(self, overrides: dict[str, float], category: str) -> float:
        """Read an existing category override using case-insensitive matching."""
        normalized_category = category.strip().lower()
        for key, value in overrides.items():
            if key.strip().lower() == normalized_category:
                return float(value)
        return 0.0

    def _bounded_review_threshold(self, proposed_value: float, *, bid_now_threshold: float) -> float:
        """Keep review threshold inside a sane range below the bid-now threshold."""
        upper_bound = max(0.0, min(1.0, float(bid_now_threshold) - 0.01))
        return round(max(0.0, min(float(proposed_value), upper_bound)), 4)

    def _bounded_bid_now_threshold(self, proposed_value: float, *, review_threshold: float) -> float:
        """Keep bid-now threshold above the review threshold while staying in unit range."""
        lower_bound = max(0.0, min(1.0, float(review_threshold) + 0.01))
        return round(min(1.0, max(float(proposed_value), lower_bound)), 4)

    def _run_has_applied_thresholds(self, run: DecisionExperimentRun) -> bool:
        """Return whether this experiment run already wrote threshold updates into its notes."""
        return self.THRESHOLD_APPLICATION_PREFIX in str(run.notes or "")

    def _run_has_applied_strategy(self, run: DecisionExperimentRun) -> bool:
        """Return whether this experiment run already wrote strategy tuning updates into its notes."""
        return self.STRATEGY_APPLICATION_PREFIX in str(run.notes or "")

    def _build_threshold_application_note(
        self,
        threshold_updates: list[dict[str, Any]],
        *,
        append_note: str | None,
    ) -> str:
        """Build one audit-friendly notes line for applied threshold changes."""
        summary = ", ".join(
            f"{item['label']} {item['previous_value']:.4f}→{item['suggested_value']:.4f}"
            for item in threshold_updates
        )
        base_note = f"{self.THRESHOLD_APPLICATION_PREFIX} {summary}"
        extra_note = str(append_note or "").strip()
        if extra_note:
            return f"{base_note} | {extra_note}"
        return base_note

    def _build_strategy_application_note(
        self,
        strategy_updates: list[dict[str, Any]],
        *,
        append_note: str | None,
    ) -> str:
        """Build one audit-friendly notes line for applied strategy tuning changes."""
        summary_parts: list[str] = []
        for item in strategy_updates:
            if item["parameter"] == "auto_workload_penalty_multiplier":
                summary_parts.append(
                    f"{item['label']} {float(item['previous_value']):.4f}→{float(item['suggested_value']):.4f}"
                )
            elif item["parameter"] == "category_priority_overrides":
                changed = ", ".join(
                    f"{category} {delta:+.4f}"
                    for category, delta in dict(item.get("delta") or {}).items()
                )
                summary_parts.append(f"{item['label']} {changed or 'no-op'}")

        base_note = f"{self.STRATEGY_APPLICATION_PREFIX} {', '.join(summary_parts)}"
        extra_note = str(append_note or "").strip()
        if extra_note:
            return f"{base_note} | {extra_note}"
        return base_note
