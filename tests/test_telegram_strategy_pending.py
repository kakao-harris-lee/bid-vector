"""전략 편집 pending 상태의 영속 경계 테스트 (복원 + degrade 정책).

이 스트림은 모든 chat 이 공유하는 append-only 로그라, 되읽기 실패를 어떻게 다루는지가
**전략 쓰기 부작용**과 직결된다(끝난 편집 단계를 부활시키면 다음 메시지가 그 단계의 값으로
해석된다). 그래서 정상 복원뿐 아니라 손상 행 정책까지 의도로 고정한다.
"""

from __future__ import annotations

import logging

from app.core.constants import TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE
from app.core.single_user import ensure_operator_account
from app.models.models import Analytics
from app.schemas.analytics_events import (
    TelegramStrategyPendingEditActivated,
    TelegramStrategyPendingEditCleared,
)
from app.services.notifications import telegram_strategy_pending as pending_store

CHAT_KEY = "1594710346"


def _seed_raw_pending_row(test_db, *, raw: str) -> Analytics:
    """Append a hand-crafted (corrupted) pending-edit row."""
    operator = ensure_operator_account(test_db)
    row = Analytics(
        user_id=operator.id,
        event_type=TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
        event_data=raw,
    )
    test_db.add(row)
    test_db.commit()
    return row


def test_activated_row_round_trips_through_the_declared_contract(test_db):
    """정상 경로: 적재 행은 필드 그대로 복원된다."""
    pending_store.record_pending_edit(
        test_db,
        TelegramStrategyPendingEditActivated(
            chat_id=CHAT_KEY,
            field_key="min_priority_score",
            stage="awaiting_value",
            updates={"min_priority_score": 0.7},
        ),
    )

    restored = pending_store.load_latest_pending_edit(test_db, chat_key=CHAT_KEY)

    assert restored is not None
    assert restored.active is True
    assert restored.field_key == "min_priority_score"
    assert restored.stage == "awaiting_value"
    assert restored.updates == {"min_priority_score": 0.7}


def test_other_chats_rows_are_skipped(test_db):
    """다른 chat 의 최신 행은 이 chat 의 상태가 아니다."""
    pending_store.record_pending_edit(
        test_db,
        TelegramStrategyPendingEditActivated(
            chat_id=CHAT_KEY,
            field_key="min_priority_score",
            stage="awaiting_value",
        ),
    )
    pending_store.record_pending_edit(
        test_db, TelegramStrategyPendingEditCleared(chat_id="999999")
    )

    restored = pending_store.load_latest_pending_edit(test_db, chat_key=CHAT_KEY)

    assert restored is not None
    assert restored.chat_id == CHAT_KEY
    assert restored.active is True


def test_corrupted_row_degrades_to_no_pending_edit(test_db, caplog):
    """손상 행은 경고와 함께 '편집 없음'으로 내려간다."""
    _seed_raw_pending_row(test_db, raw="{not json at all")

    with caplog.at_level(logging.WARNING):
        restored = pending_store.load_latest_pending_edit(test_db, chat_key=CHAT_KEY)

    assert restored is None
    assert "analytics event_data 해석 실패" in caplog.text


def test_corrupted_latest_row_does_not_resurrect_an_older_active_edit(test_db):
    """손상된 최신 행을 건너뛰고 오래된 ``active`` 행으로 되돌아가지 않는다.

    손상 행에는 chat_id 가 없어 그것이 이 chat 의 해제 행이었는지 알 수 없다. 건너뛰면
    이미 끝난 편집 단계가 부활해 다음 메시지가 그 단계의 값으로 해석되고, 운영자가
    의도하지 않은 전략 변경이 적용될 수 있다. 상태 불명은 "편집 없음"으로 다룬다.
    """
    pending_store.record_pending_edit(
        test_db,
        TelegramStrategyPendingEditActivated(
            chat_id=CHAT_KEY,
            field_key="min_priority_score",
            stage="awaiting_value",
        ),
    )
    _seed_raw_pending_row(test_db, raw="{not json at all")

    assert pending_store.load_latest_pending_edit(test_db, chat_key=CHAT_KEY) is None


def test_cleared_row_is_returned_as_inactive_state(test_db):
    """해제 행은 (부재가 아니라) 비활성 상태로 복원돼 호출부가 캐시를 비운다."""
    pending_store.record_pending_edit(
        test_db,
        TelegramStrategyPendingEditActivated(
            chat_id=CHAT_KEY,
            field_key="min_priority_score",
            stage="awaiting_value",
        ),
    )
    pending_store.record_pending_edit(
        test_db, TelegramStrategyPendingEditCleared(chat_id=CHAT_KEY)
    )

    restored = pending_store.load_latest_pending_edit(test_db, chat_key=CHAT_KEY)

    assert restored is not None
    assert restored.active is False
    assert restored.field_key is None
