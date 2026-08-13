"""Shape a smoke phase's raw ``data`` into the evidence stored on a run row.

Extracted from :mod:`app.services.smoke_test` (§4.5.4 size decomposition — that
module carries the phase bodies and was already past the soft limit). This is one
responsibility: decide which phase fields are durable evidence and stamp the
scope (canonical vs per-operator) every stored phase must carry. Pure functions,
no I/O — the phase runner owns the session and the broker.
"""

from __future__ import annotations

from pydantic import JsonValue

from app.utils.numeric import optional_int

SCHEDULED_SMOKE_EVIDENCE_SCOPE = "g0_scheduled_smoke"
SCHEDULED_SMOKE_CANONICAL_ONLY_REASON = (
    "G-0 scheduled smoke validates the canonical shared pipeline; "
    "G-2 per-operator evidence is recorded on operator-scoped monitor and experiment runs."
)

# Phase ``data`` keys worth persisting. Everything else (bulky payloads, ORM
# objects) is dropped before the run row is written.
DURABLE_EVIDENCE_KEYS = frozenset(
    {
        "evidence_scope",
        "operator_scope",
        "operator_id",
        "current_operator_id",
        "current_operator_username",
        "canonical_only_reason",
        "source_run_type",
        "source_run_id",
        "collected_count",
        "recent_collection_jobs",
        "recent_collection_count",
        "recent_collection_last_at",
        "project_id",
        "project_title",
        "predicted_bid_rate",
        "predictor_name",
        "monitor_run_id",
        "evaluated_project_count",
        "selected_candidate_count",
        "persisted_candidate_count",
        "notification_count",
        "new_candidate_count",
        "skip_reason",
        "telegram_status",
        "telegram_message_id",
        "queue_name",
        "queue_depth",
        "queue_depth_threshold",
    }
)


def with_phase_scope_evidence(
    phase_name: str, evidence: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Stamp the evidence scope so a stored phase is never scope-ambiguous."""
    scoped = dict(evidence or {})
    scoped.setdefault("evidence_scope", SCHEDULED_SMOKE_EVIDENCE_SCOPE)

    source_run_id = optional_int(
        scoped.get("source_run_id") or scoped.get("monitor_run_id")
    )
    if phase_name == "candidate_generation" and source_run_id is not None:
        scoped.setdefault("source_run_type", "operator_strategy_monitor")
        scoped.setdefault("source_run_id", source_run_id)

    operator_id = optional_int(
        scoped.get("operator_id") or scoped.get("current_operator_id")
    )
    if operator_id is not None:
        scoped["operator_id"] = operator_id
        scoped.setdefault("operator_scope", "operator")
        return scoped

    scoped.setdefault("operator_scope", "canonical_only")
    scoped.setdefault("canonical_only_reason", SCHEDULED_SMOKE_CANONICAL_ONLY_REASON)
    return scoped


def trim_phase_evidence(phase: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Keep only durable, non-null evidence keys, then stamp the scope."""
    phase_name = str(phase.get("name") or "")
    evidence = phase.get("evidence")
    if isinstance(evidence, dict):
        return with_phase_scope_evidence(phase_name, evidence)
    data = phase.get("data")
    if not isinstance(data, dict):
        return with_phase_scope_evidence(phase_name, {})
    compact = {
        key: value
        for key, value in data.items()
        if key in DURABLE_EVIDENCE_KEYS and value is not None
    }
    return with_phase_scope_evidence(phase_name, compact)
