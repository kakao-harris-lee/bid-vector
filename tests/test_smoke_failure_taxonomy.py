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
    SMOKE_FAILURE_CATEGORIES,
    classify_failure,
    guidance_for,
)


def test_guidance_map_keys_match_categories_and_are_complete():
    """Every category has guidance, and no orphan guidance keys exist."""
    assert set(FAILURE_GUIDANCE) == set(SMOKE_FAILURE_CATEGORIES)
    for category, guidance in FAILURE_GUIDANCE.items():
        assert guidance.get("action_required"), category
        assert guidance.get("retry_method"), category


@pytest.mark.parametrize(
    ("name", "detail", "expected"),
    [
        ("koneps_collect", "service key unauthorized 401", "credential"),
        ("worker_phase", "celery broker connection refused", "task_broker"),
        ("predict_price", "skipped — no eligible project", "no_candidate"),
        ("telegram_ping", "Telegram API rejected", "telegram"),
        ("sbert_embedding", "OperationalError no such table", "db_schema"),
        ("candidate_generation", "strategy monitor failed", "candidate_generation"),
        ("koneps_collect", "KONEPS OpenAPI timeout", "koneps_response"),
        ("predict_price", "guardrail exception", "prediction"),
        ("sbert_embedding", "unexpected model error", "prediction"),
        ("mystery", "nothing matches at all", "unknown"),
    ],
)
def test_classify_failure_mapping(name, detail, expected):
    assert classify_failure(name, detail) == expected


def test_classify_failure_is_priority_ordered():
    """Credential token wins over a co-occurring task/broker token."""
    assert (
        classify_failure("worker", "missing service key on celery worker")
        == "credential"
    )


def test_guidance_for_unknown_category_falls_back():
    assert guidance_for("not-a-real-category") == FAILURE_GUIDANCE["unknown"]


def test_koneps_response_retry_method_is_canonical_producer_wording():
    """The previously-drifted consumer wording is dropped; producer wins."""
    assert (
        guidance_for("koneps_response")["retry_method"]
        == "Retry the smoke after KONEPS responds normally; use "
        "`--max-items 3 --write` for a bounded manual check."
    )
