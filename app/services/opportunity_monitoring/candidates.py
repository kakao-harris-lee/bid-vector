"""Candidate collection: scan bounds, watch-rule pass, and the license gate.

``_collect_candidate_evaluations`` and its helpers, moved verbatim from the
original ``opportunity_monitoring`` module.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_
from sqlalchemy.orm import Session

import app.services.opportunity_monitoring as _monitoring_pkg
from app.core.config import settings
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    split_multi_value_text,
)
from app.core.time import utc_now
from app.models.models import OperatorStrategy, Project, User
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.license_eligibility import (
    VERDICT_INELIGIBLE,
    assess_license_eligibility,
)
from app.services.opportunity_monitoring.base import (
    StrategyCandidateEvaluation,
    _MonitoringBase,
)

# Audit logging stays under the historical logger name ("app.services.
# opportunity_monitoring", not the submodule) so the license-gate exclusion
# record keeps the exact logger name callers (and tests) capture on.
logger = logging.getLogger("app.services.opportunity_monitoring")


class _CandidateCollectionMixin(_MonitoringBase):
    """Load actionable notices and narrow them to watch-rule + license-eligible candidates."""

    def _collect_candidate_evaluations(
        self,
        db: Session,
        *,
        strategy: OperatorStrategy,
        operator: User,
        high_priority_only: bool,
        max_active_bids: int,
        current_workload_score: float | None,
        same_category_only: bool,
        similar_limit: int,
        min_similarity: float,
        scan_limit: int | None = None,
    ) -> tuple[list[StrategyCandidateEvaluation], int]:
        """Analyze currently actionable projects that pass stored watch rules.

        Only notices that are still biddable are considered: projects whose
        deadline has already passed are excluded (NULL deadlines are kept).

        When ``scan_limit`` is provided the query loads only the ``scan_limit``
        most deadline-imminent *future* notices (``deadline asc``) before
        applying the cheap watch filters and the expensive per-candidate
        analysis. This bounds the work for the interactive preview
        (``_preview_scan_limit``) and the periodic schedule
        (``_schedule_scan_limit``) so neither can scan the full open-notice
        table. When ``scan_limit`` is ``None`` (manual sync/async runs) every
        still-biddable active project is considered.
        """
        if not self._has_configured_watch_rules(strategy):
            return [], 0

        # Live bid-eligibility filter: only evaluate notices that are still
        # biddable. Projects whose deadline has already passed cannot be bid on,
        # so evaluating them wastes ML analysis and (combined with the bounded
        # ``deadline asc`` scan) can crowd out genuinely imminent future notices.
        # NULL deadlines are intentionally INCLUDED so that missing deadline
        # metadata never silently hides a candidate from the operator. This is a
        # real-time eligibility narrowing only -- it is unrelated to predictor
        # guardrails or the backtest cutoff path and introduces no leakage.
        query = (
            db.query(Project)
            .filter(Project.status.in_(self.ACTIVE_PROJECT_STATUSES))
            .filter(or_(Project.deadline.is_(None), Project.deadline > utc_now()))
            .order_by(Project.deadline.asc(), Project.id.asc())
        )
        if scan_limit is not None:
            query = query.limit(max(1, int(scan_limit)))
        open_projects = query.all()
        # Held-license context for the license-eligibility gate. Resolved once
        # per scan (never per candidate) and only when the gate is enabled — so a
        # disabled gate adds no DB query and no behavior change (see
        # _resolve_license_gate_profile_codes).
        license_gate_codes = self._resolve_license_gate_profile_codes(db, operator)
        evaluations: list[StrategyCandidateEvaluation] = []
        evaluated_project_count = 0

        for project in open_projects:
            filter_result = self._apply_strategy_filters(project, strategy)
            if not filter_result.matched:
                continue

            # License-eligibility gate — cheap eligibility_raw ↔ held-license
            # check placed BEFORE the expensive ML analysis. Only a data-confirmed
            # ineligible verdict drops the candidate; unknown/eligible pass
            # through unchanged. Excluded here (not counted as evaluated) because
            # no ML analysis runs, mirroring the strategy-filter miss above.
            if self._license_gate_excludes(project, license_gate_codes):
                continue

            evaluated_project_count += 1
            analysis = self._analyze_project(
                db,
                project,
                operator=operator,
                max_active_bids=max_active_bids,
                current_workload_score=current_workload_score,
                same_category_only=same_category_only,
                similar_limit=similar_limit,
                min_similarity=min_similarity,
            )

            if float(analysis["matched_score"]) < float(strategy.minimum_match_score or 0.0):
                continue
            if float(analysis["probability_score"]) < float(strategy.minimum_probability_score or 0.0):
                continue
            if high_priority_only and not self._is_high_priority_candidate(analysis):
                continue

            evaluations.append(
                StrategyCandidateEvaluation(
                    project=project,
                    analysis=analysis,
                    strategy_reasons=filter_result.reasons,
                )
            )

        evaluations.sort(
            key=lambda evaluation: (
                -float(evaluation.analysis.get("decision", {}).get("priority_score", 0.0) or 0.0),
                -float(evaluation.analysis.get("probability_score", 0.0) or 0.0),
                -float(evaluation.analysis.get("matched_score", 0.0) or 0.0),
                -float(evaluation.project.budget_estimate or 0.0),
                int(evaluation.project.id),
            )
        )
        return evaluations, evaluated_project_count

    def _resolve_license_gate_profile_codes(
        self, db: Session, operator: User
    ) -> str | None:
        """Held-license string for the license gate, or ``None`` when it is off.

        Returns ``None`` (gate inert) unless ``LICENSE_ELIGIBILITY_GATE_ENABLED``
        is set, so while disabled the gate performs no DB query and cannot change
        candidate selection. When enabled the operator's already-ensured company
        profile supplies ``license_codes`` (an empty/absent value stays neutral —
        the assessment then returns ``unknown``, which never excludes).

        ``ensure_operator_profile_for`` is resolved through the package module so
        the ``opportunity_monitoring.ensure_operator_profile_for`` monkeypatch
        surface (test_license_gate_wiring) keeps working after the split.
        """
        if not settings.LICENSE_ELIGIBILITY_GATE_ENABLED:
            return None
        profile = _monitoring_pkg.ensure_operator_profile_for(db, operator)
        return profile.license_codes if profile is not None else None

    def _license_gate_excludes(
        self, project: Project, profile_license_codes: str | None
    ) -> bool:
        """Whether the license gate rules ``project`` out (ineligible verdict only).

        Delegates the verdict to the pure
        :func:`assess_license_eligibility` interpreter (no DB/IO here). Only a
        data-confirmed ``ineligible`` (a published 면허요건 the operator provably
        lacks) excludes; ``unknown`` (no requirement data or no held-license data)
        and ``eligible`` pass through so a coverage gap never suppresses a
        candidate (§2 정직). ``None`` codes mean the gate is disabled → no-op.

        The exclusion reason (요구 면허 목록) is logged for audit; the notice is
        dropped like any other pre-filter miss, so nothing is persisted for it.
        """
        if profile_license_codes is None:
            return False
        assessment = assess_license_eligibility(
            getattr(project, "eligibility_raw", None), profile_license_codes
        )
        if assessment.verdict != VERDICT_INELIGIBLE:
            return False
        required = ", ".join(assessment.required_any) or "(요건 미상)"
        logger.info(
            "[license-gate] 후보 제외 project_id=%s 사유=필수면허 미보유(요구: %s, 보유 없음)",
            getattr(project, "id", None),
            required,
        )
        return True

    def _preview_scan_limit(self, resolved_limit: int) -> int:
        """Bound preview work so a UI read cannot scan the full production table."""
        scaled_limit = int(resolved_limit or self.DEFAULT_LIMIT) * self.PREVIEW_SCAN_MULTIPLIER
        return min(max(scaled_limit, self.PREVIEW_SCAN_FLOOR), self.PREVIEW_SCAN_CEILING)

    def _schedule_scan_limit(self, resolved_limit: int) -> int:
        """Bound the periodic schedule scan to the most deadline-imminent notices.

        The configured ``OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT`` is the
        primary bound, clamped into ``[SCHEDULE_SCAN_FLOOR, SCHEDULE_SCAN_CEILING]``.
        It is additionally floored to ``resolved_limit x SCHEDULE_SCAN_MULTIPLIER``
        so an unusually small per-run limit cannot starve the candidate pool that
        survives the watch filters. ``scan_limit`` is applied *before* the cheap
        watch filters in ``_collect_candidate_evaluations``, so the multiplier
        keeps enough headroom for the filter pass rate while still capping the
        number of expensive per-candidate analyses.
        """
        configured = int(getattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT", 0) or 0)
        multiplier_floor = int(resolved_limit or self.DEFAULT_LIMIT) * self.SCHEDULE_SCAN_MULTIPLIER
        candidate = max(configured, multiplier_floor, self.SCHEDULE_SCAN_FLOOR)
        return min(candidate, self.SCHEDULE_SCAN_CEILING)

    def _has_configured_watch_rules(self, strategy: OperatorStrategy) -> bool:
        """Return whether the operator has set any non-default monitoring criteria."""
        return any([
            bool(split_multi_value_text(strategy.focus_categories)),
            bool(split_multi_value_text(strategy.focus_regions)),
            bool(split_multi_value_text(strategy.exclude_regions)),
            bool(split_multi_value_text(strategy.required_keywords)),
            bool(split_multi_value_text(strategy.exclude_keywords)),
            float(strategy.min_budget_estimate or 0.0) > 0,
            float(strategy.max_budget_estimate or 0.0) > 0,
            round(float(strategy.minimum_match_score or 0.0), 4) != 0.6,
            round(float(strategy.minimum_probability_score or 0.0), 4) != 0.55,
            round(float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD), 4)
            != DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
            round(float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD), 4)
            != DEFAULT_OPERATOR_REVIEW_THRESHOLD,
            round(float(strategy.auto_workload_penalty_multiplier or 1.0), 4) != 1.0,
            bool(self._load_json(strategy.category_priority_overrides or "{}")),
            bool(strategy.notify_only_high_priority) is False,
            int(strategy.max_recommended_candidates or self.DEFAULT_LIMIT) != self.DEFAULT_LIMIT,
        ])

    def _analyze_project(
        self,
        db: Session,
        project: Project,
        *,
        operator: User,
        max_active_bids: int,
        current_workload_score: float | None,
        same_category_only: bool,
        similar_limit: int,
        min_similarity: float,
    ) -> dict:
        """Run the shared multi-angle opportunity analysis with runtime overrides."""
        return self.analysis_service.analyze_project(
            db,
            project,
            OpportunityAnalysisRequest(
                project_id=project.id,
                max_active_bids=max_active_bids,
                current_workload_score=current_workload_score,
                same_category_only=same_category_only,
                similar_limit=similar_limit,
                min_similarity=min_similarity,
            ),
            operator=operator,
        )

    def _serialize_candidate(self, evaluation: StrategyCandidateEvaluation) -> dict:
        """Convert an evaluated strategy candidate into the preview API shape."""
        decision = evaluation.analysis["decision"]
        return {
            "project_id": evaluation.project.id,
            "title": evaluation.project.title,
            "category": evaluation.project.category,
            "budget_estimate": float(evaluation.project.budget_estimate or 0.0),
            "deadline": evaluation.project.deadline,
            "matched_score": float(evaluation.analysis["matched_score"]),
            "probability_score": float(evaluation.analysis["probability_score"]),
            "priority_score": float(decision["priority_score"]),
            "action": str(decision["action"]),
            "recommended_amount": float(evaluation.analysis["recommended_amount"]),
            "analysis_summary": str(evaluation.analysis["analysis_summary"]),
            "strategy_reasons": evaluation.strategy_reasons,
        }
