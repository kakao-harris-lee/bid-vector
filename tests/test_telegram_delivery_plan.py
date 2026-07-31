"""Telegram 배달 경로 판정 코어 테스트 (순수 — DB·네트워크 없음).

종전 4단 dict 릴레이(plan → blocked delivery → channel metadata → 저장)를 값 객체
체인으로 바꾼 뒤, **판정과 조립이 종전 산출과 같은지**를 값 테이블로 고정한다(§4.7-4).

경로 status/detail 은 프론트·증적·운영 리포트가 읽는 계약이므로 문자열까지 고정한다.
"""

from __future__ import annotations

import pytest

from app.schemas.analytics_events import TelegramDeliveryEvent
from app.services.notifications.telegram_delivery_plan import (
    CHANNEL_SOURCE_LEGACY_SETTINGS,
    CHANNEL_SOURCE_MISSING,
    CHANNEL_SOURCE_OPERATOR_CHANNELS,
    DETAIL_DELIVERY_BLOCKED,
    DETAIL_READY_LEGACY_SETTINGS,
    PLAN_DETAIL_BY_STATUS,
    STATUS_BLOCKED_MISSING_OPERATOR,
    STATUS_CHANNEL_DRY_RUN,
    STATUS_CHANNEL_INACTIVE,
    STATUS_DELIVERY_BLOCKED,
    STATUS_READY,
    STATUS_ROUTE_NON_CANONICAL,
    STATUS_ROUTE_UNRESOLVED,
    STATUS_SKIPPED_NON_CANONICAL_OPERATOR,
    STATUS_SKIPPED_SYNTHETIC_OPERATOR,
    TelegramChannelFacts,
    TelegramDeliveryPlan,
    TelegramRouteContext,
    TelegramSendOutcome,
    blocked_send_outcome,
    build_telegram_delivery_event,
    failed_send_outcome,
    pending_configuration_outcome,
    resolve_telegram_delivery_plan,
)

LEGACY_ROUTE_KEY = "telegram:legacy-configured-chat"
CONFIGURED_LABEL = "chat ********0346"


def _context(
    *,
    operator_exists: bool = True,
    is_canonical: bool = True,
    is_synthetic: bool = False,
    telegram_configured: bool = True,
    can_send_when_allowed: bool = True,
) -> TelegramRouteContext:
    return TelegramRouteContext(
        operator_id=7,
        operator_exists=operator_exists,
        is_canonical_operator=is_canonical,
        is_synthetic_operator=is_synthetic,
        telegram_configured=telegram_configured,
        configured_route_key=LEGACY_ROUTE_KEY,
        configured_target_label=CONFIGURED_LABEL,
        can_send_when_allowed=can_send_when_allowed,
    )


def _channel(
    *,
    is_active: bool = True,
    dry_run_only: bool = False,
    matches_configured_sender: bool = True,
    route_key: str = LEGACY_ROUTE_KEY,
    target_label: str | None = "chat ********0346",
) -> TelegramChannelFacts:
    return TelegramChannelFacts(
        channel_id=3,
        route_key=route_key,
        target_label=target_label,
        is_active=is_active,
        dry_run_only=dry_run_only,
        matches_configured_sender=matches_configured_sender,
    )


# --- happy: 송신이 허용되는 두 경로 -----------------------------------------------


def test_canonical_operator_channel_with_configured_sender_is_ready():
    plan = resolve_telegram_delivery_plan(context=_context(), channel=_channel())

    assert plan.status == STATUS_READY
    assert plan.detail == PLAN_DETAIL_BY_STATUS[STATUS_READY]
    assert plan.channel_source == CHANNEL_SOURCE_OPERATOR_CHANNELS
    assert plan.channel_id == 3
    assert plan.route_key == LEGACY_ROUTE_KEY
    assert plan.route_send_allowed is True
    assert plan.can_send is True


def test_ready_channel_without_label_falls_back_to_the_configured_label():
    plan = resolve_telegram_delivery_plan(
        context=_context(), channel=_channel(target_label=None)
    )

    assert plan.target_label == CONFIGURED_LABEL


