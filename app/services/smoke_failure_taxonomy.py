"""Single source of truth for smoke-test failure classification + remediation.

The scheduled KONEPS/Telegram smoke (``app.services.smoke_test``) is the
*producer*: it classifies each failed phase into a fixed category and stores the
matching ``action_required`` / ``retry_method`` guidance into ``SmokeTestRun``
evidence. The operations dashboard (``app.services.analytics_reporting``) is the
*consumer*: it prefers the stored guidance and only re-derives a category /
falls back to a guidance string for guidance-less (e.g. legacy or skipped)
phases.

Both used to carry their own copy of the classifier and guidance map, which
drifted (``koneps_response`` retry text diverged). This module unifies them.

The category *values* themselves
(``credential``/``task_broker``/``no_candidate``/``telegram``/``db_schema``/
``candidate_generation``/``koneps_response``/``prediction``/``unknown``) are a
stable contract with persisted evidence and the dashboard and must not change
here — only the duplication and drifted wording are removed.
"""

from __future__ import annotations

# Canonical fixed buckets (order matters for stable dashboard breakdowns).
SMOKE_FAILURE_CATEGORIES: tuple[str, ...] = (
    "credential",
    "koneps_response",
    "candidate_generation",
    "no_candidate",
    "telegram",
    "task_broker",
    "db_schema",
    "prediction",
    "unknown",
)

# Canonical remediation guidance. The producer (smoke_test) wording is the
# source of truth because it is what gets persisted into evidence and what the
# consumer prefers to display (stored-first); the drifted consumer copy is
# dropped in favour of this map.
FAILURE_GUIDANCE: dict[str, dict[str, str]] = {
    "credential": {
        "action_required": "Rotate or restore the missing API/Telegram credential.",
        "retry_method": "Fix the credential, then rerun the scheduled smoke task or `python scripts/production_smoke_test.py --write`.",
    },
    "koneps_response": {
        "action_required": "Check KONEPS OpenAPI availability and request parameters.",
        "retry_method": "Retry the smoke after KONEPS responds normally; use `--max-items 3 --write` for a bounded manual check.",
    },
    "candidate_generation": {
        "action_required": "Inspect the strategy monitor run and candidate filters.",
        "retry_method": "Rerun `/api/v1/operator/strategy/monitor` or `python scripts/production_smoke_test.py --write --monitor-all-candidates`.",
    },
    "no_candidate": {
        "action_required": "Confirm whether no active notices match the current operator strategy.",
        "retry_method": "Rerun with wider strategy filters or `--monitor-all-candidates`; no code retry is required if the skip reason is expected.",
    },
    "telegram": {
        "action_required": "Check bot token, chat id, and whether the operator started the Telegram bot conversation.",
        "retry_method": "Fix Telegram configuration, then rerun the smoke or `python scripts/production_smoke_test.py --write --telegram-sync`.",
    },
    "task_broker": {
        "action_required": "Check Celery broker/backend and worker health.",
        "retry_method": "Restart or repair broker/workers, then rerun the scheduled smoke task.",
    },
    "db_schema": {
        "action_required": "Apply pending migrations and verify the production schema.",
        "retry_method": "Run migrations, restart the API/worker, then rerun the scheduled smoke task.",
    },
    "prediction": {
        "action_required": "Check embedding and price prediction dependencies plus recent project data.",
        "retry_method": "Rerun after model/data repair; use the smoke evidence project id to reproduce prediction locally.",
    },
    "unknown": {
        "action_required": "Inspect the phase detail and application logs.",
        "retry_method": "Rerun the same smoke command after the logged root cause is corrected.",
    },
}

_CREDENTIAL_TOKENS = (
    "credential",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    "api key",
    "apikey",
    "service key",
    "token",
    "secret",
)
_TASK_BROKER_TOKENS = (
    "celery",
    "broker",
    "rabbit",
    "redis",
    "queue",
    "worker",
    "task",
)
_NO_CANDIDATE_TOKENS = (
    "no eligible",
    "no candidate",
    "no recent project",
    "no project",
    "collected 0",
    "0 item",
)
_DB_SCHEMA_TOKENS = (
    "no such table",
    "no such column",
    "schema",
    "sqlalchemy",
    "operationalerror",
    "database",
)


def classify_failure(name: str, detail: str) -> str:
    """Classify a failed smoke phase into one fixed ``SMOKE_FAILURE_CATEGORIES`` bucket.

    ``name`` is the smoke phase name (e.g. ``koneps_collect``); ``detail`` is the
    free-text failure/skip detail. Matching is case-insensitive and token-based,
    in priority order, returning ``"unknown"`` when nothing matches.
    """
    text = f"{name or ''} {detail or ''}".strip().lower()
    if any(token in text for token in _CREDENTIAL_TOKENS):
        return "credential"
    if any(token in text for token in _TASK_BROKER_TOKENS):
        return "task_broker"
    if any(token in text for token in _NO_CANDIDATE_TOKENS):
        return "no_candidate"
    if "telegram" in text:
        return "telegram"
    if any(token in text for token in _DB_SCHEMA_TOKENS):
        return "db_schema"
    name_lower = str(name or "").strip().lower()
    if name_lower == "candidate_generation" or "strategy monitor" in text:
        return "candidate_generation"
    if name_lower == "koneps_collect" or "koneps" in text or "openapi" in text:
        return "koneps_response"
    if name_lower in {"predict_price", "sbert_embedding"}:
        return "prediction"
    return "unknown"


def guidance_for(category: str) -> dict[str, str]:
    """Return the ``{action_required, retry_method}`` map for ``category``.

    Unknown categories fall back to the ``"unknown"`` guidance.
    """
    return FAILURE_GUIDANCE.get(category) or FAILURE_GUIDANCE["unknown"]
