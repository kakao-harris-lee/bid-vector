"""Predictor artifact builders for the price-predictor training service.

Creates the lightweight LSTM/ensemble artifacts from dataset statistics and embeds
summary calibration blocks BEFORE the artifact is written to disk (so the on-disk
sha256 and the signed manifest cover them). Hyperparameters come from
:mod:`.constants`; builder bodies are moved verbatim from the original module.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from .constants import (
    _ENSEMBLE_COMPONENT_WEIGHTS,
    _ENSEMBLE_CONFIDENCE_BIAS_CAP,
    _ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR,
    _LSTM_BLEND_WEIGHTS,
    _LSTM_CONFIDENCE_BIAS_CAP,
    _LSTM_CONFIDENCE_BIAS_SAMPLE_DIVISOR,
    _LSTM_DEFAULT_STD,
    _LSTM_MIN_STD,
    _LSTM_OUTPUT_SCALE_STD_FACTOR,
    _LSTM_SCENARIO_SPREAD_MULTIPLIER,
    _MOMENTUM_WINDOW_MAX,
    _MOMENTUM_WINDOW_MIN,
    _SCENARIO_SPREAD_MULTIPLIER_NARROW,
    _SCENARIO_SPREAD_MULTIPLIER_WIDE,
    _SCENARIO_SPREAD_STD_THRESHOLD,
    _SEQUENCE_LENGTH_MAX,
    _SEQUENCE_LENGTH_MIN,
)


class ArtifactBuilderMixin:
    """LSTM/ensemble artifact construction and summary-block injection."""

    def _build_lstm_artifact(self, *, release_tag: str, bid_rates: list[float]) -> dict[str, Any]:
        """Create a valid lightweight LSTM artifact from dataset statistics."""
        average_bid_rate = mean(bid_rates)
        std_bid_rate = max(pstdev(bid_rates) if len(bid_rates) > 1 else _LSTM_DEFAULT_STD, _LSTM_MIN_STD)
        sequence_length = max(_SEQUENCE_LENGTH_MIN, min(len(bid_rates), _SEQUENCE_LENGTH_MAX))
        return {
            "artifact_version": "1",
            "model_version": f"{release_tag}-lstm",
            "sequence_length": sequence_length,
            "input_center": round(float(average_bid_rate), 6),
            "input_scale": round(float(std_bid_rate), 6),
            "output_scale": round(float(std_bid_rate) * _LSTM_OUTPUT_SCALE_STD_FACTOR, 6),
            "output_bias": round(float(average_bid_rate), 6),
            "scenario_spread_multiplier": _LSTM_SCENARIO_SPREAD_MULTIPLIER,
            "confidence_bias": min(
                _LSTM_CONFIDENCE_BIAS_CAP, len(bid_rates) / _LSTM_CONFIDENCE_BIAS_SAMPLE_DIVISOR
            ),
            "blend_weights": dict(_LSTM_BLEND_WEIGHTS),
            "weights": {
                "W_i": [[0.7]],
                "U_i": [[0.1]],
                "b_i": [2.0],
                "W_f": [[0.2]],
                "U_f": [[0.05]],
                "b_f": [2.0],
                "W_o": [[0.4]],
                "U_o": [[0.1]],
                "b_o": [1.5],
                "W_c": [[0.8]],
                "U_c": [[0.15]],
                "b_c": [0.0],
                "dense_W": [0.6],
                "dense_b": [0.0],
            },
        }

    def _build_ensemble_artifact(
        self,
        *,
        release_tag: str,
        lstm_artifact_path: Path,
        lstm_artifact: dict[str, Any] | None,
        bid_rates: list[float],
    ) -> dict[str, Any]:
        """Create an ensemble artifact that links to the generated LSTM artifact."""
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        artifact = {
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
            "lstm_artifact_path": self._relative_path_from(
                lstm_artifact_path,
                base_path=self.repo_root / "models" / "predictors" / "ensemble",
            ),
        }
        if lstm_artifact is not None:
            artifact["lstm_artifact"] = lstm_artifact
        return artifact

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
