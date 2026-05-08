"""Telegram update processing helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import Project
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import TelegramNotificationService


class TelegramUpdateProcessor:
    """Process inbound Telegram updates from webhook or polling."""

    START_COMMANDS = {"/start", "/help"}

    def __init__(self, telegram_service: TelegramNotificationService | None = None) -> None:
        self.telegram = telegram_service or TelegramNotificationService()

    def process_update(self, db: Session, update: dict) -> dict[str, object]:
        """Process one Telegram update payload."""
        if "callback_query" in update:
            return self._process_callback_query(db, update)
        if "message" in update:
            return self._process_message(update)
        return {
            "status": "ignored",
            "detail": "Unsupported Telegram update type.",
            "chat_id": None,
        }

    def _process_callback_query(self, db: Session, update: dict) -> dict[str, object]:
        callback_query = update.get("callback_query") or {}
        callback_query_id = str(callback_query.get("id") or "")
        callback_data = str(callback_query.get("data") or "")
        parsed_callback = self.telegram.parse_bid_decision_callback_data(callback_data)
        if parsed_callback is None:
            return {
                "status": "ignored",
                "detail": "Unsupported Telegram callback payload.",
                "chat_id": self._extract_chat_id(update),
            }

        decision_record_id, requested_action = parsed_callback
        decision_service = BidDecisionService()
        record = decision_service.apply_telegram_action(db, decision_record_id, requested_action)

        project = db.query(Project).filter(Project.id == record.project_id).first()
        if project is None:
            raise ValueError("Project not found")

        OperatorNotificationService().create_bid_decision_notification(
            db,
            operator_id=record.operator_id,
            project=project,
            decision_record=record,
        )

        acknowledgement_text = {
            "bid_now": "투찰 처리 완료",
            "review": "검토 처리 완료",
            "skip": "보류 처리 완료",
        }[record.action]

        if callback_query_id:
            self.telegram.answer_callback_query(callback_query_id, acknowledgement_text)

        return {
            "status": "processed",
            "detail": acknowledgement_text,
            "chat_id": self._extract_chat_id(update),
            "decision_record_id": record.id,
            "action": record.action,
            "decision_status": record.decision_status,
        }

    def _process_message(self, update: dict) -> dict[str, object]:
        message = update.get("message") or {}
        chat_id = self._extract_chat_id(update)
        text = str(message.get("text") or "").strip()

        if not chat_id:
            return {
                "status": "ignored",
                "detail": "Telegram message has no chat id.",
                "chat_id": None,
            }

        if text in self.START_COMMANDS:
            configured_chat_id = self.telegram.get_configured_chat_id() or "(not configured)"
            response_text = self.telegram.build_message(
                "bid-vector 연결 준비",
                (
                    f"봇 연결을 확인했습니다.\n"
                    f"- 감지된 chat id: {chat_id}\n"
                    f"- 현재 설정된 chat id: {configured_chat_id}\n"
                    f"- 이 chat id가 맞다면 `.env`의 TELEGRAM_CHAT_ID에 같은 값을 사용하세요.\n"
                    f"- 고우선순위 입찰 판단은 이 대화로 바로 전달됩니다."
                ),
            )
            self.telegram.send_message(response_text, chat_id=str(chat_id))
            return {
                "status": "processed",
                "detail": "Telegram start/help message handled.",
                "chat_id": chat_id,
            }

        return {
            "status": "ignored",
            "detail": "Telegram message type does not require processing.",
            "chat_id": chat_id,
        }

    def _extract_chat_id(self, update: dict) -> int | None:
        message = update.get("message") or {}
        callback_message = (update.get("callback_query") or {}).get("message") or {}
        chat = message.get("chat") or callback_message.get("chat") or {}
        try:
            return int(chat.get("id"))
        except (TypeError, ValueError):
            return None


class TelegramSyncService:
    """Synchronize Telegram updates using the Bot API polling interface."""

    def __init__(self, telegram_service: TelegramNotificationService | None = None) -> None:
        self.telegram = telegram_service or TelegramNotificationService()
        self.processor = TelegramUpdateProcessor(self.telegram)

    def sync_updates(self, db: Session, limit: int | None = None, timeout_seconds: int | None = None) -> dict[str, object]:
        """Fetch pending Telegram updates, process them, and acknowledge the processed offset."""
        updates = self.telegram.get_updates(limit=limit, timeout_seconds=timeout_seconds)
        if not updates:
            return {
                "status": "idle",
                "detail": "No Telegram updates were available.",
                "processed_count": 0,
                "processed_update_ids": [],
                "known_chat_ids": [],
            }

        processed_update_ids: list[int] = []
        known_chat_ids = self.telegram.extract_chat_ids(updates)
        last_update_id: int | None = None

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                processed_update_ids.append(update_id)
                last_update_id = update_id
            self.processor.process_update(db, update)

        if last_update_id is not None:
            self.telegram.get_updates(offset=last_update_id + 1, limit=1, timeout_seconds=0)

        return {
            "status": "processed",
            "detail": f"Processed {len(processed_update_ids)} Telegram updates.",
            "processed_count": len(processed_update_ids),
            "processed_update_ids": processed_update_ids,
            "known_chat_ids": known_chat_ids,
        }