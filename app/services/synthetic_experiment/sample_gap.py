"""Sample-gap plan primitives: input coercion, run context, and the accumulator.

Read-only helpers that turn persisted run summaries into backfill-plan gap items.
``_SampleGapAccumulator`` merges lacking-group rows across runs into one ranked
recommendation. No DB writes or run enqueues happen here.
"""

from __future__ import annotations

import shlex
from collections import Counter
from typing import Any, Optional

from app.core.constants import PAPER_BID_ACTIONS
from app.models.models import SyntheticExperiment, SyntheticExperimentRun

from .constants import (
    REPORT_STATUS_DATA_MIXED,
    SAMPLE_GAP_WARNING_MIXED_DATA,
    SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
)
from .serialization import _json_loads


def _safe_positive_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_settle_actions(value: Any) -> list[str]:
    if isinstance(value, bool):
        return ["bid_now", "review"] if value else ["bid_now"]
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return ["bid_now", "review"]
        if lowered in {"false", "0", "no", "n", ""}:
            return ["bid_now"]
        raw_actions = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_actions = value
    else:
        return ["bid_now"]

    actions: list[str] = []
    for item in raw_actions:
        action = str(item).strip()
        if action in PAPER_BID_ACTIONS and action not in actions:
            actions.append(action)
    return actions or ["bid_now"]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _sample_gap_run_context(
    run: SyntheticExperimentRun,
    *,
    summary: dict[str, Any],
    sample_report: dict[str, Any],
) -> dict[str, Any]:
    params = _json_loads(run.experiment.params_json) if run.experiment else {}
    params = params if isinstance(params, dict) else {}
    operator_slugs = (
        _json_loads(run.experiment.operator_slugs_json) if run.experiment else []
    )
    operator_slugs = operator_slugs if isinstance(operator_slugs, list) else []
    preset_name = _first_present(
        sample_report.get("preset_name"),
        run.experiment.name if run.experiment else None,
    )
    context_params = {
        "start_at": _first_present(summary.get("start_at"), params.get("start_at")),
        "end_at": _first_present(summary.get("end_at"), params.get("end_at")),
        "category": _first_present(summary.get("category"), params.get("category")),
        "limit": _safe_positive_int(
            _first_present(summary.get("limit"), params.get("limit")),
            default=100,
        ),
        "scenario": _first_present(summary.get("scenario"), params.get("scenario"))
        or "base",
        "settle_actions": _normalize_settle_actions(params.get("settle_actions")),
    }
    return {
        "run_id": run.id,
        "experiment_id": run.experiment_id,
        "preset_name": preset_name,
        "status": run.status,
        "finished_at": run.finished_at,
        **context_params,
        "params": context_params,
        "operator_slugs": [str(slug) for slug in operator_slugs],
        "synthetic_only": sample_report.get("synthetic_only") is not False,
        "report_status": sample_report.get("report_status"),
        "warnings": [],
    }


def _sample_gap_run_warnings(
    run: SyntheticExperimentRun,
    sample_report: dict[str, Any],
) -> list[dict[str, Any]]:
    mixed_data = (
        sample_report.get("synthetic_only") is False
        or sample_report.get("report_status") == REPORT_STATUS_DATA_MIXED
    )
    if not mixed_data:
        return []
    operator_slugs = sample_report.get("non_synthetic_operator_slugs") or []
    if not isinstance(operator_slugs, list):
        operator_slugs = []
    return [
        {
            "code": SAMPLE_GAP_WARNING_MIXED_DATA,
            "message": (
                "Run includes canonical/operator data mixed into a synthetic report; "
                "rerun with synthetic-only operators before using it for reporting."
            ),
            "run_ids": [run.id],
            "operator_slugs": sorted(str(slug) for slug in operator_slugs),
        }
    ]


def _dedupe_sample_gap_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for warning in warnings:
        key = (str(warning.get("code") or "unknown"), str(warning.get("message") or ""))
        current = merged.setdefault(
            key,
            {
                "code": key[0],
                "message": key[1],
                "run_ids": [],
                "operator_slugs": [],
            },
        )
        current["run_ids"].extend(
            _safe_positive_int(run_id)
            for run_id in warning.get("run_ids", []) or []
            if _safe_positive_int(run_id) > 0
        )
        current["operator_slugs"].extend(
            str(slug)
            for slug in warning.get("operator_slugs", []) or []
            if str(slug)
        )
    return [
        {
            **warning,
            "run_ids": sorted(set(warning["run_ids"])),
            "operator_slugs": sorted(set(warning["operator_slugs"])),
        }
        for warning in merged.values()
    ]


def _sample_gap_action(
    code: str,
    label: str,
    detail: str,
) -> dict[str, str]:
    return {"code": code, "label": label, "detail": detail}


