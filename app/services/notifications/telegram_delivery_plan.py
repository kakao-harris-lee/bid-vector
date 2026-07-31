"""Telegram 배달 경로 판정 코어 + 배달 레코드 조립 (순수, I/O 없음).

종전 ``OperatorNotificationService`` 는 배달 1건을 4단 dict 릴레이로 흘렸다::

    build_telegram_delivery_plan() -> dict(11키)
      -> _build_blocked_telegram_delivery(delivery_plan: dict)
      -> _with_telegram_channel_metadata(delivery: dict, delivery_plan: dict)
      -> _record_telegram_delivery(delivery: dict) -> json.dumps -> DB

어느 단계에서 어떤 키가 생기고 없어지는지 타입에 남지 않아, 저장된 레코드를 읽는
피로도 게이트(``sent``/``source``/``project_id``)와 운영 리포트(``status``/``detail``)가
쓰는 쪽과 계약 없이 맞물려 있었다. 그 릴레이를 여기서 값 객체 체인으로 바꾼다:

    TelegramRouteContext + TelegramChannelFacts
      -> resolve_telegram_delivery_plan() -> TelegramDeliveryPlan
      -> build_telegram_delivery_event(plan, TelegramSendOutcome) -> TelegramDeliveryEvent

이 모듈은 **판정만** 한다: DB 조회, 마스킹, ``settings`` 조회, 실제 송신은 모두 경계
(``OperatorNotificationService``)에 남는다. 그래서 판정은 값 테이블로 검증되고(§4.7-4),
``ENVIRONMENT`` 스니핑 같은 환경 의존은 여기로 새어 들어오지 않는다 — 호출부가 이미
계산한 ``can_send_when_allowed`` 를 사실로 받는다.
"""

from __future__ import annotations

from pydantic import ConfigDict

from app.schemas._base import FrozenStrictModel
from app.schemas.analytics_events import UNKNOWN_EVENT_STATUS, TelegramDeliveryEvent
from app.services.notifications.telegram import PENDING_CONFIGURATION_STATUS

__all__ = [
    "TelegramChannelFacts",
    "TelegramDeliveryPlan",
    "TelegramRouteContext",
    "TelegramSendOutcome",
    "blocked_send_outcome",
    "build_telegram_delivery_event",
    "failed_send_outcome",
    "pending_configuration_outcome",
    "resolve_telegram_delivery_plan",
    "unconfigured_route_key",
]

TELEGRAM_CHANNEL_TYPE = "telegram"

# 채널 출처 — 저장된 레코드와 운영자 채널 목록 API 가 함께 읽는 값이다.
CHANNEL_SOURCE_MISSING = "missing_channel"
CHANNEL_SOURCE_OPERATOR_CHANNELS = "operator_notification_channels"
CHANNEL_SOURCE_LEGACY_SETTINGS = "legacy_settings"

# 경로 판정 status. 프론트/증적/피로도 리포트가 읽는 운영 계약이므로 함수 본문의
# 리터럴이 아니라 선언적 상수로 둔다(§4.5-1).
STATUS_BLOCKED_MISSING_OPERATOR = "blocked_missing_operator"
STATUS_CHANNEL_INACTIVE = "telegram_channel_inactive"
STATUS_CHANNEL_DRY_RUN = "telegram_channel_dry_run"
STATUS_ROUTE_NON_CANONICAL = "telegram_route_non_canonical"
STATUS_ROUTE_UNRESOLVED = "telegram_route_unresolved"
STATUS_READY = "ready"
STATUS_SKIPPED_SYNTHETIC_OPERATOR = "skipped_synthetic_operator"
STATUS_SKIPPED_NON_CANONICAL_OPERATOR = "skipped_non_canonical_operator"
# 판정 status 가 비어 있는 plan 으로 배달이 막힌 경우의 최후 폴백.
STATUS_DELIVERY_BLOCKED = "telegram_delivery_blocked"
STATUS_FAILED = "failed"

# status → 사유 문구 (§4.5-2: 값 기반 분기 대신 룩업).
PLAN_DETAIL_BY_STATUS: dict[str, str] = {
    STATUS_BLOCKED_MISSING_OPERATOR: (
        "Telegram delivery skipped because the notification owner does not exist."
    ),
    STATUS_CHANNEL_INACTIVE: (
        "Telegram delivery skipped because the operator channel is inactive."
    ),
    STATUS_CHANNEL_DRY_RUN: (
        "Telegram delivery recorded as dry-run evidence for this operator channel."
    ),
    STATUS_ROUTE_NON_CANONICAL: (
        "Telegram delivery skipped because non-canonical operator routes require "
        "an explicit secret resolver."
    ),
    STATUS_ROUTE_UNRESOLVED: (
        "Telegram delivery skipped because the channel route key has no configured "
        "sender."
    ),
    STATUS_READY: "Telegram delivery route is active.",
    STATUS_SKIPPED_SYNTHETIC_OPERATOR: (
        "Telegram delivery skipped because this operator has no active Telegram "
        "channel."
    ),
    STATUS_SKIPPED_NON_CANONICAL_OPERATOR: (
        "Telegram delivery skipped because this operator has no active Telegram "
        "channel."
    ),
}
# 같은 ``ready`` 지만 사유가 다르다(운영자 채널 행 vs 레거시 설정 chat).
DETAIL_READY_LEGACY_SETTINGS = (
    "Telegram delivery route uses the legacy configured chat."
)
DETAIL_DELIVERY_BLOCKED = "Telegram delivery was not sent."
DETAIL_PENDING_CONFIGURATION = "Telegram is not configured yet."
# transport 응답이 결과 계약을 어긴 경우. 검증 오류 원문(입력값 반복)을 감사 detail 로
# 남기지 않기 위해 고정 문구를 쓴다.
DETAIL_TRANSPORT_CONTRACT_FAILURE = (
    "Telegram transport response did not match the delivery outcome contract."
)


