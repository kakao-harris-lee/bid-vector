"""Shared tuning constants, operator/run resolution, and JSON leaf helpers.

The foundation every ``DecisionExperimentService`` mixin builds on: the
experiment tuning constants, the ``analytics`` collaborator wired in
``__init__``, and the stateless leaf helpers (operator/strategy resolution,
run loading, JSON load/dump, metric predicates, note merging). Method bodies
are the original ``DecisionExperimentService`` methods, moved verbatim.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account, ensure_operator_strategy, ensure_operator_strategy_for
from app.models.models import DecisionExperimentRun, User
from app.services.decision_analytics import DecisionAnalyticsService
from app.services.stored_json_payload import load_stored_json_object

# degrade 경고에서 어느 컬럼이 해석 불가였는지 특정하는 라벨(§4.5-1 단일 출처).
BASELINE_SUMMARY_COLUMN = "decision_experiment_run.baseline_summary"
LATEST_EVALUATION_COLUMN = "decision_experiment_run.latest_evaluation"


class _DecisionExperimentBase:
    """Constants, the analytics collaborator, and stateless leaf helpers."""

    THRESHOLD_EXPERIMENT_KEYS = {
        "exp-review-threshold-tighten",
        "exp-review-threshold-relax",
        "exp-bid-now-threshold-tighten",
    }
    STRATEGY_EXPERIMENT_KEYS = {
        "exp-workload-auto-calibration",
        "exp-category-focus-shift",
    }
    RATE_SUCCESS_DELTA = 0.1
    RATE_GUARDRAIL_DROP = -0.05
    COUNT_DROP_RATIO = 0.2
    ACTIVE_PENDING_GROWTH_RATIO = 0.2
    THRESHOLD_APPLICATION_PREFIX = "Threshold 적용:"
    STRATEGY_APPLICATION_PREFIX = "Strategy 적용:"
    PARAMETER_HISTORY_LOOKBACK_DAYS = 365
    THRESHOLD_PARAMETER_DELTAS = {
        "exp-review-threshold-tighten": (0.04, 0.02, 0.06),
        "exp-review-threshold-relax": (0.03, 0.015, 0.05),
        "exp-bid-now-threshold-tighten": (0.03, 0.015, 0.05),
    }
    CATEGORY_PARAMETER_BASE_DELTA = 0.03
    CATEGORY_PARAMETER_MIN_DELTA = 0.015
    CATEGORY_PARAMETER_MAX_DELTA = 0.05

    def __init__(self) -> None:
        self.analytics = DecisionAnalyticsService()

    def _resolve_operator(self, db: Session, *, operator: User | None) -> User:
        """Resolve the target operator while preserving canonical behavior by default."""
        return operator if operator is not None else ensure_operator_account(db)

    def _resolve_strategy(self, db: Session, *, operator: User | None) -> Any:
        """Resolve the strategy row for the target operator without cross-operator fallback."""
        if operator is None:
            return ensure_operator_strategy(db)
        return ensure_operator_strategy_for(db, operator)

    def _operator_context_fields(self, operator: User) -> dict[str, Any]:
        """Return standard response fields for the resolved operator context."""
        return {
            "operator_id": int(operator.id),
            "current_operator_id": int(operator.id),
            "current_operator_username": str(operator.username or ""),
        }

    def _get_run_or_raise(
        self,
        db: Session,
        *,
        run_id: int,
        operator: User | None = None,
    ) -> DecisionExperimentRun:
        """Load one run in the target operator scope or raise a clear error."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.id == run_id,
                DecisionExperimentRun.operator_id == target_operator.id,
            )
            .first()
        )
        if run is None:
            raise ValueError(f"Decision experiment run {run_id} not found")
        return run

    def _empty_snapshot(self, window_start: datetime, window_end: datetime) -> dict[str, Any]:
        """Return an empty-but-valid snapshot shape for fallback parsing cases."""
        return {
            "window_start": window_start,
            "window_end": window_end,
            "decision_count": 0,
            "submitted_count": 0,
            "active_pending_count": 0,
            "overall_submission_rate": None,
            "workflow_submission_rate": None,
            "bid_now_submission_rate": None,
            "review_submission_rate": None,
            "auto_submission_rate": None,
            "provided_submission_rate": None,
            "best_category": None,
            "best_category_submission_rate": None,
            "worst_category": None,
            "worst_category_submission_rate": None,
        }

    def _load_json(
        self, raw_value: str | None, *, fallback: Any, context: str = ""
    ) -> Any:
        """Restore one stored snapshot/evaluation object, or the caller's fallback.

        Decoding lives in the shared restore path
        (:mod:`app.services.stored_json_payload`); the *fallback* stays a caller
        argument because the two consumers want different degrade values (an empty
        object for the dashboard row, an empty snapshot for the metric diff).

        A stored value that is valid JSON but **not an object** now falls back too
        (it used to be returned as-is, and every consumer then called ``.get()`` on
        a list).

        ``or fallback`` 로 쓰지 않는다: 정상적으로 저장된 빈 객체(``"{}"`` — 새 run 의
        ``latest_evaluation`` 초기값)는 falsy 라서 ``or`` 를 쓰면 baseline 스냅샷
        fallback 으로 바뀐다. 해석 실패(``None``)만 fallback 이다.
        """
        restored = load_stored_json_object(raw_value, context=context)
        return fallback if restored is None else restored

    def _dump_json(self, payload: Any) -> str:
        """Serialize payloads containing datetimes into JSON text for storage.

        Still ``json.dumps``: the ``default=self._json_default`` hook (datetime ->
        ``.isoformat()``) is this module's serialization contract, and pydantic
        serializes datetimes with a ``Z`` suffix instead of ``+00:00`` — routing
        this through a model would rewrite every stored snapshot string. Promoting
        it needs a key contract for the snapshot/evaluation payloads first
        (follow-up).
        """
        return json.dumps(payload, ensure_ascii=False, default=self._json_default)

    def _json_default(self, value: Any) -> str:
        """Serialize datetimes as ISO strings for JSON persistence."""
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")

    def _is_count_metric(self, metric_name: str) -> bool:
        """Return whether a metric is count-based rather than rate-based."""
        return metric_name.endswith("_count")

    def _delta(self, current_value: float | None, baseline_value: float | None) -> float | None:
        """Return a rounded metric delta when both current and baseline are known."""
        if current_value is None or baseline_value is None:
            return None
        return round(float(current_value) - float(baseline_value), 4)

    def _merge_notes(self, current_notes: str | None, appended_note: str) -> str:
        """Append operator notes without losing the existing note body."""
        stripped_note = str(appended_note or "").strip()
        if not stripped_note:
            return str(current_notes or "").strip()
        current_text = str(current_notes or "").strip()
        if not current_text:
            return stripped_note
        return f"{current_text}\n{stripped_note}".strip()