def _sample_gap_candidate_name(
    *, preset_name: Optional[str], dimension: str, key: str, action_code: str
) -> str:
    if preset_name and action_code == "rerun_related_preset":
        return str(preset_name)[:200]
    if preset_name:
        return f"{preset_name}-{action_code}"[:200]
    clean_key = key.replace("/", "-").strip() or "unknown"
    return f"sample-gap-{dimension}-{clean_key}"[:200]


def _sample_gap_candidate_description(
    *, dimension: str, key: str, action_label: str
) -> str:
    return (
        f"Sample-gap follow-up candidate for {dimension}:{key}. "
        f"Recommended action: {action_label}."
    )


def _normalized_experiment_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    normalized["limit"] = _safe_positive_int(normalized.get("limit"), default=100)
    normalized["scenario"] = str(normalized.get("scenario") or "base")
    normalized["settle_actions"] = _normalize_settle_actions(
        normalized.get("settle_actions")
    )
    return normalized


def _candidate_params_for_action(
    params: dict[str, Any],
    *,
    action_code: str,
    missing_settled_count: int,
) -> dict[str, Any]:
    candidate = _normalized_experiment_params(params)
    if action_code == "increase_limit":
        current_limit = _safe_positive_int(candidate.get("limit"), default=100)
        suggested_limit = max(
            current_limit + max(1, missing_settled_count),
            int(current_limit * 1.5),
        )
        candidate["limit"] = min(1000, suggested_limit)
    return candidate


def _sample_gap_params_match(
    experiment: SyntheticExperiment,
    params: dict[str, Any],
) -> bool:
    saved = _json_loads(experiment.params_json) or {}
    if not isinstance(saved, dict):
        return False
    return _sample_gap_core_params(saved) == _sample_gap_core_params(params)


def _sample_gap_core_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_experiment_params(params)
    return {
        "start_at": normalized.get("start_at"),
        "end_at": normalized.get("end_at"),
        "category": normalized.get("category"),
        "limit": normalized.get("limit"),
        "scenario": normalized.get("scenario"),
        "settle_actions": normalized.get("settle_actions"),
    }


def _sample_gap_operator_slugs_match(
    experiment: SyntheticExperiment,
    operator_slugs: list[str],
) -> bool:
    saved = _json_loads(experiment.operator_slugs_json) or []
    if not isinstance(saved, list):
        return False
    return [str(slug) for slug in saved] == [str(slug) for slug in operator_slugs]


def _sample_gap_matches_fixed_preset(
    *,
    definition: dict[str, Any],
    params: dict[str, Any],
    operator_slugs: list[str],
) -> bool:
    return (
        _sample_gap_core_params(definition.get("params") or {})
        == _sample_gap_core_params(params)
        and [str(slug) for slug in definition.get("operator_slugs") or []]
        == [str(slug) for slug in operator_slugs]
    )


def _sample_gap_source_context(
    *,
    gap: dict[str, Any],
    action_code: str,
    action_label: str,
    preset_name: Optional[str],
    params: dict[str, Any],
    operator_slugs: list[str],
    operator_targets: list[dict[str, Any]],
    operator_id_scope_ready: bool,
    run_allowed: bool,
    blocked_by_warnings: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "source": "sample_gap_candidate",
        "dimension": str(gap["dimension"]),
        "key": str(gap["key"]),
        "action_code": action_code,
        "action_label": action_label,
        "preset_name": preset_name,
        "related_run_ids": [
            _safe_positive_int(run_id)
            for run_id in gap.get("related_run_ids", []) or []
            if _safe_positive_int(run_id) > 0
        ],
        "missing_settled_count": _safe_positive_int(
            gap.get("missing_settled_count")
        ),
        "sample_target": _safe_positive_int(gap.get("sample_target")),
        "source_run_count": _safe_positive_int(gap.get("source_run_count")),
        "params": params,
        "operator_slugs": operator_slugs,
        "operator_targets": operator_targets,
        "operator_id_scope_ready": operator_id_scope_ready,
        "run_allowed": run_allowed,
        "blocked_by_warnings": blocked_by_warnings,
        "warnings": warnings,
    }


def _sample_gap_candidate_warning_context(
    gap: dict[str, Any],
) -> tuple[list[str], list[str], bool]:
    warnings = [str(code) for code in gap.get("warnings", []) or [] if str(code)]
    blocked_by_warnings = sorted(
        {
            SAMPLE_GAP_WARNING_MIXED_DATA
            for code in warnings
            if code == SAMPLE_GAP_WARNING_MIXED_DATA
        }
    )
    return warnings, blocked_by_warnings, not blocked_by_warnings


def _sample_gap_cli_command(
    *,
    preset_name: Optional[str],
    dimension: str,
    key: str,
    action_code: str,
    write: bool,
) -> str:
    parts = ["python", "scripts/run_g2_synthetic_evidence.py"]
    parts.append("--write" if write else "--dry-run")
    if preset_name:
        parts.extend(["--preset", preset_name])
    else:
        parts.extend(["--dimension", dimension, "--key", key])
    if action_code:
        parts.extend(["--action-code", action_code])
    return " ".join(shlex.quote(part) for part in parts)


