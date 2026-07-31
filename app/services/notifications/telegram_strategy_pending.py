"""전략 편집 진행 상태(pending edit)의 영속 경계.

Telegram 웹훅은 어느 API 워커로도 들어올 수 있어, 진행 중인 편집 단계를 프로세스
메모리에만 두면 다음 메시지를 다른 워커가 받는 순간 상태가 사라진다. 그래서 단계 상태를
``analytics`` 행(``telegram.strategy.pending_edit``)으로도 남긴다.

이 모듈은 그 **DB 왕복만** 담당한다(§4.5-5 얇은 경계):

* 인메모리 캐시(``PENDING_EDITS``)와 "살아 있는 편집인가" 판단은 프로세서가 소유한다.
* 여기서는 최신 레코드 복원과 기록만 하고, 직렬화/복원은
  :mod:`app.services.analytics_event_payload` 단일 경로를 쓴다(payload 계약은
  :mod:`app.schemas.analytics_events`).

``telegram_strategy_fields`` / ``_parsing`` / ``_render`` 와 같은 sibling 분해다 —
``telegram_strategy.py`` 는 명령 오케스트레이션만 남긴다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.constants import TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE
from app.core.single_user import ensure_operator_account
from app.models.models import Analytics
from app.schemas.analytics_events import (
    PersistedTelegramStrategyPendingEditEvent,
    TelegramStrategyPendingEditEvent,
)
from app.services.analytics_event_payload import (
    dump_analytics_event,
    load_analytics_event_as,
)

__all__ = [
    "PENDING_EVENT_FETCH_LIMIT",
    "PENDING_EVENT_TYPE",
    "load_latest_pending_edit",
    "record_pending_edit",
]

PENDING_EVENT_TYPE = TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE
# 최신 상태만 필요하지만 이 event_type 은 모든 chat 이 공유하는 append-only 스트림이라
# 한 chat 의 최신 행을 찾기 위해 최근 N 행을 훑는다(§4.5-1: 상한은 함수 밖에 선언).
PENDING_EVENT_FETCH_LIMIT = 100


def load_latest_pending_edit(
    db: Session,
    *,
    chat_key: str,
) -> PersistedTelegramStrategyPendingEditEvent | None:
    """이 chat 의 최신 pending edit 레코드를 되읽는다.

    해당 chat 의 행이 없으면 ``None`` — 호출부는 그때 인메모리 캐시로 폴백한다.

    **해석 불가 행을 만나면 건너뛰지 않고 즉시 ``None`` 을 돌려준다.** 이 스트림은 모든
    chat 이 공유하는 append-only 로그이고, 해석할 수 없는 행에는 chat_id 가 없어 그것이 이
    chat 의 **해제 행**이었는지 알 수 없다. 건너뛰면 그 아래의 오래된 ``active: true`` 행을
    최신 상태로 오해해 **이미 끝난 편집 단계를 부활**시키고, 다음 메시지를 그 단계의 값으로
    해석해 전략을 의도치 않게 바꿀 수 있다(쓰기 부작용). 상태를 모를 때는 "staged 편집
    없음"이 안전한 쪽이다 — 운영자는 명령을 다시 시작하면 되고 같은 워커에서는 인메모리
    캐시가 계속 동작한다. 해석 불가 행은 로더가 경고로 남긴다.
    """
    rows = (
        db.query(Analytics)
        .filter(Analytics.event_type == PENDING_EVENT_TYPE)
        .order_by(Analytics.timestamp.desc(), Analytics.id.desc())
        .limit(PENDING_EVENT_FETCH_LIMIT)
        .all()
    )
    for row in rows:
        payload = load_analytics_event_as(
            row.event_data,
            model=PersistedTelegramStrategyPendingEditEvent,
            event_type=PENDING_EVENT_TYPE,
        )
        if payload is None:
            return None
        if payload.chat_id != chat_key:
            continue
        return payload
    return None


def record_pending_edit(db: Session, payload: TelegramStrategyPendingEditEvent) -> None:
    """편집 단계 적재/해제를 내부 텔레메트리로 기록한다.

    운영자 활동 카운트에는 들어가지 않는다(``INTERNAL_TELEMETRY_EVENT_TYPES``).
    """
    operator = ensure_operator_account(db)
    db.add(
        Analytics(
            user_id=operator.id,
            event_type=PENDING_EVENT_TYPE,
            event_data=dump_analytics_event(payload),
        )
    )
    db.commit()
