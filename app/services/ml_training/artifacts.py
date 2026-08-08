"""Predictor artifact builders for the price-predictor training service.

Creates the lightweight ensemble artifact from dataset statistics and embeds
summary calibration blocks BEFORE the artifact is written to disk (so the on-disk
sha256 and the signed manifest cover them). Hyperparameters come from
:mod:`.constants`.

sequence-model(LSTM) 아티팩트 생성은 2026-08-09 은퇴했다. 스케줄이 꺼져 있어도 수동
훈련은 언제든 돌 수 있으므로, 은퇴한 모델을 다시 만들어 두지 않도록 빌더 자체를 없앤다.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import pstdev
from typing import Any

from .constants import (
    _ENSEMBLE_COMPONENT_WEIGHTS,
    _ENSEMBLE_CONFIDENCE_BIAS_CAP,
    _ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR,
    _MOMENTUM_WINDOW_MAX,
    _MOMENTUM_WINDOW_MIN,
    _SCENARIO_SPREAD_MULTIPLIER_NARROW,
    _SCENARIO_SPREAD_MULTIPLIER_WIDE,
    _SCENARIO_SPREAD_STD_THRESHOLD,
    _SEQUENCE_LENGTH_MAX,
    _SEQUENCE_LENGTH_MIN,
)


class ArtifactBuilderMixin:
    """Ensemble artifact construction and summary-block injection."""

    def _build_ensemble_artifact(
        self,
        *,
        release_tag: str,
        bid_rates: list[float],
    ) -> dict[str, Any]:
        """Create an ensemble artifact from dataset statistics."""
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        return {
            "artifact_version": "1",
            "model_version": f"{release_tag}-ensemble",
            "sequence_length": max(_SEQUENCE_LENGTH_MIN, min(len(bid_rates), _SEQUENCE_LENGTH_MAX)),
            "momentum_window": max(_MOMENTUM_WINDOW_MIN, min(len(bid_rates), _MOMENTUM_WINDOW_MAX)),
            "scenario_spread_multiplier": (
                _SCENARIO_SPREAD_MULTIPLIER_NARROW
                if std_bid_rate < _SCENARIO_SPREAD_STD_THRESHOLD
                else _SCENARIO_SPREAD_MULTIPLIER_WIDE
            ),
            "confidence_bias": min(
                _ENSEMBLE_CONFIDENCE_BIAS_CAP, len(bid_rates) / _ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR
            ),
            "component_weights": dict(_ENSEMBLE_COMPONENT_WEIGHTS),
        }

    @staticmethod
    def _inject_group_calibration(
        artifact: dict[str, Any],
        group_calibration: dict[str, dict[str, float | int]],
    ) -> None:
        """Mutate the in-memory ensemble artifact to embed summary.group_calibration.

        Call this BEFORE writing the artifact to disk so that the on-disk file and
        the in-memory dict are always in sync — no race window, no divergence.
        """
        if not group_calibration:
            return
        summary = artifact.setdefault("summary", {})
        if not isinstance(summary, dict):
            summary = {}
            artifact["summary"] = summary
        summary["group_calibration"] = group_calibration

    @staticmethod
    def _inject_summary_block(
        artifact: dict[str, Any],
        *,
        key: str,
        block: dict[str, Any],
    ) -> None:
        """Embed an arbitrary ``summary.<key>`` block into the in-memory artifact.

        Same write-before-disk contract as :meth:`_inject_group_calibration`: call
        BEFORE serializing so the on-disk sha256 (and the signed manifest) covers it.
        A falsy ``block`` is a no-op so empty calibration never bloats the artifact.
        """
        if not block:
            return
        summary = artifact.setdefault("summary", {})
        if not isinstance(summary, dict):
            summary = {}
            artifact["summary"] = summary
        summary[key] = block

    def _inject_group_calibration_into_ensemble_artifact(
        self,
        *,
        ensemble_artifact_path: "Path | str",
        group_calibration: dict[str, dict[str, float | int]],
    ) -> None:
        """Patch the ensemble artifact JSON with summary.group_calibration in place.

        .. deprecated::
            Use :meth:`_inject_group_calibration` (in-memory variant) instead.
            This file-based helper is retained for backward compatibility only.

        Best-effort: if the artifact doesn't exist or isn't JSON, log and return —
        this is a runtime feature enhancement, not a training prerequisite.

        JSON 직접 호출을 여기서는 의도적으로 유지한다: 이 헬퍼는 **임의의 키를 가진 기존
        아티팩트를 읽어 그대로 다시 쓴다**. 아티팩트의 sha256 은 서명된 release manifest 에
        기록되므로 저장 바이트가 계약이고, 타입 모델을 경유하면 (a) 선언되지 않은 키의 순서가
        선언 필드 뒤로 밀리고 (b) pydantic 이 지수 표기 부동소수를 다르게 적는다
        (``1e-06`` -> ``1e-6``). 읽기만 모델로 바꿔도 쓰기용 원문 dict 가 다시 필요하므로
        json 호출은 줄지 않는다. 읽기 계약이 필요한 소비 경로(예측·preflight)는
        ``app.ai.predictors.artifact_contracts`` 를 쓴다.
        """
        path = Path(ensemble_artifact_path)
        if not group_calibration or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        self._inject_group_calibration(payload, group_calibration)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
