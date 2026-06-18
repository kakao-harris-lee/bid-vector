"""Telegram notification service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import error as urlerror
from urllib import request as urlrequest

from app.core.config import settings


@dataclass(frozen=True)
class BidDecisionCallbackRoute:
    """Parsed bid-decision callback routing key."""

    decision_record_id: int
    action: str
    operator_id: int | None
    is_legacy: bool = False


class TelegramNotificationService:
    """Send messages to Telegram when configured."""

    CALLBACK_PREFIX = "bid-decision"

    def is_configured(self) -> bool:
        """Return whether Telegram settings are available."""
        return bool(
            self._has_real_setting(settings.TELEGRAM_BOT_TOKEN)
            and self._has_real_setting(settings.TELEGRAM_CHAT_ID)
        )

    def send_message(
        self,
        message: str,
        reply_markup: dict[str, object] | None = None,
        chat_id: str | None = None,
    ) -> dict[str, object]:
        """Send a message to Telegram when configuration is available."""
        if not self.is_configured():
            return {
                "sent": False,
                "status": "pending_configuration",
                "detail": "Telegram is not configured yet.",
            }

        if settings.ENVIRONMENT == "test":
            return {
                "sent": False,
                "status": "skipped_test_environment",
                "detail": "Telegram delivery skipped in test environment.",
            }

        payload = {
            "chat_id": chat_id or settings.TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        payload = self._post_json("sendMessage", payload)

        if not payload.get("ok"):
            description = payload.get("description") or payload
            raise RuntimeError(f"Telegram API rejected the message: {description}")

        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
            "telegram_message_id": payload.get("result", {}).get("message_id"),
        }

    def answer_callback_query(self, callback_query_id: str, text: str) -> dict[str, object]:
        """Acknowledge a Telegram callback query after processing an inline action."""
        if not self.is_configured():
            return {
                "sent": False,
                "status": "pending_configuration",
                "detail": "Telegram is not configured yet.",
            }

        if settings.ENVIRONMENT == "test":
            return {
                "sent": False,
                "status": "skipped_test_environment",
                "detail": "Telegram callback acknowledgement skipped in test environment.",
            }

        payload = self._post_json(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": False,
            },
        )
        if not payload.get("ok"):
            description = payload.get("description") or payload
            raise RuntimeError(f"Telegram callback acknowledgement failed: {description}")

        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram callback acknowledgement succeeded.",
        }

    def build_message(self, title: str, message: str, url: str | None = None) -> str:
        """Build a consistent Telegram notification message."""
        parts = [f"[ {title} ]", message]
        if url:
            parts.append(f"상세보기: {url}")
        return "\n".join(parts)

    def get_updates(
        self,
        offset: int | None = None,
        limit: int | None = None,
        timeout_seconds: int | None = None,
    ) -> list[dict]:
        """Fetch pending Telegram updates using long polling when configured."""
        if not self.is_configured() or settings.ENVIRONMENT == "test":
            return []

        payload: dict[str, object] = {
            "timeout": timeout_seconds if timeout_seconds is not None else settings.TELEGRAM_POLLING_TIMEOUT_SECONDS,
            "limit": limit if limit is not None else settings.TELEGRAM_POLLING_LIMIT,
        }
        if offset is not None:
            payload["offset"] = offset

        response = self._post_json("getUpdates", payload)
        if not response.get("ok"):
            description = response.get("description") or response
            raise RuntimeError(f"Telegram getUpdates failed: {description}")
        return response.get("result", [])

    def get_webhook_info(self) -> dict[str, object]:
        """Fetch Telegram webhook status for diagnostics."""
        if not self.is_configured() or settings.ENVIRONMENT == "test":
            return {
                "ok": False,
                "result": {
                    "url": "",
                    "pending_update_count": 0,
                    "has_custom_certificate": False,
                },
            }

        return self._post_json("getWebhookInfo", {})

    def extract_chat_ids(self, updates: list[dict]) -> list[int]:
        """Extract unique chat ids from mixed Telegram update payloads."""
        chat_ids: set[int] = set()
        for update in updates:
            message = update.get("message") or {}
            callback_message = (update.get("callback_query") or {}).get("message") or {}
            chat = message.get("chat") or callback_message.get("chat") or {}
            try:
                chat_ids.add(int(chat.get("id")))
            except (TypeError, ValueError):
                continue
        return sorted(chat_ids)

    def get_configured_chat_id(self) -> str:
        """Return the currently configured delivery chat id."""
        return settings.TELEGRAM_CHAT_ID

    def get_authorized_chat_id(self) -> str | None:
        """Return the configured delivery chat id only when it is a real value.

        Placeholder scaffold values (``replace-with-...`` etc.) and empty
        strings are treated as "not configured" so inbound decision/strategy
        actions cannot be authorized against a non-real chat id.
        """
        value = settings.TELEGRAM_CHAT_ID
        if not self._has_real_setting(value):
            return None
        return str(value).strip()

    def _has_real_setting(self, value: str | None) -> bool:
        """Treat scaffold placeholder values as missing runtime configuration."""
        normalized = str(value or "").strip()
        if not normalized:
            return False
        lowered = normalized.lower()
        return not (
            lowered.startswith("replace-with-")
            or lowered.startswith("your-")
            or lowered in {"changeme", "change-me", "change-me-now"}
        )

    def _post_json(self, method_name: str, payload: dict[str, object]) -> dict[str, object]:
        """POST JSON to the Telegram Bot API and parse the JSON response."""
        request = urlrequest.Request(
            self._build_api_url(method_name),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlrequest.urlopen(request, timeout=settings.TELEGRAM_SEND_TIMEOUT_SECONDS) as response:
                raw_body = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API responded with HTTP {exc.code}: {error_body}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"Telegram API connection failed: {exc.reason}") from exc

        try:
            return json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Telegram API returned an invalid JSON response.") from exc

    def _build_api_url(self, method_name: str) -> str:
        """Build a Telegram Bot API URL for the configured bot token."""
        base_url = settings.TELEGRAM_API_BASE_URL.rstrip("/")
        return f"{base_url}/bot{settings.TELEGRAM_BOT_TOKEN}/{method_name}"

    def build_bid_decision_message(
        self,
        project_title: str,
        project_id: int,
        action: str,
        decision_status: str,
        priority_score: float,
        recommended_amount: float,
        probability_score: float,
        reasoning: str,
        url: str | None = None,
    ) -> str:
        """Build a Telegram-ready summary for a bid decision."""
        action_label = {
            "bid_now": "즉시 투찰",
            "review": "추가 검토",
            "skip": "보류",
        }.get(action, action)
        message = (
            f"공고: {project_title}\n"
            f"프로젝트 ID: {project_id}\n"
            f"판단: {action_label} / 상태: {decision_status}\n"
            f"우선순위: {priority_score:.2f}\n"
            f"추천 금액: {recommended_amount:,.0f}\n"
            f"가격 적합도(추정): {probability_score:.2f}\n"
            f"판단 근거: {reasoning}"
        )
        return self.build_message("입찰 판단 알림", message, url)

    def build_bid_submission_message(
        self,
        project_title: str,
        project_id: int,
        bid_amount: float,
        decision_status: str,
        reasoning: str,
        url: str | None = None,
    ) -> str:
        """Build a Telegram-ready summary for a submitted bid."""
        message = (
            f"공고: {project_title}\n"
            f"프로젝트 ID: {project_id}\n"
            f"투찰 금액: {bid_amount:,.0f}\n"
            f"결정 상태: {decision_status}\n"
            f"메모: {reasoning}"
        )
        return self.build_message("투찰 완료 알림", message, url)

    def build_bid_decision_reply_markup(
        self,
        decision_record_id: int,
        operator_id: int | None = None,
    ) -> dict[str, object]:
        """Build inline action buttons for a Telegram bid-decision alert."""
        return {
            "inline_keyboard": [[
                {
                    "text": "✅ 투찰",
                    "callback_data": self.build_bid_decision_callback_data(
                        decision_record_id,
                        "submit",
                        operator_id=operator_id,
                    ),
                },
                {
                    "text": "🕒 검토",
                    "callback_data": self.build_bid_decision_callback_data(
                        decision_record_id,
                        "review",
                        operator_id=operator_id,
                    ),
                },
                {
                    "text": "⛔ 보류",
                    "callback_data": self.build_bid_decision_callback_data(
                        decision_record_id,
                        "skip",
                        operator_id=operator_id,
                    ),
                },
            ]],
        }

    def build_bid_decision_callback_data(
        self,
        decision_record_id: int,
        action: str,
        operator_id: int | None = None,
    ) -> str:
        """Build compact callback data for Telegram decision buttons."""
        if operator_id is not None:
            return f"{self.CALLBACK_PREFIX}:{int(operator_id)}:{int(decision_record_id)}:{action}"
        return f"{self.CALLBACK_PREFIX}:{decision_record_id}:{action}"

    def parse_bid_decision_callback_data(self, callback_data: str) -> tuple[int, str] | None:
        """Parse callback data from Telegram inline decision buttons.

        This legacy helper returns only the decision id and action. Use
        :meth:`parse_bid_decision_callback_route` when owner validation matters.
        """
        route = self.parse_bid_decision_callback_route(callback_data)
        if route is None:
            return None
        return route.decision_record_id, route.action

    def parse_bid_decision_callback_route(self, callback_data: str) -> BidDecisionCallbackRoute | None:
        """Parse owner-aware and canonical legacy bid-decision callbacks."""
        parts = str(callback_data or "").split(":")
        if len(parts) == 3:
            prefix, record_id, action = parts
            operator_id = None
            is_legacy = True
        elif len(parts) == 4:
            prefix, raw_operator_id, record_id, action = parts
            try:
                operator_id = int(raw_operator_id)
            except ValueError:
                return None
            is_legacy = False
        else:
            return None

        if prefix != self.CALLBACK_PREFIX:
            return None
        if action not in {"submit", "review", "skip"}:
            return None

        try:
            decision_record_id = int(record_id)
        except ValueError:
            return None
        return BidDecisionCallbackRoute(
            decision_record_id=decision_record_id,
            action=action,
            operator_id=operator_id,
            is_legacy=is_legacy,
        )
