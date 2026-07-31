"""Telegram notification service."""

from __future__ import annotations

from dataclasses import dataclass
import json

from app.core.config import settings
from app.services.notifications.telegram_transport import (
    TelegramApiRequest,
    TelegramConfig,
    TelegramTransport,
    resolve_telegram_transport,
)

# 송신하지 않는 경로가 돌려주는 응답. 문구·status 는 운영/텔레메트리 계약이므로
# 메서드 본문이 아니라 선언적 데이터로 둔다(§4.5-1/3). dict 상수는 반환 시 사본을
# 준다 — 호출부가 응답에 필드를 덧붙여도 이 상수가 오염되지 않아야 한다.
PENDING_CONFIGURATION_STATUS = "pending_configuration"
# status 리터럴은 프론트·텔레메트리·기존 증적과의 호환을 위해 유지한다. 실제 사유는
# "test 환경"보다 넓은 "미배달 transport" 이므로 상수명이 그것을 드러낸다.
SKIPPED_NON_DELIVERING_STATUS = "skipped_test_environment"

_PENDING_CONFIGURATION_RESULT: dict[str, object] = {
    "sent": False,
    "status": PENDING_CONFIGURATION_STATUS,
    "detail": "Telegram is not configured yet.",
}
# 스킵 문구는 해석된 transport 의 환경에서 파생한다 — 프로세스 floor 로 막힌 경우
# config 가 아니라 실제로 막은 환경 이름이 남는다.
_SKIPPED_SEND_DETAIL = "Telegram delivery skipped in {environment} environment."
_SKIPPED_CALLBACK_DETAIL = (
    "Telegram callback acknowledgement skipped in {environment} environment."
)
_UNAVAILABLE_WEBHOOK_RESULT: dict[str, object] = {
    "url": "",
    "pending_update_count": 0,
    "has_custom_certificate": False,
}


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
    LEGACY_CONFIGURED_CHAT_ROUTE_KEY = "telegram:legacy-configured-chat"

    def __init__(
        self,
        *,
        config: TelegramConfig | None = None,
        transport: TelegramTransport | None = None,
    ) -> None:
        """협력자 주입 seam(§4.7-3). 미주입 시 기존 동작과 동일하다.

        기본값을 생성 시점에 굳히지 않고 호출마다 해석하는 이유: 기존 동작이
        "호출 시점의 ``settings``" 였고, 이 서비스는 요청·태스크마다 새로 만들어
        쓰이기도 하지만 폴링 워커처럼 오래 사는 인스턴스도 있다. 스냅샷을 생성
        시점으로 옮기면 그 의미가 조용히 바뀐다.

        ``config`` 만 주입하면 조합 지점이 프로세스 환경 floor 를 함께 적용한다
        (자격증명만 주입해도 ``ENVIRONMENT=test`` 에서 송신이 새지 않는다).
        ``transport`` 를 명시적으로 주입하면 그 floor 를 **우회**하며, 배달 정책
        책임은 주입자에게 있다(문서화된 escape hatch — 테스트용 fake 경로).
        """
        self._injected_config = config
        self._injected_transport = transport

    def _resolve_config(self) -> TelegramConfig:
        """주입된 설정 스냅샷, 없으면 현재 ``settings`` 스냅샷."""
        if self._injected_config is not None:
            return self._injected_config
        return TelegramConfig.from_settings(settings)

    def _resolve_transport(self, config: TelegramConfig) -> TelegramTransport:
        """주입된 transport, 없으면 조합 지점이 고른 기본 transport."""
        if self._injected_transport is not None:
            return self._injected_transport
        return resolve_telegram_transport(config)

    def _is_configured(self, config: TelegramConfig) -> bool:
        """이미 해석된 스냅샷으로 판정한다(공개 메서드가 재해석하지 않도록)."""
        return bool(
            self._has_real_setting(config.bot_token)
            and self._has_real_setting(config.chat_id)
        )

    def is_configured(self) -> bool:
        """Return whether Telegram settings are available."""
        return self._is_configured(self._resolve_config())

    def send_message(
        self,
        message: str,
        reply_markup: dict[str, object] | None = None,
        chat_id: str | None = None,
    ) -> dict[str, object]:
        """Send a message to Telegram when configuration is available."""
        config = self._resolve_config()
        if not self._is_configured(config):
            return dict(_PENDING_CONFIGURATION_RESULT)

        transport = self._resolve_transport(config)
        if not transport.delivers:
            return {
                "sent": False,
                "status": SKIPPED_NON_DELIVERING_STATUS,
                "detail": _SKIPPED_SEND_DETAIL.format(
                    environment=transport.environment
                ),
            }

        payload = {
            "chat_id": chat_id or config.chat_id,
            "text": message,
            "disable_web_page_preview": False,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        payload = self._post_json(
            "sendMessage", payload, config=config, transport=transport
        )

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
        config = self._resolve_config()
        if not self._is_configured(config):
            return dict(_PENDING_CONFIGURATION_RESULT)

        transport = self._resolve_transport(config)
        if not transport.delivers:
            return {
                "sent": False,
                "status": SKIPPED_NON_DELIVERING_STATUS,
                "detail": _SKIPPED_CALLBACK_DETAIL.format(
                    environment=transport.environment
                ),
            }

        payload = self._post_json(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": False,
            },
            config=config,
            transport=transport,
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
        config = self._resolve_config()
        transport = self._resolve_transport(config)
        if not self._is_configured(config) or not transport.delivers:
            return []

        payload: dict[str, object] = {
            "timeout": (
                timeout_seconds
                if timeout_seconds is not None
                else config.polling_timeout_seconds
            ),
            "limit": limit if limit is not None else config.polling_limit,
        }
        if offset is not None:
            payload["offset"] = offset

        response = self._post_json(
            "getUpdates", payload, config=config, transport=transport
        )
        if not response.get("ok"):
            description = response.get("description") or response
            raise RuntimeError(f"Telegram getUpdates failed: {description}")
        return response.get("result", [])

    def get_webhook_info(self) -> dict[str, object]:
        """Fetch Telegram webhook status for diagnostics."""
        config = self._resolve_config()
        transport = self._resolve_transport(config)
        if not self._is_configured(config) or not transport.delivers:
            return {"ok": False, "result": dict(_UNAVAILABLE_WEBHOOK_RESULT)}

        return self._post_json(
            "getWebhookInfo", {}, config=config, transport=transport
        )

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
        return self._resolve_config().chat_id

    def get_configured_target_label(self) -> str:
        """Return a masked label for the configured delivery chat id."""
        return self.mask_chat_id(self._resolve_config().chat_id)

    def get_authorized_chat_id(self) -> str | None:
        """Return the configured delivery chat id only when it is a real value.

        Placeholder scaffold values (``replace-with-...`` etc.) and empty
        strings are treated as "not configured" so inbound decision/strategy
        actions cannot be authorized against a non-real chat id.
        """
        value = self._resolve_config().chat_id
        if not self._has_real_setting(value):
            return None
        return str(value).strip()

    def mask_chat_id(self, chat_id: str | int | None) -> str:
        """Mask a Telegram chat id for logs, API responses, and telemetry."""
        normalized = str(chat_id or "").strip()
        if not self._has_real_setting(normalized):
            return "(not configured)"
        if len(normalized) <= 4:
            return "*" * len(normalized)
        return f"{'*' * max(len(normalized) - 4, 0)}{normalized[-4:]}"

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

    def _post_json(
        self,
        method_name: str,
        payload: dict[str, object],
        *,
        config: TelegramConfig | None = None,
        transport: TelegramTransport | None = None,
    ) -> dict[str, object]:
        """POST JSON to the Telegram Bot API and parse the JSON response.

        HTTP/연결/URL 실패는 transport 가 ``RuntimeError`` 로 감싸 올려주고, 여기서는
        JSON 직렬화/역직렬화만 책임진다.

        ``config``/``transport`` 는 공개 메서드가 이미 해석한 협력자를 그대로 넘기는
        통로다(공개 호출당 해석 1회). 생략하면 여기서 해석한다.
        """
        resolved_config = config if config is not None else self._resolve_config()
        resolved_transport = (
            transport
            if transport is not None
            else self._resolve_transport(resolved_config)
        )
        raw_body = resolved_transport.post(
            TelegramApiRequest(
                operation=method_name,
                url=resolved_config.api_url(method_name),
                body=json.dumps(payload).encode("utf-8"),
                timeout_seconds=resolved_config.send_timeout_seconds,
            )
        )

        try:
            return json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Telegram API returned an invalid JSON response.") from exc

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
