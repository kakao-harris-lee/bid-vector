"""Allowlisted persistence shape for production smoke evidence."""

from __future__ import annotations

from collections.abc import Mapping


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

EVIDENCE_SAFE_PAYLOAD_FIELDS = frozenset(
    {
        "action", "artifact_count", "cards", "category", "collected_count",
        "completed_count", "configured", "crawl", "crawl_job_id",
        "current_operator_id", "decision_record_id", "decision_status",
        "drop_reasons", "dropped_count", "duplicate_count",
        "evaluated_project_count", "execution_mode", "failed_count",
        "fallback_count", "git_sha", "high_priority_only", "items",
        "job_status", "key", "known_chat_id_count", "limit", "max_items",
        "max_recommended_candidates", "metadata", "ml_release",
        "monitor_run_id", "new_candidate_count", "normalized_count",
        "notification_count", "notification_id", "notification_status",
        "notifications", "notify_only_high_priority", "notice_number",
        "operator_id", "pages_fetched", "passed", "payload_sha256",
        "pending_count", "pending_update_count", "persisted_candidate_count",
        "persisted_count", "processed_count", "processed_event_id_first",
        "processed_event_id_last", "priority_score", "probability_score",
        "profile_configured", "project_id", "projection_not_ready_count",
        "projection_status", "received_count", "release_sha", "release_tag",
        "result", "result_count", "results", "returned_candidate_count",
        "runtime", "run_id", "run_item_id", "selected_candidate_count",
        "semantic_input_outbox_event_ids", "service", "sha256",
        "signature_status", "similarity_score", "skipped_count", "smoke_test",
        "snapshot_status", "source", "source_total_count", "stale", "status",
        "strategy", "strategy_configured", "task_id", "tasks", "truncated",
        "write_mode",
    }
)


def _sanitize_evidence_payload(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_sanitize_evidence_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if key == "drop_reasons" and isinstance(item, dict):
            sanitized[key] = {
                str(reason): int(count)
                for reason, count in item.items()
                if isinstance(count, int) and not isinstance(count, bool)
            }
        elif key in EVIDENCE_SAFE_PAYLOAD_FIELDS:
            sanitized[key] = _sanitize_evidence_payload(item)
    return sanitized


def sanitize_evidence(evidence: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Build the explicit, non-sensitive persistence shape for smoke evidence."""
    sanitized: dict[str, JsonValue] = {
        "evidence_schema_version": 2,
        "started_at": evidence.get("started_at"),
        "finished_at": evidence.get("finished_at"),
        "status": evidence.get("status"),
        "write_mode": bool(evidence.get("write_mode")),
        "steps": [],
    }
    steps = sanitized["steps"]
    if not isinstance(steps, list):
        return sanitized
    raw_steps = evidence.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        step = {
            key: raw_step.get(key)
            for key in (
                "name", "required", "status", "summary", "skip_reason",
                "failure_category", "action_required", "retry_method",
            )
            if raw_step.get(key) is not None
        }
        if raw_step.get("error") is not None:
            step["error"] = "step failed; inspect restricted runtime logs"
        if "payload" in raw_step:
            step["payload"] = _sanitize_evidence_payload(raw_step["payload"])
        steps.append(step)
    if evidence.get("error") is not None:
        sanitized["error"] = (
            "smoke failed; inspect the failed step and restricted runtime logs"
        )
    return sanitized