class _SampleGapAccumulator:
    def __init__(self, *, dimension: str, key: str) -> None:
        self.dimension = dimension
        self.key = key
        self.settled_count = 0
        self.sample_target = 0
        self.missing_settled_count = 0
        self.total_missing_settled_count = 0
        self.related_runs: dict[int, dict[str, Any]] = {}
        self.preset_counts: Counter[str] = Counter()
        self.warning_codes: set[str] = set()

    def add(
        self,
        *,
        row: dict[str, Any],
        run_context: dict[str, Any],
        warning_codes: list[str],
    ) -> None:
        missing = _safe_positive_int(row.get("missing_settled_count"))
        settled = _safe_positive_int(row.get("settled_count"))
        target = _safe_positive_int(
            row.get("sample_target"), default=SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET
        )
        if missing <= 0:
            return
        self.total_missing_settled_count += missing
        if (
            missing > self.missing_settled_count
            or (
                missing == self.missing_settled_count
                and (self.settled_count == 0 or settled < self.settled_count)
            )
        ):
            self.missing_settled_count = missing
            self.settled_count = settled
            self.sample_target = target

        run_id = _safe_positive_int(run_context.get("run_id"))
        if run_id > 0:
            self.related_runs[run_id] = {
                **run_context,
                "warnings": sorted(set(warning_codes)),
            }
        preset_name = run_context.get("preset_name")
        if preset_name:
            self.preset_counts[str(preset_name)] += 1
        self.warning_codes.update(warning_codes)

    @property
    def source_run_count(self) -> int:
        return len(self.related_runs)

    @property
    def related_preset_names(self) -> list[str]:
        return sorted(self.preset_counts)

    def primary_run_context(self) -> dict[str, Any]:
        if not self.related_runs:
            return {}
        if self.preset_counts:
            preset_name = self.preset_counts.most_common(1)[0][0]
            for run in self.related_runs.values():
                if run.get("preset_name") == preset_name:
                    return run
        return next(iter(self.related_runs.values()))

    def recommendation(self) -> dict[str, Any]:
        run_context = self.primary_run_context()
        params = dict(run_context.get("params") or {})
        if self.dimension == "category" and self.key != "missing":
            params["category"] = self.key
        elif "category" not in params and run_context.get("category") is not None:
            params["category"] = run_context.get("category")

        current_limit = _safe_positive_int(params.get("limit"), default=100)
        if current_limit:
            params["limit"] = current_limit
        actions = [
            _sample_gap_action(
                "rerun_related_preset",
                "Rerun related preset",
                "Repeat the related synthetic experiment preset and keep the same result window first.",
            )
        ]
        if self.dimension == "category":
            actions.append(
                _sample_gap_action(
                    "expand_category_window",
                    "Expand category window",
                    "If the rerun is still thin, widen the date window for this category before changing operators.",
                )
            )
        if self.dimension in {"preset", "budget_band"}:
            actions.append(
                _sample_gap_action(
                    "increase_limit",
                    "Increase limit",
                    "Raise the backtest limit when the current preset cannot collect enough settled samples.",
                )
            )
        if self.dimension == "business_type":
            actions.append(
                _sample_gap_action(
                    "review_operator_mix",
                    "Review operator mix",
                    "Rerun the preset with operators that cover this business type before widening all categories.",
                )
            )
        if SAMPLE_GAP_WARNING_MIXED_DATA in self.warning_codes:
            actions.append(
                _sample_gap_action(
                    "rerun_synthetic_only",
                    "Rerun synthetic-only",
                    "Exclude canonical operators before using this sample set for repeatable reporting.",
                )
            )

        deduped_actions: dict[str, dict[str, str]] = {}
        for action in actions:
            deduped_actions.setdefault(action["code"], action)

        return {
            "preset_name": run_context.get("preset_name"),
            "params": params,
            "actions": list(deduped_actions.values()),
        }

    def to_item(self, *, priority: int) -> dict[str, Any]:
        related_runs = sorted(
            self.related_runs.values(),
            key=lambda item: int(item.get("run_id") or 0),
            reverse=True,
        )
        return {
            "priority": priority,
            "dimension": self.dimension,
            "key": self.key,
            "settled_count": self.settled_count,
            "sample_target": self.sample_target,
            "missing_settled_count": self.missing_settled_count,
            "total_missing_settled_count": self.total_missing_settled_count,
            "source_run_count": self.source_run_count,
            "related_preset_names": self.related_preset_names,
            "related_run_ids": [run["run_id"] for run in related_runs],
            "related_runs": related_runs,
            "recommendation": self.recommendation(),
            "warnings": sorted(self.warning_codes),
        }