def test_canonical_operator_without_channel_uses_the_legacy_configured_chat():
    plan = resolve_telegram_delivery_plan(context=_context(), channel=None)

    assert plan.status == STATUS_READY
    # 같은 ready 지만 사유는 다르다(운영자 채널 행이 아니라 레거시 설정).
    assert plan.detail == DETAIL_READY_LEGACY_SETTINGS
    assert plan.channel_source == CHANNEL_SOURCE_LEGACY_SETTINGS
    assert plan.channel_id is None
    assert plan.route_key == LEGACY_ROUTE_KEY
    assert plan.target_label == CONFIGURED_LABEL
    assert plan.channel_active is True
    assert plan.dry_run_only is False
    assert plan.route_send_allowed is True


def test_ready_route_still_cannot_send_when_the_process_is_not_allowed_to():
    """정책(route_send_allowed)과 환경(can_send)은 분리된 채로 남는다."""
    plan = resolve_telegram_delivery_plan(
        context=_context(can_send_when_allowed=False), channel=None
    )

    assert plan.route_send_allowed is True
    assert plan.can_send is False


# --- sad: 막히는 모든 경로 (값 테이블) --------------------------------------------


@pytest.mark.parametrize(
    ("case", "context", "channel", "expected_status", "expected_source"),
    [
        (
            "missing_operator",
            _context(operator_exists=False),
            None,
            STATUS_BLOCKED_MISSING_OPERATOR,
            CHANNEL_SOURCE_MISSING,
        ),
        (
            "inactive_channel",
            _context(),
            _channel(is_active=False),
            STATUS_CHANNEL_INACTIVE,
            CHANNEL_SOURCE_OPERATOR_CHANNELS,
        ),
        (
            "dry_run_channel",
            _context(),
            _channel(dry_run_only=True),
            STATUS_CHANNEL_DRY_RUN,
            CHANNEL_SOURCE_OPERATOR_CHANNELS,
        ),
        (
            "non_canonical_route",
            _context(is_canonical=False, is_synthetic=True),
            _channel(),
            STATUS_ROUTE_NON_CANONICAL,
            CHANNEL_SOURCE_OPERATOR_CHANNELS,
        ),
        (
            "unresolved_route",
            _context(),
            _channel(matches_configured_sender=False, route_key="telegram:******0346"),
            STATUS_ROUTE_UNRESOLVED,
            CHANNEL_SOURCE_OPERATOR_CHANNELS,
        ),
        (
            "synthetic_without_channel",
            _context(is_canonical=False, is_synthetic=True),
            None,
            STATUS_SKIPPED_SYNTHETIC_OPERATOR,
            CHANNEL_SOURCE_MISSING,
        ),
        (
            "non_canonical_without_channel",
            _context(is_canonical=False),
            None,
            STATUS_SKIPPED_NON_CANONICAL_OPERATOR,
            CHANNEL_SOURCE_MISSING,
        ),
    ],
)
def test_blocked_routes_report_their_reason_and_never_allow_sending(
    case, context, channel, expected_status, expected_source
):
    plan = resolve_telegram_delivery_plan(context=context, channel=channel)

    assert plan.status == expected_status, case
    assert plan.detail == PLAN_DETAIL_BY_STATUS[expected_status], case
    assert plan.channel_source == expected_source, case
    assert plan.route_send_allowed is False, case
    assert plan.can_send is False, case


def test_operator_without_channel_gets_a_placeholder_route_key():
    """채널이 없으면 원문 target 이 아니라 자리표시 route key 가 남는다."""
    plan = resolve_telegram_delivery_plan(
        context=_context(is_canonical=False, is_synthetic=True), channel=None
    )

    assert plan.route_key == "operator:7:telegram:unconfigured"
    assert plan.target_label is None


# --- 송신 결과 승격 + 배달 레코드 조립 --------------------------------------------


def test_send_outcome_promotes_the_transport_dict_and_drops_extra_keys():
    """``send_message`` 의 원시 dict 는 경계에서 검증된 값으로 승격된다."""
    outcome = TelegramSendOutcome.model_validate(
        {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
            "telegram_message_id": 908,
            "unexpected": "ignored",
        }
    )

    assert outcome.sent is True
    assert outcome.telegram_message_id == 908
    assert not hasattr(outcome, "unexpected")


