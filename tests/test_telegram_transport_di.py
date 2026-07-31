"""Telegram 송신 경계의 생성자 주입 + 조합 지점(floor) 계약.

``TelegramNotificationService`` 는 예전에 메서드 본문 15곳에서 전역 ``settings`` 를
읽고 네 곳에서 ``settings.ENVIRONMENT == "test"`` 를 감지해 return 했다. 그래서 테스트가
지나는 경로와 운영 경로가 갈렸고, 송신을 가로채려면 클래스 메서드 monkeypatch 밖에
없었다. 이 파일은 그 자리를 대체한 계약을 고정한다:

- 설정 값객체(:class:`TelegramConfig`) — ``from_settings`` 매핑, ``environment`` 필수,
  토큰 repr 미노출.
- 조합 지점(:func:`resolve_telegram_transport`) — config 환경 **과** 프로세스 환경을
  함께 보는 fail-closed floor. 자격증명만 담긴 config 주입으로 송신이 새지 않는다
  (CLAUDE.md §7 불변 요구를 이 한 곳에서 고정).
- escape hatch — 명시적 transport 주입만 floor 를 우회한다(정책 책임은 주입자).
- 주입 경로 — 가짜 transport 로 **운영과 같은 코드 경로**(payload·URL·타임아웃·에러
  래핑)를 검증한다. 실제 Telegram API 는 어느 테스트에서도 접촉하지 않는다.
"""

from __future__ import annotations

import json
from urllib import error as urlerror

import pytest

import app.services.notifications.telegram as telegram_mod
import app.services.notifications.telegram_transport as transport_mod
from app.core.config import settings
from app.services.notifications.telegram import (
    PENDING_CONFIGURATION_STATUS,
    SKIPPED_NON_DELIVERING_STATUS,
    TelegramNotificationService,
)
from app.services.notifications.telegram_transport import (
    NON_DELIVERING_ENVIRONMENTS,
    UNKNOWN_ENVIRONMENT_LABEL,
    SkippedTelegramTransport,
    TelegramApiRequest,
    TelegramConfig,
    TelegramDeliveryDisabled,
    TelegramTransport,
    UrlopenTelegramTransport,
    resolve_telegram_transport,
)

BOT_TOKEN = "unit-test-bot-token"
CHAT_ID = "1594710346"
BASE_URL = "https://telegram.invalid"
DELIVERING_ENVIRONMENT = "production"


def _delivering_config(**overrides) -> TelegramConfig:
    """실제 송신이 가능한(설정 완료 + 비-test 환경) 스냅샷."""
    base = {
        "environment": DELIVERING_ENVIRONMENT,
        "bot_token": BOT_TOKEN,
        "chat_id": CHAT_ID,
        "api_base_url": BASE_URL,
        "send_timeout_seconds": 7,
        "polling_limit": 5,
        "polling_timeout_seconds": 3,
    }
    base.update(overrides)
    return TelegramConfig(**base)


def _forbid_network(monkeypatch) -> None:
    """실 Telegram API 접촉을 즉시 실패로 만든다(스킵 경로 증명용)."""

    def fail(*args, **kwargs):
        raise AssertionError("the Telegram API must not be touched in tests")

    monkeypatch.setattr(transport_mod.urlrequest, "urlopen", fail)


class RecordingTransport(TelegramTransport):
    """운영 경로와 동일한 코드를 지나면서 외부 호출만 대체하는 가짜 transport."""

    delivers = True

    def __init__(self, body: str = '{"ok": true, "result": {"message_id": 42}}') -> None:
        self.body = body
        self.requests: list[TelegramApiRequest] = []

    def post(self, request: TelegramApiRequest) -> str:
        self.requests.append(request)
        return self.body


class ExplodingTransport(TelegramTransport):
    """전송 자체가 실패하는 경로(HTTP/연결 실패를 transport 가 감싼 형태)."""

    delivers = True

    def post(self, request: TelegramApiRequest) -> str:
        raise RuntimeError("Telegram API connection failed: boom")


