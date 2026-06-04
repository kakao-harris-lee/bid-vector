"""Telegram update processing helpers."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord, Project
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor

logger = logging.getLogger(__name__)


class TelegramUpdateProcessor:
    """Process inbound Telegram updates from webhook or polling."""

    START_COMMANDS = {"/start", "/help"}
    TEXT_DECISION_ACTIONS = {
        "submit": "submit",
        "bid": "submit",
        "투찰": "submit",
        "제출": "submit",
        "✅ 투찰": "submit",
        "✅투찰": "submit",
        "review": "review",
        "검토": "review",
        "🕒 검토": "review",
        "🕒검토": "review",
        "skip": "skip",
        "보류": "skip",
        "스킵": "skip",
        "⛔ 보류": "skip",
        "⛔보류": "skip",
    }

    def __init__(self, telegram_service: TelegramNotificationService | None = None) -> None:
        self.telegram = telegram_service or TelegramNotificationService()
        self.strategy_processor = TelegramStrategyCommandProcessor()

    def process_update(self, db: Session, update: dict) -> dict[str, object]:
        """Process one Telegram update payload."""
        if "callback_query" in update:
            return self._process_callback_query(db, update)
        if "message" in update:
            return self._process_message(db, update)
        return {
            "status": "ignored",
            "detail": "Unsupported Telegram update type.",
            "chat_id": None,
        }

    def _process_callback_query(self, db: Session, update: dict) -> dict[str, object]:
        callback_query = update.get("callback_query") or {}
        callback_query_id = str(callback_query.get("id") or "")
        callback_data = str(callback_query.get("data") or "")
        chat_id = self._extract_chat_id(update)

        strategy_reply = self.strategy_processor.process_callback(db, callback_data, chat_id=chat_id)
        if strategy_reply is not None:
            if callback_query_id:
                self._answer_callback_query(callback_query_id, "전략 수정 처리 완료")
            if chat_id:
                self.telegram.send_message(
                    strategy_reply.message,
                    reply_markup=strategy_reply.reply_markup,
                    chat_id=str(chat_id),
                )
            return {
                "status": "processed",
                "detail": "Telegram strategy callback handled.",
                "chat_id": chat_id,
            }

        parsed_callback = self.telegram.parse_bid_decision_callback_data(callback_data)
        if parsed_callback is None:
            return {
                "status": "ignored",
                "detail": "Unsupported Telegram callback payload.",
                "chat_id": chat_id,
            }

        decision_record_id, requested_action = parsed_callback
        try:
            return self._apply_bid_decision_action(
                db,
                decision_record_id=decision_record_id,
                requested_action=requested_action,
                callback_query_id=callback_query_id,
                chat_id=chat_id,
            )
        except ValueError as exc:
            detail = str(exc)
            if callback_query_id:
                self._answer_callback_query(
                    callback_query_id,
                    "처리할 입찰 판단을 찾지 못했습니다",
                )
            logger.info("Ignoring Telegram decision callback: %s", detail)
            return {
                "status": "ignored",
                "detail": detail,
                "chat_id": chat_id,
            }

    def _apply_bid_decision_action(
        self,
        db: Session,
        *,
        decision_record_id: int,
        requested_action: str,
        callback_query_id: str | None = None,
        chat_id: int | None = None,
        send_chat_ack: bool = False,
    ) -> dict[str, object]:
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
            self._answer_callback_query(callback_query_id, acknowledgement_text)

        if send_chat_ack and chat_id:
            self.telegram.send_message(
                self.telegram.build_message(
                    "입찰 판단 처리",
                    (
                        f"공고: {project.title}\n"
                        f"프로젝트 ID: {project.id}\n"
                        f"상태: {record.decision_status}\n"
                        f"결과: {acknowledgement_text}"
                    ),
                ),
                chat_id=str(chat_id),
            )

        return {
            "status": "processed",
            "detail": acknowledgement_text,
            "chat_id": chat_id,
            "decision_record_id": record.id,
            "action": record.action,
            "decision_status": record.decision_status,
        }

    def _answer_callback_query(self, callback_query_id: str, text: str) -> None:
        try:
            self.telegram.answer_callback_query(callback_query_id, text)
        except RuntimeError as exc:
            logger.warning("Telegram callback acknowledgement failed: %s", exc)

    def _process_message(self, db: Session, update: dict) -> dict[str, object]:
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

        strategy_response = self.strategy_processor.process_text(db, text, chat_id=chat_id)
        if strategy_response is not None:
            self.telegram.send_message(
                strategy_response.message,
                reply_markup=strategy_response.reply_markup,
                chat_id=str(chat_id),
            )
            return {
                "status": "processed",
                "detail": "Telegram strategy command handled.",
                "chat_id": chat_id,
            }

        requested_action = self._parse_decision_text_action(text)
        if requested_action is not None:
            return self._process_text_decision_action(
                db,
                requested_action=requested_action,
                chat_id=chat_id,
            )

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

    def _parse_decision_text_action(self, text: str) -> str | None:
        normalized = " ".join(str(text or "").strip().lower().split())
        if not normalized:
            return None
        return self.TEXT_DECISION_ACTIONS.get(normalized)

    def _process_text_decision_action(
        self,
        db: Session,
        *,
        requested_action: str,
        chat_id: int,
    ) -> dict[str, object]:
        operator = ensure_operator_account(db)
        active_statuses = tuple(BidDecisionService.ACTIVE_DECISION_STATUSES)
        record = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.decision_status.in_(active_statuses),
            )
            .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
            .first()
        )
        if record is None:
            detail = "처리할 활성 입찰 판단이 없습니다."
            self.telegram.send_message(
                self.telegram.build_message("입찰 판단 처리", detail),
                chat_id=str(chat_id),
            )
            return {
                "status": "ignored",
                "detail": detail,
                "chat_id": chat_id,
            }

        return self._apply_bid_decision_action(
            db,
            decision_record_id=int(record.id),
            requested_action=requested_action,
            chat_id=chat_id,
            send_chat_ack=True,
        )


class TelegramSyncService:
    """Synchronize Telegram updates using the Bot API polling interface."""

    def __init__(self, telegram_service: TelegramNotificationService | None = None) -> None:
        self.telegram = telegram_service or TelegramNotificationService()
        self.processor = TelegramUpdateProcessor(self.telegram)

    def sync_updates(
        self,
        db: Session,
        limit: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
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
        failed_update_ids: list[int] = []
        known_chat_ids = self.telegram.extract_chat_ids(updates)
        last_update_id: int | None = None

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                last_update_id = update_id
            try:
                self.processor.process_update(db, update)
            except Exception as exc:
                logger.warning(
                    "Telegram update processing failed: update_id=%s error=%s",
                    update_id,
                    exc,
                )
                if isinstance(update_id, int):
                    failed_update_ids.append(update_id)
                continue
            if isinstance(update_id, int):
                processed_update_ids.append(update_id)

        if last_update_id is not None:
            self.telegram.get_updates(offset=last_update_id + 1, limit=1, timeout_seconds=0)

        detail = f"Processed {len(processed_update_ids)} Telegram updates."
        if failed_update_ids:
            detail = f"{detail} Failed updates were skipped: {failed_update_ids}."

        return {
            "status": "processed" if processed_update_ids else "error",
            "detail": detail,
            "processed_count": len(processed_update_ids),
            "processed_update_ids": processed_update_ids,
            "known_chat_ids": known_chat_ids,
        }