def test_blocked_outcome_carries_the_plan_reason():
    plan = resolve_telegram_delivery_plan(
        context=_context(is_canonical=False, is_synthetic=True), channel=None
    )

    outcome = blocked_send_outcome(plan)

    assert outcome.sent is False
    assert outcome.status == STATUS_SKIPPED_SYNTHETIC_OPERATOR
    assert outcome.detail == PLAN_DETAIL_BY_STATUS[STATUS_SKIPPED_SYNTHETIC_OPERATOR]


def test_blocked_outcome_falls_back_when_the_plan_has_no_reason():
    """status/detail 이 빈 plan 도 감사 기록에 '무엇도 아님'을 남기지 않는다."""
    outcome = blocked_send_outcome(
        TelegramDeliveryPlan(
            operator_id=7,
            route_key="operator:7:telegram:unconfigured",
            channel_source=CHANNEL_SOURCE_MISSING,
        )
    )

    assert outcome.status == STATUS_DELIVERY_BLOCKED
    assert outcome.detail == DETAIL_DELIVERY_BLOCKED


def test_delivery_event_merges_the_plan_and_the_send_outcome():
    plan = resolve_telegram_delivery_plan(context=_context(), channel=_channel())
    outcome = TelegramSendOutcome.model_validate(
        {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
            "telegram_message_id": 908,
        }
    )

    event = build_telegram_delivery_event(
        plan=plan,
        outcome=outcome,
        notification_id=11,
        source="bid_decision",
        project_id=4242,
    )

    assert isinstance(event, TelegramDeliveryEvent)
    assert event.model_dump() == {
        "operator_id": 7,
        "notification_id": 11,
        "project_id": 4242,
        "source": "bid_decision",
        "sent": True,
        "status": "sent",
        "detail": "Telegram delivery succeeded.",
        "telegram_message_id": 908,
        "channel_type": "telegram",
        "channel_id": 3,
        "route_key": LEGACY_ROUTE_KEY,
        "target_label": CONFIGURED_LABEL,
        "channel_source": CHANNEL_SOURCE_OPERATOR_CHANNELS,
        "channel_active": True,
        "dry_run_only": False,
        "route_send_allowed": True,
        "telegram_configured": True,
        "can_send": True,
    }


def test_delivery_event_defaults_an_unknown_status_and_empty_detail():
    """종전 ``str(delivery.get("status") or "unknown")`` 폴백을 유지한다."""
    plan = resolve_telegram_delivery_plan(context=_context(), channel=_channel())

    event = build_telegram_delivery_event(
        plan=plan,
        outcome=TelegramSendOutcome(),
        notification_id=11,
        source="bid_submission",
        project_id=None,
    )

    assert event.status == "unknown"
    assert event.detail == ""
    assert event.project_id is None
    assert event.telegram_message_id is None


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (pending_configuration_outcome(), "pending_configuration"),
        (failed_send_outcome("boom"), "failed"),
    ],
)
def test_non_sending_outcomes_keep_their_operational_status(outcome, expected_status):
    assert outcome.sent is False
    assert outcome.status == expected_status


def test_inactive_and_dry_run_channel_reports_the_more_fundamental_reason():
    """동시 위반의 우선순위를 고정한다: 비활성 > dry-run.

    종전 if-ladder 순서를 그대로 옮긴 결과이며, 운영자에게 먼저 알려야 할 일(채널을 다시
    켜기)이 남도록 하는 선택이다. 순서를 뒤집으면 저장된 증적의 status 분포가 조용히
    바뀐다(대시보드 사유 집계가 dry-run 쪽으로 이동).
    """
    plan = resolve_telegram_delivery_plan(
        context=_context(), channel=_channel(is_active=False, dry_run_only=True)
    )

    assert plan.status == STATUS_CHANNEL_INACTIVE
    assert plan.channel_active is False
    assert plan.dry_run_only is True
    assert plan.route_send_allowed is False


def test_non_canonical_operator_with_unresolved_route_reports_non_canonical_first():
    """운영자 축(비 canonical)이 route 축(미해결)보다 먼저 보고된다."""
    plan = resolve_telegram_delivery_plan(
        context=_context(is_canonical=False, is_synthetic=True),
        channel=_channel(matches_configured_sender=False),
    )

    assert plan.status == STATUS_ROUTE_NON_CANONICAL
