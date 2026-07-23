"""온보딩 결정 감사(``onboarding_suggestions``) 이력 조회 — 읽기 전용 서비스.

apply 엔드포인트(:mod:`app.services.onboarding.apply`)가 남긴 append-only 감사
로그를 운영자가 되돌아보기 위한 조회 경계다. 이 모듈은 **쓰기를 하지 않는다** —
필터·정렬·페이지네이션만 수행하는 repository-style 읽기 서비스이며, DB 세션을 인자로
주입받는다(§4.7.3). 결정 로직(value 역직렬화)은 I/O 와 분리한 순수 함수다(§4.7.4).

per-operator/synthetic 격리: 조회는 항상 인자로 받은 ``operator`` 의 ``user_id`` 로만
스코프한다 — 다른 운영자/synthetic 계정의 감사 행이 절대 새지 않는다. 스코프 해석
(canonical fallback / 403 / 404)은 라우터의 ``resolve_read_operator`` 가 담당하고,
이 함수는 이미 해석된 operator 의 자기 행만 읽는다.

설계 규율:
- **매직값 금지**(§4.5.1): 페이지 기본/상한은 :data:`DEFAULT_HISTORY_LIMIT` /
  :data:`MAX_HISTORY_LIMIT` 로 선언해 라우터·스키마가 이 단일 출처를 참조한다.
- **status 검증은 선언 enum 재사용**(§4.5.2): 상태 필터는 apply 서비스의
  :class:`DecisionStatus` 단일 출처를 그대로 받는다(별도 화이트리스트 분기 없음).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import OnboardingSuggestion, User
from app.services.onboarding.apply import DecisionStatus

# 페이지네이션 기본/상한(선언 상수, §4.5.1). 라우터의 Query 검증(le/default)과 스키마가
# 이 단일 출처를 참조해 핸들러에 매직값을 두지 않는다.
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200

# 감사 value 의 복원 형태. apply 가 str/float/list[str] 를 JSON 텍스트로 직렬화하므로
# 역직렬화도 같은 union 으로 복원한다(요청/응답 계약 대칭, §4.6).
AuditValue = Union[str, float, list[str]]


@dataclass(frozen=True)
class AuditRecord:
    """감사 로그 한 행의 조회 표현(순수 데이터).

    ``value`` 는 저장된 JSON 텍스트를 원형(str/float/list[str])으로 복원한 값이다.
    감사 행은 불변이라 ``updated_at`` 이 없고 ``created_at`` 만 노출한다.
    """

    id: int
    field: str
    value: AuditValue
    status: str
    source: Optional[str]
    confidence: Optional[float]
    reason: Optional[str]
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "field": self.field,
            "value": self.value,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AuditHistoryPage:
    """한 페이지의 감사 레코드 + 필터 적용 후 전체 수(프론트 페이지네이션용)."""

    records: list[AuditRecord]
    total: int


def _deserialize_value(raw: str) -> AuditValue:
    """감사 value(JSON 텍스트)를 원형(str/float/list[str])으로 복원한다(순수, 안전 폴백).

    apply 는 ``json.dumps(value, ensure_ascii=False)`` 로 저장한다. 파싱 실패나 예상 밖
    타입(dict/None/bool)은 표시 안전을 위해 원문 문자열로 폴백한다 — 응답 union(str)에
    항상 든다. bool 은 감사 결정값이 아니므로 숫자로 승격하지 않고 원문을 유지한다.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, bool):
        return raw
    if isinstance(parsed, (int, float)):
        return float(parsed)
    if isinstance(parsed, str):
        return parsed
    return raw


def _clamp_limit(limit: int) -> int:
    """limit 를 [1, MAX] 로 정규화한다(직접 호출자 방어 — 라우터는 Query 로 이미 검증)."""
    return max(1, min(int(limit), MAX_HISTORY_LIMIT))


def list_onboarding_history(
    db: Session,
    *,
    operator: User,
    field: Optional[str] = None,
    status: Optional[DecisionStatus] = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
    offset: int = 0,
) -> AuditHistoryPage:
    """operator 스코프의 온보딩 감사 이력을 최신순(created_at DESC)으로 조회한다.

    - **per-operator 격리**: ``user_id == operator.id`` 로만 스코프해 다른 운영자/
      synthetic 행이 새지 않는다(이 함수의 유일한 스코프 축).
    - **선언 필터**(§4.5.2): ``field`` 는 정확 일치, ``status`` 는 :class:`DecisionStatus`
      값으로 equality 필터만 적용한다(분기 트리 없음). 둘 다 None 이면 전체.
    - **정렬**: ``created_at`` DESC + ``id`` DESC — 같은 apply 로 동시각에 들어온 행도
      결정적 newest-first 로 tie-break 한다.
    - **total**: 필터 적용 후 페이지네이션 이전 전체 수(프론트 페이지 이동용).
    """
    query = db.query(OnboardingSuggestion).filter(
        OnboardingSuggestion.user_id == operator.id
    )
    if field is not None:
        query = query.filter(OnboardingSuggestion.field == field)
    if status is not None:
        query = query.filter(OnboardingSuggestion.status == status.value)

    total = query.with_entities(func.count(OnboardingSuggestion.id)).scalar() or 0

    rows = (
        query.order_by(
            OnboardingSuggestion.created_at.desc(),
            OnboardingSuggestion.id.desc(),
        )
        .offset(max(0, int(offset)))
        .limit(_clamp_limit(limit))
        .all()
    )
    records = [
        AuditRecord(
            id=int(row.id),
            field=row.field,
            value=_deserialize_value(row.value),
            status=row.status,
            source=row.source,
            confidence=row.confidence,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return AuditHistoryPage(records=records, total=int(total))


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "MAX_HISTORY_LIMIT",
    "AuditValue",
    "AuditRecord",
    "AuditHistoryPage",
    "list_onboarding_history",
]
