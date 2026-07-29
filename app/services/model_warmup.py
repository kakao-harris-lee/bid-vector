"""Background warm-up for the shared sentence-transformers embedding model.

The classifier loads its embedding model lazily (``NoticeClassifierService.
get_embedding_model``), so the first request that needs inline ML after an api
restart pays the full model load. Measured on this host: ~25s for that first
analysis versus ~0.8s once warm. Because every deploy restarts the api, a real
operator hits that penalty on the candidate preview, reloads the page while it
hangs, and the stacked duplicate requests then fight for CPU.

This module moves that one-off cost to startup, in a daemon thread, so it never
blocks the event loop, the startup sequence, or ``/health``. A failed warm-up is
logged and ignored — the lazy loader still works, the request path is unchanged.
"""
import logging
import threading
import time
from typing import Any, Callable, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

# Constructing SentenceTransformer does not finish initialisation (tokenizer and
# the first forward pass are deferred), so the warm-up runs one tiny encode to
# reach the same state a real request would.
WARMUP_PROBE_TEXT = "웜업"
WARMUP_THREAD_NAME = "embedding-model-warmup"


class WarmupConfig(Protocol):
    """Structural view of the settings the startup gate reads."""

    ENVIRONMENT: str
    EMBEDDING_MODEL_WARMUP_ON_STARTUP: bool


def load_classifier_embedding_model() -> Any:
    """Default loader: the classifier's cached embedding model (or ``None``).

    Imported inside the function so app startup does not pull the classifier
    (and its ML dependencies) at module import time.
    """
    from app.services.classifier import NoticeClassifierService

    return NoticeClassifierService.get_embedding_model()


def warm_embedding_model(loader: Callable[[], Any] | None = None) -> bool:
    """Load the embedding model and run one encode. Returns success.

    ``loader`` is the injection seam (tests pass a fake); it defaults to the
    classifier's cached loader, so warming populates the very cache the request
    path reads. All failures are swallowed: warm-up is an optimisation and must
    never take the api down.
    """
    resolve_model = loader or load_classifier_embedding_model
    started_at = time.monotonic()
    logger.info("Embedding model warm-up started")

    try:
        model = resolve_model()
        if model is None:
            logger.warning("Embedding model warm-up skipped: model unavailable")
            return False
        model.encode([WARMUP_PROBE_TEXT], normalize_embeddings=True)
    except Exception as exc:
        logger.warning(
            "Embedding model warm-up failed: %s: %s", type(exc).__name__, exc
        )
        return False

    logger.info(
        "Embedding model warm-up finished in %.1fs", time.monotonic() - started_at
    )
    return True


def should_warm_on_startup(config: WarmupConfig) -> bool:
    """Pure startup gate: opt-out flag plus the test-environment exclusion.

    Tests must never load the real model (the classification paths already fall
    back to lexical similarity under ``ENVIRONMENT=test``).
    """
    return bool(config.EMBEDDING_MODEL_WARMUP_ON_STARTUP) and config.ENVIRONMENT != "test"


def start_embedding_model_warmup(
    config: WarmupConfig | None = None,
) -> threading.Thread | None:
    """Start the warm-up in a daemon thread when the gate is open.

    Returns the started thread, or ``None`` when warm-up is gated off. Daemon so
    a slow model load can never delay process shutdown.
    """
    active_config = config if config is not None else settings
    if not should_warm_on_startup(active_config):
        return None

    thread = threading.Thread(
        target=warm_embedding_model,
        name=WARMUP_THREAD_NAME,
        daemon=True,
    )
    thread.start()
    return thread
