"""Unit tests for the extracted KONEPS live-failure helpers.

These lock the classification mapping, retryable derivation, and payload shape
of ``app.services.koneps.live_failure`` after the pure relocation out of
``KonepsCollectorService``. Behavior must stay byte-identical to the original
collector methods.
"""

import pytest

from app.core.config import settings
from app.services.koneps import live_failure
from app.services.koneps.live_failure import KonepsLiveCollectionError


@pytest.mark.parametrize(
    "message, expected",
    [
        ("CAPTCHA challenge presented", "access_denied"),
        ("Access denied by edge", "access_denied"),
        ("Request forbidden", "access_denied"),
        ("HTTP 403 returned", "access_denied"),
        ("request was blocked", "access_denied"),
        ("Browser not available in container", "browser_runtime"),
        ("Executable doesn't exist at path", "browser_runtime"),
        ("Run playwright install to fix", "browser_runtime"),
        ("Failed to launch chromium", "browser_runtime"),
        ("Target page, context or browser has been closed", "browser_runtime"),
        ("net::ERR_NAME_NOT_RESOLVED", "network"),
        ("net::ERR_CONNECTION_RESET", "network"),
        ("ECONNREFUSED at host", "network"),
        ("transient network glitch", "network"),
        ("connection reset by peer", "network"),
        ("connection refused", "network"),
        ("no notice items found", "no_data"),
        ("query returned no result", "no_data"),
        ("empty result set", "no_data"),
        ("element could not be located", "selector_drift"),
        ("invalid selector path", "selector_drift"),
        ("locator resolved to 0 elements", "selector_drift"),
        ("strict mode violation: 2 matches", "selector_drift"),
        ("waiting for element to be visible", "selector_drift"),
        ("Timeout 30000ms exceeded", "timeout"),
        ("something entirely unexpected", "unknown"),
    ],
)
def test_classify_live_failure_message_markers(message, expected):
    assert live_failure.classify_live_failure(Exception(message)) == expected


def test_classify_live_failure_uses_exception_type_for_timeout():
    # Empty message but TimeoutError type still classifies as timeout.
    assert live_failure.classify_live_failure(TimeoutError()) == "timeout"


def test_classify_priority_access_denied_before_network():
    # access_denied markers checked before network markers; both present.
    exc = Exception("forbidden net:: blocked")
    assert live_failure.classify_live_failure(exc) == "access_denied"


def test_retryable_categories_membership():
    assert live_failure.LIVE_FAILURE_RETRYABLE_CATEGORIES == {
        "network",
        "timeout",
        "unknown",
    }


def test_live_failure_payload_retryable_timeout():
    payload = live_failure.live_failure_payload(
        TimeoutError("Timeout 30000ms exceeded"), stage="notice_search"
    )
    assert payload["category"] == "timeout"
    assert payload["retryable"] is True
    assert payload["stage"] == "notice_search"
    assert payload["exception_type"] == "TimeoutError"
    assert payload["error_message"] == "Timeout 30000ms exceeded"
    assert "attempt_count" not in payload


def test_live_failure_payload_not_retryable_access_denied():
    payload = live_failure.live_failure_payload(
        Exception("Access denied"), stage="live_collection"
    )
    assert payload["category"] == "access_denied"
    assert payload["retryable"] is False


def test_live_failure_payload_unwraps_wrapped_error_and_attempts():
    original = TimeoutError("Timeout 30000ms exceeded while loading KONEPS")
    attempts = [{"attempt": 1}, {"attempt": 2}]
    wrapped = KonepsLiveCollectionError(
        "wrapper message",
        stage="opening_result",
        attempts=attempts,
        original_error=original,
    )
    payload = live_failure.live_failure_payload(wrapped, stage="ignored_default")
    # Resolved stage comes from the wrapped error, not the default arg.
    assert payload["stage"] == "opening_result"
    # Category classified from the *original* error.
    assert payload["category"] == "timeout"
    assert payload["retryable"] is True
    # error_message reflects the original error, detail reflects the wrapper.
    assert payload["error_message"] == str(original)
    assert payload["detail"] == "wrapper message"
    assert payload["exception_type"] == "TimeoutError"
    assert payload["attempt_count"] == 2
    assert payload["attempts"] == attempts


def test_build_live_retry_attempt_non_final_has_delay():
    exc = TimeoutError("Timeout 30000ms exceeded")
    attempt = live_failure.build_live_retry_attempt(
        stage="notice_search",
        attempt_index=0,
        exc=exc,
        final_attempt=False,
    )
    assert attempt["attempt"] == 1
    assert attempt["stage"] == "notice_search"
    assert attempt["category"] == "timeout"
    assert attempt["retryable"] is True
    assert attempt["exception_type"] == "TimeoutError"
    assert attempt["final_attempt"] is False
    assert attempt["next_retry_delay_seconds"] == pytest.approx(
        settings.KONEPS_RETRY_BACKOFF_MS / 1000
    )


def test_build_live_retry_attempt_final_has_no_delay():
    exc = TimeoutError("Timeout 30000ms exceeded")
    attempt = live_failure.build_live_retry_attempt(
        stage="notice_search",
        attempt_index=1,
        exc=exc,
        final_attempt=True,
    )
    assert attempt["attempt"] == 2
    assert attempt["final_attempt"] is True
    assert attempt["next_retry_delay_seconds"] is None


def test_retry_delay_seconds_linear_backoff():
    base = settings.KONEPS_RETRY_BACKOFF_MS / 1000
    assert live_failure.retry_delay_seconds(0) == pytest.approx(base)
    assert live_failure.retry_delay_seconds(1) == pytest.approx(base * 2)
    assert live_failure.retry_delay_seconds(2) == pytest.approx(base * 3)


def test_live_collection_error_builds_message_and_context():
    original = ValueError("boom")
    attempts = [{"attempt": 1}]
    err = live_failure.live_collection_error(
        stage="opening_result",
        attempts=attempts,
        original_error=original,
    )
    assert isinstance(err, KonepsLiveCollectionError)
    assert err.stage == "opening_result"
    assert err.attempts == attempts
    assert err.original_error is original
    assert str(err) == "KONEPS opening_result failed after 1 attempt(s): boom"


def test_live_collection_error_uses_type_name_when_no_detail():
    original = ValueError()
    err = live_failure.live_collection_error(
        stage="notice_search",
        attempts=[],
        original_error=original,
    )
    assert str(err) == "KONEPS notice_search failed after 0 attempt(s): ValueError"
