"""Scheduled smoke-test reporting mixin."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import SmokeTestRun
from app.services.smoke_failure_taxonomy import (
    classify_failure,
    guidance_for,
)
from app.services.stored_json_payload import load_stored_json_array


class _SmokeTestMixin:
    """Scheduled smoke-test summary, failure taxonomy, and cards."""

    def _build_smoke_test_summary(
        self,
        db: Session,
        *,
        days: int,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate persisted daily smoke cycles into PASS/FAIL + green-streak signals.

        ``pass_rate`` = passed cycles / total cycles in the window.
        ``current_streak`` = number of consecutive most-recent cycles that passed
        overall (the roadmap "N일 연속 green" signal). Honest empty-window output:
        zero counts, ``latest=None``, no crash. ``schedule_enabled`` surfaces
        ``SMOKE_TEST_SCHEDULE_ENABLED`` so the UI can say "스케줄 비활성 / 데이터
        없음" instead of implying failure when there are simply no runs.
        """
        date_from = utc_now() - timedelta(days=days)
        runs = self._load_recent_smoke_runs(db, date_from=date_from)
        cycle_count = len(runs)
        passed_count = sum(1 for run in runs if bool(run.overall_passed))
        failed_count = cycle_count - passed_count
        current_streak = self._current_smoke_pass_streak(runs)
        per_phase, failure_categories = self._build_smoke_phase_summary(runs)

        return {
            "cycle_count": cycle_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "pass_rate": self._rate(passed_count, cycle_count),
            "current_streak": current_streak,
            "healthy_streak_target": self.SMOKE_HEALTHY_STREAK,
            "current_streak_meets_target": current_streak >= self.SMOKE_HEALTHY_STREAK,
            "schedule_enabled": bool(settings.SMOKE_TEST_SCHEDULE_ENABLED),
            "failure_category_breakdown": {
                key: value for key, value in failure_categories.items() if value > 0
            },
            "per_phase": per_phase,
            "latest": self._build_latest_smoke_summary(runs[0]) if runs else None,
            "recent_failures": self._build_recent_smoke_failures(
                runs,
                recent_limit=recent_limit,
            ),
        }

    def _load_recent_smoke_runs(
        self,
        db: Session,
        *,
        date_from: datetime,
    ) -> list[SmokeTestRun]:
        return (
            db.query(SmokeTestRun)
            .filter(SmokeTestRun.created_at >= date_from)
            .order_by(SmokeTestRun.created_at.desc(), SmokeTestRun.id.desc())
            .all()
        )

    def _current_smoke_pass_streak(self, runs: list[SmokeTestRun]) -> int:
        current_streak = 0
        for run in runs:
            if not bool(run.overall_passed):
                break
            current_streak += 1
        return current_streak

    def _build_smoke_phase_summary(
        self,
        runs: list[SmokeTestRun],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        phase_passed: dict[str, int] = {name: 0 for name in self.SMOKE_PHASE_NAMES}
        phase_attempted: dict[str, int] = {name: 0 for name in self.SMOKE_PHASE_NAMES}
        failure_categories: dict[str, int] = {
            name: 0 for name in self.SMOKE_FAILURE_CATEGORIES
        }
        for run in runs:
            for phase in self._load_smoke_phases(run.phases):
                name = str(phase.get("name") or "")
                if name not in phase_attempted:
                    continue
                if self._is_skipped_phase(phase):
                    category = self._smoke_failure_category(phase)
                    if category == "no_candidate":
                        failure_categories[category] = failure_categories.get(category, 0) + 1
                    continue
                phase_attempted[name] += 1
                if bool(phase.get("passed")):
                    phase_passed[name] += 1
                else:
                    category = self._smoke_failure_category(phase)
                    failure_categories[category] = failure_categories.get(category, 0) + 1
        per_phase = [
            {
                "name": name,
                "pass_rate": self._rate(phase_passed[name], phase_attempted[name]),
                "evaluated_count": phase_attempted[name],
            }
            for name in self.SMOKE_PHASE_NAMES
        ]
        return per_phase, failure_categories

    def _build_latest_smoke_summary(self, latest_run: SmokeTestRun) -> dict[str, Any]:
        return {
            "started_at": latest_run.started_at,
            "overall_passed": bool(latest_run.overall_passed),
            "phases": [
                self._serialize_smoke_phase(
                    phase,
                    smoke_run_id=int(latest_run.id),
                    include_passed_nulls=True,
                )
                for phase in self._load_smoke_phases(latest_run.phases)
            ],
        }

    def _build_recent_smoke_failures(
        self,
        runs: list[SmokeTestRun],
        *,
        recent_limit: int,
    ) -> list[dict[str, Any]]:
        recent_failures = []
        for run in runs:
            if bool(run.overall_passed):
                continue
            failed_phases = [
                phase
                for phase in self._load_smoke_phases(run.phases)
                if not bool(phase.get("passed"))
            ]
            run_categories: dict[str, int] = {}
            for phase in failed_phases:
                category = self._smoke_failure_category(phase)
                run_categories[category] = run_categories.get(category, 0) + 1
            phase_details = [
                self._serialize_smoke_phase(phase, smoke_run_id=int(run.id))
                for phase in failed_phases
            ]
            recent_failures.append(
                {
                    "started_at": run.started_at,
                    "failed_phases": [
                        str(phase.get("name") or "") for phase in failed_phases
                    ],
                    "failure_categories": sorted(run_categories),
                    "failure_category_breakdown": dict(
                        sorted(run_categories.items(), key=lambda item: (-item[1], item[0]))
                    ),
                    "failure_actions": sorted(
                        {
                            str(item["action_required"])
                            for item in phase_details
                            if item["action_required"]
                        }
                    ),
                    "retry_methods": sorted(
                        {
                            str(item["retry_method"])
                            for item in phase_details
                            if item["retry_method"]
                        }
                    ),
                    "phase_details": phase_details,
                }
            )
            if len(recent_failures) >= recent_limit:
                break
        return recent_failures

    def _serialize_smoke_phase(
        self,
        phase: dict[str, Any],
        *,
        smoke_run_id: int,
        include_passed_nulls: bool = False,
    ) -> dict[str, Any]:
        passed = bool(phase.get("passed"))
        if include_passed_nulls and passed:
            failure_category = None
            action_required = None
            retry_method = None
        else:
            failure_category = self._smoke_failure_category(phase)
            action_required = self._smoke_failure_action(phase)
            retry_method = self._smoke_retry_method(phase)
        return {
            "name": str(phase.get("name") or ""),
            "passed": passed,
            "detail": str(phase.get("detail") or ""),
            "failure_category": failure_category,
            "action_required": action_required,
            "retry_method": retry_method,
            "skip_reason": self._smoke_skip_reason(phase),
            "evidence": self._smoke_phase_evidence(
                phase,
                smoke_run_id=smoke_run_id,
            ),
        }

    def _with_synthetic_scope_detail(self, detail: str) -> str:
        return f"{detail} {self.SYNTHETIC_CANONICAL_ONLY_REASON}"

    def _load_smoke_phases(self, raw_phases: Any) -> list[dict[str, Any]]:
        """Restore ``SmokeTestRun.phases`` into a list of phase dicts.

        Decoding + the array shape check live in the shared restore path
        (``app.services.stored_json_payload``); this mixin keeps its own degrade
        policy (unreadable -> *no* phases, so a corrupt run counts as zero
        attempted phases rather than crashing the smoke summary) and the
        per-element ``isinstance`` filter that drops non-object entries.
        """
        if isinstance(raw_phases, list):
            return [phase for phase in raw_phases if isinstance(phase, dict)]
        items = load_stored_json_array(
            str(raw_phases or "[]"), context="smoke_test_run.phases"
        )
        return [phase for phase in items or [] if isinstance(phase, dict)]

    def _is_skipped_phase(self, phase: dict[str, Any]) -> bool:
        """Return whether a phase record marks a *skipped* (never-run) occurrence.

        A skipped phase is persisted with ``passed=False`` and a ``detail`` that
        starts with ``"skipped"`` (e.g. ``"skipped — no eligible project"``). A
        genuinely-attempted-but-failed phase has a non-skipped detail and still
        counts as attempted+fail.
        """
        if bool(phase.get("passed")):
            return False
        detail = str(phase.get("detail") or "").strip().lower()
        return detail.startswith("skipped")

    def _smoke_failure_category(self, phase: dict[str, Any]) -> str:
        """Classify a failed smoke phase into the roadmap's fixed buckets.

        Trusts the producer-stored ``failure_category`` when it is one of the
        canonical buckets, otherwise re-derives it via the shared classifier
        (guidance-less / legacy rows).
        """
        stored = str(phase.get("failure_category") or "").strip().lower()
        if stored in self.SMOKE_FAILURE_CATEGORIES:
            return stored
        return classify_failure(
            str(phase.get("name") or ""),
            str(phase.get("detail") or ""),
        )

    def _smoke_failure_action(self, phase: dict[str, Any]) -> str:
        stored = str(phase.get("action_required") or "").strip()
        if stored:
            return stored
        return self._smoke_failure_guidance(self._smoke_failure_category(phase))["action_required"]

    def _smoke_retry_method(self, phase: dict[str, Any]) -> str:
        stored = str(phase.get("retry_method") or "").strip()
        if stored:
            return stored
        return self._smoke_failure_guidance(self._smoke_failure_category(phase))["retry_method"]

    def _smoke_skip_reason(self, phase: dict[str, Any]) -> str | None:
        stored = str(phase.get("skip_reason") or "").strip()
        if stored:
            return stored
        if not self._is_skipped_phase(phase):
            return None
        detail = str(phase.get("detail") or "").strip()
        if detail.lower().startswith("skipped"):
            return detail.replace("skipped", "", 1).strip(" -—")
        return detail or None

    def _smoke_phase_evidence(
        self,
        phase: dict[str, Any],
        *,
        smoke_run_id: int | None = None,
    ) -> dict[str, Any]:
        evidence = phase.get("evidence")
        scoped = dict(evidence) if isinstance(evidence, dict) else {}
        scoped.setdefault("evidence_scope", self.SMOKE_EVIDENCE_SCOPE)
        phase_name = str(phase.get("name") or "")
        monitor_run_id = self._optional_int(scoped.get("monitor_run_id"))
        if phase_name == "candidate_generation" and monitor_run_id is not None:
            scoped.setdefault("source_run_type", "operator_strategy_monitor")
            scoped.setdefault("source_run_id", monitor_run_id)
        else:
            scoped.setdefault("source_run_type", self.SMOKE_SOURCE_RUN_TYPE)
            if smoke_run_id is not None:
                scoped.setdefault("source_run_id", int(smoke_run_id))
        if smoke_run_id is not None:
            scoped.setdefault("source_smoke_run_id", int(smoke_run_id))
        operator_id = self._optional_int(scoped.get("operator_id") or scoped.get("current_operator_id"))
        if operator_id is not None:
            scoped["operator_id"] = operator_id
            scoped.setdefault("operator_scope", "operator")
            return scoped
        scoped.setdefault("operator_scope", "canonical_only")
        scoped.setdefault("canonical_only_reason", self.SMOKE_CANONICAL_ONLY_REASON)
        return scoped

    @staticmethod
    def _smoke_failure_guidance(category: str) -> dict[str, str]:
        """Return shared remediation guidance for a smoke failure category."""
        return guidance_for(category)

    def _smoke_test_status(self, summary: dict[str, Any]) -> tuple[str, str]:
        """Convert smoke cycle telemetry into a dashboard status + detail."""
        cycle_count = int(summary.get("cycle_count") or 0)
        schedule_enabled = bool(summary.get("schedule_enabled"))
        if cycle_count == 0:
            if not schedule_enabled:
                return "info", "스모크 스케줄 비활성 / 데이터 없음."
            return "watch", "스모크 스케줄은 켜져 있으나 기록된 사이클이 없습니다."
        streak = int(summary.get("current_streak") or 0)
        pass_rate = float(summary.get("pass_rate") or 0.0)
        if streak >= self.SMOKE_HEALTHY_STREAK:
            return "healthy", f"G-0 기준 충족: 최근 {streak}회 연속 통과 ({cycle_count}회)."
        if pass_rate >= 0.9:
            return "watch", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."
        if pass_rate >= 0.65:
            return "watch", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."
        return "critical", f"G-0 연속 통과 {streak}/{self.SMOKE_HEALTHY_STREAK}회, 통과율 {pass_rate:.0%}."

    def _smoke_test_dashboard_cards(
        self,
        smoke_test_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        smoke_status, smoke_detail = self._smoke_test_status(smoke_test_summary)
        return [
            {
                "key": "smoke_test_streak",
                "label": "G-0 scheduled smoke streak",
                "value": smoke_test_summary["current_streak"],
                "unit": "count",
                "status": smoke_status,
                "detail": smoke_detail,
            },
            {
                "key": "smoke_test_pass_rate",
                "label": "G-0 scheduled smoke pass rate",
                "value": smoke_test_summary["pass_rate"],
                "unit": "ratio",
                "status": smoke_status,
                "detail": (
                    f"{smoke_test_summary['passed_count']}/{smoke_test_summary['cycle_count']} "
                    "G-0 scheduled smoke cycle(s) passed; per-operator G-2 evidence lives on monitor/experiment runs."
                ),
            },
        ]
