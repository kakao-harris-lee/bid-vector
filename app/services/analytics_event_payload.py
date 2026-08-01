"""``Analytics.event_data`` 직렬화/복원 단일 경로.

종전에는 쓰기가 생산자마다 ``json.dumps(<자유형식 dict>)`` (manager / fatigue_gate /
email / telegram_strategy / api.analytics) 였고, 읽기는 소비자마다 ``json.loads`` 후
``.get()`` 이었다. 쓰는 쪽과 읽는 쪽이 서로의 키 집합을 모르니 키가 늘거나 줄어도 아무도
실패하지 않고 **판정만 조용히 달라졌다**(피로도 게이트가 ``sent``/``source``/
``project_id`` 를 그렇게 읽는다). 그래서 직렬화·복원·복원 실패 정책을 이 한 모듈로 모으고
dump 는 ``model_dump_json()`` 만 쓴다(:mod:`app.schemas.analytics_events` 가 계약).

복원 정책:

* 디코딩 자체는 :func:`app.services.decision_analytics.events.parse_analytics_event_data`
  를 그대로 재사용한다 — JSON 실패 시 ``ast.literal_eval`` 로 legacy ``str(dict)`` 행을
  복구하는 동작은 **보존해야 하는 자산**이라 여기서 다시 구현하지 않는다(단일 출처).
* 해석 불가/미등록 타입은 ``None``(부재)이다. 없는 값을 기본값으로 지어내면 "이 배달은
  전송되지 않았다" 같은 오독이 감사 기록으로 굳는다.
* degrade 는 조용해서는 안 된다: 어떤 event_type / 모델에서 몇 건의 오류로 실패했는지
  ``logger.warning`` 으로 남긴다. **payload 원문은 로그에 남기지 않는다**(마스킹된
  target/chat id 가 들어 있고, §8 로깅 금지 대상에 인접하다).
* 집계 분모를 유지해야 하는 소비처(운영 리포트의 배달 시도 수)는 호출부에서
  ``load_... or Persisted...()`` 로 **빈 모델 degrade** 를 명시한다. 어느 쪽 정책인지를
  이 모듈이 숨기지 않고 호출부에 남긴다.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.schemas.analytics_events import (
    PERSISTED_EVENT_MODEL_BY_TYPE,
    AnalyticsEventPayload,
    PersistedAnalyticsEvent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "dump_analytics_event",
    "load_analytics_event",
    "load_analytics_event_as",
]

_PersistedT = TypeVar("_PersistedT", bound=BaseModel)

# 디코딩에 성공했지만 키가 하나도 없는 행의 원문. 디코더는 손상 행과 빈 매핑을 똑같이
# ``{}`` 로 접어 돌려주므로, 이 값 집합으로 둘을 가른다 — 빈 매핑은 손상이 아니라 "기록된
# 키가 없음"이라 경고 대상이 아니다(운영 로그에 거짓 ``reason=decode`` 를 남기지 않는다).
# 두 생산 경로 모두 정확히 이 문자열을 낸다: ``model_dump_json()`` 과 legacy ``str({})``.
_EMPTY_MAPPING_TEXTS: frozenset[str] = frozenset({"{}"})


def dump_analytics_event(payload: AnalyticsEventPayload) -> str:
    """이벤트 payload 를 ``Analytics.event_data`` 문자열로 직렬화한다.

    키 순서는 모델의 필드 선언 순서다. 종전 ``json.dumps`` 산출과 공백만 다르고 파싱
    동치가 되도록 모델은 기존 dict 리터럴과 같은 순서로 필드를 선언한다.
    """
    return payload.model_dump_json()


def _warn_degraded(event_type: str, model: type[BaseModel], *, reason: str) -> None:
    """degrade 된 행을 추적 가능하게 남긴다(payload 원문은 로그에 넣지 않는다)."""
    logger.warning(
        "analytics event_data 해석 실패 — 이벤트를 부재로 처리 "
        "(event_type=%s, model=%s, reason=%s)",
        event_type or "unknown",
        model.__name__,
        reason,
    )


def load_analytics_event_as(
    raw: str | None,
    *,
    model: type[_PersistedT],
    event_type: str = "",
) -> _PersistedT | None:
    """저장된 payload 를 지정한 복원 모델로 되읽는다. 해석 불가면 ``None``.

    두 갈래 degrade 를 **모두** 경고로 남긴다: 텍스트는 있는데 디코딩되지 않는 손상 행
    (``reason=decode``)과, 디코딩은 되지만 계약을 어긴 행(``reason=schema``). 정상적인
    부재 — 값이 아예 없는 행(``None``/빈 문자열)과 디코딩은 됐지만 키가 없는 빈 매핑
    (``"{}"``) — 만 조용히 넘어간다. 손상 행이 조용히 사라지면 판정이 왜 달라졌는지
    사후에 알 수 없고, 반대로 멀쩡한 빈 행에 경고를 남기면 진짜 손상이 묻힌다.

    ``event_type`` 은 경고 로그에서 어떤 이벤트가 degrade 됐는지 특정하기 위한 것이다.
    """
    # 지연 import: 디코더는 ``decision_analytics`` 패키지 안에 있고 그 패키지의
    # ``__init__`` 이 KPI 믹스인을 끌어오는데, 그 믹스인이 다시 이 모듈을 쓴다
    # (import 시점 순환). 함수 시점 import 는 sys.modules 캐시를 타므로 비용이 없고,
    # 디코딩 단일 출처(legacy repr 복구 포함)는 그대로 유지된다.
    from app.services.decision_analytics.events import parse_analytics_event_data

    mapping = parse_analytics_event_data(raw)
    if not mapping:
        text = (raw or "").strip()
        if text and text not in _EMPTY_MAPPING_TEXTS:
            _warn_degraded(event_type, model, reason="decode")
        return None
    try:
        return model.model_validate(mapping)
    except ValidationError as exc:
        _warn_degraded(event_type, model, reason=f"schema(errors={exc.error_count()})")
        return None


def load_analytics_event(
    raw: str | None,
    *,
    event_type: str,
) -> PersistedAnalyticsEvent | None:
    """event_type 레지스트리로 복원 모델을 골라 되읽는다.

    계약이 선언되지 않은 event_type 은 ``None`` 이다 — 임의 dict 를 흘려보내 소비처가
    ``.get()`` 으로 짐작하는 종전 경로로 되돌아가지 않게, 미등록은 명시적 부재로 만든다.
    """
    model = PERSISTED_EVENT_MODEL_BY_TYPE.get(event_type)
    if model is None:
        return None
    return load_analytics_event_as(raw, model=model, event_type=event_type)
