"""Telegram 송신 경계 — 설정 값객체 + transport 포트 + 단일 조합 지점.

``TelegramNotificationService`` 는 이전까지 생성자가 없었고, 메서드 본문 곳곳에서
전역 ``settings`` 를 직접 읽었으며 네 곳에서 ``settings.ENVIRONMENT == "test"`` 를
감지해 조용히 return 했다. 결과는 두 가지 문제였다:

* 프로덕션 코드가 테스트 환경을 스니핑하므로 **테스트가 지나는 경로 != 운영 경로** 다.
* 송신 seam 이 없으니 테스트가 클래스 메서드를 monkeypatch 하는 수밖에 없었다.

이 모듈은 그 두 축을 경계로 끌어낸다(§4.5-1/2, §4.7-1/2/3):

* :class:`TelegramConfig` — 호출 시점 설정 스냅샷(값객체). 서비스는 이 값객체만 읽는다.
* :class:`TelegramTransport` — 실제 송신 포트. 구현은 둘뿐이고, 선택은
  :func:`resolve_telegram_transport` **한 곳**에서만 일어난다.

``ENVIRONMENT=test`` 에서 송신을 스킵한다는 불변 요구(CLAUDE.md §7)는 도메인 메서드의
분기가 아니라 :data:`NON_DELIVERING_ENVIRONMENTS` 라는 **데이터**로 선언된다. 해당
환경에서는 :class:`SkippedTelegramTransport` 가 선택되어 외부 실호출이 0 이 된다.

조합 지점은 주입된 config 뿐 아니라 **프로세스 환경**도 함께 보는 fail-closed floor 다
(자격증명만 담은 config 주입이 배달 정책을 뚫지 못하게). floor 를 넘는 길은 transport
를 명시적으로 주입하는 escape hatch 하나뿐이며 그때 정책 책임은 주입자에게 있다.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib import error as urlerror
from urllib import request as urlrequest

from app.core.config import Settings, settings
from app.core.constants import (
    NON_DELIVERING_ENVIRONMENTS as _SHARED_NON_DELIVERING_ENVIRONMENTS,
)

logger = logging.getLogger(__name__)

# 실제 송신을 하지 않는 환경. 단일 출처는 ``app/core/constants.py`` 로 올라갔다(메일 경계도
# 같은 집합을 본다) — 여기서는 기존 import 경로와 이름을 유지하기 위한 별칭만 둔다.
NON_DELIVERING_ENVIRONMENTS = _SHARED_NON_DELIVERING_ENVIRONMENTS

# 환경 이름을 밝히지 않은 미배달 transport 가 스킵 문구에서 자신을 부르는 이름.
UNKNOWN_ENVIRONMENT_LABEL = "non-delivering"

JSON_CONTENT_TYPE = "application/json"
HTTP_POST_METHOD = "POST"
RESPONSE_ENCODING = "utf-8"


class TelegramDeliveryDisabled(RuntimeError):
    """송신이 비활성화된 transport 에 실제 POST 를 요청했을 때 올라온다.

    호출부가 :attr:`TelegramTransport.delivers` 확인을 건너뛴 경우에만 발생한다.
    조용히 성공한 척하지 않고 크게 실패시켜 스킵 경로가 새는 것을 막는다.
    """


@dataclass(frozen=True)
class TelegramConfig:
    """호출 경계에서 뜬 Telegram 설정 스냅샷.

    ``bot_token`` 은 ``repr`` 에서 제외한다 — 값객체가 로그·트레이스·예외 문맥에
    실려도 토큰이 새지 않아야 한다(§8). 단 ``dataclasses.asdict()``/``astuple()`` 은
    ``repr`` 설정과 무관하게 모든 필드를 복사하므로 이 값객체를 그렇게 펼쳐
    로그·응답·이벤트 payload 에 싣지 않는다.

    ``environment`` 에는 기본값이 없다: 자격증명만 채운 생성이 배달 정책을 침묵으로
    결정하지 못하게 한다(fail-closed). ``from_settings`` 는 항상 채워 넘긴다.
    """

    environment: str
    bot_token: str = field(default="", repr=False)
    chat_id: str = ""
    api_base_url: str = "https://api.telegram.org"
    send_timeout_seconds: int = 10
    polling_limit: int = 20
    polling_timeout_seconds: int = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramConfig":
        """전역 ``Settings`` 에서 이 경계가 쓰는 값만 한 번에 스냅샷한다."""
        return cls(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID,
            api_base_url=settings.TELEGRAM_API_BASE_URL,
            send_timeout_seconds=settings.TELEGRAM_SEND_TIMEOUT_SECONDS,
            polling_limit=settings.TELEGRAM_POLLING_LIMIT,
            polling_timeout_seconds=settings.TELEGRAM_POLLING_TIMEOUT_SECONDS,
            environment=settings.ENVIRONMENT,
        )

    def api_url(self, method_name: str) -> str:
        """봇 토큰이 포함된 Bot API URL(로그에 남기지 않는다)."""
        base_url = self.api_base_url.rstrip("/")
        return f"{base_url}/bot{self.bot_token}/{method_name}"


@dataclass(frozen=True)
class TelegramApiRequest:
    """Bot API 호출 한 건.

    ``url`` 에는 봇 토큰이 들어 있으므로 ``repr`` 에서 제외한다. ``operation`` 은
    ``sendMessage`` 같은 API 메서드명이라 로그·기록에 안전하다.
    """

    operation: str
    url: str = field(repr=False)
    body: bytes = field(default=b"", repr=False)
    timeout_seconds: int = 10


class TelegramTransport(ABC):
    """Bot API 송신 포트(§4.7-1).

    ``delivers`` 는 기본이 **False** 다 — 실제 외부 송신은 구현이 명시적으로
    opt-in 해야 하고, 새 구현이 플래그를 잊어도 fail-closed 로 떨어진다.
    ``environment`` 는 미배달 구현이 스킵 문구에서 자신을 설명하는 이름이다.
    """

    delivers: bool = False
    environment: str = UNKNOWN_ENVIRONMENT_LABEL

    @abstractmethod
    def post(self, request: TelegramApiRequest) -> str:
        """요청을 보내고 응답 본문(raw text)을 돌려준다."""


class UrlopenTelegramTransport(TelegramTransport):
    """운영 경로 — ``urllib`` 로 Bot API 에 POST 한다.

    HTTP/연결/URL 실패는 호출부 계약을 유지하기 위해 ``RuntimeError`` 로 감싼다.
    """

    delivers = True

    def post(self, request: TelegramApiRequest) -> str:
        try:
            http_request = urlrequest.Request(
                request.url,
                data=request.body,
                headers={"Content-Type": JSON_CONTENT_TYPE},
                method=HTTP_POST_METHOD,
            )
        except ValueError:
            # scheme 없는 base URL 등에서 ValueError 가 나며, 그 메시지는 URL(=봇
            # 토큰 포함)을 그대로 에코한다. 체이닝하면 트레이스백으로 토큰이 새므로
            # 원인을 끊고 operation 만 남긴다(§8).
            raise RuntimeError(
                f"Telegram API URL is invalid for operation={request.operation}"
            ) from None

        try:
            with urlrequest.urlopen(
                http_request, timeout=request.timeout_seconds
            ) as response:
                return response.read().decode(RESPONSE_ENCODING)
        except urlerror.HTTPError as exc:
            error_body = exc.read().decode(RESPONSE_ENCODING, errors="replace")
            raise RuntimeError(
                f"Telegram API responded with HTTP {exc.code}: {error_body}"
            ) from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"Telegram API connection failed: {exc.reason}") from exc


class SkippedTelegramTransport(TelegramTransport):
    """송신하지 않는 경로(``ENVIRONMENT=test`` 등) — 외부 실호출 0.

    정상 흐름에서 :meth:`post` 는 호출되지 않는다(서비스가 ``delivers`` 를 먼저
    본다). 그 확인을 건너뛴 호출은 조용히 성공한 척하지 않고
    :class:`TelegramDeliveryDisabled` 로 실패한다.

    스킵 자체의 기록은 이 클래스가 아니라 서비스가 돌려주는 스킵 ``status`` 와 그것을
    영속화하는 알림 텔레메트리가 담당한다(중복 카운터를 두지 않는다).
    """

    delivers = False

    def __init__(self, *, environment: str) -> None:
        self.environment = environment

    def post(self, request: TelegramApiRequest) -> str:
        logger.warning(
            "미배달 텔레그램 transport 에 직접 POST 시도 (environment=%s, operation=%s)",
            self.environment,
            request.operation,
        )
        raise TelegramDeliveryDisabled(
            f"Telegram delivery is disabled for environment={self.environment}"
        )


def resolve_telegram_transport(config: TelegramConfig) -> TelegramTransport:
    """설정 스냅샷 + 프로세스 환경으로 송신 경로를 고르는 유일한 조합 지점(§4.7-2).

    ``config.environment`` 와 **실제 프로세스 환경**(``settings.ENVIRONMENT``) 중 어느
    하나라도 :data:`NON_DELIVERING_ENVIRONMENTS` 에 속하면 실호출 0 transport 를
    돌려준다. 프로세스 환경을 함께 보는 것이 floor 다 — 자격증명만 담은 config 를
    주입해도 ``ENVIRONMENT=test`` 에서 외부 송신이 새지 않는다(fail-closed).

    이 floor 를 넘는 유일한 길은 ``TelegramNotificationService(transport=...)`` 로
    transport 를 **명시적으로** 주입하는 것이다(문서화된 escape hatch). 그 경로는
    환경 floor 를 우회하며 배달 정책 책임은 주입자에게 있다.
    """
    blocked = sorted(
        {config.environment, settings.ENVIRONMENT} & NON_DELIVERING_ENVIRONMENTS
    )
    if blocked:
        return SkippedTelegramTransport(environment=blocked[0])
    return UrlopenTelegramTransport()
