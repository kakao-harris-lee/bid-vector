"""Single-user bid pursuit decision service."""

import json
import operator

from sqlalchemy.orm import Session

from app.core.bands import resolve_band
from app.core.config import settings
from app.core.constants import ACTIVE_DECISION_STATUSES as _ACTIVE_DECISION_STATUSES
from app.core.time import utc_now
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ensure_operator_account,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.models.models import BidDecisionRecord, Project, User
from app.schemas.schemas import BidDecisionRequest
from app.services import allocation_core
from app.services.operator_strategy_tuning import (
    DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER,
    get_strategy_auto_workload_penalty_multiplier,
)
from app.utils.textfmt import append_unique_note


class BidDecisionService:
    """Decide whether a single user should pursue a bid opportunity now."""

    BID_NOW_THRESHOLD = DEFAULT_OPERATOR_BID_NOW_THRESHOLD
    REVIEW_THRESHOLD = DEFAULT_OPERATOR_REVIEW_THRESHOLD
    PROBABILITY_WEIGHT = 0.40
    MATCH_WEIGHT = 0.23
    URGENCY_WEIGHT = 0.14
    COMPETITIVENESS_WEIGHT = 0.08
    BUDGET_CAPTURE_WEIGHT = 0.06
    EXPECTED_MARGIN_WEIGHT = 0.09
    # Deadline-urgency ladder (ascending ``<=`` hours). resolve_band walks the
    # rungs with ``operator.le`` reproducing the ``<=6`` / ``<=24`` / ``<=72``
    # cascade; the ``float("inf")`` rung is the always-match "far out" fallback.
    # ``URGENCY_SCORE_UNKNOWN`` is the separate no-deadline score.
    URGENCY_SCORE_UNKNOWN = 0.3
    URGENCY_SCORE_BANDS = (
        (6, 1.0),
        (24, 0.8),
        (72, 0.55),
        (float("inf"), 0.25),
    )
    ACTIVE_DECISION_STATUSES = _ACTIVE_DECISION_STATUSES
    SUBMITTED_SYNC_NOTE = "실제 투찰이 등록되어 제출 상태로 동기화했습니다."
    FALLBACK_SUBMITTED_REASONING = "사전 결정 기록 없이 직접 투찰이 등록되어 제출 이력을 생성했습니다."
    TELEGRAM_SUBMITTED_NOTE = "텔레그램에서 투찰 버튼을 눌러 제출 상태로 전환했습니다."
    TELEGRAM_REVIEW_NOTE = "텔레그램에서 검토 버튼을 눌러 검토 대기 상태로 유지했습니다."
    TELEGRAM_SKIP_NOTE = "텔레그램에서 보류 버튼을 눌러 이번 공고를 보류 처리했습니다."

    def evaluate_opportunity(
        self,
        request: BidDecisionRequest,
        db: Session | None = None,
        *,
        operator: User | None = None,
    ) -> dict:
        """Score a notice for a single user and decide whether to pursue it."""
        bid_now_threshold, review_threshold, auto_workload_penalty_multiplier = self._resolve_strategy_settings(
            db,
            operator=operator,
        )
        urgency_score = self._compute_urgency_score(request.deadline_hours_remaining)
        active_load_ratio = self._compute_active_load_ratio(
            current_active_bids=request.current_active_bids,
            max_active_bids=request.max_active_bids,
        )
        competitiveness_score = self._normalize_unit_score(request.competitiveness_score, default=0.5)
        budget_capture_score = self._compute_budget_capture_score(
            recommended_amount=request.recommended_amount,
            budget_estimate=request.budget_estimate,
        )
        expected_margin_score = self._normalize_unit_score(
            request.expected_margin_score,
            default=budget_capture_score,
        )
        execution_complexity_score = self._normalize_unit_score(
            request.execution_complexity_score,
            default=0.35,
        )
        load_penalty = self._compute_load_penalty(
            current_active_bids=request.current_active_bids,
            max_active_bids=request.max_active_bids,
            workload_score=request.current_workload_score,
        )
        if request.workload_source == "auto":
            load_penalty = round(load_penalty * auto_workload_penalty_multiplier, 4)
        complexity_penalty = self._compute_complexity_penalty(execution_complexity_score)

        opportunity_score = (
            request.probability_score * self.PROBABILITY_WEIGHT
            + request.matched_score * self.MATCH_WEIGHT
            + urgency_score * self.URGENCY_WEIGHT
            + competitiveness_score * self.COMPETITIVENESS_WEIGHT
            + budget_capture_score * self.BUDGET_CAPTURE_WEIGHT
            + expected_margin_score * self.EXPECTED_MARGIN_WEIGHT
        )
        total_penalty = load_penalty + complexity_penalty
        priority_score = max(0.0, min(1.0, opportunity_score - total_penalty))

        # Collect the gate thresholds once at the boundary, then run the pure
        # decision core on the computed scores (§4.7.4).
        decision = allocation_core.decide(
            allocation_core.DecisionSignals(
                probability_score=request.probability_score,
                matched_score=request.matched_score,
                urgency_score=urgency_score,
                competitiveness_score=competitiveness_score,
                budget_capture_score=budget_capture_score,
                expected_margin_score=expected_margin_score,
                execution_complexity_score=execution_complexity_score,
                budget_estimate=request.budget_estimate,
                workload_source=request.workload_source,
                current_workload_score=request.current_workload_score,
                auto_workload_penalty_multiplier=auto_workload_penalty_multiplier,
                load_penalty=load_penalty,
                complexity_penalty=complexity_penalty,
                priority_score=priority_score,
                current_active_bids=request.current_active_bids,
                max_active_bids=request.max_active_bids,
            ),
            allocation_core.AllocationThresholds.from_settings(
                settings,
                bid_now_threshold=bid_now_threshold,
                review_threshold=review_threshold,
            ),
        )
        action = decision.action
        reasons = decision.reasons

        return {
            "project_id": request.project_id,
            "pursue_bid": action != "skip",
            "action": action,
            "priority_score": round(priority_score, 2),
            "recommended_amount": request.recommended_amount,
            "probability_score": request.probability_score,
            "urgency_score": round(urgency_score, 2),
            "competitiveness_score": round(competitiveness_score, 2),
            "budget_capture_score": round(budget_capture_score, 2),
            "expected_margin_score": round(expected_margin_score, 2),
            "execution_complexity_score": round(execution_complexity_score, 2),
            "workload_source": request.workload_source,
            "score_breakdown": {
                "probability_signal": round(float(request.probability_score), 2),
                "matched_signal": round(float(request.matched_score), 2),
                "urgency_signal": round(urgency_score, 2),
                "competitiveness_signal": round(competitiveness_score, 2),
                "budget_capture_signal": round(budget_capture_score, 2),
                "expected_margin_signal": round(expected_margin_score, 2),
                "execution_complexity_signal": round(execution_complexity_score, 2),
                "active_load_ratio": round(active_load_ratio, 2),
                "workload_score_used": round(float(request.current_workload_score), 2),
                "opportunity_score": round(opportunity_score, 2),
                "auto_workload_penalty_multiplier": round(float(auto_workload_penalty_multiplier), 4),
                "load_penalty": round(load_penalty, 2),
                "execution_complexity_penalty": round(complexity_penalty, 2),
                "total_penalty": round(total_penalty, 2),
            },
            "reasoning": " ".join(reasons),
        }

    def save_decision(
        self,
        db: Session,
        request: "BidDecisionSaveRequest",
        *,
        operator: User | None = None,
    ) -> BidDecisionRecord:
        """Persist or update a single operator bid-decision record."""
        from app.schemas.schemas import BidDecisionSaveRequest

        if not isinstance(request, BidDecisionSaveRequest):
            request = BidDecisionSaveRequest(**request.model_dump())

        if request.budget_estimate is None:
            project = db.query(Project).filter(Project.id == request.project_id).first()
            if project is not None:
                request = request.model_copy(update={"budget_estimate": float(project.budget_estimate or 0.0)})

        operator = operator or ensure_operator_account(db)
        decision = self.evaluate_opportunity(request, db=db, operator=operator)
        decision_status = self._resolve_decision_status(decision["action"], request.decision_status)
        record = self._get_existing_active_record(db, request.project_id, operator.id)

        if record is None:
            record = BidDecisionRecord(
                project_id=request.project_id,
                operator_id=operator.id,
                initial_action=decision["action"],
                initial_decision_status=decision_status,
                first_decided_at=utc_now(),
            )
            db.add(record)
        else:
            self._ensure_initial_tracking_fields(
                record,
                fallback_action=record.action or decision["action"],
                fallback_status=record.decision_status or decision_status,
            )

        record.pursue_bid = decision["pursue_bid"]
        record.action = decision["action"]
        record.decision_status = decision_status
        record.recommended_amount = request.recommended_amount
        record.probability_score = request.probability_score
        record.matched_score = request.matched_score
        record.priority_score = decision["priority_score"]
        record.urgency_score = decision.get("urgency_score", 0.0)
        record.competitiveness_score = decision.get("competitiveness_score", 0.0)
        record.budget_capture_score = decision.get("budget_capture_score", 0.0)
        record.expected_margin_score = decision.get("expected_margin_score", 0.0)
        record.execution_complexity_score = decision.get("execution_complexity_score", 0.0)
        record.deadline_hours_remaining = request.deadline_hours_remaining
        record.current_active_bids = request.current_active_bids
        record.max_active_bids = request.max_active_bids
        record.current_workload_score = request.current_workload_score
        record.workload_source = decision.get("workload_source", request.workload_source)
        record.score_breakdown = self._serialize_score_breakdown(
            decision.get("score_breakdown") or {},
            strengths=getattr(request, "strengths", None) or [],
            risk_flags=getattr(request, "risk_flags", None) or [],
        )
        record.reasoning = decision["reasoning"]

        db.commit()
        db.refresh(record)
        return record

    def _serialize_score_breakdown(
        self,
        signals: dict,
        *,
        strengths: list[str],
        risk_flags: list[str],
    ) -> str:
        """Merge decision reasons into the score-breakdown JSON blob.

        Decision signals and the human-facing strengths/risk_flags share the
        same Text column so reasons can be persisted without a schema change.
        Signal keys are preserved as-is; reason lists are stored under the
        dedicated ``strengths``/``risk_flags`` keys (empty lists stay omitted).
        """
        payload: dict = dict(signals or {})
        normalized_strengths = [str(item) for item in (strengths or []) if str(item)]
        normalized_risk_flags = [str(item) for item in (risk_flags or []) if str(item)]
        if normalized_strengths:
            payload["strengths"] = normalized_strengths
        if normalized_risk_flags:
            payload["risk_flags"] = normalized_risk_flags
        return json.dumps(payload, ensure_ascii=False)

    def get_decision_detail(self, db: Session, decision_record_id: int, timeline_limit: int = 10) -> dict:
        """Return one persisted decision record with project context and project-level history."""
        operator = ensure_operator_account(db)
        record = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.id == decision_record_id,
                BidDecisionRecord.operator_id == operator.id,
            )
            .first()
        )
        if record is None:
            raise ValueError("Bid decision record not found")
        if record.project is None:
            raise ValueError("Project not found for bid decision record")

        timeline_limit = max(1, int(timeline_limit))
        timeline_query = self._build_project_timeline_query(db, project_id=record.project_id, operator_id=operator.id)
        return {
            "record": record,
            "project": self._serialize_project_snapshot(record.project),
            "timeline_count": timeline_query.count(),
            "timeline_limit_applied": timeline_limit,
            "timeline": self._order_timeline_query(timeline_query).limit(timeline_limit).all(),
        }

    def get_project_timeline(self, db: Session, project: Project, limit: int = 20) -> dict:
        """Return recent persisted decision history for one project."""
        operator = ensure_operator_account(db)
        limit = max(1, int(limit))
        timeline_query = self._build_project_timeline_query(db, project_id=project.id, operator_id=operator.id)
        timeline = self._order_timeline_query(timeline_query).limit(limit).all()
        return {
            "operator_id": operator.id,
            "project": self._serialize_project_snapshot(project),
            "result_count": timeline_query.count(),
            "limit_applied": limit,
            "latest_decision_record_id": int(timeline[0].id) if timeline else None,
            "timeline": timeline,
        }

    def apply_telegram_action(
        self,
        db: Session,
        decision_record_id: int,
        requested_action: str,
    ) -> BidDecisionRecord:
        """Apply a Telegram inline action to an existing bid-decision record."""
        record = db.query(BidDecisionRecord).filter(BidDecisionRecord.id == decision_record_id).first()
        if record is None:
            raise ValueError("Bid decision record not found")

        if requested_action == "submit":
            record.pursue_bid = True
            record.action = "bid_now"
            record.decision_status = "submitted"
            record.priority_score = max(record.priority_score or 0.0, 0.85)
            self._append_reasoning_note(record, self.TELEGRAM_SUBMITTED_NOTE)
        elif requested_action == "review":
            record.pursue_bid = True
            record.action = "review"
            record.decision_status = "reviewing"
            self._append_reasoning_note(record, self.TELEGRAM_REVIEW_NOTE)
        elif requested_action == "skip":
            record.pursue_bid = False
            record.action = "skip"
            record.decision_status = "skipped"
            self._append_reasoning_note(record, self.TELEGRAM_SKIP_NOTE)
        else:
            raise ValueError("Unsupported Telegram action")

        db.commit()
        db.refresh(record)
        return record

    def _get_existing_active_record(self, db: Session, project_id: int, operator_id: int) -> BidDecisionRecord | None:
        """Reuse the latest active decision record to avoid duplicate planning entries."""
        return self._get_latest_record(
            db,
            project_id=project_id,
            operator_id=operator_id,
            statuses=tuple(self.ACTIVE_DECISION_STATUSES),
        )

    def sync_submitted_bid(
        self,
        db: Session,
        project_id: int,
        operator_id: int,
        bid_amount: float,
    ) -> BidDecisionRecord:
        """Promote the relevant bid decision record to `submitted` when a bid is actually filed."""
        record = self._get_existing_active_record(db, project_id, operator_id)
        if record is None:
            record = self._get_latest_record(
                db,
                project_id=project_id,
                operator_id=operator_id,
                statuses=("submitted",),
            )

        if record is None:
            record = BidDecisionRecord(
                project_id=project_id,
                operator_id=operator_id,
                pursue_bid=True,
                action="bid_now",
                decision_status="submitted",
                initial_action="bid_now",
                initial_decision_status="submitted",
                first_decided_at=utc_now(),
                recommended_amount=bid_amount,
                probability_score=0.0,
                matched_score=0.0,
                priority_score=1.0,
                urgency_score=0.0,
                competitiveness_score=0.0,
                budget_capture_score=0.0,
                expected_margin_score=0.0,
                execution_complexity_score=0.0,
                current_active_bids=0,
                max_active_bids=3,
                current_workload_score=0.0,
                workload_source="provided",
                score_breakdown="{}",
                reasoning=self.FALLBACK_SUBMITTED_REASONING,
            )
            db.add(record)
            db.flush()
            return record

        self._ensure_initial_tracking_fields(
            record,
            fallback_action=record.action or "bid_now",
            fallback_status=record.decision_status or "planned",
        )
        record.pursue_bid = True
        record.action = "bid_now"
        record.decision_status = "submitted"
        record.recommended_amount = bid_amount
        record.priority_score = max(record.priority_score or 0.0, 0.85)

        if record.reasoning:
            if self.SUBMITTED_SYNC_NOTE not in record.reasoning:
                record.reasoning = f"{record.reasoning} {self.SUBMITTED_SYNC_NOTE}".strip()
        else:
            record.reasoning = self.SUBMITTED_SYNC_NOTE

        db.flush()
        return record

    def _get_latest_record(
        self,
        db: Session,
        project_id: int,
        operator_id: int,
        statuses: tuple[str, ...] | None = None,
    ) -> BidDecisionRecord | None:
        """Fetch the newest bid decision record for a project/operator pair."""
        query = db.query(BidDecisionRecord).filter(
            BidDecisionRecord.project_id == project_id,
            BidDecisionRecord.operator_id == operator_id,
        )

        if statuses:
            query = query.filter(BidDecisionRecord.decision_status.in_(statuses))

        return query.order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc()).first()

    def _build_project_timeline_query(self, db: Session, project_id: int, operator_id: int):
        """Create a reusable query for one operator's decision history on a project."""
        return db.query(BidDecisionRecord).filter(
            BidDecisionRecord.project_id == project_id,
            BidDecisionRecord.operator_id == operator_id,
        )

    def _order_timeline_query(self, query):
        """Keep decision-history ordering consistent across timeline endpoints."""
        return query.order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())

    def _serialize_project_snapshot(self, project: Project) -> dict:
        """Build a compact project payload for persisted decision detail views."""
        return {
            "id": int(project.id),
            "title": str(project.title),
            "category": project.category,
            "status": str(project.status),
            "budget_estimate": float(project.budget_estimate or 0.0),
            "deadline": project.deadline,
            "notice_number": project.notice_number,
            "source_url": project.source_url,
            "issuing_agency": project.issuing_agency,
            "demand_agency": project.demand_agency,
        }

    def _resolve_decision_status(self, action: str, requested_status: str | None) -> str:
        """Map a decision action to a persisted workflow status."""
        if requested_status:
            return requested_status
        if action == "bid_now":
            return "planned"
        if action == "review":
            return "reviewing"
        return "skipped"

    def _ensure_initial_tracking_fields(
        self,
        record: BidDecisionRecord,
        *,
        fallback_action: str,
        fallback_status: str,
    ) -> None:
        """Preserve the first workflow state so funnel analytics can survive later status updates."""
        if not record.initial_action:
            record.initial_action = fallback_action or "skip"
        if not record.initial_decision_status:
            record.initial_decision_status = fallback_status or "planned"
        if record.first_decided_at is None:
            record.first_decided_at = record.created_at or utc_now()

    def _append_reasoning_note(self, record: BidDecisionRecord, note: str) -> None:
        """Append a note to reasoning without duplicating the same sentence."""
        appended_reasoning = append_unique_note(record.reasoning, note)
        if appended_reasoning != record.reasoning:
            record.reasoning = appended_reasoning

    def _compute_urgency_score(self, deadline_hours_remaining: int | None) -> float:
        """Convert remaining hours into an urgency score."""
        if deadline_hours_remaining is None:
            return self.URGENCY_SCORE_UNKNOWN
        return resolve_band(
            deadline_hours_remaining,
            self.URGENCY_SCORE_BANDS,
            compare=operator.le,
        )

    def _compute_load_penalty(
        self,
        current_active_bids: int,
        max_active_bids: int,
        workload_score: float,
    ) -> float:
        """Estimate how much current workload should reduce pursuit priority."""
        load_ratio = self._compute_active_load_ratio(
            current_active_bids=current_active_bids,
            max_active_bids=max_active_bids,
        )
        normalized_workload = max(0.0, min(1.0, workload_score))
        return round(load_ratio * 0.18 + normalized_workload * 0.12, 4)

    def _compute_active_load_ratio(self, current_active_bids: int, max_active_bids: int) -> float:
        """Normalize current active bid count into a 0-1 load ratio."""
        safe_max = max(1, max_active_bids)
        return min(1.0, current_active_bids / safe_max)

    def _compute_budget_capture_score(self, recommended_amount: float, budget_estimate: float | None) -> float:
        """Estimate how much of the published budget the current recommendation preserves."""
        if budget_estimate is None or float(budget_estimate) <= 0:
            return 0.5
        normalized_capture = float(recommended_amount or 0.0) / float(budget_estimate)
        return self._normalize_unit_score(normalized_capture, default=0.5)

    def _compute_complexity_penalty(self, execution_complexity_score: float) -> float:
        """Apply a mild penalty only when the underlying execution complexity is high."""
        if execution_complexity_score <= 0.55:
            return 0.0
        return round(min(0.12, (execution_complexity_score - 0.55) * 0.18), 4)

    def _normalize_unit_score(self, value: float | None, *, default: float) -> float:
        """Clamp score-like inputs into a stable 0-1 range."""
        if value is None:
            return default
        return max(0.0, min(1.0, float(value)))

    def _resolve_thresholds(self, db: Session | None) -> tuple[float, float]:
        """Resolve bid-now/review thresholds from persisted operator strategy when available."""
        bid_now_threshold, review_threshold, _ = self._resolve_strategy_settings(db)
        return bid_now_threshold, review_threshold

    def _resolve_strategy_settings(
        self,
        db: Session | None,
        *,
        operator: User | None = None,
    ) -> tuple[float, float, float]:
        """Resolve persisted operator decision settings used by scoring."""
        if db is None:
            return (
                self.BID_NOW_THRESHOLD,
                self.REVIEW_THRESHOLD,
                DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER,
            )

        strategy = ensure_operator_strategy_for(db, operator) if operator is not None else ensure_operator_strategy(db)
        bid_now_threshold = self._normalize_unit_score(
            getattr(strategy, "bid_now_threshold", None),
            default=self.BID_NOW_THRESHOLD,
        )
        review_threshold = self._normalize_unit_score(
            getattr(strategy, "review_threshold", None),
            default=self.REVIEW_THRESHOLD,
        )
        if review_threshold > bid_now_threshold:
            review_threshold = bid_now_threshold
        auto_workload_penalty_multiplier = get_strategy_auto_workload_penalty_multiplier(strategy)
        return bid_now_threshold, review_threshold, auto_workload_penalty_multiplier


# Backward-compatible alias while the route name migrates from allocation to bid decision.
AllocationService = BidDecisionService
