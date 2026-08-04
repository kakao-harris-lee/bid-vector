"""Recoverable inference outbox processing for similarity projections."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Callable

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import ensure_utc, utc_now
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
    project_embedding_input_state,
)


INFERENCE_OUTBOX_EVENT_SEMANTIC_INPUT_CHANGED = "semantic_input.changed"
INFERENCE_OUTBOX_EVENT_EMBEDDING_READY = "embedding.ready"
INFERENCE_OUTBOX_EMBEDDING_TEXT_HASH_KEY = "embedding_semantic_text_sha256"
INFERENCE_OUTBOX_STATUS_PENDING = "pending"
INFERENCE_OUTBOX_STATUS_RUNNING = "running"
INFERENCE_OUTBOX_STATUS_COMPLETED = "completed"
INFERENCE_OUTBOX_STATUS_FAILED = "failed"
INFERENCE_OUTBOX_ACTIVE_STATUSES = frozenset(
    {INFERENCE_OUTBOX_STATUS_PENDING, INFERENCE_OUTBOX_STATUS_RUNNING}
)
EMBEDDING_PAYLOAD_ADAPTER = TypeAdapter(list[float])


class InferenceOutboxService:
    """Append, claim, retry and recover inference projection events."""

    def __init__(
        self,
        read_model: ProjectSimilarityReadModelService | None = None,
        embedding_refresher: Callable[..., tuple[list[float], str]] | None = None,
    ) -> None:
        self._read_model = read_model
        self._embedding_refresher = embedding_refresher

    def append_semantic_input_changed_event(
        self,
        db: Session,
        project: Project,
    ) -> InferenceOutboxEvent:
        """Stage a source-neutral embedding request in the caller's transaction."""
        dedupe_key = self._semantic_input_dedupe_key(project)
        return self._append_event(
            db,
            event_type=INFERENCE_OUTBOX_EVENT_SEMANTIC_INPUT_CHANGED,
            project=project,
            dedupe_key=dedupe_key,
            payload={"input_fingerprint": dedupe_key},
            reactivate_completed=True,
        )

    def ensure_semantic_input_changed_event(
        self,
        db: Session,
        project: Project,
        *,
        semantic_input_changed: bool,
    ) -> InferenceOutboxEvent | None:
        """Ensure current semantic input has recoverable embedding work.

        Identical collection input is not automatically a no-op: legacy rows
        can be vectorless, and a previously exhausted event must be recoverable.
        An active matching row already represents the work, while a completed
        matching row is satisfied only when its stored embedding is healthy.
        """
        dedupe_key = self._semantic_input_dedupe_key(project)
        existing = self._find_event(
            db,
            event_type=INFERENCE_OUTBOX_EVENT_SEMANTIC_INPUT_CHANGED,
            project_id=int(project.id),
            dedupe_key=dedupe_key,
        )
        if existing is not None and existing.status in INFERENCE_OUTBOX_ACTIVE_STATUSES:
            return None
        embedding_healthy = existing is not None and self._stored_embedding_is_healthy(
            project, existing
        )
        requires_work = (
            semantic_input_changed
            or existing is None
            or existing.status == INFERENCE_OUTBOX_STATUS_FAILED
            or not embedding_healthy
        )
        if not requires_work:
            return None
        return self._append_event(
            db,
            event_type=INFERENCE_OUTBOX_EVENT_SEMANTIC_INPUT_CHANGED,
            project=project,
            dedupe_key=dedupe_key,
            payload={"input_fingerprint": dedupe_key},
            reactivate_completed=True,
        )

    def append_embedding_ready_event(
        self,
        db: Session,
        project: Project,
        *,
        reactivate_completed: bool = False,
    ) -> InferenceOutboxEvent | None:
        if not self._embedding_is_ready(project):
            return None
        dedupe_key = self._embedding_dedupe_key(project)
        event = self._append_event(
            db,
            event_type=INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
            project=project,
            dedupe_key=dedupe_key,
            payload={
                "same_category_only": True,
                "min_similarity": SIMILARITY_READ_MODEL_MIN_SIMILARITY,
                "limit": SIMILARITY_READ_MODEL_LIMIT,
            },
            reactivate_completed=reactivate_completed,
        )
        return None if event.status == INFERENCE_OUTBOX_STATUS_COMPLETED else event

    def _append_event(
        self,
        db: Session,
        *,
        event_type: str,
        project: Project,
        dedupe_key: str,
        payload: dict[str, bool | float | int | str],
        reactivate_completed: bool = False,
    ) -> InferenceOutboxEvent:
        existing = self._find_event(
            db,
            event_type=event_type,
            project_id=int(project.id),
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return self._reactivate(
                existing, reactivate_completed=reactivate_completed
            )
        now = utc_now()
        event = InferenceOutboxEvent(
            event_type=event_type,
            aggregate_type="project",
            aggregate_id=int(project.id),
            dedupe_key=dedupe_key,
            payload_json=payload,
            status=INFERENCE_OUTBOX_STATUS_PENDING,
            attempts=0,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        return self._flush_new_event(
            db,
            event,
            project,
            dedupe_key,
            reactivate_completed=reactivate_completed,
        )

    def _flush_new_event(
        self,
        db: Session,
        event: InferenceOutboxEvent,
        project: Project,
        dedupe_key: str,
        *,
        reactivate_completed: bool,
    ) -> InferenceOutboxEvent:
        savepoint = db.begin_nested()
        try:
            db.add(event)
            db.flush()
            savepoint.commit()
            return event
        except IntegrityError:
            savepoint.rollback()
            concurrent = self._find_event(
                db,
                event_type=str(event.event_type),
                project_id=int(project.id),
                dedupe_key=dedupe_key,
            )
            if concurrent is None:
                raise
            return self._reactivate(
                concurrent, reactivate_completed=reactivate_completed
            )

    def _find_event(
        self,
        db: Session,
        *,
        event_type: str,
        project_id: int,
        dedupe_key: str,
    ) -> InferenceOutboxEvent | None:
        return (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.event_type == event_type,
                InferenceOutboxEvent.aggregate_type == "project",
                InferenceOutboxEvent.aggregate_id == project_id,
                InferenceOutboxEvent.dedupe_key == dedupe_key,
            )
            .first()
        )

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
        handlers = {
            INFERENCE_OUTBOX_EVENT_SEMANTIC_INPUT_CHANGED: self._refresh_embedding,
            INFERENCE_OUTBOX_EVENT_EMBEDDING_READY: self._recompute_projection,
        }
        handler = handlers.get(str(event.event_type))
        if handler is None:
            raise ValueError(f"unsupported event_type {event.event_type}")
        return handler(db, event)

    def _refresh_embedding(
        self,
        db: Session,
        event: InferenceOutboxEvent,
    ) -> SimilarityProjectionResult:
        project = db.get(Project, int(event.aggregate_id))
        if project is None:
            return SimilarityProjectionResult(
                status="skipped",
                project_id=int(event.aggregate_id),
                reason="project_not_found",
            )
        if event.dedupe_key != self._semantic_input_dedupe_key(project):
            return SimilarityProjectionResult(
                status="skipped",
                project_id=int(project.id),
                reason="superseded_semantic_input",
            )
        if self._embedding_refresher is None:
            raise RuntimeError("semantic input event requires an embedding refresher")
        self._embedding_refresher(db, project, force=False)
        db.flush()
        payload = dict(event.payload_json or {})
        payload[INFERENCE_OUTBOX_EMBEDDING_TEXT_HASH_KEY] = self._semantic_text_hash(
            project
        )
        event.payload_json = payload
        ready_event = self.append_embedding_ready_event(db, project)
        if ready_event is not None:
            db.flush()
        return SimilarityProjectionResult(
            status="skipped",
            project_id=int(project.id),
            reason=(
                f"embedding_ready_event:{int(ready_event.id)}"
                if ready_event is not None
                else "embedding_ready_event_deduplicated"
            ),
        )

    def _recompute_projection(
        self,
        db: Session,
        event: InferenceOutboxEvent,
    ) -> SimilarityProjectionResult:
        if self._read_model is None:
            raise RuntimeError("embedding-ready event requires a similarity read model")
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
        if self._read_model is None:
            raise RuntimeError("embedding readiness requires a similarity read model")
        return bool(
            project.id is not None
            and project.embedding_updated_at is not None
            and project.embedding_model
            and self._read_model.embedding_state(project).vector
        )

    def _stored_embedding_is_healthy(
        self,
        project: Project,
        event: InferenceOutboxEvent,
    ) -> bool:
        payload = str(project.embedding_payload or "").strip()
        try:
            vector = EMBEDDING_PAYLOAD_ADAPTER.validate_json(payload) if payload else []
        except ValidationError:
            vector = []
        stored_text_hash = (event.payload_json or {}).get(
            INFERENCE_OUTBOX_EMBEDDING_TEXT_HASH_KEY
        )
        return bool(
            vector
            and project.embedding_model
            and project.embedding_updated_at is not None
            and str(project.semantic_text or "").strip()
            and stored_text_hash == self._semantic_text_hash(project)
        )

    def _semantic_text_hash(self, project: Project) -> str:
        semantic_text = str(project.semantic_text or "").strip()
        return hashlib.sha256(semantic_text.encode("utf-8")).hexdigest()

    def _embedding_dedupe_key(self, project: Project) -> str:
        updated_at = project.embedding_updated_at
        return f"{project.embedding_model}:{updated_at.isoformat() if updated_at else 'missing'}"

    def _semantic_input_dedupe_key(self, project: Project) -> str:
        input_state = project_embedding_input_state(project)
        state = input_state.model_dump(mode="json")
        if project.deadline is not None:
            state["deadline"] = ensure_utc(project.deadline).isoformat()
        serialized = type(input_state).model_validate(state).model_dump_json()
        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"semantic:{fingerprint}"

    def _reactivate(
        self,
        event: InferenceOutboxEvent,
        *,
        reactivate_completed: bool,
    ) -> InferenceOutboxEvent:
        if event.status == INFERENCE_OUTBOX_STATUS_FAILED or (
            reactivate_completed
            and event.status == INFERENCE_OUTBOX_STATUS_COMPLETED
        ):
            event.status = INFERENCE_OUTBOX_STATUS_PENDING
            event.attempts = 0
            event.available_at = utc_now()
            event.locked_at = None
            event.processed_at = None
            event.last_error = None
            event.updated_at = utc_now()
        return event
