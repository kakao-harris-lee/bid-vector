"""Single-user bid pursuit decision service."""

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord
from app.schemas.schemas import BidDecisionRequest


class BidDecisionService:
    """Decide whether a single user should pursue a bid opportunity now."""

    BID_NOW_THRESHOLD = 0.7
    REVIEW_THRESHOLD = 0.45
    ACTIVE_DECISION_STATUSES = {"planned", "reviewing"}
    SUBMITTED_SYNC_NOTE = "실제 투찰이 등록되어 제출 상태로 동기화했습니다."
    FALLBACK_SUBMITTED_REASONING = "사전 결정 기록 없이 직접 투찰이 등록되어 제출 이력을 생성했습니다."
    TELEGRAM_SUBMITTED_NOTE = "텔레그램에서 투찰 버튼을 눌러 제출 상태로 전환했습니다."
    TELEGRAM_REVIEW_NOTE = "텔레그램에서 검토 버튼을 눌러 검토 대기 상태로 유지했습니다."
    TELEGRAM_SKIP_NOTE = "텔레그램에서 보류 버튼을 눌러 이번 공고를 보류 처리했습니다."

    def evaluate_opportunity(self, request: BidDecisionRequest) -> dict:
        """Score a notice for a single user and decide whether to pursue it."""
        urgency_score = self._compute_urgency_score(request.deadline_hours_remaining)
        load_penalty = self._compute_load_penalty(
            current_active_bids=request.current_active_bids,
            max_active_bids=request.max_active_bids,
            workload_score=request.current_workload_score,
        )

        opportunity_score = (
            request.probability_score * 0.5
            + request.matched_score * 0.3
            + urgency_score * 0.2
        )
        priority_score = max(0.0, min(1.0, opportunity_score - load_penalty))

        reasons = [
            f"낙찰 가능성 점수 {request.probability_score:.2f}를 반영했습니다.",
            f"공고 적합도 점수 {request.matched_score:.2f}를 반영했습니다.",
            f"마감 임박도 점수 {urgency_score:.2f}를 반영했습니다.",
        ]

        if load_penalty > 0:
            reasons.append(f"현재 진행 중인 입찰/업무 부담으로 {load_penalty:.2f} 감점을 적용했습니다.")

        action = "skip"
        if request.current_active_bids >= request.max_active_bids and priority_score < 0.8:
            reasons.append("현재 동시 관리 중인 입찰 수가 한도에 가까워 보수적으로 보류했습니다.")
        elif priority_score >= self.BID_NOW_THRESHOLD or (
            request.probability_score >= 0.8 and request.matched_score >= 0.7
        ):
            action = "bid_now"
            reasons.append("우선순위가 높아 바로 투찰 검토 대상으로 올렸습니다.")
        elif priority_score >= self.REVIEW_THRESHOLD:
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
            "reasoning": " ".join(reasons),
        }

    def save_decision(self, db: Session, request: "BidDecisionSaveRequest") -> BidDecisionRecord:
        """Persist or update a single operator bid-decision record."""
        from app.schemas.schemas import BidDecisionSaveRequest

        if not isinstance(request, BidDecisionSaveRequest):
            request = BidDecisionSaveRequest(**request.model_dump())

        operator = ensure_operator_account(db)
        decision = self.evaluate_opportunity(request)
        decision_status = self._resolve_decision_status(decision["action"], request.decision_status)
        record = self._get_existing_active_record(db, request.project_id, operator.id)

        if record is None:
            record = BidDecisionRecord(project_id=request.project_id, operator_id=operator.id)
            db.add(record)

        record.pursue_bid = decision["pursue_bid"]
        record.action = decision["action"]
        record.decision_status = decision_status
        record.recommended_amount = request.recommended_amount
        record.probability_score = request.probability_score
        record.matched_score = request.matched_score
        record.priority_score = decision["priority_score"]
        record.deadline_hours_remaining = request.deadline_hours_remaining
        record.current_active_bids = request.current_active_bids
        record.max_active_bids = request.max_active_bids
        record.current_workload_score = request.current_workload_score
        record.reasoning = decision["reasoning"]

        db.commit()
        db.refresh(record)
        return record

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
                recommended_amount=bid_amount,
                probability_score=0.0,
                matched_score=0.0,
                priority_score=1.0,
                current_active_bids=0,
                max_active_bids=3,
                current_workload_score=0.0,
                reasoning=self.FALLBACK_SUBMITTED_REASONING,
            )
            db.add(record)
            db.flush()
            return record

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

    def _resolve_decision_status(self, action: str, requested_status: str | None) -> str:
        """Map a decision action to a persisted workflow status."""
        if requested_status:
            return requested_status
        if action == "bid_now":
            return "planned"
        if action == "review":
            return "reviewing"
        return "skipped"

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
        safe_max = max(1, max_active_bids)
        load_ratio = min(1.0, current_active_bids / safe_max)
        normalized_workload = max(0.0, min(1.0, workload_score))
        return round(load_ratio * 0.18 + normalized_workload * 0.12, 4)


# Backward-compatible alias while the route name migrates from allocation to bid decision.
AllocationService = BidDecisionService
