"""Unit tests for the shared smoke failure taxonomy module.

Guards the single source of truth used by both the smoke producer
(``app.services.smoke_test``) and the operations dashboard consumer
(``app.services.analytics_reporting``): category set, guidance-map key
completeness, and the classification mapping.
"""

from __future__ import annotations

import pytest

from app.services.smoke_failure_taxonomy import (
    FAILURE_GUIDANCE,
    FAILURE_RULES,
    SMOKE_FAILURE_CATEGORIES,
    classify_failure,
    guidance_for,
)
from scripts.production_smoke_test import FAILURE_GUIDANCE as CLI_FAILURE_GUIDANCE
from scripts.production_smoke_test import failure_guidance as cli_failure_guidance


def test_guidance_map_keys_match_categories_and_are_complete():
    """Every category has guidance, and no orphan guidance keys exist."""
    assert set(FAILURE_GUIDANCE) == set(SMOKE_FAILURE_CATEGORIES)
    for category, guidance in FAILURE_GUIDANCE.items():
        assert guidance.get("action_required"), category
        assert guidance.get("retry_method"), category


def test_failure_rules_are_well_formed_and_known_categories():
    """The rule table stays a list of (known-category, non-empty tokens)."""
    seen = set()
    for category, tokens in FAILURE_RULES:
        assert category in SMOKE_FAILURE_CATEGORIES, category
        assert category not in seen, f"duplicate rule category {category}"
        seen.add(category)
        assert isinstance(tokens, tuple) and tokens, category
        assert all(isinstance(token, str) and token for token in tokens), category


@pytest.mark.parametrize(
    ("name", "detail", "expected"),
    [
        # One matching case per category (token- and name-driven paths).
        ("koneps_collect", "service key unauthorized 401", "credential"),
        ("worker_phase", "celery broker connection refused", "task_broker"),
        ("predict_price", "skipped — no eligible project", "no_candidate"),
        ("koneps_collect", "collected 0 items today", "no_candidate"),
        ("telegram_ping", "Telegram API rejected", "telegram"),
        ("sbert_embedding", "OperationalError no such table", "db_schema"),
        ("candidate_generation", "strategy monitor failed", "candidate_generation"),
        ("candidate_generation", "monitor produced nothing", "candidate_generation"),
        ("some_phase", "strategy monitor blew up", "candidate_generation"),
        ("koneps_collect", "KONEPS OpenAPI timeout", "koneps_response"),
        ("koneps_collect", "empty response body", "koneps_response"),
        ("some_phase", "openapi gateway 502", "koneps_response"),
        ("predict_price", "guardrail exception", "prediction"),
        ("sbert_embedding", "unexpected model error", "prediction"),
        ("mystery", "nothing matches at all", "unknown"),
    ],
)
def test_classify_failure_mapping(name, detail, expected):
    assert classify_failure(name, detail) == expected


@pytest.mark.parametrize(
    ("name", "detail", "expected"),
    [
        # credential (rule 1) wins over a co-occurring task/broker token.
        ("worker", "missing service key on celery worker", "credential"),
        # task_broker (rule 2) wins over a co-occurring no_candidate token.
        ("phase", "worker found no candidate", "task_broker"),
        # no_candidate (rule 3) wins over a co-occurring telegram token.
        ("phase", "telegram: no candidate", "no_candidate"),
        # db_schema (rule 5) wins over the name-aware candidate_generation rule.
        ("candidate_generation", "database schema error", "db_schema"),
        # A token rule wins over the name-aware koneps_response rule.
        ("koneps_collect", "token missing", "credential"),
    ],
)
def test_classify_failure_is_priority_ordered(name, detail, expected):
    """First matching rule in FAILURE_RULES order wins; token rules precede
    the name-aware rules."""
    assert classify_failure(name, detail) == expected


def test_guidance_for_unknown_category_falls_back():
    assert guidance_for("not-a-real-category") == FAILURE_GUIDANCE["unknown"]


def test_koneps_response_retry_method_is_canonical_producer_wording():
    """The previously-drifted consumer wording is dropped; producer wins."""
    assert (
        guidance_for("koneps_response")["retry_method"]
        == "Retry the smoke after KONEPS responds normally; use "
        "`--max-items 3 --write` for a bounded manual check."
    )


# --- Standalone CLI runner (scripts/production_smoke_test.py) ----------------
# The CLI runner keeps its OWN remediation wording ("rerun this smoke command")
# because its evidence file is read by whoever ran that command, while the
# scheduled producer's wording points at the beat task. Only the *lookup* was
# duplicated, so the CLI now reuses ``guidance_for`` with its own table. These
# tests pin both the CLI wording and the shared fallback behaviour.


def test_cli_guidance_table_covers_every_category():
    assert set(CLI_FAILURE_GUIDANCE) == set(SMOKE_FAILURE_CATEGORIES)
    for category, guidance in CLI_FAILURE_GUIDANCE.items():
        assert guidance.get("action_required"), category
        assert guidance.get("retry_method"), category


@pytest.mark.parametrize(
    ("category", "action_required", "retry_method"),
    [
        (
            "credential",
            "Rotate or restore the missing API/Telegram credential.",
            "Fix the credential, then rerun this smoke command.",
        ),
        (
            "koneps_response",
            "Check KONEPS OpenAPI availability and request parameters.",
            "Retry with `--write --max-items 3` after KONEPS responds normally.",
        ),
        (
            "unknown",
            "Inspect the step error and application logs.",
            "Rerun the same command after the logged root cause is corrected.",
        ),
    ],
)
def test_cli_failure_guidance_wording_is_pinned(category, action_required, retry_method):
    guidance = cli_failure_guidance(category)
    assert guidance["action_required"] == action_required
    assert guidance["retry_method"] == retry_method


def test_cli_failure_guidance_unknown_category_falls_back_to_cli_table():
    assert cli_failure_guidance("not-a-real-category") == CLI_FAILURE_GUIDANCE["unknown"]


def test_cli_wording_stays_distinct_from_the_scheduled_producer():
    """The two tables are intentionally different; consolidation is code-only."""
    assert (
        cli_failure_guidance("credential")["retry_method"]
        != guidance_for("credential")["retry_method"]
    )
