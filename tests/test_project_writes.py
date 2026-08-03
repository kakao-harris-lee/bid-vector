"""Atomic manual-project writes through the durable inference outbox."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from app.api import projects as projects_api
from app.core.time import utc_now
from app.models.models import InferenceOutboxEvent, Project
from app.schemas.project import ProjectCreate
from app.services.project_similarity import ProjectSimilarityService
from app.services.project_writes import ProjectWriteService, get_project_write_service
from app.tasks import inference_jobs, jobs


def _project_input(*, title: str = "수동 등록 공고") -> ProjectCreate:
    return ProjectCreate(
        title=title,
        description="데이터 플랫폼 구축",
        requirements="분석 대시보드와 운영 지원",
        budget_estimate=100_000_000.0,
        budget_min=90_000_000.0,
        budget_max=110_000_000.0,
        category="software",
        notice_number="MANUAL-OUTBOX-1",
        source_url="https://example.com/manual/1",
        issuing_agency="조달청",
        demand_agency="서울특별시",
    )


def _seed_embedded_project(test_db) -> Project:
    project = Project(**_project_input().model_dump())
    project.semantic_text = ProjectSimilarityService().build_semantic_text(project)
    project.embedding_payload = "[0.1, 0.2]"
    project.embedding_model = "test-model"
    project.embedding_updated_at = utc_now()
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _raise_notification(event_ids: list[int]) -> None:
    raise RuntimeError(f"notification unavailable for {event_ids}")


class _RecordingOutbox:
    def __init__(self) -> None:
        self.semantic_change_flags: list[bool] = []

    def ensure_semantic_input_changed_event(
        self,
        db: Session,
        project: Project,
        *,
        semantic_input_changed: bool,
    ) -> InferenceOutboxEvent | None:
        del db, project
        self.semantic_change_flags.append(semantic_input_changed)
        return None


def test_create_commits_project_and_current_input_event_before_notification(test_db):
    observed: list[list[int]] = []

    def _observe_committed(event_ids: list[int]) -> None:
        assert not test_db.in_transaction()
        observed.append(list(event_ids))

    created = ProjectWriteService(notify_committed=_observe_committed).create(
        test_db, _project_input()
    )

    event = test_db.query(InferenceOutboxEvent).one()
    assert observed == [[event.id]]
    assert event.event_type == "semantic_input.changed"
    assert event.aggregate_id == created.id
    assert event.status == "pending"
    assert event.payload_json == {"input_fingerprint": event.dedupe_key}
    assert created.semantic_text == ""
    assert created.embedding_payload == "[]"
    assert created.embedding_model is None
    assert created.embedding_updated_at is None


def test_update_commits_facts_invalidation_and_current_input_event_atomically(test_db):
    project = _seed_embedded_project(test_db)
    observed: list[list[int]] = []

    def _observe_committed(event_ids: list[int]) -> None:
        assert not test_db.in_transaction()
        observed.append(list(event_ids))

    updated = ProjectWriteService(notify_committed=_observe_committed).update(
        test_db,
        project_id=int(project.id),
        project_update=_project_input(title="수정된 수동 등록 공고"),
    )

    event = test_db.query(InferenceOutboxEvent).one()
    assert observed == [[event.id]]
    assert event.aggregate_id == updated.id
    assert event.payload_json == {"input_fingerprint": event.dedupe_key}
    assert updated.title == "수정된 수동 등록 공고"
    assert updated.embedding_payload == "[]"
    assert updated.embedding_model is None
    assert updated.embedding_updated_at is None


def test_unchanged_update_is_an_outbox_and_notification_noop(test_db):
    notified: list[list[int]] = []
    service = ProjectWriteService(
        notify_committed=lambda ids: notified.append(list(ids))
    )
    created = service.create(test_db, _project_input())
    notified.clear()

    updated = service.update(
        test_db,
        project_id=int(created.id),
        project_update=_project_input(),
    )

    assert updated.id == created.id
    assert notified == []
    assert test_db.query(InferenceOutboxEvent).count() == 1


def test_source_url_only_legacy_update_without_event_is_a_true_outbox_noop(test_db):
    project = _seed_embedded_project(test_db)
    notified: list[list[int]] = []

    updated = ProjectWriteService(
        notify_committed=lambda ids: notified.append(list(ids))
    ).update(
        test_db,
        project_id=int(project.id),
        project_update=_project_input().model_copy(
            update={"source_url": "https://example.com/manual/updated"}
        ),
    )

    assert updated.source_url == "https://example.com/manual/updated"
    assert updated.embedding_payload == "[0.1, 0.2]"
    assert updated.embedding_model == "test-model"
    assert updated.embedding_updated_at is not None
    assert notified == []
    assert test_db.query(InferenceOutboxEvent).count() == 0


def test_non_semantic_update_does_not_consult_recovery_outbox(test_db):
    project = _seed_embedded_project(test_db)
    outbox = _RecordingOutbox()
    notified: list[list[int]] = []

    ProjectWriteService(
        notify_committed=lambda ids: notified.append(list(ids)),
        outbox=outbox,
    ).update(
        test_db,
        project_id=int(project.id),
        project_update=_project_input().model_copy(
            update={"source_url": "https://example.com/manual/updated"}
        ),
    )

    assert outbox.semantic_change_flags == []
    assert notified == []


def test_notification_failure_keeps_committed_project_and_pending_event(test_db):
    created = ProjectWriteService(notify_committed=_raise_notification).create(
        test_db, _project_input()
    )

    test_db.expire_all()
    assert test_db.get(Project, int(created.id)) is not None
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.status == "pending"


def test_broker_enqueue_failure_keeps_periodic_sweep_recovery(test_db, monkeypatch):
    def _fail_enqueue(*, limit: int = 50):
        raise RuntimeError(f"broker unavailable at limit={limit}")

    monkeypatch.setattr(
        inference_jobs, "enqueue_inference_outbox_processing", _fail_enqueue
    )

    created = get_project_write_service().create(test_db, _project_input())

    assert test_db.get(Project, int(created.id)) is not None
    event = test_db.query(InferenceOutboxEvent).one()
    assert event.status == "pending"


def test_failed_update_commit_rolls_back_facts_invalidation_and_event(
    test_db, monkeypatch
):
    project = _seed_embedded_project(test_db)
    original_commit: Callable[[], None] = test_db.commit

    def _fail_commit() -> None:
        raise RuntimeError("simulated write commit failure")

    monkeypatch.setattr(test_db, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="simulated write commit failure"):
        ProjectWriteService(notify_committed=lambda ids: None).update(
            test_db,
            project_id=int(project.id),
            project_update=_project_input(title="롤백되어야 하는 제목"),
        )

    monkeypatch.setattr(test_db, "commit", original_commit)
    test_db.expire_all()
    unchanged = test_db.get(Project, int(project.id))
    assert unchanged is not None
    assert unchanged.title == "수동 등록 공고"
    assert unchanged.embedding_payload == "[0.1, 0.2]"
    assert unchanged.embedding_model == "test-model"
    assert unchanged.embedding_updated_at is not None
    assert test_db.query(InferenceOutboxEvent).count() == 0


def test_retried_current_input_update_is_idempotent(test_db):
    notified: list[list[int]] = []
    service = ProjectWriteService(
        notify_committed=lambda ids: notified.append(list(ids))
    )
    created = service.create(test_db, _project_input())
    notified.clear()
    changed = _project_input(title="재시도 대상 제목")

    service.update(test_db, project_id=int(created.id), project_update=changed)
    service.update(test_db, project_id=int(created.id), project_update=changed)

    events = (
        test_db.query(InferenceOutboxEvent)
        .order_by(InferenceOutboxEvent.id.asc())
        .all()
    )
    assert len(events) == 2
    assert notified == [[events[-1].id]]
    assert len({event.dedupe_key for event in events}) == 2


def test_manual_create_and_update_do_not_call_direct_embedding_tasks(
    client, monkeypatch
):
    def _fail_direct_task(*args, **kwargs):
        raise AssertionError(
            f"direct embedding task called: args={args}, kwargs={kwargs}"
        )

    monkeypatch.setattr(jobs, "enqueue_project_embedding_backfill", _fail_direct_task)
    monkeypatch.setattr(
        projects_api, "enqueue_project_embedding_refresh", _fail_direct_task
    )

    created = client.post(
        "/api/v1/projects/", json=_project_input().model_dump(mode="json")
    )
    assert created.status_code == 200

    updated = client.put(
        f"/api/v1/projects/{created.json()['id']}",
        json=_project_input(title="직접 태스크 없는 수정").model_dump(mode="json"),
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "직접 태스크 없는 수정"
