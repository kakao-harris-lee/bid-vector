"""실투찰(real-bid) 트랙 도메인 서비스.

운영자가 KONEPS 에 **실제로 제출한** 투찰가를 ``BidDecisionRecord`` 에 기록해
개찰 후 낙찰결과 검증/알림 파이프라인이 그 실투찰을 추적할 수 있게 한다.

핵심 원칙:

- 실투찰가는 시스템 추천(``recommended_amount``)과 **구분**해 ``submitted_bid_amount``
  에 저장한다(추천을 덮어쓰지 않음).
- 기존 ``BidDecisionRecord`` 를 재사용한다. 사전 결정 기록이 있으면 그 위에
  제출 상태를 동기화하고, 없으면 제출 이력을 새로 만든다(§4 모델 재사용).
- ``award_notified_at`` 은 알림 파이프라인의 멱등 마커이므로 여기서 건드리지
  않는다(등록/재등록이 알림 재발송을 유발하지 않음).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.models.models import BidDecisionRecord, Project, User
from app.services.award_verification import (
    _coerce_float,
    _find_project,
    _tender_result,
    strip_notice_suffix,
)


class RealBidNotFoundError(ValueError):
    """Raised when neither project_id nor notice_number resolves to a project."""


class RealBidTrackService:
    """Register and list operator real bids on top of ``BidDecisionRecord``."""

    REGISTER_NOTE = "운영자가 실투찰을 등록했습니다."

    def resolve_project(
        self,
        db: Session,
        *,
        project_id: int | None,
        notice_number: str | None,
    ) -> Project:
        """Resolve the target project by id first, then by notice-number prefix."""
        if project_id is not None:
            project = db.query(Project).filter(Project.id == int(project_id)).first()
            if project is None:
                raise RealBidNotFoundError(f"project_id={project_id} not found")
            return project

        base_notice = strip_notice_suffix(notice_number or "")
        project = _find_project(db, base_notice)
        if project is None:
            raise RealBidNotFoundError(f"notice_number={notice_number!r} not found")
        return project

    def record_real_bid(
        self,
        db: Session,
        *,
        operator: User,
        project: Project,
        bid_amount: float,
        floor_rate: float | None = None,
        submitted_at: datetime | None = None,
        note: str | None = None,
    ) -> BidDecisionRecord:
        """Upsert the real-bid submission onto the latest decision record."""
        record = self._latest_record(db, project_id=project.id, operator_id=operator.id)
        now = utc_now()
        submitted_ts = ensure_utc(submitted_at) if submitted_at is not None else now

        if record is None:
            # No prior system recommendation existed. Intentionally leave
            # recommended_amount NULL — seeding it with the actual bid would
            # misrepresent the operator's submission as a system recommendation
            # (§2 정직 명세). The distinct submitted_bid_amount carries the actual.
            record = BidDecisionRecord(
                project_id=project.id,
                operator_id=operator.id,
                initial_action="bid_now",
                initial_decision_status="submitted",
                first_decided_at=now,
            )
            db.add(record)

        record.pursue_bid = True
        record.action = "bid_now"
        record.decision_status = "submitted"
        record.submitted_bid_amount = float(bid_amount)
        record.submitted_at = submitted_ts
        if floor_rate is not None:
            record.submitted_floor_rate = float(floor_rate)
        self._append_note(record, note or self.REGISTER_NOTE)

        db.commit()
        db.refresh(record)
        return record

    def list_real_bids(
        self,
        db: Session,
        *,
        operator: User,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent-first real-bid records with award status (audit view)."""
        limit = max(1, min(int(limit), 500))
        records = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.submitted_bid_amount.isnot(None),
            )
            .order_by(
                BidDecisionRecord.submitted_at.desc().nullslast(),
                BidDecisionRecord.id.desc(),
            )
            .limit(limit)
            .all()
        )
        return [self.serialize_record(db, record) for record in records]

    def tracked_pending_award(
        self,
        db: Session,
        *,
        operator: User,
        limit: int = 50,
    ) -> list[BidDecisionRecord]:
        """Real bids awaiting the award-result notification (not yet notified)."""
        limit = max(1, min(int(limit), 500))
        return (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.submitted_bid_amount.isnot(None),
                BidDecisionRecord.award_notified_at.is_(None),
            )
            .order_by(BidDecisionRecord.submitted_at.asc().nullsfirst(), BidDecisionRecord.id.asc())
            .limit(limit)
            .all()
        )

    def serialize_record(self, db: Session, record: BidDecisionRecord) -> dict:
        """Build an operator-facing real-bid payload with the latest award info."""
        project = record.project
        tender = _tender_result(db, project)
        winning_company = str(tender.winning_company).strip() if tender and tender.winning_company else None
        winning_amount = _coerce_float(tender.winning_amount) if tender else None
        return {
            "decision_record_id": int(record.id),
            "project_id": int(record.project_id) if record.project_id is not None else None,
            "notice_number": project.notice_number if project is not None else None,
            "project_title": project.title if project is not None else None,
            "submitted_bid_amount": _coerce_float(record.submitted_bid_amount),
            "submitted_floor_rate": _coerce_float(record.submitted_floor_rate),
            "recommended_amount": _coerce_float(record.recommended_amount),
            "submitted_at": record.submitted_at,
            "award_notified_at": record.award_notified_at,
            "decision_status": str(record.decision_status or ""),
            "winning_company": winning_company,
            "winning_amount": winning_amount,
            "award_public": bool(winning_company or (winning_amount and winning_amount > 0)),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def _latest_record(
        self,
        db: Session,
        *,
        project_id: int,
        operator_id: int,
    ) -> BidDecisionRecord | None:
        """Fetch the newest decision record for a project/operator pair."""
        return (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.project_id == project_id,
                BidDecisionRecord.operator_id == operator_id,
            )
            .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
            .first()
        )

    def _append_note(self, record: BidDecisionRecord, note: str) -> None:
        """Append a note to reasoning without duplicating the same sentence."""
        if not note:
            return
        if record.reasoning:
            if note not in record.reasoning:
                record.reasoning = f"{record.reasoning} {note}".strip()
            return
        record.reasoning = note
