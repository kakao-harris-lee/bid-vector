"""Recoverable inference outbox processing for similarity projections."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import InferenceOutboxEvent, Project
from app.schemas.similarity_runtime import (
    InferenceOutboxFailure,
    InferenceOutboxProcessedEvent,
    InferenceOutboxProcessResult,
    SimilarityProjectionResult,
)
from app.services.similarity_read_model import (
    SIMILARITY_READ_MODEL_LIMIT,
    SIMILARITY_READ_MODEL_MIN_SIMILARITY,
    ProjectSimilarityReadModelService,
)


INFERENCE_OUTBOX_EVENT_EMBEDDING_READY = "embedding.ready"
INFERENCE_OUTBOX_STATUS_PENDING = "pending"
INFERENCE_OUTBOX_STATUS_RUNNING = "running"
INFERENCE_OUTBOX_STATUS_COMPLETED = "completed"
INFERENCE_OUTBOX_STATUS_FAILED = "failed"


class InferenceOutboxService:
    """Append, claim, retry and recover inference projection events."""

    def __init__(self, read_model: ProjectSimilarityReadModelService) -> None:
        self._read_model = read_model

    def append_embedding_ready_event(
        self,
        db: Session,
        project: Project,
    ) -> InferenceOutboxEvent | None:
        if not self._embedding_is_ready(project):
            return None
        dedupe_key = self._embedding_dedupe_key(project)
        existing = (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.event_type
                == INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
                InferenceOutboxEvent.aggregate_type == "project",
                InferenceOutboxEvent.aggregate_id == int(project.id),
                InferenceOutboxEvent.dedupe_key == dedupe_key,
            )
            .first()
        )
        if existing is not None:
            return self._reactivate_failed(existing)
        return self._insert_embedding_ready_event(db, project, dedupe_key)

    def _insert_embedding_ready_event(
        self, db: Session, project: Project, dedupe_key: str
    ) -> InferenceOutboxEvent | None:
        now = utc_now()
        event = InferenceOutboxEvent(
            event_type=INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
            aggregate_type="project",
            aggregate_id=int(project.id),
            dedupe_key=dedupe_key,
            payload_json={
                "same_category_only": True,
                "min_similarity": SIMILARITY_READ_MODEL_MIN_SIMILARITY,
                "limit": SIMILARITY_READ_MODEL_LIMIT,
            },
            status=INFERENCE_OUTBOX_STATUS_PENDING,
            attempts=0,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        return self._flush_new_event(db, event, project, dedupe_key)

    def _flush_new_event(
        self,
        db: Session,
        event: InferenceOutboxEvent,
        project: Project,
        dedupe_key: str,
    ) -> InferenceOutboxEvent | None:
        savepoint = db.begin_nested()
        try:
            db.add(event)
            db.flush()
            savepoint.commit()
            return event
        except IntegrityError:
            savepoint.rollback()
            concurrent = db.query(InferenceOutboxEvent).filter(
                InferenceOutboxEvent.event_type
                == INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
                InferenceOutboxEvent.aggregate_type == "project",
                InferenceOutboxEvent.aggregate_id == int(project.id),
                InferenceOutboxEvent.dedupe_key == dedupe_key,
            ).first()
            return self._reactivate_failed(concurrent) if concurrent else None

    def process(self, db: Session, *, limit: int = 50) -> InferenceOutboxProcessResult:
        recovered = self.recover_stale_claims(db)
        rows = self._pending_rows(db, limit)
        processed: list[InferenceOutboxProcessedEvent] = []
        failures: list[InferenceOutboxFailure] = []
        skipped = 0
        for row in rows:
            if not self._claim(db, int(row.id)):
                skipped += 1
                continue
            try:
                current = db.get(InferenceOutboxEvent, int(row.id))
                if current is None:
                    skipped += 1
                    continue
                result = self._process_event(db, current)
                self._mark_completed(current)
                db.commit()
                processed.append(
                    InferenceOutboxProcessedEvent(event_id=current.id, result=result)
                )
            except Exception as exc:  # noqa: BLE001 - one event must not stop the sweep
                db.rollback()
                permanently_failed = self._reschedule_or_fail(db, int(row.id), str(exc))
                if permanently_failed:
                    failures.append(
                        InferenceOutboxFailure(event_id=int(row.id), error=str(exc))
                    )
                else:
                    skipped += 1
        return InferenceOutboxProcessResult(
            processed_count=len(processed),
            failed_count=len(failures),
            skipped_count=skipped,
            recovered_count=recovered,
            event_ids=[item.event_id for item in processed],
            failed_event_ids=[item.event_id for item in failures],
            results=processed,
            failures=failures,
        )

    def recover_stale_claims(self, db: Session) -> int:
        cutoff = utc_now() - timedelta(
            seconds=max(1, int(settings.INFERENCE_OUTBOX_LOCK_TIMEOUT_SECONDS))
        )
        rows = (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.status == INFERENCE_OUTBOX_STATUS_RUNNING,
                InferenceOutboxEvent.locked_at <= cutoff,
            )
            .all()
        )
        now = utc_now()
        for row in rows:
            exhausted = int(row.attempts or 0) >= settings.INFERENCE_OUTBOX_MAX_ATTEMPTS
            row.status = (
                INFERENCE_OUTBOX_STATUS_FAILED
                if exhausted
                else INFERENCE_OUTBOX_STATUS_PENDING
            )
            row.available_at = now
            row.locked_at = None
            row.updated_at = now
            row.last_error = "stale claim recovered after worker interruption"
        if rows:
            db.commit()
        return len(rows)

    def _pending_rows(self, db: Session, limit: int) -> list[InferenceOutboxEvent]:
        return (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.status == INFERENCE_OUTBOX_STATUS_PENDING,
                InferenceOutboxEvent.available_at <= utc_now(),
            )
            .order_by(InferenceOutboxEvent.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )

    def _claim(self, db: Session, event_id: int) -> bool:
        now = utc_now()
        updated = (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.id == event_id,
                InferenceOutboxEvent.status == INFERENCE_OUTBOX_STATUS_PENDING,
            )
            .update(
                {
                    "status": INFERENCE_OUTBOX_STATUS_RUNNING,
                    "locked_at": now,
                    "updated_at": now,
                    "attempts": InferenceOutboxEvent.attempts + 1,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return bool(updated)

    def _process_event(
        self,
        db: Session,
        event: InferenceOutboxEvent,
    ) -> SimilarityProjectionResult:
        if event.event_type != INFERENCE_OUTBOX_EVENT_EMBEDDING_READY:
            raise ValueError(f"unsupported event_type {event.event_type}")
        payload = event.payload_json or {}
        return self._read_model.recompute(
            db,
            project_id=int(event.aggregate_id),
            same_category_only=bool(payload.get("same_category_only", True)),
            min_similarity=float(
                payload.get("min_similarity", SIMILARITY_READ_MODEL_MIN_SIMILARITY)
            ),
            limit=int(payload.get("limit", SIMILARITY_READ_MODEL_LIMIT)),
        )

    def _reschedule_or_fail(self, db: Session, event_id: int, error: str) -> bool:
        row = db.get(InferenceOutboxEvent, event_id)
        if row is None:
            return True
        attempts = int(row.attempts or 0)
        exhausted = attempts >= max(1, int(settings.INFERENCE_OUTBOX_MAX_ATTEMPTS))
        row.status = (
            INFERENCE_OUTBOX_STATUS_FAILED
            if exhausted
            else INFERENCE_OUTBOX_STATUS_PENDING
        )
        row.available_at = utc_now() + timedelta(seconds=self._retry_delay(attempts))
        row.locked_at = None
        row.last_error = error[:2000]
        row.updated_at = utc_now()
        db.commit()
        return exhausted

    def _retry_delay(self, attempts: int) -> int:
        base = max(1, int(settings.INFERENCE_OUTBOX_RETRY_BASE_SECONDS))
        return min(base * (2 ** max(0, attempts - 1)), 300)

    def _mark_completed(self, event: InferenceOutboxEvent) -> None:
        event.status = INFERENCE_OUTBOX_STATUS_COMPLETED
        event.processed_at = utc_now()
        event.locked_at = None
        event.updated_at = utc_now()
        event.last_error = None

    def _embedding_is_ready(self, project: Project) -> bool:
        return bool(
            project.id is not None
            and project.embedding_updated_at is not None
            and project.embedding_model
            and self._read_model.embedding_state(project).vector
        )

    def _embedding_dedupe_key(self, project: Project) -> str:
        updated_at = project.embedding_updated_at
        return f"{project.embedding_model}:{updated_at.isoformat() if updated_at else 'missing'}"

    def _reactivate_failed(
        self,
        event: InferenceOutboxEvent,
    ) -> InferenceOutboxEvent | None:
        if event.status == INFERENCE_OUTBOX_STATUS_COMPLETED:
            return None
        if event.status == INFERENCE_OUTBOX_STATUS_FAILED:
            event.status = INFERENCE_OUTBOX_STATUS_PENDING
            event.attempts = 0
            event.available_at = utc_now()
            event.locked_at = None
            event.last_error = None
            event.updated_at = utc_now()
        return event
