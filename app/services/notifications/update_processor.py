"""Telegram update processing helpers."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord, Project
from app.services.allocation import BidDecisionService
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import BidDecisionCallbackRoute, TelegramNotificationService
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

    def __init__(
        self,
        telegram_service: TelegramNotificationService | None = None,
        *,
        notification_service: OperatorNotificationService | None = None,
        decision_service: BidDecisionService | None = None,
    ) -> None:
        self.telegram = telegram_service or TelegramNotificationService()
        self.strategy_processor = TelegramStrategyCommandProcessor()
        # Injectable collaborators. Kept as the injected value (or ``None``) and
        # resolved per use site so an un-injected processor creates a fresh
        # instance exactly where — and as often as — it did before.
        self._notification_service = notification_service
        self._decision_service = decision_service

    def _resolve_notification_service(self) -> OperatorNotificationService:
        """Return the injected notification service, or a fresh default instance."""
        return self._notification_service or OperatorNotificationService()

    def _resolve_decision_service(self) -> BidDecisionService:
        """Return the injected decision service, or a fresh default instance."""
        return self._decision_service or BidDecisionService()

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

    def _is_authorized_chat(self, chat_id: int | None) -> bool:
        """Return whether an inbound chat id may drive decision/strategy actions.

        Only the configured ``TELEGRAM_CHAT_ID`` (when it is a real value) is
        authorized. A missing/placeholder configuration authorizes no chat so
        decision and strategy commands are ignored by default. ``/start`` and
        ``/help`` are handled before this gate so the operator can still
        discover their chat id.
        """
        configured_chat_id = self.telegram.get_authorized_chat_id()
        if configured_chat_id is None or chat_id is None:
            return False
        return str(chat_id) == str(configured_chat_id)

    def _process_callback_query(self, db: Session, update: dict) -> dict[str, object]:
        callback_query = update.get("callback_query") or {}
        callback_query_id = str(callback_query.get("id") or "")
        callback_data = str(callback_query.get("data") or "")
        chat_id = self._extract_chat_id(update)

        if not self._is_authorized_chat(chat_id):
            logger.info(
                "Ignoring Telegram callback from unauthorized chat: chat_id=%s",
                self.telegram.mask_chat_id(chat_id),
            )
            return {
                "status": "ignored",
                "detail": "unauthorized chat",
                "chat_id": chat_id,
            }

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

        decision_route = self.telegram.parse_bid_decision_callback_route(callback_data)
        if decision_route is None:
            return {
                "status": "ignored",
                "detail": "Unsupported Telegram callback payload.",
                "chat_id": chat_id,
            }

        try:
            expected_operator_id = self._validate_bid_decision_callback_route(db, decision_route)
            return self._apply_bid_decision_action(
                db,
                decision_record_id=decision_route.decision_record_id,
                requested_action=decision_route.action,
                expected_operator_id=expected_operator_id,
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                send_chat_ack=True,
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

    def _validate_bid_decision_callback_route(
        self,
        db: Session,
        route: BidDecisionCallbackRoute,
    ) -> int:
        """Return the verified decision owner for a Telegram callback route."""
        record = (
            db.query(BidDecisionRecord)
            .filter(BidDecisionRecord.id == route.decision_record_id)
            .first()
        )
        if record is None:
            raise ValueError("Bid decision record not found")

        record_operator_id = int(record.operator_id)
        notification_service = self._resolve_notification_service()
        if route.operator_id is not None:
            if int(route.operator_id) != record_operator_id:
                raise ValueError("Bid decision record not found")
            if not notification_service.has_active_telegram_callback_route(
                db, operator_id=record_operator_id
            ):
                raise ValueError("Bid decision record not found")
            return record_operator_id

        if not notification_service.has_active_telegram_callback_route(
            db, operator_id=record_operator_id
        ):
            raise ValueError("Bid decision record not found")
        return record_operator_id

    def _apply_bid_decision_action(
        self,
        db: Session,
        *,
        decision_record_id: int,
        requested_action: str,
        expected_operator_id: int | None = None,
        callback_query_id: str | None = None,
        chat_id: int | None = None,
        send_chat_ack: bool = False,
    ) -> dict[str, object]:
        decision_service = self._resolve_decision_service()
        record = decision_service.apply_telegram_action(db, decision_record_id, requested_action)
        if expected_operator_id is not None and int(record.operator_id) != int(expected_operator_id):
            raise ValueError("Bid decision record not found")

        project = db.query(Project).filter(Project.id == record.project_id).first()
        if project is None:
            raise ValueError("Project not found")

        self._resolve_notification_service().create_bid_decision_notification(
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

        # Domain note: bid-vector records the operator's decision but never
        # submits a bid form to 나라장터(KONEPS). Make the chat confirmation
        # explicit so "투찰 처리 완료" is not mistaken for "투찰서가 제출됨".
        decision_guidance = {
            "bid_now": (
                "투찰 결정이 기록되었습니다. "
                "실제 나라장터 투찰서 제출은 직접 진행하세요."
            ),
            "review": (
                "검토 상태로 기록되었습니다. "
                "투찰 여부를 확정한 뒤 나라장터에서 직접 진행하세요."
            ),
            "skip": (
                "보류(스킵)로 기록되었습니다. "
                "나라장터에 별도 조치는 필요하지 않습니다."
            ),
        }[record.action]

        if callback_query_id:
            self._answer_callback_query(callback_query_id, acknowledgement_text)

        if send_chat_ack and chat_id:
            self._send_chat_ack_message(
                chat_id=chat_id,
                project=project,
                record=record,
                acknowledgement_text=acknowledgement_text,
                decision_guidance=decision_guidance,
            )

        return {
            "status": "processed",
            "detail": acknowledgement_text,
            "chat_id": chat_id,
            "decision_record_id": record.id,
            "operator_id": int(record.operator_id),
            "action": record.action,
            "decision_status": record.decision_status,
        }

    def _send_chat_ack_message(
        self,
        *,
        chat_id: int,
        project: Project,
        record: BidDecisionRecord,
        acknowledgement_text: str,
        decision_guidance: str,
    ) -> None:
        """Send the persistent chat confirmation for an applied decision.

        Best-effort: a delivery failure must not break callback/text handling,
        which has already mutated and committed the decision record.
        """
        try:
            self.telegram.send_message(
                self.telegram.build_message(
                    "입찰 판단 처리",
                    (
                        f"공고: {project.title}\n"
                        f"프로젝트 ID: {project.id}\n"
                        f"상태: {record.decision_status}\n"
                        f"결과: {acknowledgement_text}\n"
                        f"{decision_guidance}"
                    ),
                ),
                chat_id=str(chat_id),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort confirmation
            logger.warning(
                "Telegram decision confirmation send failed: decision_id=%s error=%s",
                record.id,
                exc,
            )

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

        if not self._is_authorized_chat(chat_id):
            logger.info(
                "Ignoring Telegram message from unauthorized chat: chat_id=%s",
                self.telegram.mask_chat_id(chat_id),
            )
            return {
                "status": "ignored",
                "detail": "unauthorized chat",
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
            try:
                self.telegram.get_updates(
                    offset=last_update_id + 1, limit=1, timeout_seconds=0
                )
            except Exception as exc:
                # The Telegram server already advances the offset when it
                # serves the next getUpdates call, so a failed acknowledgement
                # here must not drop the work we just processed.
                logger.warning(
                    "Telegram offset acknowledgement failed: offset=%s error=%s",
                    last_update_id + 1,
                    exc,
                )

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
