"""낙찰결과 자동 텔레그램 알림 오케스트레이션 (beat 태스크 백엔드).

추적 중인 실투찰(``BidDecisionRecord.submitted_bid_amount`` 존재)이면서 아직
알림을 보내지 않은(``award_notified_at IS NULL``) 공고를 대상으로, 해당 공고의
``TenderResult`` 에 낙찰자가 채워진 것만 골라 개찰 검증(``verify_one``)을 돌리고
운영자 텔레그램으로 요약 1건을 발송한 뒤 ``award_notified_at`` 을 기록한다(멱등).

원칙:

- 검증/메시지 코어는 ``app.services.award_verification`` 를 재사용한다(스크립트와
  동일 로직 — 중복 금지).
- canonical operator 채널만 사용한다(synthetic 제외).
- 발송이 실제로 시도된 경우에만(``status='sent'``) ``award_notified_at`` 을
  기록한다. 발송 예외(``status='error'``)면 기록하지 않아 다음 사이클에 재시도된다.
- ``ENVIRONMENT=test`` 텔레그램 스킵은 ``TelegramNotificationService`` 가 담당한다.
- 시크릿/개인정보는 로그/메시지에 남기지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, Project, User
from app.services.award_verification import (
    VERDICT_NOT_SETTLED,
    FetchDetail,
    TelegramSender,
    _coerce_float,
    _default_fetch_detail,
    _tender_result,
    build_telegram_message,
    verify_one,
)
from app.services.real_bid_track import RealBidTrackService


class AwardResultNotificationService:
    """Notify the operator once per real bid whose award result is now public."""

    def collect_and_notify(
        self,
        db: Session,
        *,
        operator: User | None = None,
        limit: int = 50,
        sender: TelegramSender | None = None,
        fetch_detail: FetchDetail | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify newly-awarded tracked bids and send one idempotent Telegram alarm."""
        operator = operator or ensure_operator_account(db)
        fetch_detail = fetch_detail or _default_fetch_detail

        pending = RealBidTrackService().tracked_pending_award(
            db, operator=operator, limit=limit
        )
        gated = self._verify_public_awards(db, pending, fetch_detail=fetch_detail)

        if not gated:
            return {
                "status": "skipped",
                "sent": False,
                "notified_count": 0,
                "pending_count": len(pending),
            }

        message = build_telegram_message([result for _, result in gated])
        sender = sender or self._default_sender()
        try:
            send_result = sender.send_message(message)
        except Exception as exc:  # noqa: BLE001 - alarm failure must not lose tracking
            # Do NOT mark award_notified_at so the next cycle retries.
            return {
                "status": "error",
                "sent": False,
                "notified_count": 0,
                "pending_count": len(pending),
                "error": f"{type(exc).__name__}: {exc}",
            }

        marked = self._mark_notified(db, [record for record, _ in gated], now=now)
        return {
            "status": "sent",
            "sent": bool((send_result or {}).get("sent")),
            "notified_count": marked,
            "pending_count": len(pending),
            "result": send_result or {},
        }

    def _verify_public_awards(
        self,
        db: Session,
        records: list[BidDecisionRecord],
        *,
        fetch_detail: FetchDetail,
    ) -> list[tuple[BidDecisionRecord, dict[str, Any]]]:
        """Keep records whose award is public (winner in TenderResult) and settled."""
        gated: list[tuple[BidDecisionRecord, dict[str, Any]]] = []
        for record in records:
            project = record.project
            if project is None or not self._award_public(db, project):
                continue
            result = verify_one(
                db,
                str(project.notice_number or ""),
                _coerce_float(record.submitted_bid_amount) or 0.0,
                _coerce_float(record.submitted_floor_rate),
                fetch_detail=fetch_detail,
            )
            if result.get("verdict") == VERDICT_NOT_SETTLED:
                continue
            gated.append((record, result))
        return gated

    def _award_public(self, db: Session, project: Project) -> bool:
        """True once the notice's TenderResult carries a winner (company or amount)."""
        tender = _tender_result(db, project)
        if tender is None:
            return False
        company = str(tender.winning_company or "").strip()
        amount = _coerce_float(tender.winning_amount) or 0.0
        return bool(company) or amount > 0

    def _mark_notified(
        self,
        db: Session,
        records: list[BidDecisionRecord],
        *,
        now: datetime | None,
    ) -> int:
        """Stamp award_notified_at on the notified records (idempotency marker)."""
        stamp = now or utc_now()
        for record in records:
            record.award_notified_at = stamp
        db.commit()
        return len(records)

    def _default_sender(self) -> TelegramSender:
        """Instantiate the real Telegram service (no-ops in test/unconfigured)."""
        from app.services.notifications.telegram import TelegramNotificationService

        return TelegramNotificationService()
