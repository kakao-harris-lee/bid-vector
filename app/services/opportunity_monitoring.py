"""Strategy-driven project monitoring for the singleton operator."""

from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.core.single_user import ensure_operator_account, ensure_operator_profile, ensure_operator_strategy, split_multi_value_text
from app.models.models import OperatorStrategy, OperatorStrategyRun, Project
from app.schemas.schemas import BidDecisionSaveRequest, OpportunityAnalysisRequest, OperatorStrategyMonitorRequest
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService
from app.services.opportunity_analysis import OpportunityAnalysisService


@dataclass
class StrategyFilterResult:
    """Intermediate result for strategy pre-filter checks."""

    matched: bool
    reasons: list[str]


@dataclass
class StrategyCandidateEvaluation:
    """A strategy-filtered project paired with its analysis payload."""

    project: Project
    analysis: dict
    strategy_reasons: list[str]


class StrategyMonitoringService:
    """Evaluate open procurement notices against the operator's watch strategy."""

    ACTIVE_PROJECT_STATUSES = ("open", "re_notice")
    DEFAULT_LIMIT = 10
    DEFAULT_MAX_ACTIVE_BIDS = 3
    DEFAULT_SAME_CATEGORY_ONLY = True
    DEFAULT_SIMILAR_LIMIT = 3
    DEFAULT_MIN_SIMILARITY = 0.15
    SYNC_TRIGGER_SOURCE = "manual_sync"
    ASYNC_TRIGGER_SOURCE = "manual_async"
    SCHEDULED_TRIGGER_SOURCE = "scheduled"

    def __init__(self) -> None:
        self.analysis_service = OpportunityAnalysisService()
        self.decision_service = BidDecisionService()
        self.notification_service = OperatorNotificationService()

    def preview_candidates(
        self,
        db: Session,
        *,
        limit: int | None = None,
        high_priority_only: bool | None = None,
    ) -> dict:
        """Return strategy-matched candidates ranked by decision priority."""
        operator = ensure_operator_account(db)
        ensure_operator_profile(db)
        strategy = ensure_operator_strategy(db)

        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=limit,
            high_priority_only=high_priority_only,
        )
        evaluations, evaluated_project_count = self._collect_candidate_evaluations(
            db,
            strategy=strategy,
            high_priority_only=resolved_high_priority_only,
            max_active_bids=self.DEFAULT_MAX_ACTIVE_BIDS,
            current_workload_score=None,
            same_category_only=self.DEFAULT_SAME_CATEGORY_ONLY,
            similar_limit=self.DEFAULT_SIMILAR_LIMIT,
            min_similarity=self.DEFAULT_MIN_SIMILARITY,
        )
        candidates = [self._serialize_candidate(evaluation) for evaluation in evaluations[:resolved_limit]]

        return {
            "operator_id": operator.id,
            "evaluated_project_count": evaluated_project_count,
            "returned_candidate_count": len(candidates),
            "high_priority_only": resolved_high_priority_only,
            "candidates": candidates,
        }

    def execute_monitoring(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str = SYNC_TRIGGER_SOURCE,
        existing_run_id: int | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Run the stored strategy, persist bid decisions, and create notifications."""
        operator = ensure_operator_account(db)
        ensure_operator_profile(db)
        strategy = ensure_operator_strategy(db)

        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=request.limit,
            high_priority_only=request.high_priority_only,
        )
        monitor_run = self._prepare_monitor_run(
            db,
            operator_id=operator.id,
            request=request,
            trigger_source=trigger_source,
            resolved_high_priority_only=resolved_high_priority_only,
            resolved_limit=resolved_limit,
            existing_run_id=existing_run_id,
            task_id=task_id,
        )
        previous_run = self._get_previous_completed_run(
            db,
            operator_id=operator.id,
            exclude_run_id=monitor_run.id,
            before_run_id=monitor_run.id,
        )
        previous_result_payload = self._load_json(previous_run.result_payload if previous_run else None)
        previous_candidate_project_ids = {
            int(item["project_id"])
            for item in self._extract_result_items(previous_result_payload)
        }

        try:
            evaluations, evaluated_project_count = self._collect_candidate_evaluations(
                db,
                strategy=strategy,
                high_priority_only=resolved_high_priority_only,
                max_active_bids=request.max_active_bids,
                current_workload_score=request.current_workload_score,
                same_category_only=request.same_category_only,
                similar_limit=request.similar_limit,
                min_similarity=request.min_similarity,
            )

            results: list[dict] = []
            selected_evaluations = evaluations[:resolved_limit]

            for evaluation in selected_evaluations:
                project = evaluation.project
                refreshed_analysis = self._analyze_project(
                    db,
                    project,
                    max_active_bids=request.max_active_bids,
                    current_workload_score=request.current_workload_score,
                    same_category_only=request.same_category_only,
                    similar_limit=request.similar_limit,
                    min_similarity=request.min_similarity,
                )

                if float(refreshed_analysis["matched_score"]) < float(strategy.minimum_match_score or 0.0):
                    continue
                if float(refreshed_analysis["probability_score"]) < float(strategy.minimum_probability_score or 0.0):
                    continue
                if resolved_high_priority_only and not self._is_high_priority_candidate(refreshed_analysis):
                    continue

                decision_record = self.decision_service.save_decision(
                    db,
                    BidDecisionSaveRequest(
                        project_id=project.id,
                        recommended_amount=float(refreshed_analysis["recommended_amount"]),
                        probability_score=float(refreshed_analysis["probability_score"]),
                        matched_score=float(refreshed_analysis["matched_score"]),
                        deadline_hours_remaining=refreshed_analysis.get("deadline_hours_remaining"),
                        current_active_bids=int(refreshed_analysis.get("current_active_bids") or 0),
                        max_active_bids=int(refreshed_analysis.get("max_active_bids") or request.max_active_bids),
                        current_workload_score=self._resolve_current_workload_score(
                            refreshed_analysis,
                            fallback=request.current_workload_score,
                        ),
                        budget_estimate=float(project.budget_estimate or 0.0),
                        competitiveness_score=self._resolve_competitiveness_score(refreshed_analysis),
                        expected_margin_score=self._resolve_expected_margin_score(refreshed_analysis),
                        execution_complexity_score=self._resolve_execution_complexity_score(refreshed_analysis),
                        workload_source=str(
                            refreshed_analysis.get("workload_source")
                            or ("provided" if request.current_workload_score is not None else "auto")
                        ),
                    ),
                )
                is_new_candidate = project.id not in previous_candidate_project_ids
                notification = None
                if is_new_candidate:
                    notification = self.notification_service.create_bid_decision_notification(
                        db,
                        operator_id=operator.id,
                        project=project,
                        decision_record=decision_record,
                    )
                results.append({
                    "project_id": project.id,
                    "title": project.title,
                    "decision_record_id": int(decision_record.id),
                    "notification_id": int(notification.id) if notification is not None else None,
                    "action": str(decision_record.action),
                    "decision_status": str(decision_record.decision_status),
                    "priority_score": float(decision_record.priority_score or 0.0),
                    "probability_score": float(decision_record.probability_score or 0.0),
                    "matched_score": float(decision_record.matched_score or 0.0),
                    "recommended_amount": float(decision_record.recommended_amount or 0.0),
                    "analysis_summary": str(refreshed_analysis.get("analysis_summary") or ""),
                    "is_new_candidate": is_new_candidate,
                    "notification_created": notification is not None,
                    "strategy_reasons": evaluation.strategy_reasons,
                })

            run_diff = self._build_run_diff({"results": results}, previous_result_payload)
            notification_count = sum(1 for item in results if item.get("notification_created"))

            response = {
                "monitor_run_id": monitor_run.id,
                "task_id": monitor_run.task_id,
                "trigger_source": trigger_source,
                "previous_run_id": previous_run.id if previous_run else None,
                "operator_id": operator.id,
                "evaluated_project_count": evaluated_project_count,
                "selected_candidate_count": len(selected_evaluations),
                "persisted_candidate_count": len(results),
                "notification_count": notification_count,
                "new_candidate_count": run_diff["new_candidate_count"],
                "continuing_candidate_count": run_diff["continuing_candidate_count"],
                "dropped_candidate_count": run_diff["dropped_candidate_count"],
                "high_priority_only": resolved_high_priority_only,
                "limit_applied": resolved_limit,
                "new_candidate_project_ids": run_diff["new_candidate_project_ids"],
                "continuing_candidate_project_ids": run_diff["continuing_candidate_project_ids"],
                "dropped_candidate_project_ids": run_diff["dropped_candidate_project_ids"],
                "results": results,
            }
            self._mark_run_completed(db, run_id=monitor_run.id, response=response)
            return response
        except Exception as exc:
            self._mark_run_failed(db, run_id=monitor_run.id, error_message=str(exc))
            raise

    def create_monitor_run(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str,
        task_id: str | None = None,
        status: str = "queued",
    ) -> OperatorStrategyRun:
        """Create a persisted monitoring run record before execution starts."""
        operator = ensure_operator_account(db)
        strategy = ensure_operator_strategy(db)
        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=request.limit,
            high_priority_only=request.high_priority_only,
        )
        monitor_run = OperatorStrategyRun(
            operator_id=operator.id,
            task_id=task_id,
            trigger_source=trigger_source,
            status=status,
            high_priority_only=resolved_high_priority_only,
            limit_applied=resolved_limit,
            request_payload=self._dump_json(request.model_dump(mode="json")),
            started_at=utc_now() if status == "running" else None,
        )
        db.add(monitor_run)
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def update_monitor_run_task_id(self, db: Session, *, run_id: int, task_id: str) -> OperatorStrategyRun | None:
        """Attach the async task id to an already-created monitoring run."""
        monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
        if monitor_run is None:
            return None
        monitor_run.task_id = task_id
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def list_recent_runs(self, db: Session, *, limit: int = 20, run_status: str | None = None) -> list[OperatorStrategyRun]:
        """Return recent monitoring execution history for the singleton operator."""
        operator = ensure_operator_account(db)
        query = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.operator_id == operator.id)
        if run_status:
            query = query.filter(OperatorStrategyRun.status == run_status)
        return (
            query.order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
            .limit(limit)
            .all()
        )

    def get_run_detail(self, db: Session, *, run_id: int) -> dict:
        """Return one monitoring run with stored payloads and previous-run diff details."""
        operator = ensure_operator_account(db)
        monitor_run = (
            db.query(OperatorStrategyRun)
            .filter(
                OperatorStrategyRun.id == run_id,
                OperatorStrategyRun.operator_id == operator.id,
            )
            .first()
        )
        if monitor_run is None:
            raise ValueError("Monitoring run not found")

        request_payload = self._load_json(monitor_run.request_payload)
        result_payload = self._load_json(monitor_run.result_payload)
        previous_run = self._get_previous_completed_run(
            db,
            operator_id=operator.id,
            exclude_run_id=monitor_run.id,
            before_run_id=monitor_run.id,
        )
        previous_result_payload = self._load_json(previous_run.result_payload if previous_run else None)
        diff = self._build_run_diff(result_payload, previous_result_payload) if result_payload else {
            "new_candidate_count": 0,
            "continuing_candidate_count": 0,
            "dropped_candidate_count": 0,
            "new_candidates": [],
            "continuing_candidates": [],
            "dropped_candidates": [],
        }

        return {
            **self.serialize_run(monitor_run),
            "previous_run_id": result_payload.get("previous_run_id") or (previous_run.id if previous_run else None),
            "new_candidate_count": int(result_payload.get("new_candidate_count", diff["new_candidate_count"])),
            "continuing_candidate_count": int(result_payload.get("continuing_candidate_count", diff["continuing_candidate_count"])),
            "dropped_candidate_count": int(result_payload.get("dropped_candidate_count", diff["dropped_candidate_count"])),
            "request_payload": request_payload,
            "result": result_payload or None,
            "new_candidates": diff["new_candidates"],
            "continuing_candidates": diff["continuing_candidates"],
            "dropped_candidates": diff["dropped_candidates"],
        }

    def serialize_run(self, monitor_run: OperatorStrategyRun) -> dict:
        """Convert a stored monitoring run into the public API response shape."""
        return {
            "id": int(monitor_run.id),
            "operator_id": int(monitor_run.operator_id),
            "task_id": monitor_run.task_id,
            "trigger_source": str(monitor_run.trigger_source),
            "status": str(monitor_run.status),
            "high_priority_only": bool(monitor_run.high_priority_only),
            "limit_applied": int(monitor_run.limit_applied or self.DEFAULT_LIMIT),
            "evaluated_project_count": int(monitor_run.evaluated_project_count or 0),
            "selected_candidate_count": int(monitor_run.selected_candidate_count or 0),
            "persisted_candidate_count": int(monitor_run.persisted_candidate_count or 0),
            "notification_count": int(monitor_run.notification_count or 0),
            "error_message": monitor_run.error_message,
            "created_at": monitor_run.created_at,
            "started_at": monitor_run.started_at,
            "completed_at": monitor_run.completed_at,
        }

    def _resolve_runtime_options(
        self,
        strategy: OperatorStrategy,
        *,
        limit: int | None,
        high_priority_only: bool | None,
    ) -> tuple[int, bool]:
        """Resolve limit and high-priority mode from per-run overrides or stored strategy."""
        resolved_limit = max(1, min(int(limit or strategy.max_recommended_candidates or self.DEFAULT_LIMIT), 100))
        resolved_high_priority_only = strategy.notify_only_high_priority if high_priority_only is None else bool(high_priority_only)
        return resolved_limit, resolved_high_priority_only

    def _prepare_monitor_run(
        self,
        db: Session,
        *,
        operator_id: int,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str,
        resolved_high_priority_only: bool,
        resolved_limit: int,
        existing_run_id: int | None,
        task_id: str | None,
    ) -> OperatorStrategyRun:
        """Create or transition a monitoring run record into the running state."""
        if existing_run_id is None:
            return self.create_monitor_run(
                db,
                request=request,
                trigger_source=trigger_source,
                task_id=task_id,
                status="running",
            )

        monitor_run = db.query(OperatorStrategyRun).filter(
            OperatorStrategyRun.id == existing_run_id,
            OperatorStrategyRun.operator_id == operator_id,
        ).first()
        if monitor_run is None:
            raise ValueError("Monitoring run not found")

        monitor_run.task_id = task_id or monitor_run.task_id
        monitor_run.trigger_source = trigger_source
        monitor_run.status = "running"
        monitor_run.high_priority_only = resolved_high_priority_only
        monitor_run.limit_applied = resolved_limit
        monitor_run.request_payload = self._dump_json(request.model_dump(mode="json"))
        monitor_run.error_message = None
        monitor_run.started_at = monitor_run.started_at or utc_now()
        monitor_run.completed_at = None
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def _get_previous_completed_run(
        self,
        db: Session,
        *,
        operator_id: int,
        exclude_run_id: int,
        before_run_id: int | None = None,
    ) -> OperatorStrategyRun | None:
        """Return the latest completed run before the target run id."""
        query = db.query(OperatorStrategyRun).filter(
            OperatorStrategyRun.operator_id == operator_id,
            OperatorStrategyRun.status == "completed",
            OperatorStrategyRun.id != exclude_run_id,
        )
        if before_run_id is not None:
            query = query.filter(OperatorStrategyRun.id < before_run_id)
        return query.order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc()).first()

    def _mark_run_completed(self, db: Session, *, run_id: int, response: dict) -> None:
        """Persist the final summary of a completed monitoring run."""
        monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
        if monitor_run is None:
            return
        monitor_run.status = "completed"
        monitor_run.evaluated_project_count = int(response.get("evaluated_project_count") or 0)
        monitor_run.selected_candidate_count = int(response.get("selected_candidate_count") or 0)
        monitor_run.persisted_candidate_count = int(response.get("persisted_candidate_count") or 0)
        monitor_run.notification_count = int(response.get("notification_count") or 0)
        monitor_run.result_payload = self._dump_json(response)
        monitor_run.error_message = None
        monitor_run.completed_at = utc_now()
        db.commit()

    def _mark_run_failed(self, db: Session, *, run_id: int, error_message: str) -> None:
        """Persist failure metadata for a monitoring run after rollback-safe recovery."""
        db.rollback()
        monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
        if monitor_run is None:
            return
        monitor_run.status = "failed"
        monitor_run.error_message = error_message
        monitor_run.completed_at = utc_now()
        db.commit()

    def _collect_candidate_evaluations(
        self,
        db: Session,
        *,
        strategy: OperatorStrategy,
        high_priority_only: bool,
        max_active_bids: int,
        current_workload_score: float | None,
        same_category_only: bool,
        similar_limit: int,
        min_similarity: float,
    ) -> tuple[list[StrategyCandidateEvaluation], int]:
        """Analyze all currently actionable projects that pass stored watch rules."""
        open_projects = db.query(Project).filter(Project.status.in_(self.ACTIVE_PROJECT_STATUSES)).all()
        evaluations: list[StrategyCandidateEvaluation] = []
        evaluated_project_count = 0

        for project in open_projects:
            filter_result = self._apply_strategy_filters(project, strategy)
            if not filter_result.matched:
                continue

            evaluated_project_count += 1
            analysis = self._analyze_project(
                db,
                project,
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

    def _analyze_project(
        self,
        db: Session,
        project: Project,
        *,
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

    def _apply_strategy_filters(self, project: Project, strategy: OperatorStrategy) -> StrategyFilterResult:
        """Apply cheap watch-rule filters before running heavier analysis."""
        project_text = self._build_project_text(project)
        reasons: list[str] = []

        focus_categories = split_multi_value_text(strategy.focus_categories)
        if focus_categories:
            project_category = (project.category or "").strip().lower()
            normalized_categories = {value.lower() for value in focus_categories}
            if project_category not in normalized_categories:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 카테고리 일치: {project.category or '미분류'}")

        focus_regions = split_multi_value_text(strategy.focus_regions)
        matched_focus_regions = self._matched_terms(project_text, focus_regions)
        if focus_regions:
            if not matched_focus_regions:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 지역 일치: {', '.join(matched_focus_regions[:2])}")

        exclude_regions = split_multi_value_text(strategy.exclude_regions)
        if self._matched_terms(project_text, exclude_regions):
            return StrategyFilterResult(matched=False, reasons=[])

        required_keywords = split_multi_value_text(strategy.required_keywords)
        matched_keywords = self._matched_terms(project_text, required_keywords)
        if required_keywords:
            if not matched_keywords:
                return StrategyFilterResult(matched=False, reasons=[])
            reasons.append(f"관심 키워드 일치: {', '.join(matched_keywords[:3])}")

        exclude_keywords = split_multi_value_text(strategy.exclude_keywords)
        if self._matched_terms(project_text, exclude_keywords):
            return StrategyFilterResult(matched=False, reasons=[])

        project_budget = float(project.budget_estimate or 0.0)
        min_budget = float(strategy.min_budget_estimate or 0.0)
        max_budget = float(strategy.max_budget_estimate or 0.0)
        if min_budget > 0 and project_budget < min_budget:
            return StrategyFilterResult(matched=False, reasons=[])
        if max_budget > 0 and project_budget > max_budget:
            return StrategyFilterResult(matched=False, reasons=[])
        if min_budget > 0 or max_budget > 0:
            reasons.append("예산 범위 일치")

        if not reasons:
            reasons.append("기본 전략 조건 통과")

        return StrategyFilterResult(matched=True, reasons=reasons)

    def _build_project_text(self, project: Project) -> str:
        """Flatten the main searchable project fields into lowercase text."""
        return " ".join(
            part.strip()
            for part in [project.title or "", project.description or "", project.requirements or "", project.category or ""]
            if part and part.strip()
        ).lower()

    def _matched_terms(self, project_text: str, terms: list[str]) -> list[str]:
        """Return watch terms that appear in the project text, preserving user order."""
        matches: list[str] = []
        seen: set[str] = set()
        for term in terms:
            normalized = term.strip().lower()
            if not normalized or normalized in seen:
                continue
            if normalized in project_text:
                matches.append(term.strip())
                seen.add(normalized)
        return matches

    def _is_high_priority_candidate(self, analysis: dict) -> bool:
        """Align preview filtering with the service's high-priority action semantics."""
        decision = analysis.get("decision", {})
        return bool(decision.get("pursue_bid")) and str(decision.get("action")) == "bid_now"

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
