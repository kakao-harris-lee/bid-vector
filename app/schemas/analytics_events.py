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

Phase 4.3 에서 ``project_view`` / ``recommendation_feedback`` 복원 모델에 **실소비자**를
붙였다: 리뷰 시간 KPI(``_earliest_view_by_project``)와 피드백 dedupe
(``_dedupe_latest_feedback_verdicts``)가 raw dict ``.get()`` 대신 이 모델을 읽는다. 그
승격은 **산출 불변**이어야 했으므로 두 모델은 pydantic 기본 강제 대신 관용 validator
(:func:`coerce_payload_int` / :func:`coerce_payload_str`)를 쓴다 — 종전 ``_coerce_int``
와 같은 규칙이며 그 동치는 ``tests/test_analytics_persisted_consumers.py`` 의 값 테이블이
고정한다. 남은 두 모델(이메일 배달 · 보류 레코드)은 아직 읽기 계약 선언까지다.

G-2 증적 sweep 이벤트(``g2_candidate_recheck`` / ``collect_g2_evidence``)의 계약은
per-operator 2모양 union 때문에 분량이 커서 :mod:`app.schemas.g2_evidence` 에 따로
선언하고 여기서 레지스트리에만 등록한다(Phase 5).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.core.constants import (
    BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE,
    COLLECT_G2_EVIDENCE_EVENT_TYPE,
    G2_CANDIDATE_RECHECK_EVENT_TYPE,
    PROJECT_VIEW_EVENT_TYPE,
    RECOMMENDATION_FEEDBACK_EVENT_TYPE,
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
    TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
)
from app.schemas._base import StrictModel
from app.schemas.g2_evidence import (
    G2CandidateRecheckSummary,
    G2CollectEvidenceSummary,
    PersistedG2CandidateRecheckSummary,
    PersistedG2CollectEvidenceSummary,
)

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
    "coerce_payload_int",
    "coerce_payload_str",
    "enforce_client_payload_caps",
]

# 해석 불가/미기록 상태를 나타내는 대체 문구. 종전 생산 경로의 ``or`` 폴백과 같은 값을
# 한 곳에서 선언한다(§4.5-1).
UNKNOWN_EVENT_STATUS = "unknown"


class AnalyticsEventEnvelope(BaseModel):
    """임의 payload 를 **키 그대로 보존**하는 통과 모델.

    ``POST /api/v1/analytics/event`` 는 프론트가 올리는 텔레메트리 싱크다. 여기에
    ``extra="forbid"`` 를 걸면 새 클라이언트 이벤트가 400 이 되고, 반대로 특정 모델로
    좁히면 기록되던 키가 조용히 사라진다. 그래서 이 모델은 키를 검증하지 않고 **직렬화
    단일 경로를 태우기 위한 seam** 으로만 쓴다(``json.dumps`` 제거).

    키 *집합* 은 열려 있지만 payload *크기* 는 열려 있지 않다 —
    :func:`enforce_client_payload_caps` 가 키 수/직렬화 길이 상한을 강제한다(그 상한은
    ``event_type`` 어휘 제한과 함께 이 엔드포인트의 유일한 입력 방어선이다).
    """

    model_config = ConfigDict(extra="allow")


