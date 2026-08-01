"""Realtime fanout wire 계약 — pg_notify payload 의 발신/수신 모델.

방어적 DTO 규율 Phase 5. 크로스 프로세스 fanout 은 ``json.dumps`` 로 만든
``{"publisher_id": ..., "event": {...}}`` 문자열을 PostgreSQL ``NOTIFY`` 로 보내고,
리스너가 ``json.loads`` 후 ``.get()`` 으로 되읽었다. 발신자와 수신자가 **다른 API
프로세스**(배포 시점이 다를 수 있다)이므로 이 문자열은 사실상 프로세스 간 프로토콜인데,
그 모양이 코드 어디에도 선언되어 있지 않았다.

두 갈래 비대칭(P1 ``Persisted*`` / P4.2 선례와 같은 이유):

* **발신 모델**(:class:`~app.schemas._base.StrictModel`, ``extra="forbid"``) — 보내는
  쪽은 자기 코드가 만든 이벤트만 싣는다. 오타 키는 즉시 거부해야 한다. 필드 선언 순서가
  전송 문자열의 키 순서이므로 종전 dict 리터럴과 같은 순서로 선언한다(공백만 다르고 파싱
  동치 — ``model_dump_json`` 은 종전 ``separators=(",", ":")`` 과 같은 compact 출력).
* **수신 모델**(``Persisted*``, ``extra="ignore"`` + 모든 필드 ``| None``) — 상대 프로세스가
  구버전/신버전일 수 있다. forbid 로 읽으면 새 키 하나가 리스너를 끊고, 기본값을 채우면
  받지 않은 값이 받은 것처럼 로컬 히스토리에 들어간다. 미기록은 ``None`` 으로 두고,
  종전처럼 ``event_type`` 이 비면 그 이벤트를 버리는 판정은 매니저가 한다.

``payload`` 는 ``dict[str, JsonValue]`` 다: 여기 실릴 수 있는 값은 JSON 원시값뿐이라는
제약이 종전에도 있었다(``json.dumps`` 에 ``default`` 가 없어 datetime 이 들어가면 그
자리에서 TypeError 였다). 그 제약을 타입으로 옮겨 선언한다.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, JsonValue, field_validator

from app.schemas._base import StrictModel

__all__ = [
    "PersistedRealtimeEvent",
    "PersistedRealtimeFanoutEnvelope",
    "RealtimeEvent",
    "RealtimeFanoutEnvelope",
]


class RealtimeEvent(StrictModel):
    """대시보드 WebSocket 이벤트 1건의 봉투(발신 계약).

    ``created_at`` 은 ``datetime`` 이 아니라 ``.isoformat()`` 문자열이다 — pydantic 의
    datetime 직렬화(``Z`` 접미)와 종전 산출(``+00:00``)이 달라 전송 문자열이 바뀐다
    (P4.2 ``recorded_at`` 과 같은 이유).
    """

    event_id: str
    event_type: str
    created_at: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class RealtimeFanoutEnvelope(StrictModel):
    """``pg_notify`` 로 나가는 최상위 payload(발신 계약).

    ``publisher_id`` 는 발신 프로세스의 매니저 인스턴스 id 다. 수신 측이 자기 id 와 같으면
    자기가 보낸 이벤트이므로 되받아 이중 방송하지 않는다(fanout 루프 차단).
    """

    publisher_id: str
    event: RealtimeEvent


class PersistedRealtimeEvent(StrictModel):
    """다른 프로세스에서 받은 이벤트 복원용 (관용 경로).

    모든 필드가 ``| None`` 이다. ``event_type`` 이 없는 이벤트는 매니저가 버리고
    (``_normalize_external_event``), ``event_id`` / ``created_at`` 이 없으면 매니저가
    로컬 값으로 채운다 — 종전 ``.get()`` 폴백과 같은 판정을 같은 자리에 남긴다.
    """

    model_config = ConfigDict(extra="ignore")

    event_id: str | None = None
    event_type: str | None = None
    created_at: str | None = None
    payload: dict[str, JsonValue] | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def _ignore_non_object_payload(cls, value: JsonValue) -> JsonValue:
        """객체가 아닌 ``payload`` 는 부재로 낮춘다(종전 ``isinstance`` 검사 보존).

        종전 수신 경로는 ``payload if isinstance(payload, dict) else {}`` 로 **이벤트는
        살리고 payload 만 비웠다**. 여기서 검증 오류를 내면 이벤트 자체가 사라지므로,
        그 관용을 복원 규칙으로 옮긴다.
        """
        return None if not isinstance(value, dict) else value


class PersistedRealtimeFanoutEnvelope(StrictModel):
    """``pg_notify`` 로 받은 최상위 payload 복원용 (관용 경로).

    ``event`` 가 ``None`` 이면(키 부재 또는 객체가 아님) 조용히 버린다 — 종전
    ``isinstance(event, dict)`` 검사가 하던 일과 같다. 손상된 JSON 문자열만 경고 대상이다.
    """

    model_config = ConfigDict(extra="ignore")

    publisher_id: str | None = None
    event: PersistedRealtimeEvent | None = None

    @field_validator("event", mode="before")
    @classmethod
    def _ignore_non_object_event(cls, value: JsonValue) -> JsonValue:
        """객체가 아닌 ``event`` 는 부재로 낮춘다(종전 ``isinstance`` 검사 보존)."""
        return None if not isinstance(value, dict) else value
