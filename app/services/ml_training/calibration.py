"""Per-group calibration fitting for the price-predictor training service.

Builds the winning_rate group-calibration block and the Platt (logistic)
낙찰-가능성 calibration curves embedded in the ensemble artifact. Label policy and
the Newton-step curve fit are moved verbatim from the original module.
"""

from __future__ import annotations

from typing import Any


class CalibrationMixin:
    """Group winning_rate stats + Platt-scaled probability calibration."""

    def _build_probability_calibration(
        self, calibration_dataset: dict[str, Any]
    ) -> dict[str, Any]:
        """Fit a per-group logistic (Platt) calibration for the낙찰-가능성 score.

        Mirrors :meth:`_build_group_calibration` in spirit: it consumes the labeled
        settlement dataset produced by
        ``PredictionDatasetService.build_probability_calibration_dataset`` and emits a
        compact, audit-friendly dict keyed by ``business_group`` plus a ``__global__``
        fallback. Each entry stores Platt scale/bias over the heuristic probability
        signal so inference can map the raw heuristic onto observed win frequencies.

        **Label policy.** The primary label is the eligibility-gated
        ``eligible_favorable`` flag (1) vs. any other *adjudicated* eligibility
        verdict (0); ``unknown`` rows were already dropped upstream (``label is
        None``). If a group lacks both classes after that filter, it falls back to
        the price-only ``plausible`` label so a sparse, freshly-backfilled history
        still yields a usable curve. The fallback is recorded per group so reviewers
        can audit which curve used which label.

        **No external deps.** Uses numpy/stdlib only — a few Newton steps fit the
        1-parameter logistic, with an isotonic-free closed-form fallback for
        degenerate (single-class) groups.

        Returns ``{}`` when there is nothing usable so callers fall through to the
        legacy heuristic with no behavior change.
        """
        items = calibration_dataset.get("items") or []
        if not items:
            return {}

        groups: dict[str, list[tuple[float, int, int]]] = {}
        for item in items:
            features = item.get("features") or {}
            raw = self._raw_probability_signal(features)
            primary = item.get("label")
            price_only = item.get("price_close_label")
            try:
                price_only_int = int(price_only) if price_only is not None else None
            except (TypeError, ValueError):
                price_only_int = None
            group_key = str(features.get("business_group") or "__global__")
            primary_int: int | None
            try:
                primary_int = int(primary) if primary is not None else None
            except (TypeError, ValueError):
                primary_int = None
            # -1 marks a missing label (e.g. would_have_won_final == "unknown")
            # so the curve fitter can drop it without confusing it with class 0.
            row = (
                raw,
                primary_int if primary_int is not None else -1,
                price_only_int if price_only_int is not None else -1,
            )
            groups.setdefault(group_key, []).append(row)
            groups.setdefault("__global__", []).append(row)

        calibration: dict[str, Any] = {}
        for group_key, rows in groups.items():
            curve = self._fit_group_probability_curve(rows)
            if curve is not None:
                calibration[group_key] = curve
        return calibration

    @staticmethod
    def _raw_probability_signal(features: dict[str, Any]) -> float:
        """Build the Platt-curve input from inference-time features.

        Delegates to the single source of truth
        (:func:`app.ai.predictors.historical.calibration_raw_signal`) so training and
        serving feed the fitted curve a byte-identical raw value. Uses ONLY
        confidence/matched — ``historical_sample_size`` is excluded to avoid the
        train/serve skew (history is always 0 in the training dataset).
        """
        from app.ai.predictors.historical import calibration_raw_signal

        return calibration_raw_signal(
            features.get("confidence_score", 0.0),
            features.get("matched_score", 0.0),
        )

    def _fit_group_probability_curve(
        self, rows: list[tuple[float, int, int]]
    ) -> dict[str, Any] | None:
        """Fit a 1-parameter logistic over the raw heuristic for one group.

        ``rows`` is a list of ``(raw_signal, primary_label, price_only_label)`` where
        ``-1`` marks a missing label (e.g. ``would_have_won_final == "unknown"`` was
        dropped to ``None`` upstream and arrives here as ``-1``).
        """
        import math

        primary = [(raw, label) for raw, label, _ in rows if label in (0, 1)]
        label_source = "eligible_favorable"
        usable = primary
        if not primary or len({label for _, label in primary}) < 2:
            # Fall back to the price-only label when the eligibility-gated labels
            # are absent or single-class for this group.
            fallback = [(raw, label) for raw, _, label in rows if label in (0, 1)]
            if fallback and len({label for _, label in fallback}) >= 2:
                usable = fallback
                label_source = "price_close"
            elif primary:
                usable = primary
                label_source = "eligible_favorable"
            else:
                usable = fallback or primary

        if not usable:
            return None

        sample_count = len(usable)
        positives = sum(1 for _, label in usable if label == 1)
        base_rate = positives / sample_count if sample_count else 0.0

        # Degenerate single-class group: no slope is identifiable. Emit a flat curve
        # that simply reports the observed base rate (still better-calibrated than the
        # raw heuristic, and audit-transparent).
        if len({label for _, label in usable}) < 2:
            return {
                "method": "base_rate",
                "label_source": label_source,
                "scale": 0.0,
                "bias": self._logit(base_rate),
                "base_rate": round(base_rate, 6),
                "sample_count": sample_count,
                "positive_count": positives,
            }

        # Platt scaling: fit p = sigmoid(scale * raw + bias) via a few Newton steps
        # on the log-loss. 2 parameters, tiny data — this converges in <10 iters.
        scale, bias = 1.0, 0.0
        for _ in range(50):
            g0 = g1 = 0.0
            h00 = h01 = h11 = 0.0
            for raw, label in usable:
                z = scale * raw + bias
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
                err = p - label
                g0 += err * raw
                g1 += err
                w = p * (1.0 - p)
                h00 += w * raw * raw
                h01 += w * raw
                h11 += w
            # Ridge term keeps the Hessian invertible for thin/collinear data.
            h00 += 1e-6
            h11 += 1e-6
            det = h00 * h11 - h01 * h01
            if abs(det) < 1e-12:
                break
            d_scale = (g0 * h11 - g1 * h01) / det
            d_bias = (g1 * h00 - g0 * h01) / det
            scale -= d_scale
            bias -= d_bias
            if abs(d_scale) < 1e-6 and abs(d_bias) < 1e-6:
                break

        return {
            "method": "platt",
            "label_source": label_source,
            "scale": round(float(scale), 6),
            "bias": round(float(bias), 6),
            "base_rate": round(base_rate, 6),
            "sample_count": sample_count,
            "positive_count": positives,
        }

    @staticmethod
    def _logit(p: float) -> float:
        import math

        clamped = min(1.0 - 1e-6, max(1e-6, float(p)))
        return round(math.log(clamped / (1.0 - clamped)), 6)

    def _build_group_calibration(self, dataset: dict[str, Any]) -> dict[str, dict[str, float | int]]:
        """Aggregate per-group winning_rate stats for inclusion in the release manifest."""
        import statistics

        # Support both "items" (test fixture) and "series" (production dataset builder)
        items = dataset.get("items") or dataset.get("series") or []
        groups: dict[str, list[float]] = {}
        for item in items:
            group = item.get("business_group")
            rate = item.get("winning_rate")
            if not group or rate in (None, ""):
                continue
            try:
                groups.setdefault(group, []).append(float(rate))
            except (TypeError, ValueError):
                continue
        calibration: dict[str, dict[str, float | int]] = {}
        for group, values in groups.items():
            if not values:
                continue
            sorted_values = sorted(values)
            n = len(sorted_values)
            calibration[group] = {
                "median_rate": round(statistics.median(sorted_values), 6),
                "std": round(statistics.pstdev(sorted_values), 6) if n > 1 else 0.0,
                "p25": sorted_values[(n - 1) * 1 // 4],
                "p75": sorted_values[(n - 1) * 3 // 4],
                "sample_count": n,
            }
        return calibration