def unconfigured_route_key(operator_id: int) -> str:
    """채널이 없는 운영자의 자리표시 route key (원문 target 을 담지 않는다)."""
    return f"operator:{int(operator_id)}:{TELEGRAM_CHANNEL_TYPE}:unconfigured"


class TelegramChannelFacts(FrozenStrictModel):
    """운영자 Telegram 채널 행에서 **판정에 필요한 사실만** 뽑은 값 객체.

    ``route_key`` / ``target_label`` 은 이미 마스킹된 값이다(마스킹은 경계의 책임 —
    원문 chat id 가 이 모듈을 통과하지 않아야 저장 레코드로 새지 않는다). 원문 route key
    비교도 경계에서 끝내고 그 결과만 ``matches_configured_sender`` 로 받는다.
    """

    channel_id: int
    route_key: str
    target_label: str | None = None
    is_active: bool
    dry_run_only: bool
    matches_configured_sender: bool


class TelegramRouteContext(FrozenStrictModel):
    """운영자/설정 쪽 사실 — 판정에 필요한 것만.

    ``can_send_when_allowed`` 는 "경로가 허용됐다면 이 프로세스가 실제로 송신할 수
    있는가"다. 그 판단에는 환경 스니핑이 들어가므로 경계에서 계산해 넘긴다.
    """

    operator_id: int
    operator_exists: bool
    is_canonical_operator: bool
    is_synthetic_operator: bool
    telegram_configured: bool
    configured_route_key: str
    configured_target_label: str | None = None
    can_send_when_allowed: bool


class TelegramDeliveryPlan(FrozenStrictModel):
    """배달 경로 판정 결과 (종전 11키 base + ``status``/``detail`` dict).

    ``route_send_allowed`` 는 "이 경로로 보내도 되는가"(정책), ``can_send`` 는 "지금 이
    프로세스가 실제로 보낼 수 있는가"(환경)다. 둘을 한 필드로 합치면 dry-run 증적과
    설정 누락이 구분되지 않는다.
    """

    operator_id: int
    channel_type: str = TELEGRAM_CHANNEL_TYPE
    channel_id: int | None = None
    route_key: str
    target_label: str | None = None
    channel_source: str
    channel_active: bool = False
    dry_run_only: bool = True
    route_send_allowed: bool = False
    telegram_configured: bool = False
    can_send: bool = False
    status: str = ""
    detail: str = ""


class TelegramSendOutcome(FrozenStrictModel):
    """송신 시도 1건의 결과 — ``send_message`` 의 원시 dict 를 받는 경계 모델.

    ``TelegramNotificationService.send_message`` 는 D2a 산출물이라 반환 계약이 여전히
    ``dict[str, object]`` 다. 그 dict 를 판정/기록으로 그대로 흘리지 않고 여기서
    ``model_validate`` 로 승격한다(``extra="ignore"`` — 응답에 붙은 부가 키는 저장
    레코드에 들어가지 않던 종전 동작을 유지한다).
    """

    model_config = ConfigDict(extra="ignore")

    sent: bool = False
    status: str | None = None
    detail: str | None = None
    telegram_message_id: int | None = None


def _with_status(
    plan: TelegramDeliveryPlan,
    status: str,
    *,
    detail: str | None = None,
) -> TelegramDeliveryPlan:
    """판정 status 와 그 사유 문구를 확정한다."""
    return plan.model_copy(
        update={
            "status": status,
            "detail": detail or PLAN_DETAIL_BY_STATUS.get(status, ""),
        }
    )


def _base_plan(context: TelegramRouteContext) -> TelegramDeliveryPlan:
    """채널이 확정되지 않은 상태의 기본 plan (송신 불허)."""
    return TelegramDeliveryPlan(
        operator_id=context.operator_id,
        route_key=unconfigured_route_key(context.operator_id),
        channel_source=CHANNEL_SOURCE_MISSING,
        telegram_configured=context.telegram_configured,
    )