class PortDefaultTransport(TelegramTransport):
    """``delivers`` 를 명시하지 않은 구현 — 포트 기본값(미배달)에 그대로 기댄다."""

    def post(self, request: TelegramApiRequest) -> str:
        raise AssertionError("a non-delivering transport must never be posted to")


# --- 설정 값객체 -----------------------------------------------------------------
class TestTelegramConfig:
    def test_from_settings_snapshots_every_boundary_value(self, monkeypatch):
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", CHAT_ID)
        monkeypatch.setattr(settings, "TELEGRAM_API_BASE_URL", BASE_URL)
        monkeypatch.setattr(settings, "TELEGRAM_SEND_TIMEOUT_SECONDS", 11)
        monkeypatch.setattr(settings, "TELEGRAM_POLLING_LIMIT", 13)
        monkeypatch.setattr(settings, "TELEGRAM_POLLING_TIMEOUT_SECONDS", 17)
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")

        config = TelegramConfig.from_settings(settings)

        assert config == TelegramConfig(
            environment="development",
            bot_token=BOT_TOKEN,
            chat_id=CHAT_ID,
            api_base_url=BASE_URL,
            send_timeout_seconds=11,
            polling_limit=13,
            polling_timeout_seconds=17,
        )

    def test_environment_is_required_so_credentials_only_configs_cannot_be_built(self):
        """배달 정책을 침묵으로 결정하는 자격증명-only 생성을 생성 단계에서 막는다."""
        with pytest.raises(TypeError):
            TelegramConfig(bot_token=BOT_TOKEN, chat_id=CHAT_ID)  # type: ignore[call-arg]

    def test_repr_never_leaks_the_bot_token(self):
        rendered = repr(_delivering_config())

        assert BOT_TOKEN not in rendered
        assert CHAT_ID in rendered

    def test_api_url_joins_base_and_token_without_double_slash(self):
        config = _delivering_config(api_base_url=f"{BASE_URL}/")

        assert config.api_url("sendMessage") == (
            f"{BASE_URL}/bot{BOT_TOKEN}/sendMessage"
        )


