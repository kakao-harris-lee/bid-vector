"""Pure live-failure classification/retry helpers for the KONEPS collector.

These functions, the ``KonepsLiveCollectionError`` exception, and the
``LIVE_FAILURE_RETRYABLE_CATEGORIES`` constant were extracted verbatim from
``KonepsCollectorService`` (``collector.py``). They have no IO (``requests`` /
Playwright ``page``), DB (``Session`` / ``db.query`` / ``db.add``), or
instance-state dependencies -- they operate only on plain exceptions/dicts and
the module-level ``settings`` (linear backoff window) -- so they live here as
module-level pure helpers to keep the collector class focused on orchestration,
DB persistence, and IO.

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite. To avoid an import cycle, this module must
never import ``collector``: the collector imports ``live_failure`` (and the
sibling ``parsing`` / ``openapi`` / ``html_parsing`` / ``matching`` modules),
not the other way around. The collector re-imports ``KonepsLiveCollectionError``
from here so its ``raise`` / ``except`` paths stay compatible, and keeps thin
delegator methods (``_build_live_retry_attempt`` / ``_live_collection_error``)
for external callers that invoke them as instance methods.
"""

from typing import Any

from app.core.config import settings


LIVE_FAILURE_RETRYABLE_CATEGORIES = {"network", "timeout", "unknown"}


class KonepsLiveCollectionError(RuntimeError):
    """Wrap live crawl failures with retry attempts and crawl-stage metadata."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        attempts: list[dict[str, Any]] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.attempts = attempts or []
        self.original_error = original_error


def live_collection_error(
    *,
    stage: str,
    attempts: list[dict[str, Any]],
    original_error: Exception,
) -> KonepsLiveCollectionError:
    """Build a live crawl exception with retry context attached."""
    attempt_count = len(attempts)
    detail = str(original_error) or type(original_error).__name__
    message = f"KONEPS {stage} failed after {attempt_count} attempt(s): {detail}"
    return KonepsLiveCollectionError(
        message,
        stage=stage,
        attempts=attempts,
        original_error=original_error,
    )


def build_live_retry_attempt(
    *,
    stage: str,
    attempt_index: int,
    exc: Exception,
    final_attempt: bool,
) -> dict[str, Any]:
    """Build one retry-attempt payload for operations diagnostics."""
    failure_payload = live_failure_payload(exc, stage=stage)
    next_delay_seconds = None if final_attempt else retry_delay_seconds(attempt_index)
    return {
        "attempt": attempt_index + 1,
        "stage": stage,
        "category": failure_payload["category"],
        "retryable": failure_payload["retryable"],
        "exception_type": failure_payload["exception_type"],
        "error_message": failure_payload["error_message"],
        "next_retry_delay_seconds": next_delay_seconds,
        "final_attempt": final_attempt,
    }


def live_failure_payload(exc: Exception, *, stage: str) -> dict[str, Any]:
    """Classify a live crawl exception into a stable operations payload."""
    original_error = getattr(exc, "original_error", None) or exc
    resolved_stage = str(getattr(exc, "stage", stage) or stage)
    attempts = getattr(exc, "attempts", None)
    category = classify_live_failure(original_error)
    retryable = category in LIVE_FAILURE_RETRYABLE_CATEGORIES
    detail = str(exc) or str(original_error) or type(original_error).__name__

    payload: dict[str, Any] = {
        "stage": resolved_stage,
        "category": category,
        "retryable": retryable,
        "detail": detail,
        "error_message": str(original_error) or detail,
        "exception_type": type(original_error).__name__,
    }
    if attempts:
        payload["attempt_count"] = len(attempts)
        payload["attempts"] = attempts
    return payload


def classify_live_failure(exc: Exception) -> str:
    """Map browser/network/parser errors to operator-actionable categories."""
    lowered_message = str(exc or "").lower()
    exception_name = type(exc).__name__.lower()

    if any(
        marker in lowered_message
        for marker in ("captcha", "access denied", "forbidden", "403", "blocked")
    ):
        return "access_denied"
    if any(
        marker in lowered_message
        for marker in (
            "browser not available",
            "executable doesn't exist",
            "playwright install",
            "failed to launch",
            "target page, context or browser has been closed",
        )
    ):
        return "browser_runtime"
    if any(
        marker in lowered_message
        for marker in (
            "err_name_not_resolved",
            "err_connection",
            "econn",
            "net::",
            "network",
            "connection reset",
            "connection refused",
        )
    ):
        return "network"
    if any(
        marker in lowered_message
        for marker in ("no notice items", "no result", "empty result")
    ):
        return "no_data"
    if any(
        marker in lowered_message
        for marker in (
            "could not be located",
            "selector",
            "locator",
            "strict mode violation",
            "waiting for",
        )
    ):
        return "selector_drift"
    if "timeout" in lowered_message or "timeout" in exception_name:
        return "timeout"
    return "unknown"


def retry_delay_seconds(attempt_index: int) -> float:
    """Return linear backoff delay for the next retry attempt."""
    return (settings.KONEPS_RETRY_BACKOFF_MS * (attempt_index + 1)) / 1000
