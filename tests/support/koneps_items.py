"""수집 item DTO 빌더 — KONEPS 수집/영속화 테스트 공용.

``persistence`` / ``matching`` 의 소비 함수들은 원시 dict 대신
``KonepsCollectedItem`` 을 받는다(방어적 DTO Phase 3). 테스트가 기존처럼 dict 리터럴로
표본을 쓰되 승격 한 줄만 거치게 하는 얇은 헬퍼다.
"""

from __future__ import annotations

from typing import Any

from app.schemas.koneps_items import KonepsCollectedItem


def collected_item(**fields: Any) -> KonepsCollectedItem:
    """dict 표본을 수집 item DTO 로 승격한다(필수 필드 기본값 제공).

    ``notice_number`` / ``title`` / ``base_amount`` 는 DTO 필수 필드라서, 그 결손을
    검증하려는 테스트는 이 헬퍼를 쓰지 말고 ``KonepsCollectedItem`` 을 직접 구성한다.
    """
    payload: dict[str, Any] = {
        "notice_number": "TEST-NOTICE",
        "title": "테스트 공고",
        "base_amount": 0.0,
        **fields,
    }
    return KonepsCollectedItem.model_validate(payload)
