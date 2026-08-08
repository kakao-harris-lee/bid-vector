"""Tests for predictor selection and metadata."""

import json
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock, Thread

import numpy as np
import pytest

from app.ai.price_prediction import predict_price
from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PricePredictionContext,
)
from app.ai.predictors.registry import normalize_predictor_registry
from app.core.config import settings
from app.models.models import HistoricalData


def _build_grouped_history(count_per_group: int, *, base_rate: float = 0.910) -> list[dict]:
    """Build historical records with alternating business_group values."""
    records = []
    groups = ["construction", "service"]
    for i in range(count_per_group * len(groups)):
        group = groups[i % len(groups)]
        rate = round(base_rate + i * 0.001, 6)
        records.append({
            "bid_rate": rate,
            "base_amount": 100_000_000.0,
            "predicted_price": round(100_000_000.0 * rate, 2),
            "business_group": group,
        })
    return records


def _build_bid_rate_history(count: int, *, base_rate: float = 0.914, step: float = 0.0012) -> list[dict[str, float]]:
    return [
        {
            "bid_rate": round(base_rate + (index * step), 6),
            "base_amount": 100000000.0,
            "predicted_price": round(100000000.0 * (base_rate + (index * step)), 2),
        }
        for index in range(count)
    ]



def _write_ensemble_artifact(tmp_path) -> str:
    artifact_path = tmp_path / "ensemble_artifact.json"
    artifact_path.write_text(
        json.dumps(
            {
                "artifact_version": "1",
                "model_version": "v2.0-ensemble",
                "sequence_length": 8,
                "momentum_window": 5,
                "scenario_spread_multiplier": 1.05,
                "confidence_bias": 0.02,
                "component_weights": {
                    "historical": 0.5,
                    "momentum": 0.2,
                    "mean_reversion": 0.15,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(artifact_path)


class InjectedRegistryPredictor(BasePricePredictor):
    name = "injected_registry_predictor"
    family = "test"

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        if self.should_fail:
            raise RuntimeError("injected failure")
        # Built through the typed constructor, so this fake proves an injected
        # predictor satisfies the SAME output contract as the real ones — the
        # nullable-but-required fields must be stated, not omitted.
        return PredictionResult(
            predicted_price=context.budget * 0.91,
            price_range_min=context.budget * 0.90,
            price_range_max=context.budget * 0.92,
            confidence_score=0.7,
            model_version="injected-v1",
            pricing_mode="historical_blend",
            historical_sample_size=context.historical_sample_size,
            agency_match_sample_size=0,
            predicted_bid_rate=0.91,
            bid_rate_candidates=[
                {"label": "base", "bid_rate": 0.91, "predicted_price": context.budget * 0.91},
            ],
            reserve_price_context=None,
            feedback_calibration=None,
            guardrail_applied=False,
            guardrail_reason=None,
            floor_bid_rate=None,
            floor_price=None,
            explanation="injected registry predictor",
        )


def test_predict_price_accepts_injected_predictor_registry(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "injected")

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="injected registry",
        historical_records=[{"bid_rate": 0.91}],
        predictor_registry={
            "historical": InjectedRegistryPredictor(),
            "injected": InjectedRegistryPredictor(),
        },
    )

    assert prediction["predictor_name"] == "injected_registry_predictor"
    assert prediction["predictor_family"] == "test"


def test_predict_price_uses_injected_historical_fallback_when_selected_predictor_fails(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "injected")

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="fallback registry",
        historical_records=[{"bid_rate": 0.91}],
        predictor_registry={
            "historical": InjectedRegistryPredictor(),
            "injected": InjectedRegistryPredictor(should_fail=True),
        },
    )

    assert prediction["predictor_name"] == "injected_registry_predictor"
    assert "injected failure" in prediction["fallback_reason"]


def test_normalize_predictor_registry_keeps_explicit_empty_mapping_sparse():
    registry = normalize_predictor_registry({})

    assert "historical" in registry
    assert "ensemble" not in registry


class _RecordingHistoricalPredictor(BasePricePredictor):
    """Fake historical anchor that records calls and returns a sentinel rate.

    Injected into the ensemble/LSTM payload builders to prove the historical
    statistical anchor is consumed through the injection seam (not a hard-coded
    ``HistoricalStatisticalPredictor()`` inside the function body).
    """

    name = "recording_historical"
    family = "test"

    def __init__(self, *, bid_rate: float) -> None:
        self._bid_rate = bid_rate
        self.calls = 0

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        self.calls += 1
        predicted_price = round(context.budget * self._bid_rate, 2)
        return PredictionResult(
            predicted_bid_rate=self._bid_rate,
            predicted_price=predicted_price,
            price_range_min=predicted_price,
            price_range_max=predicted_price,
            confidence_score=0.6,
            model_version="recording-historical",
            pricing_mode="historical_blend",
            historical_sample_size=context.historical_sample_size,
            agency_match_sample_size=0,
            bid_rate_candidates=[],
            reserve_price_context=None,
            feedback_calibration=None,
            guardrail_applied=False,
            guardrail_reason=None,
            floor_bid_rate=None,
            floor_price=None,
            explanation="recording historical",
        )


def test_build_ensemble_prediction_payload_consumes_injected_historical_predictor():
    from app.ai.predictors.ensemble import build_ensemble_prediction_payload, load_ensemble_artifact

    artifact = load_ensemble_artifact(
        {
            "artifact_version": "1",
            "model_version": "v2.0-ensemble",
            "sequence_length": 8,
            "momentum_window": 5,
            "scenario_spread_multiplier": 1.05,
            "confidence_bias": 0.02,
            "component_weights": {"historical": 0.5, "momentum": 0.3, "mean_reversion": 0.2},
        }
    )
    context = PricePredictionContext(
        budget=100_000_000.0,
        category="software",
        description="ensemble injection",
        historical_records=tuple(_build_bid_rate_history(12)),
    )
    fake = _RecordingHistoricalPredictor(bid_rate=0.8123)

    payload = build_ensemble_prediction_payload(context, artifact=artifact, historical_predictor=fake)

    # The injected fake is the anchor: called exactly once, and its sentinel
    # rate flows into the ensemble's "historical" component (surfaced verbatim
    # in the explanation's component breakdown).
    assert fake.calls == 1
    assert "historical(0.8123" in payload["explanation"]



def test_predict_price_reports_historical_predictor_metadata_by_default():
    """The baseline historical predictor should identify itself in the response payload."""
    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="predictor metadata test",
        historical_records=[
            {"bid_rate": 0.914},
            {"bid_rate": 0.921},
            {"bid_rate": 0.933},
            {"bid_rate": 0.941},
        ],
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["predictor_family"] == "statistical"
    assert prediction["fallback_reason"] is None
    assert prediction["training_window_size"] == 4


def test_predict_price_falls_back_to_historical_when_experimental_is_unavailable(monkeypatch):
    """Unavailable experimental predictors should fall back to the stable historical baseline."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="ensemble fallback test",
        historical_records=[
            {"bid_rate": 0.914},
            {"bid_rate": 0.921},
            {"bid_rate": 0.933},
            {"bid_rate": 0.941},
        ],
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["predictor_family"] == "statistical"
    assert prediction["training_window_size"] == 4
    assert prediction["fallback_reason"] is not None
    assert "ensemble_blend" in prediction["fallback_reason"]
    assert "unavailable" in prediction["fallback_reason"].lower()



def test_predict_price_uses_ensemble_predictor_when_artifact_is_configured(monkeypatch, tmp_path):
    """Configured ensemble artifacts should blend multiple components into one prediction."""
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="ensemble predictor inference test",
        historical_records=_build_bid_rate_history(12, base_rate=0.912, step=0.001),
    )

    assert prediction["predictor_name"] == "ensemble_blend"
    assert prediction["predictor_family"] == "ensemble"
    assert prediction["fallback_reason"] is None
    assert prediction["model_version"] == "v2.0-ensemble"
    assert prediction["training_window_size"] == 12
    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["historical_sample_size"] == 12
    assert 0.9 <= prediction["predicted_bid_rate"] <= 1.05
    assert "ensemble이" in prediction["explanation"]



def test_artifact_provider_resets_pid_local_lock_and_cache_after_fork_boundary(
    monkeypatch, tmp_path
):
    """A child PID must never wait on a lock inherited from a parent thread."""
    import app.ai.predictors.artifact_provider as artifact_provider
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    artifact_path = tmp_path / "pid-local-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    load_calls: list[str] = []
    child_hooks = []

    monkeypatch.setattr(
        artifact_provider.os,
        "register_at_fork",
        lambda **hooks: child_hooks.append(hooks["after_in_child"]),
    )

    def _load(source):
        load_calls.append(str(source))
        return json.loads(Path(source).read_text(encoding="utf-8"))

    provider = VersionAwareArtifactProvider(_load)
    assert provider.load(artifact_path)["model_version"] == "v1"
    parent_state = provider._state
    parent_pid = artifact_provider.os.getpid()
    completed = Event()
    loaded_versions: list[str] = []
    errors: list[BaseException] = []

    parent_state.lock.acquire()
    monkeypatch.setattr(artifact_provider.os, "getpid", lambda: parent_pid + 1)
    assert len(child_hooks) == 1
    child_hooks[0]()

    def _load_in_child_pid() -> None:
        try:
            loaded_versions.append(provider.load(artifact_path)["model_version"])
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            completed.set()

    thread = Thread(target=_load_in_child_pid)
    thread.start()
    completed_without_parent_unlock = completed.wait(timeout=1.0)
    parent_state.lock.release()
    thread.join(timeout=1.0)

    assert completed_without_parent_unlock is True
    assert errors == []
    assert loaded_versions == ["v1"]
    assert load_calls == [str(artifact_path), str(artifact_path)]


def test_artifact_provider_remains_portable_without_register_at_fork(
    monkeypatch, tmp_path
):
    """Platforms without a fork hook retain the atomic lazy PID fallback."""
    import app.ai.predictors.artifact_provider as artifact_provider
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    artifact_path = tmp_path / "portable-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    monkeypatch.delattr(artifact_provider.os, "register_at_fork", raising=False)

    provider = VersionAwareArtifactProvider(
        lambda source: json.loads(Path(source).read_text(encoding="utf-8"))
    )

    assert provider.load(artifact_path)["model_version"] == "v1"


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded, use of fork.*:DeprecationWarning"
)
def test_hookless_artifact_provider_does_not_wait_on_inherited_transition_lock(
    monkeypatch, tmp_path
):
    """A real fork must finish even when another parent thread owns the fallback lock."""
    import os

    import app.ai.predictors.artifact_provider as artifact_provider
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    if not hasattr(os, "fork"):
        pytest.skip("requires os.fork")

    artifact_path = tmp_path / "hookless-fork-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    monkeypatch.delattr(artifact_provider.os, "register_at_fork", raising=False)
    provider = VersionAwareArtifactProvider(
        lambda source: json.loads(Path(source).read_text(encoding="utf-8"))
    )
    assert provider.load(artifact_path)["model_version"] == "v1"

    lock_held = Event()
    release_parent_lock = Event()

    def _hold_parent_transition_lock() -> None:
        with provider._process_transition_lock:
            lock_held.set()
            release_parent_lock.wait(timeout=5.0)

    holder = Thread(target=_hold_parent_transition_lock)
    holder.start()
    assert lock_held.wait(timeout=1.0)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:  # pragma: no cover - assertions execute in the parent
        os.close(read_fd)
        try:
            version = provider.load(artifact_path)["model_version"]
            os.write(write_fd, str(version).encode("utf-8"))
            exit_code = 0
        except BaseException as exc:
            os.write(write_fd, f"error:{exc}".encode("utf-8"))
            exit_code = 1
        finally:
            os.close(write_fd)
        os._exit(exit_code)

    os.close(write_fd)
    child_status = None
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline:
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                child_status = status
                break
            time.sleep(0.01)
        if child_status is None:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
    finally:
        release_parent_lock.set()
        holder.join(timeout=1.0)

    child_output = os.read(read_fd, 4096).decode("utf-8")
    os.close(read_fd)
    assert child_status is not None, "child deadlocked on an inherited transition lock"
    assert os.waitstatus_to_exitcode(child_status) == 0
    assert child_output == "v1"


def test_artifact_provider_serializes_concurrent_first_loads_after_pid_transition(
    monkeypatch, tmp_path
):
    """One child-side state wins even when first callers observe the new PID together."""
    import app.ai.predictors.artifact_provider as artifact_provider
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    artifact_path = tmp_path / "concurrent-child-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    load_lock = Lock()
    load_calls = 0

    def _load(source):
        nonlocal load_calls
        with load_lock:
            load_calls += 1
        return json.loads(Path(source).read_text(encoding="utf-8"))

    monkeypatch.delattr(artifact_provider.os, "register_at_fork", raising=False)
    provider = VersionAwareArtifactProvider(_load)
    assert provider.load(artifact_path)["model_version"] == "v1"

    worker_count = 8
    child_pid = artifact_provider.os.getpid() + 1
    pid_barrier = Barrier(worker_count)
    constructor_lock = Lock()
    all_child_constructors_started = Event()
    release_child_constructors = Event()
    child_constructor_count = 0
    original_state_type = artifact_provider._ArtifactProcessState

    class _SlowChildStateFactory:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __new__(cls, process_id):
            nonlocal child_constructor_count
            state = original_state_type(process_id)
            if process_id == child_pid:
                with constructor_lock:
                    child_constructor_count += 1
                    if child_constructor_count == worker_count:
                        all_child_constructors_started.set()
                assert release_child_constructors.wait(timeout=2.0)
            return state

    def _child_pid_after_rendezvous():
        pid_barrier.wait(timeout=2.0)
        return child_pid

    monkeypatch.setattr(
        artifact_provider, "_ArtifactProcessState", _SlowChildStateFactory
    )
    monkeypatch.setattr(
        artifact_provider.os, "getpid", _child_pid_after_rendezvous
    )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(provider.load, artifact_path) for _ in range(worker_count)]
        all_child_constructors_started.wait(timeout=0.5)
        release_child_constructors.set()
        artifacts = [future.result(timeout=2.0) for future in futures]

    assert [artifact["model_version"] for artifact in artifacts] == ["v1"] * worker_count
    assert load_calls == 2


def test_artifact_provider_serializes_concurrent_loads_for_one_version(tmp_path):
    """Concurrent first callers parse and normalize one immutable identity once."""
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    artifact_path = tmp_path / "concurrent-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    call_lock = Lock()
    load_calls = 0

    def _load(source):
        nonlocal load_calls
        with call_lock:
            load_calls += 1
        time.sleep(0.02)
        return {
            "model_version": json.loads(
                Path(source).read_text(encoding="utf-8")
            )["model_version"],
            "weights": np.asarray([[1.0, 2.0]], dtype=float),
        }

    provider = VersionAwareArtifactProvider(_load)
    with ThreadPoolExecutor(max_workers=8) as executor:
        artifacts = list(executor.map(provider.load, [artifact_path] * 8))

    assert load_calls == 1
    assert [artifact["model_version"] for artifact in artifacts] == ["v1"] * 8


def test_artifact_provider_returns_mutation_isolated_cached_values(tmp_path):
    """Caller mutation cannot poison later predictions that reuse the cache."""
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider

    artifact_path = tmp_path / "isolated-artifact.json"
    artifact_path.write_text('{"model_version": "v1"}', encoding="utf-8")
    load_calls = 0

    def _load(source):
        nonlocal load_calls
        load_calls += 1
        return {
            "metadata": {"model_version": "v1", "weights": {"lstm": 0.75}},
            "weights": np.asarray([[1.0, 2.0]], dtype=float),
        }

    provider = VersionAwareArtifactProvider(_load)
    first = provider.load(artifact_path)
    first["metadata"]["weights"]["lstm"] = 0.0
    first["weights"][0, 0] = 999.0

    second = provider.load(artifact_path)

    assert load_calls == 1
    assert second["metadata"]["weights"]["lstm"] == 0.75
    np.testing.assert_array_equal(second["weights"], np.asarray([[1.0, 2.0]]))


def test_ensemble_predictor_caches_unchanged_artifact_and_reloads_changed_identity(
    monkeypatch, tmp_path
):
    """The ensemble provider shares one normalized artifact across predictions."""
    from app.ai.predictors.artifact_contracts import VersionAwareArtifactProvider
    from app.ai.predictors.ensemble import EnsembleBidRatePredictor, load_ensemble_artifact
    import app.ai.predictors.historical as historical

    artifact_path = Path(_write_ensemble_artifact(tmp_path))
    load_calls: list[str] = []

    def _load_from_file(source):
        load_calls.append(str(source))
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        return load_ensemble_artifact(payload)

    provider = VersionAwareArtifactProvider(_load_from_file, max_entries=2)
    predictor = EnsembleBidRatePredictor(artifact_provider=provider)
    context = PricePredictionContext(
        budget=100_000_000.0,
        category="software",
        description="version-aware ensemble cache",
        historical_records=tuple(_build_bid_rate_history(12)),
    )
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(artifact_path))
    monkeypatch.setattr(historical, "load_group_calibration", lambda: {})

    assert predictor.check_availability(context).available is True
    assert predictor.predict(context).model_version == "v2.0-ensemble"
    assert predictor.predict(context).model_version == "v2.0-ensemble"
    assert load_calls == [str(artifact_path)]

    changed = json.loads(artifact_path.read_text(encoding="utf-8"))
    changed["model_version"] = "v2.0-ensemble-released-2"
    artifact_path.write_text(json.dumps(changed), encoding="utf-8")

    assert predictor.predict(context).model_version == "v2.0-ensemble-released-2"
    assert load_calls == [str(artifact_path), str(artifact_path)]


def test_ensemble_prediction_applies_service_procurement_rate_bands(monkeypatch, tmp_path):
    """Ensemble output should share the same service subtype target bands as the baseline."""
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)
    history = _build_bid_rate_history(12, base_rate=0.912, step=0.001)

    negotiated_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="콘텐츠 플랫폼 운영 위탁 용역 협상에 의한 계약",
        historical_records=history,
    )
    competitive_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="건설폐기물 처리용역 가격입찰",
        historical_records=history,
    )
    service_low_tail_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="[당진]지방도615호 덕두교 등 2개교 정밀안전진단용역",
        historical_records=history,
    )

    assert negotiated_prediction["predictor_name"] == "ensemble_blend"
    assert negotiated_prediction["procurement_rate_band"] == "service_high_negotiated"
    assert negotiated_prediction["predicted_bid_rate"] == 1.0
    assert competitive_prediction["predictor_name"] == "ensemble_blend"
    assert competitive_prediction["procurement_rate_band"] == "service_price_competitive"
    assert competitive_prediction["predicted_bid_rate"] == 0.9
    assert service_low_tail_prediction["predictor_name"] == "ensemble_blend"
    assert service_low_tail_prediction["procurement_rate_band"] == "service_price_competitive"
    assert service_low_tail_prediction["predicted_bid_rate"] == 0.9


def test_ensemble_prediction_lifts_goods_recent_high_rate_tail(monkeypatch, tmp_path):
    """Goods outcomes clustered near 100% should lift the recommended/base scenario."""
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)

    recent_high_tail = [{"bid_rate": rate, "base_amount": 100000000.0} for rate in [
        0.982,
        0.991,
        0.998,
        1.0,
        0.974,
        0.989,
        1.0,
        0.996,
        0.951,
        0.986,
        0.997,
        1.0,
    ]]
    older_low_band = [{"bid_rate": rate, "base_amount": 100000000.0} for rate in [
        0.862,
        0.875,
        0.881,
        0.889,
        0.902,
        0.918,
        0.927,
        0.934,
    ]]

    prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="디지털서비스 클라우드 구독 라이선스 단독공급",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )

    assert prediction["predictor_name"] == "ensemble_blend"
    assert prediction["predicted_bid_rate"] >= 0.97
    assert prediction["high_rate_tail_adjustment"]["reason"] == "goods_recent_high_rate_tail"
    assert "최근 고율 낙찰 분포" in prediction["explanation"]

    competitive_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="관급자재 부스터펌프 구매 및 설치 소액수의 견적 제출",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )
    deep_discount_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="급식용 농산물 구매 2단계 입찰 공고",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )
    narrow_control_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="백암면(평창5블록) 인입지점 복선화 사업(계측제어)",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )

    assert competitive_prediction["predictor_name"] == "ensemble_blend"
    assert competitive_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert competitive_prediction["predicted_bid_rate"] == 0.9
    assert competitive_prediction["high_rate_tail_adjustment"] is None
    assert all(item["bid_rate"] <= 0.91 for item in competitive_prediction["bid_rate_candidates"])
    assert deep_discount_prediction["predictor_name"] == "ensemble_blend"
    assert deep_discount_prediction["procurement_rate_band"] == "goods_deep_discount"
    assert deep_discount_prediction["predicted_bid_rate"] == 0.841
    assert deep_discount_prediction["high_rate_tail_adjustment"] is None
    assert narrow_control_prediction["predictor_name"] == "ensemble_blend"
    assert narrow_control_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert narrow_control_prediction["predicted_bid_rate"] == 0.9
    assert narrow_control_prediction["high_rate_tail_adjustment"] is None


def test_predict_price_auto_selector_uses_backtest_metadata(monkeypatch):
    """Auto predictor selection should run a rolling backtest and expose selector metadata."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "auto")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 3)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 3)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="auto selector backtest metadata",
        historical_records=_build_bid_rate_history(8, base_rate=0.91, step=0.001),
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["selector_name"] == "rolling_backtest"
    assert prediction["backtest_sample_count"] == 3
    assert prediction["backtest_average_absolute_error_rate"] is not None
    assert prediction["selection_reason"]
    assert prediction["backtest_report"]["best_predictor_key"] == "historical"


def test_price_prediction_endpoint_exposes_predictor_metadata(client, test_db):
    """The API response should surface the selected predictor metadata."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Predictor Metadata Project",
            "description": "predictor metadata endpoint test",
            "requirements": "Need predictor metadata",
            "budget_estimate": 130000000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number="PREDICTOR-META-1",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.916,
            bid_rate=0.916,
        ),
        HistoricalData(
            notice_number="PREDICTOR-META-2",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.924,
            bid_rate=0.924,
        ),
        HistoricalData(
            notice_number="PREDICTOR-META-3",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.931,
            bid_rate=0.931,
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 130000000.0,
            "category": "software",
            "description": "predictor metadata endpoint test",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["predictor_name"] == "historical_statistical"
    assert data["predictor_family"] == "statistical"
    assert data["fallback_reason"] is None
    assert data["training_window_size"] == 3


def test_price_prediction_endpoint_can_use_experimental_predictor(client, test_db, monkeypatch, tmp_path):
    """The API should surface experimental predictor metadata when a real artifact is configured."""
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 6)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Ensemble Predictor Project",
            "description": "endpoint should expose ensemble predictor metadata",
            "requirements": "Need blended predictor metadata",
            "budget_estimate": 125000000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    now = datetime.now(UTC)
    test_db.add_all([
        HistoricalData(
            notice_number=f"ENSEMBLE-PREDICTOR-{index}",
            category="software",
            base_amount=125000000.0,
            predicted_price=125000000.0 * bid_rate,
            bid_rate=bid_rate,
            opened_at=now - timedelta(days=(8 - index)),
        )
        for index, bid_rate in enumerate([0.913, 0.916, 0.919, 0.923, 0.927, 0.931, 0.934], start=1)
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 125000000.0,
            "category": "software",
            "description": "endpoint should expose ensemble predictor metadata",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["predictor_name"] == "ensemble_blend"
    assert data["predictor_family"] == "ensemble"
    assert data["fallback_reason"] is None
    assert data["model_version"] == "v2.0-ensemble"
    assert data["training_window_size"] == 7


def test_backtest_report_includes_by_group_dimension(monkeypatch):
    """Report includes per-group aggregated metrics (construction/service)."""
    from app.ai.predictor_backtest import build_predictor_backtest_report
    from app.ai.predictors.base import PricePredictionContext
    from app.ai.predictors.historical import HistoricalStatisticalPredictor

    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 4)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 4)

    # 12 records total: 6 construction + 6 service, interleaved — last 4 become holdout
    historical_records = tuple(_build_grouped_history(6, base_rate=0.910))

    context = PricePredictionContext(
        budget=100_000_000.0,
        category="construction",
        description="by_group dimension test",
        historical_records=historical_records,
    )
    registry = {"historical": HistoricalStatisticalPredictor()}

    report = build_predictor_backtest_report(context, registry)

    assert "by_group" in report, "report must contain 'by_group' key"
    by_group = report["by_group"]
    assert isinstance(by_group, dict), "by_group must be a dict"
    # both groups must be present (interleaved records ensure at least one holdout each)
    assert set(by_group.keys()) >= {"construction", "service"}, (
        f"expected both groups, got {set(by_group.keys())}"
    )
    construction = by_group["construction"]
    assert "sample_count" in construction, "group entry must have sample_count"
    assert construction["sample_count"] >= 1
    assert "average_absolute_error_rate" in construction or "mae" in construction, (
        "group entry must have error metric"
    )
