"""Shared pure helpers for the price-predictor training service.

Value/IO glue used across the training mixins: dataset row normalization, bid-rate
resolution, JSON dump, and repo-relative path helpers. Method bodies are moved
verbatim from the original module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.time import utc_now
from app.domain.aggregates import average


class HelpersMixin:
    """Dataset/prediction resolution and path/serialization helpers."""

    def _sort_dataset_series(self, series: list[Any]) -> list[dict[str, Any]]:
        """Return dataset rows in oldest-first order."""
        normalized_rows = [dict(item) for item in series if isinstance(item, dict)]

        def sort_key(item: dict[str, Any]) -> tuple[str, int]:
            opened_at = item.get("opened_at") or ""
            if hasattr(opened_at, "isoformat"):
                opened_at = opened_at.isoformat()
            return str(opened_at), int(item.get("historical_data_id") or 0)

        return sorted(normalized_rows, key=sort_key)

    def _resolve_dataset_bid_rate(self, item: dict[str, Any]) -> float | None:
        """Resolve a usable bid rate from a dataset row."""
        try:
            bid_rate = float(item.get("bid_rate") or 0.0)
        except (TypeError, ValueError):
            bid_rate = 0.0
        if bid_rate <= 0:
            try:
                predicted_price = float(item.get("predicted_price") or 0.0)
                base_amount = float(item.get("base_amount") or 0.0)
            except (TypeError, ValueError):
                predicted_price = 0.0
                base_amount = 0.0
            if predicted_price > 0 and base_amount > 0:
                bid_rate = predicted_price / base_amount
        if 0.5 <= bid_rate <= 1.5:
            return float(bid_rate)
        return None

    def _resolve_dataset_budget(self, item: dict[str, Any]) -> float:
        """Resolve the base amount used for one holdout prediction."""
        try:
            budget = float(item.get("base_amount") or 0.0)
        except (TypeError, ValueError):
            budget = 0.0
        if budget > 0:
            return budget
        bid_rate = self._resolve_dataset_bid_rate(item)
        try:
            predicted_price = float(item.get("predicted_price") or 0.0)
        except (TypeError, ValueError):
            predicted_price = 0.0
        if bid_rate and predicted_price > 0:
            return predicted_price / bid_rate
        return 0.0

    def _resolve_prediction_bid_rate(self, prediction: dict[str, Any], *, budget: float) -> float | None:
        """Resolve a predicted bid rate from one predictor response."""
        try:
            bid_rate = float(prediction.get("predicted_bid_rate") or 0.0)
        except (TypeError, ValueError):
            bid_rate = 0.0
        if bid_rate <= 0 and budget > 0:
            try:
                bid_rate = float(prediction.get("predicted_price") or 0.0) / budget
            except (TypeError, ValueError):
                bid_rate = 0.0
        if 0.0 < bid_rate < 2.0:
            return float(bid_rate)
        return None

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average while preserving empty sets."""
        return average(values, digits=6)

    def _safe_ratio(self, numerator: int | float, denominator: int | float) -> float:
        """Return a zero-safe ratio."""
        denominator_value = float(denominator or 0.0)
        if denominator_value <= 0:
            return 0.0
        return float(numerator or 0.0) / denominator_value

    def _top_reason_counts(self, reasons: list[str], *, limit: int = 3) -> list[dict[str, Any]]:
        """Return top skip reasons with counts."""
        counts: dict[str, int] = {}
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
        return [
            {"reason": reason, "count": count}
            for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    def _resolve_release_tag(self, value: Any) -> str:
        cleaned = self._clean_optional(value)
        release_tag = cleaned or f"price-predictor-{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
        if any(token in release_tag for token in ("/", "\\", "..")):
            raise ValueError("release_tag must not contain path separators or '..'.")
        return release_tag

    def _clean_optional(self, value: Any) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None

    def _dump_json(self, payload: dict[str, Any]) -> str:
        # 학습 리포트/아티팩트 쓰기 경로는 ``json.dumps`` 를 유지한다: payload 는 학습이
        # 조립한 자유형식 dict 이고 ``default=str`` 폴백(Decimal·datetime)에 의존한다.
        # 산출 파일의 sha256 은 서명된 release manifest 에 기록되므로 저장 바이트가 계약이며,
        # pydantic 직렬화는 지수 표기 부동소수를 다르게 적는다(``1e-06`` -> ``1e-6``).
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"

    def _to_portable_path(self, path: Path) -> str:
        resolved_path = path.resolve()
        try:
            return resolved_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(resolved_path)

    def _relative_path_from(self, path: Path, *, base_path: Path) -> str:
        return Path(os.path.relpath(path.resolve(), start=base_path.resolve())).as_posix()
