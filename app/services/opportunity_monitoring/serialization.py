"""JSON (de)serialization, run diffing, and decision-score resolvers.

Payload helpers shared across the monitoring mixins, moved verbatim from the
original ``opportunity_monitoring`` module.
"""

from __future__ import annotations

import json

from app.services.opportunity_monitoring.base import _MonitoringBase


class _SerializationMixin(_MonitoringBase):
    """Serialize/parse stored payloads, diff runs, and pull decision scores."""

    def _dump_json(self, payload: dict) -> str:
        """Serialize monitoring payloads without escaping Korean text."""
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _load_json(self, raw_payload: str | None) -> dict:
        """Parse stored JSON payloads defensively for empty or legacy rows."""
        if not raw_payload:
            return {}
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _extract_result_items(self, payload: dict) -> list[dict]:
        """Return valid monitor result items from a stored payload."""
        return [
            item
            for item in payload.get("results", [])
            if isinstance(item, dict) and isinstance(item.get("project_id"), int)
        ]

    def _build_run_diff(self, current_payload: dict, previous_payload: dict) -> dict:
        """Compare current and previous run results to highlight new, continuing, and dropped candidates."""
        current_items = self._extract_result_items(current_payload)
        previous_items = self._extract_result_items(previous_payload)

        current_by_project = {int(item["project_id"]): item for item in current_items}
        previous_by_project = {int(item["project_id"]): item for item in previous_items}

        new_candidate_project_ids = [
            int(item["project_id"])
            for item in current_items
            if int(item["project_id"]) not in previous_by_project
        ]
        continuing_candidate_project_ids = [
            int(item["project_id"])
            for item in current_items
            if int(item["project_id"]) in previous_by_project
        ]
        dropped_candidate_project_ids = [
            int(item["project_id"])
            for item in previous_items
            if int(item["project_id"]) not in current_by_project
        ]

        return {
            "new_candidate_count": len(new_candidate_project_ids),
            "continuing_candidate_count": len(continuing_candidate_project_ids),
            "dropped_candidate_count": len(dropped_candidate_project_ids),
            "new_candidate_project_ids": new_candidate_project_ids,
            "continuing_candidate_project_ids": continuing_candidate_project_ids,
            "dropped_candidate_project_ids": dropped_candidate_project_ids,
            "new_candidates": [current_by_project[project_id] for project_id in new_candidate_project_ids],
            "continuing_candidates": [current_by_project[project_id] for project_id in continuing_candidate_project_ids],
            "dropped_candidates": [previous_by_project[project_id] for project_id in dropped_candidate_project_ids],
        }

    def _resolve_current_workload_score(self, analysis: dict, *, fallback: float | None) -> float:
        """Resolve a concrete workload score for persistence, preserving explicit zero values."""
        analysis_workload = analysis.get("current_workload_score")
        if analysis_workload is not None:
            return float(analysis_workload)
        if fallback is not None:
            return float(fallback)
        return 0.0

    def _resolve_competitiveness_score(self, analysis: dict) -> float:
        """Extract competitiveness safely from an analysis payload for decision persistence."""
        market_insights = analysis.get("market_insights")
        if isinstance(market_insights, dict) and market_insights.get("competitiveness_score") is not None:
            return float(market_insights.get("competitiveness_score") or 0.0)
        decision = analysis.get("decision")
        if isinstance(decision, dict) and decision.get("competitiveness_score") is not None:
            return float(decision.get("competitiveness_score") or 0.0)
        return 0.5

    def _resolve_expected_margin_score(self, analysis: dict) -> float:
        """Extract expected-margin metadata from analysis payloads for persistence."""
        decision = analysis.get("decision")
        if isinstance(decision, dict) and decision.get("expected_margin_score") is not None:
            return float(decision.get("expected_margin_score") or 0.0)
        return 0.5

    def _resolve_execution_complexity_score(self, analysis: dict) -> float:
        """Extract execution-complexity metadata from analysis payloads for persistence."""
        decision = analysis.get("decision")
        if isinstance(decision, dict) and decision.get("execution_complexity_score") is not None:
            return float(decision.get("execution_complexity_score") or 0.0)
        return 0.35