# --- 조합 지점(floor) ------------------------------------------------------------
class TestTransportResolution:
    def test_test_environment_in_config_resolves_to_a_non_delivering_transport(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENVIRONMENT", DELIVERING_ENVIRONMENT)

        transport = resolve_telegram_transport(_delivering_config(environment="test"))

        assert isinstance(transport, SkippedTelegramTransport)
        assert transport.delivers is False
        assert transport.environment == "test"

    def test_process_environment_floors_a_delivering_config(self, monkeypatch):
        """자격증명 + 배달 의도 config 라도 프로세스 환경이 막는다(fail-closed)."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "test")

        transport = resolve_telegram_transport(_delivering_config())

        assert isinstance(transport, SkippedTelegramTransport)
        assert transport.environment == "test"

    @pytest.mark.parametrize("environment", ["production", "development", "staging"])
    def test_delivering_environments_resolve_to_the_urlopen_transport(
        self, monkeypatch, environment
    ):
        monkeypatch.setattr(settings, "ENVIRONMENT", environment)

        transport = resolve_telegram_transport(
            _delivering_config(environment=environment)
        )

        assert isinstance(transport, UrlopenTelegramTransport)
        assert transport.delivers is True

    def test_non_delivering_environments_is_declared_as_data(self):
        """스킵 규칙은 코드 분기가 아니라 데이터로 선언된다(§4.5-3)."""
        assert NON_DELIVERING_ENVIRONMENTS == frozenset({"test"})

    def test_port_defaults_to_not_delivering(self):
        """새 구현이 플래그를 잊으면 배달이 아니라 스킵으로 떨어진다(fail-closed)."""
        transport = PortDefaultTransport()

        assert transport.delivers is False
        assert transport.environment == UNKNOWN_ENVIRONMENT_LABEL

    def test_skipping_transport_refuses_bypassed_posts(self):
        transport = SkippedTelegramTransport(environment="test")

        with pytest.raises(TelegramDeliveryDisabled):
            transport.post(
                TelegramApiRequest(operation="sendMessage", url="https://ignored")
            )


# --- ENVIRONMENT=test 스킵 (불변 요구) --------------------------------------------
class TestTestEnvironmentSkip:
    """설정이 완료돼 있어도 test 환경에서는 실호출이 0 이어야 한다."""

    def test_send_message_is_skipped_and_never_calls_the_api(self, monkeypatch):
        _forbid_network(monkeypatch)
        service = TelegramNotificationService(
            config=_delivering_config(environment="test")
        )

        delivery = service.send_message("dry-run only")

        assert delivery["sent"] is False
        assert delivery["status"] == SKIPPED_NON_DELIVERING_STATUS
        assert delivery["detail"] == "Telegram delivery skipped in test environment."

    def test_credentials_only_injection_cannot_bypass_the_process_floor(
        self, monkeypatch
    ):
        """배달 환경 config 를 주입해도 프로세스 env=test 면 송신하지 않는다."""
        _forbid_network(monkeypatch)
        monkeypatch.setattr(settings, "ENVIRONMENT", "test")
        service = TelegramNotificationService(config=_delivering_config())

        delivery = service.send_message("dry-run only")

        assert delivery["status"] == SKIPPED_NON_DELIVERING_STATUS
        assert delivery["detail"] == "Telegram delivery skipped in test environment."
        assert service.get_updates() == []
        assert service.answer_callback_query("cq-1", "확인")["sent"] is False

    def test_callback_acknowledgement_is_skipped(self, monkeypatch):
        _forbid_network(monkeypatch)
        service = TelegramNotificationService(
            config=_delivering_config(environment="test")
        )

        result = service.answer_callback_query("cq-1", "확인")

        assert result["sent"] is False
        assert result["status"] == SKIPPED_NON_DELIVERING_STATUS
        assert result["detail"] == (
            "Telegram callback acknowledgement skipped in test environment."
        )

    def test_polling_and_webhook_diagnostics_are_inert(self, monkeypatch):
        _forbid_network(monkeypatch)
        service = TelegramNotificationService(
            config=_delivering_config(environment="test")
        )

        assert service.get_updates() == []
        assert service.get_webhook_info() == {
            "ok": False,
            "result": {
                "url": "",
                "pending_update_count": 0,
                "has_custom_certificate": False,
            },
        }

    def test_default_settings_path_skips_when_environment_is_test(self, monkeypatch):
        """주입 없이 전역 settings 만으로도 스킵 경로가 선택된다."""
        _forbid_network(monkeypatch)
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", CHAT_ID)
        monkeypatch.setattr(settings, "ENVIRONMENT", "test")

        delivery = TelegramNotificationService().send_message("dry-run only")

        assert delivery["status"] == SKIPPED_NON_DELIVERING_STATUS

    def test_skip_detail_falls_back_to_the_port_label(self):
        """환경 이름을 밝히지 않은 미배달 transport 도 문구가 깨지지 않는다."""
        service = TelegramNotificationService(
            config=_delivering_config(), transport=PortDefaultTransport()
        )

        assert service.send_message("본문")["detail"] == (
            f"Telegram delivery skipped in {UNKNOWN_ENVIRONMENT_LABEL} environment."
        )

    def test_returned_results_are_copies_callers_cannot_poison(self, monkeypatch):
        _forbid_network(monkeypatch)
        skipping = TelegramNotificationService(
            config=_delivering_config(environment="test")
        )
        unconfigured = TelegramNotificationService(
            config=_delivering_config(bot_token="", environment="test")
        )

        skipping.send_message("첫 번째")["detail"] = "mutated"
        unconfigured.send_message("첫 번째")["detail"] = "mutated"

        assert skipping.send_message("두 번째")["detail"] != "mutated"
        assert unconfigured.send_message("두 번째")["detail"] != "mutated"


# --- 주입된 transport 로 검증하는 운영 경로 ---------------------------------------
class TestInjectedDeliveryPath:
    def test_explicit_transport_injection_is_the_documented_escape_hatch(
        self, monkeypatch
    ):
        """명시적 transport 주입만 환경 floor 를 우회한다(정책 책임은 주입자)."""
        _forbid_network(monkeypatch)
        monkeypatch.setattr(settings, "ENVIRONMENT", "test")
        transport = RecordingTransport()
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        assert service.send_message("본문")["sent"] is True
        assert len(transport.requests) == 1

    def test_send_message_posts_the_configured_payload(self):
        transport = RecordingTransport()
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "x"}]]}

        delivery = service.send_message("본문", reply_markup=markup)

        assert delivery == {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
            "telegram_message_id": 42,
        }
        assert len(transport.requests) == 1
        request = transport.requests[0]
        assert request.operation == "sendMessage"
        assert request.url == f"{BASE_URL}/bot{BOT_TOKEN}/sendMessage"
        assert request.timeout_seconds == 7
        assert json.loads(request.body.decode("utf-8")) == {
            "chat_id": CHAT_ID,
            "text": "본문",
            "disable_web_page_preview": False,
            "reply_markup": markup,
        }

    def test_explicit_chat_id_overrides_the_configured_default(self):
        transport = RecordingTransport()
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        service.send_message("본문", chat_id="999")

        assert json.loads(transport.requests[0].body.decode("utf-8"))["chat_id"] == "999"

    def test_injected_config_wins_over_global_settings(self, monkeypatch):
        """주입된 스냅샷이 전역 settings 를 덮는다 — 전역 미구성이어도 송신한다."""
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
        transport = RecordingTransport()
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        assert service.is_configured() is True
        assert service.get_configured_chat_id() == CHAT_ID
        assert service.get_authorized_chat_id() == CHAT_ID
        assert service.send_message("본문")["sent"] is True

    def test_callback_acknowledgement_uses_the_transport(self):
        transport = RecordingTransport(body='{"ok": true}')
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        result = service.answer_callback_query("cq-1", "확인")

        assert result["status"] == "sent"
        assert transport.requests[0].operation == "answerCallbackQuery"
        assert json.loads(transport.requests[0].body.decode("utf-8")) == {
            "callback_query_id": "cq-1",
            "text": "확인",
            "show_alert": False,
        }

    def test_get_updates_sends_configured_polling_defaults(self):
        transport = RecordingTransport(body='{"ok": true, "result": [{"update_id": 1}]}')
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        assert service.get_updates(offset=9) == [{"update_id": 1}]
        assert json.loads(transport.requests[0].body.decode("utf-8")) == {
            "timeout": 3,
            "limit": 5,
            "offset": 9,
        }

    def test_get_webhook_info_returns_the_transport_payload(self):
        transport = RecordingTransport(body='{"ok": true, "result": {"url": "u"}}')
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        assert service.get_webhook_info() == {"ok": True, "result": {"url": "u"}}

    def test_public_calls_resolve_config_and_transport_once(self, monkeypatch):
        """공개 호출당 스냅샷 1회·transport 해석 1회(위임 비용 회귀 가드)."""
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", CHAT_ID)
        monkeypatch.setattr(settings, "ENVIRONMENT", DELIVERING_ENVIRONMENT)
        snapshots: list[str] = []
        resolutions: list[str] = []
        original_from_settings = TelegramConfig.from_settings
        transport = RecordingTransport()

        def counting_from_settings(runtime_settings):
            snapshots.append(runtime_settings.ENVIRONMENT)
            return original_from_settings(runtime_settings)

        def counting_resolver(config):
            resolutions.append(config.environment)
            return transport

        monkeypatch.setattr(TelegramConfig, "from_settings", counting_from_settings)
        monkeypatch.setattr(
            telegram_mod, "resolve_telegram_transport", counting_resolver
        )

        assert TelegramNotificationService().send_message("본문")["sent"] is True

        assert snapshots == [DELIVERING_ENVIRONMENT]
        assert resolutions == [DELIVERING_ENVIRONMENT]


# --- 실패 경로 -------------------------------------------------------------------
class TestFailurePaths:
    def test_unconfigured_service_reports_pending_and_never_posts(self):
        transport = RecordingTransport()
        service = TelegramNotificationService(
            config=_delivering_config(bot_token="replace-with-telegram-bot-token"),
            transport=transport,
        )

        delivery = service.send_message("본문")

        assert delivery["status"] == PENDING_CONFIGURATION_STATUS
        assert transport.requests == []

    def test_api_rejection_raises(self):
        transport = RecordingTransport(
            body='{"ok": false, "description": "chat not found"}'
        )
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        with pytest.raises(RuntimeError, match="Telegram API rejected the message"):
            service.send_message("본문")

    def test_callback_rejection_raises(self):
        transport = RecordingTransport(body='{"ok": false, "description": "expired"}')
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        with pytest.raises(RuntimeError, match="callback acknowledgement failed"):
            service.answer_callback_query("cq-1", "확인")

    def test_get_updates_rejection_raises(self):
        transport = RecordingTransport(body='{"ok": false, "description": "conflict"}')
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        with pytest.raises(RuntimeError, match="getUpdates failed"):
            service.get_updates()

    def test_invalid_json_response_raises(self):
        transport = RecordingTransport(body="not-json")
        service = TelegramNotificationService(
            config=_delivering_config(), transport=transport
        )

        with pytest.raises(RuntimeError, match="invalid JSON response"):
            service.send_message("본문")

    def test_transport_failure_propagates(self):
        service = TelegramNotificationService(
            config=_delivering_config(), transport=ExplodingTransport()
        )

        with pytest.raises(RuntimeError, match="connection failed"):
            service.send_message("본문")


# --- urlopen transport 의 에러 매핑 (네트워크 미접촉) ------------------------------
class TestUrlopenTransportErrorMapping:
    @staticmethod
    def _request(url: str | None = None) -> TelegramApiRequest:
        return TelegramApiRequest(
            operation="sendMessage",
            url=url or f"{BASE_URL}/bot{BOT_TOKEN}/sendMessage",
            body=b"{}",
            timeout_seconds=1,
        )

    def test_http_error_is_wrapped_with_status_and_body(self, monkeypatch):
        def raise_http_error(*args, **kwargs):
            raise urlerror.HTTPError(
                url=BASE_URL, code=429, msg="Too Many Requests", hdrs=None, fp=None
            )

        monkeypatch.setattr(transport_mod.urlrequest, "urlopen", raise_http_error)

        with pytest.raises(RuntimeError, match="responded with HTTP 429"):
            UrlopenTelegramTransport().post(self._request())

    def test_url_error_is_wrapped_as_connection_failure(self, monkeypatch):
        def raise_url_error(*args, **kwargs):
            raise urlerror.URLError("dns failure")

        monkeypatch.setattr(transport_mod.urlrequest, "urlopen", raise_url_error)

        with pytest.raises(RuntimeError, match="connection failed"):
            UrlopenTelegramTransport().post(self._request())

    def test_invalid_url_never_echoes_the_bot_token(self, monkeypatch):
        """scheme 없는 base URL: ValueError 메시지에 토큰이 실리므로 원인을 끊는다(§8)."""
        _forbid_network(monkeypatch)
        scheme_less = f"telegram.invalid/bot{BOT_TOKEN}/sendMessage"

        with pytest.raises(RuntimeError) as excinfo:
            UrlopenTelegramTransport().post(self._request(url=scheme_less))

        assert str(excinfo.value) == (
            "Telegram API URL is invalid for operation=sendMessage"
        )
        assert BOT_TOKEN not in str(excinfo.value)
        assert excinfo.value.__cause__ is None
