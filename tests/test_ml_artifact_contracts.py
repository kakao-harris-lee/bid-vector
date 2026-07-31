"""ML 아티팩트·릴리스 문서 JSON 왕복 계약 테스트.

세 갈래를 고정한다:

1. **predictor 아티팩트**(LSTM/앙상블) — happy 경로의 정규화 산출이 종전과 같고, 손상
   아티팩트는 종전과 같은 ``ValueError`` 로 거부되며, 관용해야 하는 과거 배치(flat weights,
   ``summary`` 덧붙임, 비매핑 블록)는 계속 통과한다.
2. **보정 표 읽기** — 손상 아티팩트는 조용히 사라지지 않고 경고와 함께 빈 표로 degrade 한다.
3. **release 문서** — 원문 매핑 보존이 **서명 바이트 동일성**으로 고정된다(지수 표기
   부동소수 포함). 이 성질이 깨지면 이미 발행된 릴리스의 서명 검증이 전부 깨진다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.ai.llm_output_contracts import LLMDocumentAnalysisOutput
from app.ai.predictors.artifact_contracts import (
    PersistedArtifactSummaryDocument,
    PersistedEnsembleArtifact,
    PersistedLSTMArtifact,
    read_persisted_artifact,
)
from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.historical import load_group_calibration
from app.ai.predictors.historical.calibration import load_probability_calibration
from app.ai.predictors.lstm import load_lstm_artifact
from app.core.config import settings
from app.services.ml_release import MLReleasePromotionService
from app.services.ml_release.contracts import (
    MLReleaseJsonDocument,
    ReleaseStorageProbeObject,
    is_json_decode_error,
    json_document_error_detail,
)

_LSTM_WEIGHTS = {
    "W_i": [[0.9]],
    "U_i": [[0.15]],
    "b_i": [3.0],
    "W_f": [[0.2]],
    "U_f": [[0.05]],
    "b_f": [2.8],
    "W_o": [[0.4]],
    "U_o": [[0.1]],
    "b_o": [2.5],
    "W_c": [[1.1]],
    "U_c": [[0.2]],
    "b_c": [0.0],
    "dense_W": [0.85],
    "dense_b": [0.0],
}

_LSTM_ARTIFACT = {
    "artifact_version": "1",
    "model_version": "v2.0-lstm",
    "sequence_length": 6,
    "input_center": 0.9,
    "input_scale": 0.05,
    "output_scale": 0.03,
    "output_bias": 0.9,
    "scenario_spread_multiplier": 1.1,
    "confidence_bias": 0.03,
    "blend_weights": {"lstm": 0.72, "historical": 0.18, "trend": 0.10},
    "weights": _LSTM_WEIGHTS,
}

_ENSEMBLE_ARTIFACT = {
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
        "lstm": 0.15,
    },
}


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# A. predictor 아티팩트 — happy
# ---------------------------------------------------------------------------
def test_lstm_artifact_file_and_embedded_mapping_normalize_identically(tmp_path):
    """같은 아티팩트는 파일로 읽어도 임베드 dict 로 읽어도 같은 정규화 산출을 준다."""
    path = _write_json(tmp_path / "lstm.json", _LSTM_ARTIFACT)

    from_file = load_lstm_artifact(str(path))
    from_mapping = load_lstm_artifact(dict(_LSTM_ARTIFACT))

    assert from_file["model_version"] == from_mapping["model_version"] == "v2.0-lstm"
    assert from_file["sequence_length"] == from_mapping["sequence_length"] == 6
    assert from_file["blend_weights"] == from_mapping["blend_weights"]
    assert from_file["weights"]["b_i"].tolist() == [3.0]


def test_lstm_artifact_accepts_flat_weight_layout(tmp_path):
    """게이트 텐서가 최상위에 있는 과거 배치도 계속 읽힌다(``weights`` 블록 부재)."""
    flat_artifact = {
        key: value for key, value in _LSTM_ARTIFACT.items() if key != "weights"
    }
    flat_artifact.update(_LSTM_WEIGHTS)
    path = _write_json(tmp_path / "flat.json", flat_artifact)

    artifact = load_lstm_artifact(str(path))

    assert artifact["weights"]["dense_W"].tolist() == [[0.85]]


def test_lstm_artifact_accepts_dense_weight_aliases(tmp_path):
    """``dense_weight``/``dense_bias`` 별칭 폴백이 계약에서도 유지된다."""
    weights = {
        key: value
        for key, value in _LSTM_WEIGHTS.items()
        if key not in {"dense_W", "dense_b"}
    }
    weights["dense_weight"] = [0.85]
    weights["dense_bias"] = [0.0]
    path = _write_json(
        tmp_path / "alias.json", {**_LSTM_ARTIFACT, "weights": weights}
    )

    artifact = load_lstm_artifact(str(path))

    assert artifact["weights"]["dense_W"].tolist() == [[0.85]]


def test_lstm_artifact_tolerates_unknown_and_string_encoded_fields(tmp_path):
    """학습이 덧붙이는 ``summary`` 블록과 문자열로 저장된 숫자를 모두 관용한다."""
    path = _write_json(
        tmp_path / "extra.json",
        {
            **_LSTM_ARTIFACT,
            "sequence_length": "9",
            "summary": {"group_calibration": {"service": {"median_rate": 0.91}}},
            "trained_at": "2026-07-30T00:00:00+00:00",
        },
    )

    artifact = load_lstm_artifact(str(path))

    assert artifact["sequence_length"] == 9


def test_ensemble_artifact_keeps_embedded_lstm_as_mapping(tmp_path):
    """임베드 LSTM 은 원문 매핑으로 남는다 — manifest 의 ``has_embedded_lstm`` 판정 보존."""
    path = _write_json(
        tmp_path / "ensemble.json",
        {**_ENSEMBLE_ARTIFACT, "lstm_artifact": _LSTM_ARTIFACT},
    )

    artifact = load_ensemble_artifact(str(path))

    assert isinstance(artifact["lstm_artifact"], dict)
    assert artifact["lstm_artifact"]["model_version"] == "v2.0-lstm"


def test_ensemble_artifact_non_mapping_blocks_fall_back_to_defaults(tmp_path):
    """비매핑 ``component_weights``/``lstm_artifact`` 는 부재로 관용된다(종전 isinstance 검사)."""
    path = _write_json(
        tmp_path / "odd.json",
        {**_ENSEMBLE_ARTIFACT, "component_weights": "nope", "lstm_artifact": "nope"},
    )

    artifact = load_ensemble_artifact(str(path))

    assert artifact["lstm_artifact"] is None
    assert artifact["component_weights"] == pytest.approx(
        {
            "historical": 0.52,
            "momentum": 0.18,
            "mean_reversion": 0.15,
            "lstm": 0.15,
        }
    )


# ---------------------------------------------------------------------------
# B. predictor 아티팩트 — sad (손상 아티팩트는 ValueError 로 거부)
# ---------------------------------------------------------------------------
def test_missing_artifact_file_raises_not_found(tmp_path):
    with pytest.raises(ValueError, match="LSTM model artifact was not found"):
        load_lstm_artifact(str(tmp_path / "absent.json"))


def test_corrupt_artifact_file_raises_invalid_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="is not valid JSON"):
        load_lstm_artifact(str(path))


def test_non_object_artifact_file_is_rejected_as_contract_violation(tmp_path):
    path = _write_json(tmp_path / "list.json", [1, 2, 3])

    with pytest.raises(ValueError, match="does not match the artifact contract"):
        load_lstm_artifact(str(path))


def test_artifact_with_wrong_field_type_is_rejected(tmp_path):
    """스칼라 자리에 객체가 오면 계약 위반으로 거부한다(조용한 기본값 대체 금지)."""
    path = _write_json(
        tmp_path / "typed.json", {**_LSTM_ARTIFACT, "sequence_length": {"n": 6}}
    )

    with pytest.raises(ValueError, match="does not match the artifact contract"):
        load_lstm_artifact(str(path))


def test_artifact_missing_required_weight_still_raises_missing_weight(tmp_path):
    """텐서 누락은 종전과 같은 진단 문구로 실패한다(계약이 이 검사를 가리지 않는다)."""
    weights = {key: value for key, value in _LSTM_WEIGHTS.items() if key != "W_i"}
    path = _write_json(tmp_path / "partial.json", {**_LSTM_ARTIFACT, "weights": weights})

    with pytest.raises(ValueError, match="Missing required LSTM weight 'W_i'"):
        load_lstm_artifact(str(path))


def test_embedded_mapping_contract_violation_is_reported_as_embedded(tmp_path):
    with pytest.raises(ValueError, match="Embedded LSTM model artifact"):
        load_lstm_artifact({**_LSTM_ARTIFACT, "input_center": {"bad": True}})


def test_read_persisted_artifact_is_shared_by_both_predictor_families(tmp_path):
    """두 predictor 가 같은 읽기 경로를 쓴다(문구만 label 로 갈린다)."""
    path = _write_json(tmp_path / "ens.json", _ENSEMBLE_ARTIFACT)

    artifact = read_persisted_artifact(
        str(path), model=PersistedEnsembleArtifact, label="Ensemble model artifact"
    )

    assert isinstance(artifact, PersistedEnsembleArtifact)
    assert artifact.momentum_window == 5

    with pytest.raises(ValueError, match="Ensemble model artifact was not found"):
        read_persisted_artifact(
            str(tmp_path / "absent.json"),
            model=PersistedEnsembleArtifact,
            label="Ensemble model artifact",
        )


def test_persisted_lstm_artifact_weight_block_prefers_nested_layout():
    """``weight_block`` 은 nested 우선, 부재 시 flat — 종전 폴백의 타입 표현."""
    nested = PersistedLSTMArtifact.model_validate(_LSTM_ARTIFACT)
    flat = PersistedLSTMArtifact.model_validate(
        {key: value for key, value in _LSTM_WEIGHTS.items()}
    )

    assert nested.weight_block() is nested.weights
    assert flat.weight_block() is flat


# ---------------------------------------------------------------------------
# C. 보정 표 읽기 — degrade + 경고
# ---------------------------------------------------------------------------
def test_calibration_blocks_are_read_from_active_artifact_summary(tmp_path, monkeypatch):
    path = _write_json(
        tmp_path / "cal.json",
        {
            **_ENSEMBLE_ARTIFACT,
            "summary": {
                "group_calibration": {"service": {"median_rate": 0.91, "sample_count": 7}},
                "probability_calibration": {
                    "__global__": {"scale": 2.0, "bias": -1.0, "method": "platt"}
                },
            },
        },
    )
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(path))

    assert load_group_calibration()["service"]["median_rate"] == pytest.approx(0.91)
    assert load_probability_calibration()["__global__"]["method"] == "platt"


def test_calibration_blocks_degrade_to_empty_with_warning_on_corrupt_artifact(
    tmp_path, monkeypatch, caplog
):
    """손상 아티팩트는 빈 표로 degrade 하되 조용히 사라지지 않는다(원문 미로깅)."""
    path = tmp_path / "corrupt.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(path))

    with caplog.at_level(logging.WARNING):
        assert load_group_calibration() == {}

    assert any(
        "calibration summary 해석 실패" in record.message for record in caplog.records
    )
    assert not any("broken" in record.getMessage() for record in caplog.records)


def test_calibration_blocks_are_empty_when_summary_is_absent_or_not_a_mapping(
    tmp_path, monkeypatch
):
    """summary 부재/비매핑은 오류가 아니라 '보정 없음'이다(경고 없이 빈 표)."""
    without_summary = _write_json(tmp_path / "plain.json", _ENSEMBLE_ARTIFACT)
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(without_summary)
    )
    assert load_group_calibration() == {}

    odd_summary = _write_json(
        tmp_path / "odd.json", {**_ENSEMBLE_ARTIFACT, "summary": "nope"}
    )
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(odd_summary)
    )
    assert load_group_calibration() == {}
    assert load_probability_calibration() == {}


def test_calibration_summary_document_tolerates_non_mapping_blocks():
    document = PersistedArtifactSummaryDocument.model_validate(
        {"summary": {"group_calibration": ["not", "a", "table"]}}
    )

    assert document.summary.group_calibration == {}
    assert document.summary.probability_calibration == {}


def test_broken_calibration_entry_drops_only_that_entry(caplog):
    """leaf 하나가 어긋나도 그 entry 만 빠진다 — 표 전체도, 다른 표도 살아남는다."""
    with caplog.at_level(logging.WARNING):
        document = PersistedArtifactSummaryDocument.model_validate(
            {
                "summary": {
                    "group_calibration": {
                        "service": {"median_rate": 0.91, "sample_count": 7},
                        "construction": {"median_rate": [0.9, 0.92]},
                        "goods": "not-a-mapping",
                    },
                    "probability_calibration": {
                        "__global__": {"scale": 2.0, "bias": -1.0, "method": "platt"}
                    },
                }
            }
        )

    assert set(document.summary.group_calibration) == {"service"}
    assert document.summary.group_calibration["service"]["sample_count"] == 7
    assert document.summary.probability_calibration["__global__"]["method"] == "platt"
    assert any(
        "calibration entry 계약 위반" in record.message for record in caplog.records
    )


def test_broken_group_entry_does_not_empty_probability_table_on_disk(
    tmp_path, monkeypatch
):
    """디스크 아티팩트에서도 두 표가 서로를 끌어내리지 않는다(읽기 독립 보존)."""
    path = _write_json(
        tmp_path / "partial.json",
        {
            **_ENSEMBLE_ARTIFACT,
            "summary": {
                "group_calibration": {
                    "service": {"median_rate": 0.91},
                    "construction": {"median_rate": {"nested": 1}},
                },
                "probability_calibration": {"__global__": {"scale": 2.0, "bias": -1.0}},
            },
        },
    )
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", str(path))

    assert set(load_group_calibration()) == {"service"}
    assert load_probability_calibration()["__global__"]["scale"] == pytest.approx(2.0)


def test_training_calibration_blocks_emit_scalar_leaves_only():
    """writer 회귀: 학습이 내보내는 보정 표의 entry 값은 스칼라만이어야 한다.

    읽기 계약(``CalibrationValue``)이 스칼라 leaf 를 전제하므로, 학습이 중첩 구조를 넣기
    시작하면 그 entry 가 추론에서 조용히 드롭된다. 계약의 양쪽을 함께 고정한다.
    """
    from app.services.ml_training import PricePredictionTrainingService

    service = PricePredictionTrainingService()
    group_items = [
        {"business_group": "service", "winning_rate": 0.91},
        {"business_group": "service", "winning_rate": 0.93},
        {"business_group": "construction", "winning_rate": 0.88},
    ]
    probability_items = [
        {
            "paper_bid_id": index,
            "project_id": index,
            "features": {
                "confidence_score": 0.9 if index % 2 else 0.3,
                "matched_score": 0.9 if index % 2 else 0.2,
                "historical_sample_size": 10,
                "business_group": "service",
                "category": "service",
            },
            "label": index % 2,
            "would_have_won_final": (
                "eligible_favorable" if index % 2 else "eligible_but_outbid"
            ),
            "price_close_label": 0,
            "would_have_won_price_only": "plausible" if index % 2 else "unlikely",
        }
        for index in range(16)
    ]

    tables = [
        service._build_group_calibration({"items": group_items}),
        service._build_probability_calibration({"items": probability_items}),
    ]

    assert tables[0] and tables[1]
    for table in tables:
        for entry in table.values():
            assert isinstance(entry, dict)
            for leaf in entry.values():
                assert leaf is None or isinstance(leaf, (str, int, float, bool))


# ---------------------------------------------------------------------------
# D. LLM 출력 검증
# ---------------------------------------------------------------------------
def test_llm_document_analysis_output_degrades_per_field():
    """타입이 어긋난 필드만 기본값으로 떨어지고 정상 필드는 살아남는다."""
    output = LLMDocumentAnalysisOutput.model_validate_json(
        json.dumps(
            {
                "key_requirements": ["보안 로그인"],
                "complexity_score": "high",
                "estimated_effort": "1.5",
                "risks": "보안",
                "rationale": "무관한 부가 키",
            }
        )
    )

    assert output.key_requirements == ["보안 로그인"]
    assert output.complexity_score == 0.0
    assert output.estimated_effort == pytest.approx(1.5)
    assert output.risks == []


def test_llm_document_analysis_output_rejects_bool_scores():
    """``true`` 를 1.0 점으로 승격하지 않는다(없는 근거를 만들지 않는다)."""
    output = LLMDocumentAnalysisOutput.model_validate({"complexity_score": True})

    assert output.complexity_score == 0.0


def test_llm_document_analysis_port_warns_and_degrades_on_non_object_response(caplog):
    """산문/배열 응답은 빈 분석 결과 + 경고. 응답 원문은 로그에 남기지 않는다."""
    from app.ai.llm_interfaces import LLMRequest, LLMResponse, RequestExecutor
    from app.ai.service_adapters import ExecutorDocumentAnalysisPort

    class _ProseExecutor(RequestExecutor):
        def execute(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text="죄송합니다. JSON 대신 설명을 드리면...")

    port = ExecutorDocumentAnalysisPort(executor=_ProseExecutor())

    with caplog.at_level(logging.WARNING):
        result = port.analyze("사양서 본문")

    assert result == {
        "key_requirements": [],
        "complexity_score": 0.0,
        "estimated_effort": 0.0,
        "risks": [],
    }
    assert any("LLM 출력 해석 실패" in record.message for record in caplog.records)
    assert not any("죄송합니다" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# E. release 문서 — 원문 보존 = 서명 바이트 동일성
# ---------------------------------------------------------------------------
_SIGNING_SENSITIVE_MANIFEST = {
    "manifest_schema_version": "2",
    "release_tag": "2026-07-30-bytes",
    "git_sha": None,
    "notes": "한글 노트 · 지수 표기 부동소수 포함",
    "promotion_gate": {
        "predictor_backtest": {
            "passed": True,
            "metrics": {
                "average_absolute_error_rate": 3.2e-05,
                "guardrail_rate": 1e-06,
                "sample_count": 12345678901234,
                "dataset_quality_status": None,
            },
            "thresholds": {"max_guardrail_rate": 0.05, "require_report": False},
            "reasons": [],
        }
    },
    "artifacts": {"embedding_model": None, "predictors": {"lstm": {}, "ensemble": None}},
    "recommended_env": {"PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS": True},
}


def test_manifest_document_preserves_canonical_signing_payload():
    """문서 계약 복원이 서명 대상 canonical 바이트를 한 비트도 바꾸지 않는다.

    이 성질이 깨지면 이미 발행된 릴리스의 HMAC 검증이 전부 실패한다. 지수 표기
    부동소수(``1e-06``·``3.2e-05``)를 일부러 포함한다 — 직렬화기를 갈아치우면 가장 먼저
    갈라지는 값이다.
    """
    service = MLReleasePromotionService()
    text = json.dumps(_SIGNING_SENSITIVE_MANIFEST, ensure_ascii=False, indent=2) + "\n"

    from_json = service._canonical_manifest_payload(json.loads(text))
    from_document = service._canonical_manifest_payload(
        MLReleaseJsonDocument.model_validate_json(text).root
    )

    assert from_document == from_json


def test_manifest_document_round_trip_preserves_key_set_and_values():
    """미지 키 제거도, 없던 필드의 기본값 주입도 없다."""
    text = json.dumps(_SIGNING_SENSITIVE_MANIFEST, ensure_ascii=False, indent=2) + "\n"

    restored = MLReleaseJsonDocument.model_validate_json(text).root

    assert restored == json.loads(text)
    assert list(restored) == list(_SIGNING_SENSITIVE_MANIFEST)


def test_signed_manifest_with_exponent_floats_still_verifies(tmp_path, monkeypatch):
    """지수 표기 부동소수를 담은 서명 manifest 가 계약 경유 로딩 후에도 검증된다."""
    monkeypatch.setattr(settings, "ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE", True)
    service = MLReleasePromotionService(repo_root=tmp_path)
    manifest = dict(_SIGNING_SENSITIVE_MANIFEST)
    manifest["signature"] = service._sign_manifest(manifest)
    manifest_path = _write_json_indented(
        tmp_path / "models" / "releases" / "2026-07-30-bytes.json", manifest
    )

    loaded, resolved_path = service.load_release_manifest(manifest_path)

    assert resolved_path == manifest_path
    assert loaded["release_tag"] == "2026-07-30-bytes"
    assert service.verify_release_manifest(loaded)["verified"] is True


def _write_json_indented(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def test_manifest_document_rejects_non_object_and_classifies_errors():
    """JSON 아님 / 최상위가 객체 아님을 구분해 보고한다(호출부 문구가 갈린다)."""
    with pytest.raises(Exception) as broken:
        MLReleaseJsonDocument.model_validate_json("{not json")
    assert is_json_decode_error(broken.value) is True
    assert json_document_error_detail(broken.value)

    with pytest.raises(Exception) as not_object:
        MLReleaseJsonDocument.model_validate_json("[1, 2]")
    assert is_json_decode_error(not_object.value) is False


def test_load_release_manifest_rejects_corrupt_and_non_object_documents(tmp_path):
    """손상 manifest 의 거부 문구가 종전과 같다(디코딩 실패 / 객체 아님 구분)."""
    service = MLReleasePromotionService(repo_root=tmp_path)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    not_object = _write_json(tmp_path / "list.json", [1, 2])

    with pytest.raises(ValueError, match="Release manifest is not valid JSON"):
        service.load_release_manifest(corrupt)
    with pytest.raises(
        ValueError, match="Release manifest must decode to a JSON object"
    ):
        service.load_release_manifest(not_object)
    with pytest.raises(ValueError, match="Release manifest was not found"):
        service.load_release_manifest(tmp_path / "absent.json")


def test_preflight_rollout_reports_corrupt_and_non_object_manifest(tmp_path):
    """preflight 는 손상 manifest 를 ``manifest_load`` 실패 체크로 보고한다(예외 아님)."""
    service = MLReleasePromotionService(repo_root=tmp_path)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")

    result = service.preflight_release_rollout(corrupt, probe_write=False)

    load_check = next(
        check for check in result["checks"] if check["name"] == "manifest_load"
    )
    assert result["passed"] is False
    assert load_check["status"] == "invalid_json"
    assert "is not valid JSON" in load_check["detail"]
    assert load_check["error"]

    not_object = _write_json(tmp_path / "list.json", [1, 2])
    result = service.preflight_release_rollout(not_object, probe_write=False)
    load_check = next(
        check for check in result["checks"] if check["name"] == "manifest_load"
    )
    assert load_check["status"] == "invalid_json"
    assert "must decode to a JSON object" in load_check["detail"]
    assert "error" not in load_check


def test_predictor_backtest_report_must_decode_to_json_object(tmp_path):
    """promotion gate 가 읽는 백테스트 리포트의 거부 문구도 그대로 유지된다."""
    service = MLReleasePromotionService(repo_root=tmp_path)
    corrupt = tmp_path / "report.json"
    corrupt.write_text("{broken", encoding="utf-8")
    not_object = _write_json(tmp_path / "report-list.json", [1, 2])

    with pytest.raises(
        ValueError, match="Predictor backtest report is not valid JSON"
    ):
        service._load_predictor_backtest_report(str(corrupt))
    with pytest.raises(
        ValueError, match="Predictor backtest report must decode to a JSON object"
    ):
        service._load_predictor_backtest_report(str(not_object))

    valid = _write_json(
        tmp_path / "ok.json", {"status": "completed", "sample_count": 12}
    )
    report = service._load_predictor_backtest_report(str(valid))
    assert report is not None
    assert report["status"] == "completed"
    assert report["report_path"]


def test_manifest_operations_summary_marks_unreadable_manifest_invalid(tmp_path):
    """운영 리포트는 읽을 수 없는 manifest 를 invalid 로 요약한다(집계에서 사라지지 않음)."""
    from app.services.analytics_reporting import AnalyticsReportingService

    service = AnalyticsReportingService()
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    not_object = _write_json(tmp_path / "list.json", [1, 2])

    corrupt_summary = service._read_manifest_summary(corrupt)
    assert corrupt_summary["signature_status"] == "invalid"
    assert corrupt_summary["gate_passed"] is False
    assert corrupt_summary["detail"].startswith("Manifest could not be read")

    non_object_summary = service._read_manifest_summary(not_object)
    assert non_object_summary["detail"] == "Manifest JSON is not an object."

    missing_summary = service._read_manifest_summary(tmp_path / "absent.json")
    assert missing_summary["detail"].startswith("Manifest could not be read")


_NON_FINITE_MANIFEST = {
    "release_tag": "2026-07-30-nonfinite",
    "promotion_gate": {
        "predictor_backtest": {
            "metrics": {
                # json.dumps 는 이 값들을 비표준 리터럴(NaN/Infinity)로 적는다. manifest 는
                # 그 산출을 그대로 되읽어 서명을 재계산하므로, 파서가 이들을 거부하면 기존
                # 릴리스가 로드 불가가 된다 — pydantic-core 업그레이드 보험용 케이스.
                "average_absolute_error_rate": float("nan"),
                "guardrail_rate": float("inf"),
                "fallback_rate": float("-inf"),
                # i64 를 넘는 정수(파서가 float 로 강등하면 canonical 바이트가 달라진다).
                "sample_count": 9223372036854775808,
                "row_id": 123456789012345678901234567890,
            }
        }
    },
}


def test_manifest_document_preserves_canonical_bytes_for_non_finite_and_big_ints():
    """NaN/Infinity·i64 초과 정수도 서명 canonical 바이트가 동일해야 한다."""
    service = MLReleasePromotionService()
    text = json.dumps(_NON_FINITE_MANIFEST, ensure_ascii=False, indent=2) + "\n"

    from_document = service._canonical_manifest_payload(
        MLReleaseJsonDocument.model_validate_json(text).root
    )

    assert from_document == service._canonical_manifest_payload(json.loads(text))
    restored_metrics = MLReleaseJsonDocument.model_validate_json(text).root[
        "promotion_gate"
    ]["predictor_backtest"]["metrics"]
    assert isinstance(restored_metrics["sample_count"], int)
    assert restored_metrics["row_id"] == 123456789012345678901234567890


def test_release_storage_probe_object_is_json_object_with_marker():
    """probe 본문은 표식 + 생성시각을 담은 JSON 객체다(바이트 표현은 계약이 아니다)."""
    payload = ReleaseStorageProbeObject(
        created_at="2026-07-30T00:00:00+00:00"
    ).model_dump_json()

    assert json.loads(payload) == {
        "probe": "ml-release-rollout",
        "created_at": "2026-07-30T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# F. promote 게이트는 fail-closed (손상 블록을 '블록 없음'으로 접지 않는다)
# ---------------------------------------------------------------------------
def _gate(path: Path):
    from scripts.promote_ml_release import evaluate_preflight_gate

    return evaluate_preflight_gate(path)


def test_promote_gate_rejects_broken_summary_and_names_the_violation(tmp_path):
    """손상된 ``summary``/표/entry 는 통과가 아니라 거부이고, 사유가 위치를 가리킨다."""
    non_mapping_summary = _write_json(
        tmp_path / "summary.json", {**_ENSEMBLE_ARTIFACT, "summary": "nope"}
    )
    non_mapping_table = _write_json(
        tmp_path / "table.json",
        {**_ENSEMBLE_ARTIFACT, "summary": {"group_calibration": "nope"}},
    )
    broken_entry = _write_json(
        tmp_path / "entry.json",
        {
            **_ENSEMBLE_ARTIFACT,
            "summary": {
                "group_calibration": {"service": {"sample_count": {"nested": 1}}}
            },
        },
    )

    summary_result = _gate(non_mapping_summary)
    table_result = _gate(non_mapping_table)
    entry_result = _gate(broken_entry)

    assert summary_result.ok is False
    assert "summary" in (summary_result.reason or "")
    assert table_result.ok is False
    assert "summary.group_calibration" in (table_result.reason or "")
    assert entry_result.ok is False
    assert "group_calibration" in (entry_result.reason or "")


def test_promote_gate_still_passes_without_a_calibration_block(tmp_path):
    """보정 블록이 아예 없는 아티팩트는 종전처럼 통과한다(선택적 블록)."""
    without_summary = _write_json(tmp_path / "plain.json", _ENSEMBLE_ARTIFACT)

    assert _gate(without_summary).ok is True


def test_promote_gate_reports_json_and_non_object_failures_distinctly(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    not_object = _write_json(tmp_path / "list.json", [1, 2])

    corrupt_result = _gate(corrupt)
    non_object_result = _gate(not_object)

    assert corrupt_result.ok is False
    assert "읽기 실패" in (corrupt_result.reason or "")
    assert non_object_result.ok is False
    assert non_object_result.reason == "manifest가 JSON 객체가 아님"


def test_promote_gate_rejects_group_below_threshold_with_valid_entries(
    tmp_path, monkeypatch
):
    """정상 entry 의 임계 판정은 그대로다(관용 변경이 게이트 판정을 흔들지 않는다)."""
    monkeypatch.setattr(settings, "GROUP_CALIBRATION_MIN_SAMPLES", 100)
    path = _write_json(
        tmp_path / "low.json",
        {
            **_ENSEMBLE_ARTIFACT,
            "summary": {
                "group_calibration": {
                    "service": {"median_rate": 0.91, "sample_count": 3}
                }
            },
        },
    )

    result = _gate(path)

    assert result.ok is False
    assert "service=3" in (result.reason or "")


# ---------------------------------------------------------------------------
# G. 원격 rebuild 응답 — 트리거된 뒤이므로 증적 우선
# ---------------------------------------------------------------------------
def _trigger_remote_rebuild(tmp_path, monkeypatch, *, body: str):
    from app.services.ml_release import MLReleasePromotionRequest

    repo_root = tmp_path / "repo"
    embedding_dir = repo_root / "models" / "embeddings" / "snapshot"
    embedding_dir.mkdir(parents=True, exist_ok=True)
    (embedding_dir / "config.json").write_text(
        json.dumps({"model_type": "bert"}), encoding="utf-8"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-07-30-remote",
            embedding_model_path=str(embedding_dir),
        )
    )

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return body.encode("utf-8")

        def getcode(self):
            return 200

    monkeypatch.setattr(
        "app.services.ml_release.request.urlopen",
        lambda request_object, timeout: _FakeResponse(),
    )
    return service.trigger_remote_embedding_rebuild(
        "2026-07-30-remote", base_url="http://example.test"
    )


def test_remote_rebuild_echoes_non_object_json_body(tmp_path, monkeypatch):
    """JSON 이지만 객체가 아닌 본문은 그대로 증적으로 남긴다(이미 트리거된 작업)."""
    result = _trigger_remote_rebuild(tmp_path, monkeypatch, body=json.dumps(["queued"]))

    assert result["status_code"] == 200
    assert result["response"] == ["queued"]


def test_remote_rebuild_rejects_undecodable_body_with_url(tmp_path, monkeypatch):
    """디코딩조차 안 되는 본문만 거부하고, 어디서 왔는지 URL 을 붙인다."""
    with pytest.raises(RuntimeError, match="not valid JSON") as failure:
        _trigger_remote_rebuild(tmp_path, monkeypatch, body="<html>502</html>")

    assert "http://example.test" in str(failure.value)


def test_remote_rebuild_keeps_empty_body_as_absent(tmp_path, monkeypatch):
    result = _trigger_remote_rebuild(tmp_path, monkeypatch, body="   ")

    assert result["response"] is None
