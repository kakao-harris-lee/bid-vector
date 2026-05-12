"""Single-user bid pursuit decision service."""

import json

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ensure_operator_account,
    ensure_operator_strategy,
)
from app.models.models import BidDecisionRecord, Project
from app.schemas.schemas import BidDecisionRequest


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
    ACTIVE_DECISION_STATUSES = {"planned", "reviewing"}
    SUBMITTED_SYNC_NOTE = "실제 투찰이 등록되어 제출 상태로 동기화했습니다."
    FALLBACK_SUBMITTED_REASONING = "사전 결정 기록 없이 직접 투찰이 등록되어 제출 이력을 생성했습니다."
    TELEGRAM_SUBMITTED_NOTE = "텔레그램에서 투찰 버튼을 눌러 제출 상태로 전환했습니다."
    TELEGRAM_REVIEW_NOTE = "텔레그램에서 검토 버튼을 눌러 검토 대기 상태로 유지했습니다."
    TELEGRAM_SKIP_NOTE = "텔레그램에서 보류 버튼을 눌러 이번 공고를 보류 처리했습니다."

    def evaluate_opportunity(self, request: BidDecisionRequest, db: Session | None = None) -> dict:
        """Score a notice for a single user and decide whether to pursue it."""
        bid_now_threshold, review_threshold = self._resolve_thresholds(db)
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

        reasons = [
            f"낙찰 가능성 점수 {request.probability_score:.2f}를 반영했습니다.",
            f"공고 적합도 점수 {request.matched_score:.2f}를 반영했습니다.",
            f"마감 임박도 점수 {urgency_score:.2f}를 반영했습니다.",
            f"시장 경쟁력 점수 {competitiveness_score:.2f}를 반영했습니다.",
        ]

        if request.budget_estimate and request.budget_estimate > 0:
            reasons.append(f"예산 대비 추천가 유지율 {budget_capture_score:.2f}를 반영했습니다.")
        reasons.append(f"예상 수익성 점수 {expected_margin_score:.2f}를 반영했습니다.")

        if request.workload_source == "auto":
            reasons.append(f"현재 진행 중인 입찰 현황을 바탕으로 업무부하 점수 {request.current_workload_score:.2f}를 자동 산정했습니다.")
        elif request.current_workload_score > 0:
            reasons.append(f"입력된 업무부하 점수 {request.current_workload_score:.2f}를 반영했습니다.")

        if load_penalty > 0:
            reasons.append(f"현재 진행 중인 입찰/업무 부담으로 {load_penalty:.2f} 감점을 적용했습니다.")
        if complexity_penalty > 0:
            reasons.append(
                f"실행 복잡도 점수 {execution_complexity_score:.2f}를 반영해 {complexity_penalty:.2f} 추가 감점을 적용했습니다."
            )

        action = "skip"
        if request.current_active_bids >= request.max_active_bids and priority_score < 0.8:
            reasons.append("현재 동시 관리 중인 입찰 수가 한도에 가까워 보수적으로 보류했습니다.")
        elif priority_score >= bid_now_threshold or (
            request.probability_score >= 0.8 and request.matched_score >= 0.7
        ):
            action = "bid_now"
            reasons.append("우선순위가 높아 바로 투찰 검토 대상으로 올렸습니다.")
        elif priority_score >= review_threshold:
            action = "review"
            reasons.append("즉시 투찰까지는 아니지만 추가 검토 가치가 있어 검토 대기열에 올렸습니다.")
        else:
            reasons.append("현재 기준에서는 우선순위가 낮아 보류하는 편이 유리합니다.")

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
                "load_penalty": round(load_penalty, 2),
                "execution_complexity_penalty": round(complexity_penalty, 2),
                "total_penalty": round(total_penalty, 2),
            },
            "reasoning": " ".join(reasons),
        }

    def save_decision(self, db: Session, request: "BidDecisionSaveRequest") -> BidDecisionRecord:
        """Persist or update a single operator bid-decision record."""
        from app.schemas.schemas import BidDecisionSaveRequest

        if not isinstance(request, BidDecisionSaveRequest):
            request = BidDecisionSaveRequest(**request.model_dump())

        if request.budget_estimate is None:
            project = db.query(Project).filter(Project.id == request.project_id).first()
            if project is not None:
                request = request.model_copy(update={"budget_estimate": float(project.budget_estimate or 0.0)})

        operator = ensure_operator_account(db)
        decision = self.evaluate_opportunity(request, db=db)
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
        record.score_breakdown = json.dumps(decision.get("score_breakdown") or {}, ensure_ascii=False)
        record.reasoning = decision["reasoning"]

        db.commit()
        db.refresh(record)
        return record

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
        if not note:
            return
        if record.reasoning:
            if note not in record.reasoning:
                record.reasoning = f"{record.reasoning} {note}".strip()
            return
        record.reasoning = note

    def _compute_urgency_score(self, deadline_hours_remaining: int | None) -> float:
        """Convert remaining hours into an urgency score."""
        if deadline_hours_remaining is None:
            return 0.3
        if deadline_hours_remaining <= 6:
            return 1.0
        if deadline_hours_remaining <= 24:
            return 0.8
        if deadline_hours_remaining <= 72:
            return 0.55
        return 0.25

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
        if db is None:
            return self.BID_NOW_THRESHOLD, self.REVIEW_THRESHOLD

        strategy = ensure_operator_strategy(db)
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
        return bid_now_threshold, review_threshold


# Backward-compatible alias while the route name migrates from allocation to bid decision.
AllocationService = BidDecisionService
