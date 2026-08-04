"""Per-candidate monitor persistence within the run transaction."""

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import OperatorStrategyRunItem, Project, User
from app.services.opportunity_monitoring.base import (
    StrategyCandidateEvaluation,
)


class MonitorCandidatePersistenceMixin:
    def _process_monitor_evaluations(
        self,
        db: Session,
        *,
        monitor_run_id: int,
        selected_evaluations: list[StrategyCandidateEvaluation],
        operator: User,
        previous_candidate_project_ids: set[int],
    ):
        results = []
        for evaluation in selected_evaluations:
            result = self._process_monitor_evaluation(
                db,
                monitor_run_id=monitor_run_id,
                evaluation=evaluation,
                operator=operator,
                previous_candidate_project_ids=previous_candidate_project_ids,
            )
            if result is not None:
                results.append(result)
        return results

    def _process_monitor_evaluation(
        self,
        db: Session,
        *,
        monitor_run_id: int,
        evaluation: StrategyCandidateEvaluation,
        operator: User,
        previous_candidate_project_ids: set[int],
    ):
        item = OperatorStrategyRunItem(
            run_id=monitor_run_id,
            project_id=evaluation.project_id,
            status="processing",
            stage="selected",
        )
        db.add(item)
        db.flush()
        project = self._load_monitor_project(db, evaluation.project_id)
        try:
            decision = self._persist_monitor_decision(db, evaluation, operator, item)
            is_new = project.id not in previous_candidate_project_ids
            item.is_new_candidate = is_new
            notification = self._persist_monitor_notification(
                db, operator, project, decision, is_new, item
            )
            return self._complete_monitor_item(
                db, item, evaluation, project, decision, notification, is_new
            )
        finally:
            if project in db:
                db.expunge(project)

    def _load_monitor_project(self, db, project_id: int) -> Project:
        project = db.get(Project, project_id)
        if project is not None:
            return project
        exc = RuntimeError(f"selected monitor project disappeared: {project_id}")
        exc.monitor_project_id = project_id
        exc.monitor_stage = "project_reload"
        raise exc

    def _persist_monitor_decision(self, db, evaluation, operator, item):
        item.stage = "workload"
        inputs = evaluation.decision_inputs
        workload = self.analysis_service.resolve_workload_context(
            db,
            operator_id=int(operator.id),
            max_active_bids=inputs.max_active_bids,
            current_workload_score=inputs.provided_workload_score,
            exclude_project_id=evaluation.project_id,
        )
        item.stage = "decision"
        try:
            return self.decision_service.save_decision(
                db,
                self._build_monitor_decision_request(inputs, workload_context=workload),
                operator=operator,
            )
        except Exception as exc:
            exc.monitor_project_id = evaluation.project_id
            exc.monitor_stage = "decision"
            raise

    def _persist_monitor_notification(
        self, db, operator, project, decision, is_new, item
    ):
        item.stage = "notification"
        try:
            if not is_new:
                return None
            return self.notification_service.create_bid_decision_notification(
                db,
                operator_id=operator.id,
                project=project,
                decision_record=decision,
            )
        except Exception as exc:
            exc.monitor_project_id = int(project.id)
            exc.monitor_stage = "notification"
            raise

    def _complete_monitor_item(
        self, db, item, evaluation, project, decision, notification, is_new
    ):
        result = {
            "project_id": project.id,
            "title": project.title,
            "decision_record_id": int(decision.id),
            "notification_id": int(notification.id) if notification else None,
            "action": str(decision.action),
            "decision_status": str(decision.decision_status),
            "priority_score": float(decision.priority_score or 0.0),
            "probability_score": float(decision.probability_score or 0.0),
            "matched_score": float(decision.matched_score or 0.0),
            "recommended_amount": float(decision.recommended_amount or 0.0),
            "analysis_summary": evaluation.decision_inputs.analysis_summary,
            "is_new_candidate": is_new,
            "notification_created": notification is not None,
            "strategy_reasons": evaluation.strategy_reasons,
        }
        item.status = "completed"
        item.stage = "completed"
        item.decision_record_id = int(decision.id)
        item.notification_id = int(notification.id) if notification else None
        result["run_item_id"] = int(item.id)
        item.result_payload = self._dump_json(result)
        item.completed_at = utc_now()
        db.flush()
        return result