def _plan_for_channel(
    context: TelegramRouteContext,
    channel: TelegramChannelFacts,
) -> TelegramDeliveryPlan:
    """운영자 채널 행이 있을 때의 판정.

    **첫 위반 사유가 이긴다**(종전 if-ladder 순서 유지): 비활성 → dry-run → 비 canonical
    → 미해결 route. 예컨대 비활성이면서 dry-run 인 채널은 ``telegram_channel_inactive``
    로 남는다 — 채널을 다시 켜는 것이 먼저 할 일이므로 더 근본적인 사유를 보고한다.
    """
    plan = TelegramDeliveryPlan(
        operator_id=context.operator_id,
        channel_id=channel.channel_id,
        route_key=channel.route_key,
        target_label=channel.target_label,
        channel_source=CHANNEL_SOURCE_OPERATOR_CHANNELS,
        channel_active=channel.is_active,
        dry_run_only=channel.dry_run_only,
        telegram_configured=context.telegram_configured,
    )
    if not channel.is_active:
        return _with_status(plan, STATUS_CHANNEL_INACTIVE)
    if channel.dry_run_only:
        return _with_status(plan, STATUS_CHANNEL_DRY_RUN)
    if not context.is_canonical_operator:
        return _with_status(plan, STATUS_ROUTE_NON_CANONICAL)
    if not channel.matches_configured_sender:
        return _with_status(plan, STATUS_ROUTE_UNRESOLVED)
    return _with_status(
        plan.model_copy(
            update={
                "target_label": channel.target_label or context.configured_target_label,
                "route_send_allowed": True,
                "can_send": context.can_send_when_allowed,
            }
        ),
        STATUS_READY,
    )


def _plan_for_legacy_settings(context: TelegramRouteContext) -> TelegramDeliveryPlan:
    """채널 행이 없는 canonical 운영자는 레거시 설정 chat 으로 배달한다."""
    return TelegramDeliveryPlan(
        operator_id=context.operator_id,
        route_key=context.configured_route_key,
        target_label=context.configured_target_label,
        channel_source=CHANNEL_SOURCE_LEGACY_SETTINGS,
        channel_active=True,
        dry_run_only=False,
        route_send_allowed=True,
        telegram_configured=context.telegram_configured,
        can_send=context.can_send_when_allowed,
        status=STATUS_READY,
        detail=DETAIL_READY_LEGACY_SETTINGS,
    )


def resolve_telegram_delivery_plan(
    *,
    context: TelegramRouteContext,
    channel: TelegramChannelFacts | None,
) -> TelegramDeliveryPlan:
    """운영자별 Telegram 배달 경로를 판정한다(원문 target 노출 없음)."""
    if not context.operator_exists:
        return _with_status(_base_plan(context), STATUS_BLOCKED_MISSING_OPERATOR)
    if channel is not None:
        return _plan_for_channel(context, channel)
    if context.is_canonical_operator:
        return _plan_for_legacy_settings(context)
    skipped_status = (
        STATUS_SKIPPED_SYNTHETIC_OPERATOR
        if context.is_synthetic_operator
        else STATUS_SKIPPED_NON_CANONICAL_OPERATOR
    )
    return _with_status(_base_plan(context), skipped_status)


def blocked_send_outcome(plan: TelegramDeliveryPlan) -> TelegramSendOutcome:
    """경로가 막혔을 때 기록할 결과 — 판정 사유를 그대로 감사 기록에 옮긴다."""
    return TelegramSendOutcome(
        sent=False,
        status=plan.status or STATUS_DELIVERY_BLOCKED,
        detail=plan.detail or DETAIL_DELIVERY_BLOCKED,
    )


def pending_configuration_outcome() -> TelegramSendOutcome:
    """Telegram 설정 자체가 없을 때 기록할 결과."""
    return TelegramSendOutcome(
        sent=False,
        status=PENDING_CONFIGURATION_STATUS,
        detail=DETAIL_PENDING_CONFIGURATION,
    )


def failed_send_outcome(detail: str) -> TelegramSendOutcome:
    """송신이 예외로 실패했을 때 기록할 결과."""
    return TelegramSendOutcome(sent=False, status=STATUS_FAILED, detail=detail)


def build_telegram_delivery_event(
    *,
    plan: TelegramDeliveryPlan,
    outcome: TelegramSendOutcome,
    notification_id: int,
    source: str,
    project_id: int | None = None,
) -> TelegramDeliveryEvent:
    """저장할 배달 레코드를 조립한다 (경로 판정 + 송신 결과의 합).

    ``project_id`` 가 피로도 게이트가 같은 공고의 재알림을 인지하는 유일한 키다 —
    이 키가 없던 시절의 행은 그냥 매칭되지 않는다.
    """
    return TelegramDeliveryEvent(
        operator_id=plan.operator_id,
        notification_id=int(notification_id),
        project_id=None if project_id is None else int(project_id),
        source=source,
        sent=outcome.sent,
        status=outcome.status or UNKNOWN_EVENT_STATUS,
        detail=outcome.detail or "",
        telegram_message_id=outcome.telegram_message_id,
        channel_type=plan.channel_type,
        channel_id=plan.channel_id,
        route_key=plan.route_key,
        target_label=plan.target_label,
        channel_source=plan.channel_source,
        channel_active=plan.channel_active,
        dry_run_only=plan.dry_run_only,
        route_send_allowed=plan.route_send_allowed,
        telegram_configured=plan.telegram_configured,
        can_send=plan.can_send,
    )
