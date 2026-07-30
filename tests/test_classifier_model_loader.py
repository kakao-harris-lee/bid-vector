"""NoticeClassifierService 임베딩 모델 로더의 단일 로드 보장 가드.

구 test_model_warmup.py §6 에서 이전(설계 2026-07-30 §6.3 — API 시작 웜업
제거로 warmup 모듈은 삭제됐지만, 요청 경로 lazy-load 가 동시에 두 번 모델을
만들지 않는다는 계약은 단일 공고 ML 경로에 여전히 유효하다).
"""
import threading
import time
from types import SimpleNamespace

import pytest

from app.services.classifier import NoticeClassifierService


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
    """A request arriving mid-load waits for that load, it does not start one."""
    service = clean_embedding_cache
    constructions = []
    entered_constructor = threading.Event()
    release_constructor = threading.Event()

    def slow_build(model_name):
        """Stand in for the ~25s SentenceTransformer construction."""
        constructions.append(model_name)
        entered_constructor.set()
        release_constructor.wait(timeout=5)
        return SimpleNamespace(name=model_name)

    # Patch only the construction delegator, not the sentence_transformers module
    # (§4.7: keep the monkeypatch surface to one delegator).
    monkeypatch.setattr(service, "_build_embedding_model", slow_build)

    results = []

    def call_loader():
        results.append(service.get_embedding_model())

    first_thread = threading.Thread(target=call_loader)
    second_thread = threading.Thread(target=call_loader)

    first_thread.start()
    assert entered_constructor.wait(timeout=5)
    second_thread.start()
    time.sleep(0.05)  # the second caller must be blocked on the load lock
    assert len(constructions) == 1

    release_constructor.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert len(constructions) == 1
    assert len(results) == 2
    assert results[0] is results[1] is service._embedding_model
