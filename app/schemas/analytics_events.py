"""``Analytics.event_data`` payload 계약 — 이벤트 타입별 DTO + 복원 레지스트리.

방어적 DTO 규율 Phase 4.2. ``analytics.event_data`` 는 ``Text`` 컬럼이라 생산자마다
자유 형식 dict 를 ``json.dumps`` 로 밀어 넣고, 소비자는 ``json.loads`` 후 ``.get()`` 으로
읽는 **무스키마 왕복 회로**였다. 특히 Telegram 배달 레코드는 쓰기(manager)와 읽기
(fatigue_gate / analytics_reporting)가 서로의 키 집합을 모르는 상태로 판정에 쓰였다.

여기서 이벤트 타입별 계약을 선언하고, 직렬화/복원 단일 경로
(:mod:`app.services.analytics_event_payload`)가 이 모델만 쓴다.

두 갈래 비대칭(P1 ``Persisted*`` 선례와 동일한 이유):

* **생산 모델**(:class:`~app.schemas._base.StrictModel`, ``extra="forbid"``) — 오타 키를
  즉시 거부한다. 키 선언 순서가 저장 문자열의 키 순서이므로 기존 산출과 같은 순서로
  선언한다(공백만 다르고 파싱 동치).
* **복원 모델**(``Persisted*``, ``extra="ignore"`` + 모든 필드 ``| None``) — 과거 행은
  지금 없는 키가 있거나 지금 있는 키가 없다. forbid 로 읽으면 오래된 한 행이 대시보드
  전체를 500 으로 만들고, 생산 기본값(``0``/``False``)을 쓰면 **기록되지 않은 값이
  기록된 것처럼 날조**된다. 미기록은 ``None`` 으로 보존한다.

``project_view`` / ``recommendation_feedback`` 는 프론트가 열린 텔레메트리 엔드포인트로
올리는 이벤트라 생산 모델을 두지 않는다(엔드포인트는
:class:`AnalyticsEventEnvelope` 로 키를 보존만 한다). 읽기 계약만 선언해 레지스트리에
등록한다 — 어떤 키를 기대하는지가 코드 주석이 아니라 타입으로 남는다.

이 중 일부 복원 모델(``project_view`` / ``recommendation_feedback`` / 이메일·보류 레코드)
은 아직 **읽기 계약 선언**까지다. KPI·리포트 소비처를 이 모델로 승격하는 작업은 값 테이블
특성 테스트가 선행돼야 하므로 **Phase 4.3 으로 예약**한다(현 소비처는 종전 dict 경로 유지).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import (
    BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE,
    PROJECT_VIEW_EVENT_TYPE,
    RECOMMENDATION_FEEDBACK_EVENT_TYPE,
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
    TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
)
from app.schemas._base import StrictModel

__all__ = [
    "PERSISTED_EVENT_MODEL_BY_TYPE",
    "AnalyticsEventEnvelope",
    "AnalyticsEventPayload",
    "BidReportEmailDeliveryEvent",
    "PersistedAnalyticsEvent",
    "PersistedBidReportEmailDeliveryEvent",
    "PersistedProjectViewEvent",
    "PersistedRecommendationFeedbackEvent",
    "PersistedTelegramDeliveryEvent",
    "PersistedTelegramDeliverySuppressedEvent",
    "PersistedTelegramStrategyPendingEditEvent",
    "TelegramDeliveryEvent",
    "TelegramDeliverySuppressedEvent",
    "TelegramStrategyPendingEditActivated",
    "TelegramStrategyPendingEditCleared",
    "TelegramStrategyPendingEditEvent",
]

# 해석 불가/미기록 상태를 나타내는 대체 문구. 종전 생산 경로의 ``or`` 폴백과 같은 값을
# 한 곳에서 선언한다(§4.5-1).
UNKNOWN_EVENT_STATUS = "unknown"


class AnalyticsEventEnvelope(BaseModel):
    """임의 payload 를 **키 그대로 보존**하는 통과 모델.

    ``POST /api/v1/analytics/event`` 는 프론트가 올리는 열린 텔레메트리 싱크다. 여기에
    ``extra="forbid"`` 를 걸면 새 클라이언트 이벤트가 400 이 되고, 반대로 특정 모델로
    좁히면 기록되던 키가 조용히 사라진다. 그래서 이 모델은 검증하지 않고 **직렬화
    단일 경로를 태우기 위한 seam** 으로만 쓴다(``json.dumps`` 제거).
    """

    model_config = ConfigDict(extra="allow")


class TelegramDeliveryEvent(StrictModel):
    """``telegram.delivery`` — Telegram 배달 시도 1건의 감사 레코드.

    ``sent`` / ``source`` / ``project_id`` 는 알림 피로도 게이트의 판정 입력이고
    (:mod:`app.services.notifications.fatigue_gate`), ``status`` / ``detail`` 은 운영
    대시보드의 성공률·실패 사유 집계 입력이다. 즉 이 모델은 텔레메트리가 아니라
    **판정 계약**이라 생산 경로에서 forbid 로 고정한다.

    ``target_label`` / ``route_key`` 는 마스킹된 값만 들어온다(원문 chat id 금지 — §8).
    """

    operator_id: int
    notification_id: int
    project_id: int | None = None
    source: str
    sent: bool
    status: str
    detail: str
    telegram_message_id: int | None = None
    channel_type: str
    channel_id: int | None = None
    route_key: str
    target_label: str | None = None
    channel_source: str
    channel_active: bool
    dry_run_only: bool
    route_send_allowed: bool
    telegram_configured: bool
    can_send: bool


class PersistedTelegramDeliveryEvent(TelegramDeliveryEvent):
    """저장된 ``telegram.delivery`` 행 복원용 (관용 경로).

    ``project_id`` 는 이 키가 생기기 전 행에 아예 없다 — 그 행은 재알림 쿨다운 매칭에서
    빠지되(``None``) 일일 상한 카운트에는 남아야 하므로, 없는 값을 ``0`` 으로 채우지
    않는다.
    """

    model_config = ConfigDict(extra="ignore")

    operator_id: int | None = None
    notification_id: int | None = None
    source: str | None = None
    sent: bool | None = None
    status: str | None = None
    detail: str | None = None
    channel_type: str | None = None
    route_key: str | None = None
    channel_source: str | None = None
    channel_active: bool | None = None
    dry_run_only: bool | None = None
    route_send_allowed: bool | None = None
    telegram_configured: bool | None = None
    can_send: bool | None = None


class TelegramDeliverySuppressedEvent(StrictModel):
    """``telegram.delivery.suppressed`` — 가치 게이트를 통과한 알림을 보류한 근거.

    배달 실패가 아니므로 배달 성공률 분모에 들어가지 않는다(별도 event_type인 이유).
    ``status`` 는 ``reason`` 과 같은 값을 담는다 — 종전 산출을 유지하기 위한 중복이며,
    운영 리포트가 다른 이벤트와 같은 ``status`` 키로 사유를 읽을 수 있게 한다.
    """

    operator_id: int
    notification_id: int
    project_id: int | None = None
    source: str
    sent: bool = False
    status: str
    allowed: bool
    reason: str
    detail: str
    daily_sent_count: int
    daily_cap: int
    hours_since_project_send: float | None = None
    renotify_cooldown_hours: float


class PersistedTelegramDeliverySuppressedEvent(TelegramDeliverySuppressedEvent):
    """저장된 ``telegram.delivery.suppressed`` 행 복원용 (관용 경로)."""

    model_config = ConfigDict(extra="ignore")

    operator_id: int | None = None
    notification_id: int | None = None
    source: str | None = None
    sent: bool | None = None
    status: str | None = None
    allowed: bool | None = None
    reason: str | None = None
    detail: str | None = None
    daily_sent_count: int | None = None
    daily_cap: int | None = None
    renotify_cooldown_hours: float | None = None


class BidReportEmailDeliveryEvent(StrictModel):
    """``email.bid_report.delivery`` — 투찰 보고서 메일 전달 시도 텔레메트리.

    ``masked_recipient`` 만 담는다(원문 주소 금지 — §8). ``recorded_at`` 은 ``datetime``
    이 아니라 ``.isoformat()`` 문자열이다: pydantic 의 datetime 직렬화(``Z`` 접미)와
    종전 산출(``+00:00``)이 달라 저장 문자열이 바뀌기 때문이다.
    """

    operator_id: int | None = None
    project_id: int | None = None
    decision_record_id: int | None = None
    dry_run: bool
    sent: bool
    delivery_status: str
    masked_recipient: str
    has_draft_attachment: bool
    recorded_at: str


class PersistedBidReportEmailDeliveryEvent(BidReportEmailDeliveryEvent):
    """저장된 ``email.bid_report.delivery`` 행 복원용 (관용 경로)."""

    model_config = ConfigDict(extra="ignore")

    dry_run: bool | None = None
    sent: bool | None = None
    delivery_status: str | None = None
    masked_recipient: str | None = None
    has_draft_attachment: bool | None = None
    recorded_at: str | None = None


class TelegramStrategyPendingEditActivated(StrictModel):
    """``telegram.strategy.pending_edit`` — 진행 중인 전략 편집 단계를 적재한 행.

    해제 행(:class:`TelegramStrategyPendingEditCleared`)과 **키 집합이 다르다**. 한
    모델로 합치면 해제 행에 ``field_key: null`` 같은 없던 키가 생기므로 모양별로 나눈다
    (P1 의 historical/forward 요청 스냅샷 분리와 같은 이유). ``active`` 는 ``Literal``
    이라 두 모양이 구조적으로 배타적이다.
    """

    chat_id: str
    active: Literal[True] = True
    field_key: str
    stage: str
    updates: dict[str, Any] = Field(default_factory=dict)


class TelegramStrategyPendingEditCleared(StrictModel):
    """``telegram.strategy.pending_edit`` — 편집 상태를 해제한 행(2키)."""

    chat_id: str
    active: Literal[False] = False


# 생산 union: pending edit 이벤트를 기록하는 경로의 인자 타입.
TelegramStrategyPendingEditEvent = (
    TelegramStrategyPendingEditActivated | TelegramStrategyPendingEditCleared
)


class PersistedTelegramStrategyPendingEditEvent(StrictModel):
    """저장된 pending edit 행 복원용 — 적재/해제 두 모양을 함께 읽는다.

    생산 union 을 그대로 복원에 쓰면 두 모양 중 하나로 판별해야 하는데, 읽는 쪽이
    필요한 것은 "이 chat 의 최신 상태가 살아 있는지"뿐이다. 그래서 복원은 합집합
    모양 하나로 관용 처리한다(``active`` 는 ``bool | None`` — 미기록은 비활성 취급).
    """

    model_config = ConfigDict(extra="ignore")

    chat_id: str | None = None
    active: bool | None = None
    field_key: str | None = None
    stage: str | None = None
    updates: dict[str, Any] | None = None


class PersistedProjectViewEvent(StrictModel):
    """저장된 ``project_view`` 행 복원용 (프론트 생산, 읽기 계약만 선언)."""

    model_config = ConfigDict(extra="ignore")

    project_id: int | None = None


class PersistedRecommendationFeedbackEvent(StrictModel):
    """저장된 ``recommendation_feedback`` 행 복원용 (프론트 생산).

    ``verdict`` 는 ``Literal`` 이 아니라 ``str | None`` 이다 — 어휘가 바뀌기 전 행을
    읽어도 KPI 집계가 죽지 않아야 하고, 유효 어휘 판정은 소비처의 책임이다.
    """

    model_config = ConfigDict(extra="ignore")

    project_id: int | None = None
    decision_record_id: int | None = None
    verdict: str | None = None


# 복원 union: 레지스트리 조회 결과의 타입.
PersistedAnalyticsEvent = (
    PersistedTelegramDeliveryEvent
    | PersistedTelegramDeliverySuppressedEvent
    | PersistedBidReportEmailDeliveryEvent
    | PersistedTelegramStrategyPendingEditEvent
    | PersistedProjectViewEvent
    | PersistedRecommendationFeedbackEvent
)

# 생산 union: 직렬화 단일 경로의 인자 타입.
AnalyticsEventPayload = (
    TelegramDeliveryEvent
    | TelegramDeliverySuppressedEvent
    | BidReportEmailDeliveryEvent
    | TelegramStrategyPendingEditActivated
    | TelegramStrategyPendingEditCleared
    | AnalyticsEventEnvelope
)

# event_type → 복원 모델 (§4.5-2: 값 기반 분기는 룩업 테이블로).
#
# 새 이벤트 타입을 기록하는 생산자를 추가하면 여기 한 줄을 추가한다. 등록되지 않은
# 타입은 ``load_analytics_event`` 가 ``None`` 을 돌려주므로(조용한 오독이 아니라 부재),
# 어떤 타입이 계약을 갖는지가 이 표 하나로 드러난다.
PERSISTED_EVENT_MODEL_BY_TYPE: dict[str, type[PersistedAnalyticsEvent]] = {
    TELEGRAM_DELIVERY_EVENT_TYPE: PersistedTelegramDeliveryEvent,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE: PersistedTelegramDeliverySuppressedEvent,
    BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE: PersistedBidReportEmailDeliveryEvent,
    TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE: (
        PersistedTelegramStrategyPendingEditEvent
    ),
    PROJECT_VIEW_EVENT_TYPE: PersistedProjectViewEvent,
    RECOMMENDATION_FEEDBACK_EVENT_TYPE: PersistedRecommendationFeedbackEvent,
}
