"""미배달 환경 단일 출처(``NON_DELIVERING_ENVIRONMENTS``) 소비처 고정 테스트.

``ENVIRONMENT=test`` 에서 외부 송신이 0 이어야 한다는 불변(CLAUDE.md §7)은 종전 채널마다
``settings.ENVIRONMENT == "test"`` 인라인 비교로 흩어져 있었다. 새 채널이 그 비교를
빠뜨려도 실패하는 테스트가 없다는 게 문제였다(조용한 실송신). 이제 판정은 선언 데이터
멤버십이고, 이 모듈이 **세 소비처가 같은 집합을 본다**는 것과 **스킵 동작이 그대로임**을
고정한다.

새 배달 채널을 추가하면 여기에 그 채널의 판정 함수를 한 줄 추가한다.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.constants import NON_DELIVERING_ENVIRONMENTS
from app.services.notifications.email import EmailNotificationService
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram_transport import (
    NON_DELIVERING_ENVIRONMENTS as TRANSPORT_NON_DELIVERING_ENVIRONMENTS,
)


def test_non_delivering_environments_is_declared_once_as_data():
    """값과 단일 출처를 고정한다 — transport 모듈은 별칭(re-export)일 뿐이다."""
    assert NON_DELIVERING_ENVIRONMENTS == frozenset({"test"})
    assert TRANSPORT_NON_DELIVERING_ENVIRONMENTS is NON_DELIVERING_ENVIRONMENTS


@pytest.mark.parametrize("environment", sorted(NON_DELIVERING_ENVIRONMENTS))
def test_email_never_sends_live_in_a_non_delivering_environment(
    environment, monkeypatch
):
    """메일 전달 게이트를 모두 켜도 미배달 환경에서는 라이브 송신이 열리지 않는다."""
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)

    assert EmailNotificationService()._should_send_live() is False


def test_email_sends_live_when_a_delivering_environment_enables_it(monkeypatch):
    """대조군: 배달 환경 + 게이트 ON 이면 라이브 경로가 열린다(게이트가 죽지 않았다)."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "EMAIL_DELIVERY_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)

    assert EmailNotificationService()._should_send_live() is True


@pytest.mark.parametrize("environment", sorted(NON_DELIVERING_ENVIRONMENTS))
def test_telegram_manager_never_sends_in_a_non_delivering_environment(
    environment, monkeypatch
):
    """라우트가 허용되고 자격증명이 있어도 미배달 환경에서는 실송신하지 않는다."""
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    service = OperatorNotificationService()
    monkeypatch.setattr(service.telegram, "is_configured", lambda: True)

    assert service._can_actually_send_telegram(route_send_allowed=True) is False


def test_telegram_manager_can_send_in_a_delivering_environment(monkeypatch):
    """대조군: 배달 환경 + 라우트 허용 + 자격증명이면 실송신 가능 판정이 True 다."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    service = OperatorNotificationService()
    monkeypatch.setattr(service.telegram, "is_configured", lambda: True)

    assert service._can_actually_send_telegram(route_send_allowed=True) is True


@pytest.mark.parametrize(
    ("route_send_allowed", "configured"),
    [(False, True), (True, False), (False, False)],
)
def test_telegram_manager_requires_route_and_credentials_too(
    route_send_allowed, configured, monkeypatch
):
    """환경 floor 는 다른 두 조건을 대체하지 않는다(AND 게이트 유지)."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    service = OperatorNotificationService()
    monkeypatch.setattr(service.telegram, "is_configured", lambda: configured)

    assert (
        service._can_actually_send_telegram(route_send_allowed=route_send_allowed)
        is False
    )
