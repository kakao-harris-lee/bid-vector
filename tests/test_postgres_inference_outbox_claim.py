"""inference outbox 클레임의 상호배제 — 실제 Postgres 동시 세션.

배경
----
:meth:`app.services.inference_outbox.InferenceOutboxService._claim` 은
``UPDATE ... WHERE id = :id AND status = 'pending'`` 의 영향 행 수로 소유권을
정한다. 워커 여러 대가 같은 큐를 소비하므로, 같은 이벤트를 둘이 동시에 집으면
임베딩/프로젝션이 중복 실행된다.

SQLite 스위트는 이 계약을 검증할 수 없다. 파일 락 기반이라 동시 쓰기 세션이
"경합"이 아니라 "직렬화 실패"로 나타나고, 프로덕션이 실제로 쓰는 잠금 의미론
(Postgres 행 잠금 + READ COMMITTED 재평가)과 다르기 때문이다.

여기서는 독립 세션 2개(각자 커밋)로 순차 경합과 스레드 동시 경합을 모두 건다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.time import utc_now
from app.models.models import InferenceOutboxEvent, Project
from app.services.inference_outbox import (
    INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
    INFERENCE_OUTBOX_STATUS_PENDING,
    INFERENCE_OUTBOX_STATUS_RUNNING,
    InferenceOutboxService,
)

pytestmark = pytest.mark.postgres


def _seed_pending_event(session) -> int:
    project = Project(title="아웃박스 경합", category="construction", status="open")
    session.add(project)
    session.commit()
    session.refresh(project)

    now = utc_now()
    event = InferenceOutboxEvent(
        event_type=INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
        aggregate_type="project",
        aggregate_id=int(project.id),
        dedupe_key=f"contended:{project.id}",
        payload_json={"limit": 5},
        status=INFERENCE_OUTBOX_STATUS_PENDING,
        attempts=0,
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return int(event.id)


def test_second_session_cannot_claim_a_running_event(postgres_session_factory):
    """이미 클레임된 이벤트는 다른 세션이 다시 집지 못한다 (CAS)."""
    service = InferenceOutboxService()
    seeder = postgres_session_factory()
    event_id = _seed_pending_event(seeder)

    first = postgres_session_factory()
    second = postgres_session_factory()

    assert service._claim(first, event_id) is True
    assert service._claim(second, event_id) is False

    row = second.get(InferenceOutboxEvent, event_id)
    second.refresh(row)
    assert row.status == INFERENCE_OUTBOX_STATUS_RUNNING
    assert int(row.attempts) == 1


def test_concurrent_claims_elect_exactly_one_owner(postgres_session_factory):
    """두 세션이 동시에 같은 행을 집으면 정확히 한쪽만 성공한다.

    Postgres 는 두 번째 ``UPDATE`` 를 행 잠금에서 대기시키고, 잠금이 풀린 뒤
    ``status = 'pending'`` 술어를 다시 평가해 0행을 돌려준다. attempts 가 1 이라는
    것이 "핸들러가 한 번만 돈다"의 실제 근거다.
    """
    service = InferenceOutboxService()
    seeder = postgres_session_factory()
    event_id = _seed_pending_event(seeder)

    sessions = [postgres_session_factory(), postgres_session_factory()]

    with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        outcomes = list(
            pool.map(lambda session: service._claim(session, event_id), sessions)
        )

    assert sorted(outcomes) == [False, True]

    row = seeder.get(InferenceOutboxEvent, event_id)
    seeder.refresh(row)
    assert row.status == INFERENCE_OUTBOX_STATUS_RUNNING
    assert int(row.attempts) == 1


def test_claim_succeeds_again_after_stale_recovery(postgres_session_factory):
    """회수된(pending 으로 되돌린) 이벤트는 다시 클레임 가능해야 한다."""
    service = InferenceOutboxService()
    session = postgres_session_factory()
    event_id = _seed_pending_event(session)

    assert service._claim(session, event_id) is True
    row = session.get(InferenceOutboxEvent, event_id)
    row.status = INFERENCE_OUTBOX_STATUS_PENDING
    row.locked_at = None
    session.commit()

    assert service._claim(session, event_id) is True
    session.refresh(row)
    assert int(row.attempts) == 2
