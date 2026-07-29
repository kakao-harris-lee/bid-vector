"""Tests for the startup embedding-model warm-up (app.services.model_warmup).

The warm-up exists to move the ~25s lazy SentenceTransformer load off the first
request after an api restart. These tests pin the three contracts that matter:

1. ``warm_embedding_model`` uses the injected loader and forces one ``encode``.
2. Failure (loader raising / returning no model) is swallowed — a warm-up must
   never take the api down.
3. The startup gate is pure and OFF in tests, so no test process ever loads the
   real model (conftest pins ENVIRONMENT=test).

It also pins the loader's single-load guarantee: the warm-up thread and a
concurrent request must share one model, never build two copies of it.
"""
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core.config import settings
from app.services import model_warmup
from app.services.classifier import NoticeClassifierService


class FakeEmbeddingModel:
    """Minimal stand-in recording the encode calls the warm-up makes."""

    def __init__(self):
        self.encode_calls = []

    def encode(self, texts, **kwargs):
        self.encode_calls.append((texts, kwargs))
        return [[0.0, 1.0]]


def _config(*, environment: str, warmup_enabled: bool) -> SimpleNamespace:
    """Build the minimal structural config the startup gate reads."""
    return SimpleNamespace(
        ENVIRONMENT=environment,
        EMBEDDING_MODEL_WARMUP_ON_STARTUP=warmup_enabled,
    )


# ---------------------------------------------------------------------------
# 1. warm_embedding_model — happy path
# ---------------------------------------------------------------------------
def test_warm_embedding_model_uses_injected_loader_and_encodes_once():
    model = FakeEmbeddingModel()
    loader_calls = []

    def loader():
        loader_calls.append(True)
        return model

    assert model_warmup.warm_embedding_model(loader=loader) is True
    assert len(loader_calls) == 1
    assert len(model.encode_calls) == 1

    texts, _kwargs = model.encode_calls[0]
    assert texts == [model_warmup.WARMUP_PROBE_TEXT]
    assert model_warmup.WARMUP_PROBE_TEXT.strip()


# ---------------------------------------------------------------------------
# 2. warm_embedding_model — failures are swallowed
# ---------------------------------------------------------------------------
def test_warm_embedding_model_returns_false_when_loader_raises():
    def loader():
        raise RuntimeError("model files missing")

    assert model_warmup.warm_embedding_model(loader=loader) is False


def test_warm_embedding_model_returns_false_when_model_unavailable():
    """The classifier loader returns None when sentence-transformers is absent."""
    assert model_warmup.warm_embedding_model(loader=lambda: None) is False


def test_warm_embedding_model_returns_false_when_encode_raises():
    class ExplodingModel:
        def encode(self, texts, **kwargs):
            raise ValueError("bad tokenizer")

    assert model_warmup.warm_embedding_model(loader=lambda: ExplodingModel()) is False


# ---------------------------------------------------------------------------
# 3. should_warm_on_startup — pure gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("environment", "warmup_enabled", "expected"),
    [
        ("production", True, True),
        ("development", True, True),
        ("production", False, False),
        ("test", True, False),
        ("test", False, False),
    ],
)
def test_should_warm_on_startup_gate(environment, warmup_enabled, expected):
    config = _config(environment=environment, warmup_enabled=warmup_enabled)
    assert model_warmup.should_warm_on_startup(config) is expected


def test_should_warm_on_startup_is_false_for_the_test_environment():
    """conftest pins ENVIRONMENT=test, so the live settings must gate warm-up off."""
    assert settings.ENVIRONMENT == "test"
    assert model_warmup.should_warm_on_startup(settings) is False


# ---------------------------------------------------------------------------
# 4. start_embedding_model_warmup — background daemon thread, gated
# ---------------------------------------------------------------------------
def test_start_embedding_model_warmup_runs_in_daemon_thread_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(model_warmup, "warm_embedding_model", lambda: calls.append(True))

    thread = model_warmup.start_embedding_model_warmup(
        _config(environment="production", warmup_enabled=True)
    )

    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=5)
    assert thread.is_alive() is False
    assert calls == [True]


def test_start_embedding_model_warmup_skips_when_gate_is_closed(monkeypatch):
    calls = []
    monkeypatch.setattr(model_warmup, "warm_embedding_model", lambda: calls.append(True))

    assert (
        model_warmup.start_embedding_model_warmup(
            _config(environment="production", warmup_enabled=False)
        )
        is None
    )
    assert (
        model_warmup.start_embedding_model_warmup(
            _config(environment="test", warmup_enabled=True)
        )
        is None
    )
    assert calls == []


def test_start_embedding_model_warmup_defaults_to_app_settings(monkeypatch):
    """Called with no config it reads settings, which is ENVIRONMENT=test here."""
    calls = []
    monkeypatch.setattr(model_warmup, "warm_embedding_model", lambda: calls.append(True))

    assert model_warmup.start_embedding_model_warmup() is None
    assert calls == []


# ---------------------------------------------------------------------------
# 5. lifespan wiring — startup never loads the model under ENVIRONMENT=test
# ---------------------------------------------------------------------------
def test_app_startup_does_not_warm_the_model_in_tests(monkeypatch):
    loader_calls = []
    monkeypatch.setattr(
        model_warmup, "warm_embedding_model", lambda: loader_calls.append(True)
    )
    default_loader_calls = []
    monkeypatch.setattr(
        model_warmup,
        "load_classifier_embedding_model",
        lambda: default_loader_calls.append(True),
    )

    with TestClient(main.app) as test_client:
        assert test_client.get("/health").status_code == 200

    assert loader_calls == []
    assert default_loader_calls == []


# ---------------------------------------------------------------------------
# 6. Loader single-load guarantee (the warm-up thread races real requests)
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_embedding_cache():
    """Run with an empty class-level model cache, then restore the real one."""
    service = NoticeClassifierService
    saved = (
        service._embedding_model,
        service._embedding_model_name,
        service._embedding_model_failed,
    )
    service._embedding_model = None
    service._embedding_model_name = None
    service._embedding_model_failed = False
    yield service
    (
        service._embedding_model,
        service._embedding_model_name,
        service._embedding_model_failed,
    ) = saved


def test_concurrent_callers_load_the_embedding_model_only_once(
    monkeypatch, clean_embedding_cache
):
    """A request arriving mid warm-up waits for that load, it does not start one."""
    service = clean_embedding_cache
    constructions = []
    entered_constructor = threading.Event()
    release_constructor = threading.Event()

    class SlowSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            constructions.append(model_name)
            entered_constructor.set()
            release_constructor.wait(timeout=5)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=SlowSentenceTransformer),
    )

    results = []

    def call_loader():
        results.append(service.get_embedding_model())

    warmup_thread = threading.Thread(target=call_loader)
    request_thread = threading.Thread(target=call_loader)

    warmup_thread.start()
    assert entered_constructor.wait(timeout=5)
    request_thread.start()
    time.sleep(0.05)  # the second caller must be blocked on the load lock
    assert len(constructions) == 1

    release_constructor.set()
    warmup_thread.join(timeout=5)
    request_thread.join(timeout=5)

    assert len(constructions) == 1
    assert len(results) == 2
    assert results[0] is results[1] is service._embedding_model