def coerce_payload_int(value: JsonValue) -> int | None:
    """저장된 payload 의 식별자를 관용적으로 int 로 읽는다(불가하면 ``None``).

    ``analytics.event_data`` 는 스키마 없는 Text 컬럼이라 과거 행에는 ``"42"``(문자열)나
    ``42.0``(부동소수)로 들어간 식별자가 있다. 그 행을 계속 세려면 관용 해석이 필요하고,
    동시에 **정수가 아닌 값은 지어내지 않아야** 한다:

    * ``12.7`` 은 ``12`` 로 절삭하지 않고 거부한다 — 틀린 프로젝트에 조인될 수 있다.
    * ``True`` 는 ``int`` 서브클래스지만 식별자가 아니다.

    이 규칙의 단일 출처다: 복원 모델의 관용 validator 와
    ``_DecisionAnalyticsBase._coerce_int`` 가 모두 이 함수를 쓴다. 두 곳에 따로 적으면
    같은 행이 소비처마다 다르게 해석된다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def coerce_payload_str(value: JsonValue) -> str | None:
    """저장된 payload 의 라벨을 관용적으로 문자열로 읽는다.

    종전 소비처는 ``str(payload.get("verdict") or "")`` 로 어떤 타입이든 문자열화한 뒤
    어휘를 판정했다. 그 산출을 유지하려면 복원 모델도 비문자열을 거부(=행 전체 유실)하지
    않고 문자열화해야 한다 — 유효 어휘 판정은 소비처의 책임이다.
    """
    if value is None or isinstance(value, str):
        return value
    return str(value)


def enforce_client_payload_caps(
    payload: dict[str, JsonValue],
    *,
    max_keys: int,
    max_chars: int,
) -> dict[str, JsonValue]:
    """열린 클라이언트 payload 의 크기 상한을 검사하고 그대로 돌려준다.

    상한 두 값은 **주입**받는다(§4.7-3) — 순수 함수라 설정 없이 값 테이블로 검증된다.
    상한의 단일 출처는 요청 경계인 ``app/schemas/analytics.py`` 의 모듈 상수
    (``ANALYTICS_EVENT_MAX_PAYLOAD_KEYS`` / ``..._CHARS``)이고, 왜 ``Settings`` 가 아니라
    거기에 선언하는지는 그 모듈 주석에 있다(§4.5-1).

    * ``max_keys`` — 최상위 키 수. 키를 무한히 늘려 컬럼을 채우는 경로를 막는다.
    * ``max_chars`` — **저장될 문자열**의 길이. 중첩 구조까지 포함한 실제 크기를 재려면
      직렬화된 결과를 봐야 하므로, 저장 경로와 **같은** 단일 직렬화
      (:class:`AnalyticsEventEnvelope` 의 ``model_dump_json``)를 써서 잰다. 다른 방법으로
      재면 "상한을 통과했는데 저장 문자열은 상한을 넘는" 불일치가 생긴다.

    초과는 ``ValueError`` 로 올린다 — pydantic validator 안에서 호출되므로 라우터까지
    가지 않고 ``422`` 로 매핑된다.
    """
    if len(payload) > max_keys:
        raise ValueError(
            f"event_data 키 수 상한({max_keys})을 초과했습니다: {len(payload)}"
        )
    serialized = AnalyticsEventEnvelope.model_validate(payload).model_dump_json()
    if len(serialized) > max_chars:
        raise ValueError(
            f"event_data 직렬화 길이 상한({max_chars}자)을 초과했습니다: "
            f"{len(serialized)}자"
        )
    return payload


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
    """저장된 ``project_view`` 행 복원용 (프론트 생산, 리뷰 시간 KPI 입력).

    ``project_id`` 는 pydantic 기본 강제(``12.7`` → ValidationError)가 아니라
    :func:`coerce_payload_int` 규칙으로 읽는다. 이 모델은 **행 하나를 거부하는** 게
    아니라 **식별할 수 없는 값을 부재로 접는** 계약이다 — 엄격하게 걸면 옛 행 하나가
    KPI 전체를 500 으로 만들고, 관용적으로 접으면 그 행만 조인에서 빠진다.
    """

    model_config = ConfigDict(extra="ignore")

    project_id: int | None = None

    @field_validator("project_id", mode="before")
    @classmethod
    def _coerce_project_id(cls, value: JsonValue) -> int | None:
        return coerce_payload_int(value)


class PersistedRecommendationFeedbackEvent(StrictModel):
    """저장된 ``recommendation_feedback`` 행 복원용 (프론트 생산).

    ``verdict`` 는 ``Literal`` 이 아니라 ``str | None`` 이다 — 어휘가 바뀌기 전 행을
    읽어도 KPI 집계가 죽지 않아야 하고, 유효 어휘 판정은 소비처의 책임이다.

    세 필드 모두 관용 해석이다(:func:`coerce_payload_int` / :func:`coerce_payload_str`).
    한 키가 망가진 행에서 **나머지 키는 살아 있어야** 하기 때문이다: ``project_id`` 가
    쓰레기값이어도 ``decision_record_id``/``verdict`` 이 유효하면 그 피드백은 계속
    집계된다(엄격 검증은 그 행을 통째로 지워 ``feedback_count`` 를 조용히 줄인다).
    """

    model_config = ConfigDict(extra="ignore")

    project_id: int | None = None
    decision_record_id: int | None = None
    verdict: str | None = None

    @field_validator("project_id", "decision_record_id", mode="before")
    @classmethod
    def _coerce_identifiers(cls, value: JsonValue) -> int | None:
        return coerce_payload_int(value)

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, value: JsonValue) -> str | None:
        return coerce_payload_str(value)


# 복원 union: 레지스트리 조회 결과의 타입.
PersistedAnalyticsEvent = (
    PersistedTelegramDeliveryEvent
    | PersistedTelegramDeliverySuppressedEvent
    | PersistedBidReportEmailDeliveryEvent
    | PersistedTelegramStrategyPendingEditEvent
    | PersistedProjectViewEvent
    | PersistedRecommendationFeedbackEvent
    | PersistedG2CandidateRecheckSummary
    | PersistedG2CollectEvidenceSummary
)

# 생산 union: 직렬화 단일 경로의 인자 타입.
AnalyticsEventPayload = (
    TelegramDeliveryEvent
    | TelegramDeliverySuppressedEvent
    | BidReportEmailDeliveryEvent
    | TelegramStrategyPendingEditActivated
    | TelegramStrategyPendingEditCleared
    | G2CandidateRecheckSummary
    | G2CollectEvidenceSummary
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
    G2_CANDIDATE_RECHECK_EVENT_TYPE: PersistedG2CandidateRecheckSummary,
    COLLECT_G2_EVIDENCE_EVENT_TYPE: PersistedG2CollectEvidenceSummary,
}
