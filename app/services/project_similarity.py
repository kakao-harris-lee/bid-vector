"""Project embedding persistence and similar-project retrieval."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Iterable

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.core.vector import PGVECTOR_SQLALCHEMY_AVAILABLE
from app.models.models import InferenceOutboxEvent, Project, ProjectSimilarityEdge
from app.services.classifier import NoticeClassifierService

PROJECT_VECTOR_DIMENSIONS = 384
FALLBACK_EMBEDDING_MODEL = "fallback-hash-v1"
SIMILARITY_READ_MODEL_LIMIT = 20
SIMILARITY_READ_MODEL_MIN_SIMILARITY = 0.15
SIMILARITY_READ_MODEL_SOURCE_PGVECTOR = "pgvector_hnsw"
SIMILARITY_READ_MODEL_SOURCE_PYTHON = "python_fallback"
INFERENCE_OUTBOX_EVENT_EMBEDDING_READY = "embedding.ready"
INFERENCE_OUTBOX_STATUS_PENDING = "pending"
INFERENCE_OUTBOX_STATUS_RUNNING = "running"
INFERENCE_OUTBOX_STATUS_COMPLETED = "completed"
INFERENCE_OUTBOX_STATUS_FAILED = "failed"


def ensure_project_vector_schema(engine) -> None:
    """Ensure pgvector extension, project embedding columns, and HNSW index exist."""
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS semantic_text TEXT DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_payload TEXT DEFAULT '[]'",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(255)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ",
        f"ALTER TABLE projects ADD COLUMN IF NOT EXISTS embedding VECTOR({PROJECT_VECTOR_DIMENSIONS})",
        "CREATE INDEX IF NOT EXISTS ix_projects_embedding_hnsw ON projects USING hnsw (embedding vector_cosine_ops)",
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def ensure_project_metadata_schema(engine) -> None:
    """Ensure project metadata columns used for crawl linkage exist in already-created databases."""
    inspector = inspect(engine)
    if "projects" not in set(inspector.get_table_names()):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("projects")}
    column_statements = {
        "notice_number": "VARCHAR(100)",
        "source_url": "TEXT",
        "issuing_agency": "VARCHAR(255)",
        "demand_agency": "VARCHAR(255)",
    }

    with engine.begin() as connection:
        for column_name, ddl in column_statements.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {column_name} {ddl}")

        if engine.dialect.name in {"sqlite", "postgresql"}:
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_projects_notice_number ON projects (notice_number)")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_projects_issuing_agency ON projects (issuing_agency)")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_projects_demand_agency ON projects (demand_agency)")


class ProjectSimilarityService:
    """Persist project embeddings and query similar projects."""

    def __init__(self) -> None:
        self._classifier = NoticeClassifierService()

    def refresh_project_embedding(self, db: Session, project: Project, force: bool = False) -> tuple[list[float], str]:
        """Rebuild and persist a project's embedding when its semantic text changed."""
        semantic_text = self.build_semantic_text(project)
        cached_vector = self._load_embedding(project)

        if not force and semantic_text == (project.semantic_text or "").strip() and cached_vector:
            if self._can_persist_pgvector(db) and project.embedding is None:
                project.embedding = cached_vector
                project.embedding_updated_at = project.embedding_updated_at or utc_now()
                db.add(project)
            return cached_vector, project.embedding_model or FALLBACK_EMBEDDING_MODEL

        embedding, model_name = self._embed_text(semantic_text)
        project.semantic_text = semantic_text
        project.embedding_payload = json.dumps(embedding, ensure_ascii=False)
        project.embedding_model = model_name
        project.embedding_updated_at = utc_now()
        project.embedding = embedding if self._can_persist_pgvector(db) else None
        db.add(project)
        return embedding, model_name

    def resolve_embedding_without_persist(self, project: Project) -> tuple[list[float], str]:
        """``refresh_project_embedding`` 이 반환했을 (vector, model) 을 세션 쓰기 없이 돌려준다.

        분기 구조는 refresh 와 동일하다: semantic_text 가 저장본과 같고 payload
        캐시가 있으면 캐시 벡터, 아니면 인메모리 재계산. ORM 행에 아무것도
        대입하지 않고 ``db.add`` 도 없으므로 read-only 스캔이 Project 행을
        dirty/고정할 수 없다 (설계 2026-07-30 §5 PR-A-2 / S4). 스캔 중 임베딩
        freshness 는 수집/backfill 파이프라인 소관이다.
        """
        semantic_text = self.build_semantic_text(project)
        cached_vector = self._load_embedding(project)
        if semantic_text == (project.semantic_text or "").strip() and cached_vector:
            return cached_vector, project.embedding_model or FALLBACK_EMBEDDING_MODEL
        embedding, model_name = self._embed_text(semantic_text)
        return embedding, model_name

    def stored_embedding_state(self, project: Project) -> dict[str, Any]:
        """Return target embedding readiness without computing a replacement."""
        semantic_text = self.build_semantic_text(project)
        cached_vector = self._load_embedding(project)
        stored_text = (project.semantic_text or "").strip()
        if not cached_vector:
            status = "pending"
        elif semantic_text != stored_text:
            status = "stale"
        else:
            status = "ready"
        return {
            "vector": cached_vector,
            "status": status,
            "model": project.embedding_model if cached_vector else None,
            "updated_at": project.embedding_updated_at,
            "refresh_required": status != "ready",
        }

    def refresh_project_embeddings(self, db: Session, projects: Iterable[Project]) -> None:
        """Refresh embeddings for a batch of projects within the current transaction."""
        touched = False
        for project in projects:
            self.refresh_project_embedding(db, project)
            touched = True

        if touched:
            db.flush()

    def refresh_project_embedding_details(
        self,
        db: Session,
        project: Project,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Refresh one project's embedding and return API-ready status metadata."""
        _, model_name = self.refresh_project_embedding(db, project, force=force)
        db.flush()
        result = self._serialize_embedding_state(db, project, model_name)
        outbox_event = self.append_embedding_ready_outbox_event(db, project)
        if outbox_event is not None:
            db.flush()
            result["outbox_event_id"] = int(outbox_event.id)
        return result

    def rebuild_project_embeddings(
        self,
        db: Session,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        project_status: str | None = None,
        force: bool = False,
        project_ids: Iterable[int] | None = None,
    ) -> dict[str, Any]:
        """Refresh stored embeddings for a filtered batch of projects.

        When ``project_ids`` is supplied the rebuild targets exactly those rows
        (used by the async backfill that follows deferred-embedding crawl
        persistence); ``offset``/``limit`` paging is then bypassed. This is a
        pure row-selection filter and does not change embedding computation.
        """
        query = db.query(Project)

        normalized_ids = (
            sorted({int(pid) for pid in project_ids}) if project_ids is not None else None
        )
        if normalized_ids is not None:
            query = query.filter(Project.id.in_(normalized_ids))

        if category:
            query = query.filter(Project.category == category)

        if project_status:
            query = query.filter(Project.status == project_status)

        query = query.order_by(Project.id.asc())
        if normalized_ids is None:
            query = query.offset(offset).limit(limit)

        projects = query.all()
        results = [
            self.refresh_project_embedding_details(db, project, force=force)
            for project in projects
        ]

        return {
            "processed_count": len(results),
            "limit": limit,
            "offset": offset,
            "category": category,
            "project_status": project_status,
            "force": force,
            "requested_project_ids": normalized_ids,
            "vector_storage_enabled": self._can_persist_pgvector(db),
            "project_ids": [result["project_id"] for result in results],
            "outbox_event_ids": [
                int(result["outbox_event_id"])
                for result in results
                if result.get("outbox_event_id") is not None
            ],
            "results": results,
        }

    def append_embedding_ready_outbox_event(
        self,
        db: Session,
        project: Project,
    ) -> InferenceOutboxEvent | None:
        """Record an embedding-ready event in the current transaction."""
        if (
            project.id is None
            or project.embedding_updated_at is None
            or not project.embedding_model
            or not self._load_embedding(project)
        ):
            return None

        event = InferenceOutboxEvent(
            event_type=INFERENCE_OUTBOX_EVENT_EMBEDDING_READY,
            aggregate_type="project",
            aggregate_id=int(project.id),
            payload_json={
                "project_id": int(project.id),
                "embedding_model": project.embedding_model,
                "embedding_updated_at": project.embedding_updated_at.isoformat(),
                "same_category_only": True,
                "min_similarity": SIMILARITY_READ_MODEL_MIN_SIMILARITY,
                "limit": SIMILARITY_READ_MODEL_LIMIT,
            },
            status=INFERENCE_OUTBOX_STATUS_PENDING,
            attempts=0,
            available_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(event)
        return event

    def process_inference_outbox_events(self, db: Session, *, limit: int = 50) -> dict[str, Any]:
        """Claim pending inference outbox rows and update similarity read models."""
        rows = (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.status == INFERENCE_OUTBOX_STATUS_PENDING,
                InferenceOutboxEvent.available_at <= utc_now(),
            )
            .order_by(InferenceOutboxEvent.id.asc())
            .limit(max(1, int(limit)))
            .all()
        )

        processed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped = 0

        for row in rows:
            if not self._claim_inference_outbox_event(db, int(row.id)):
                skipped += 1
                continue
            try:
                current = (
                    db.query(InferenceOutboxEvent)
                    .filter(InferenceOutboxEvent.id == int(row.id))
                    .first()
                )
                if current is None:
                    skipped += 1
                    continue
                result = self._process_inference_outbox_event(db, current)
                current.status = INFERENCE_OUTBOX_STATUS_COMPLETED
                current.processed_at = utc_now()
                current.updated_at = utc_now()
                current.last_error = None
                db.commit()
                processed.append({"event_id": int(current.id), "result": result})
            except Exception as exc:  # noqa: BLE001 - one bad event must not stop the sweep
                db.rollback()
                self._mark_inference_outbox_failed(db, int(row.id), str(exc))
                failed.append({"event_id": int(row.id), "error": str(exc)})

        return {
            "processed_count": len(processed),
            "failed_count": len(failed),
            "skipped_count": skipped,
            "event_ids": [item["event_id"] for item in processed],
            "failed_event_ids": [item["event_id"] for item in failed],
            "results": processed,
        }

    def _claim_inference_outbox_event(self, db: Session, event_id: int) -> bool:
        now = utc_now()
        updated = (
            db.query(InferenceOutboxEvent)
            .filter(
                InferenceOutboxEvent.id == int(event_id),
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

    def _mark_inference_outbox_failed(self, db: Session, event_id: int, error: str) -> None:
        row = (
            db.query(InferenceOutboxEvent)
            .filter(InferenceOutboxEvent.id == int(event_id))
            .first()
        )
        if row is None:
            return
        row.status = INFERENCE_OUTBOX_STATUS_FAILED
        row.last_error = error
        row.updated_at = utc_now()
        db.commit()

    def _process_inference_outbox_event(
        self,
        db: Session,
        event: InferenceOutboxEvent,
    ) -> dict[str, Any]:
        if event.event_type != INFERENCE_OUTBOX_EVENT_EMBEDDING_READY:
            return {"status": "skipped", "reason": f"unsupported event_type {event.event_type}"}
        payload = dict(event.payload_json or {})
        return self.recompute_similarity_read_model(
            db,
            project_id=int(event.aggregate_id),
            same_category_only=bool(payload.get("same_category_only", True)),
            min_similarity=float(
                payload.get("min_similarity", SIMILARITY_READ_MODEL_MIN_SIMILARITY)
            ),
            limit=int(payload.get("limit", SIMILARITY_READ_MODEL_LIMIT)),
        )

    def recompute_similarity_read_model(
        self,
        db: Session,
        *,
        project_id: int,
        same_category_only: bool = True,
        min_similarity: float = SIMILARITY_READ_MODEL_MIN_SIMILARITY,
        limit: int = SIMILARITY_READ_MODEL_LIMIT,
    ) -> dict[str, Any]:
        """Recompute persisted similarity edges for one ready target project."""
        project = db.query(Project).filter(Project.id == int(project_id)).first()
        if project is None:
            return {"status": "skipped", "reason": "project_not_found", "project_id": int(project_id)}

        state = self.stored_embedding_state(project)
        if state["status"] != "ready" or not state["vector"]:
            return {
                "status": "skipped",
                "reason": f"target_embedding_{state['status']}",
                "project_id": int(project.id),
            }

        if self._can_query_pgvector(db):
            results = self._search_with_postgres(
                db,
                project=project,
                query_embedding=state["vector"],
                limit=max(1, int(limit)),
                min_similarity=float(min_similarity),
                same_category_only=bool(same_category_only),
            )
            source = SIMILARITY_READ_MODEL_SOURCE_PGVECTOR
        else:
            candidate_query = db.query(Project).filter(Project.id != project.id)
            if same_category_only and project.category:
                candidate_query = candidate_query.filter(Project.category == project.category)
            results = self._search_with_python(
                candidate_query.all(),
                query_embedding=state["vector"],
                limit=max(1, int(limit)),
                min_similarity=float(min_similarity),
                embedding_resolver=self._load_embedding,
            )
            source = SIMILARITY_READ_MODEL_SOURCE_PYTHON

        edge_count = self.replace_similarity_edges(
            db,
            project=project,
            results=results,
            same_category_only=bool(same_category_only),
            min_similarity=float(min_similarity),
            source=source,
        )
        db.flush()
        return {
            "status": "completed",
            "project_id": int(project.id),
            "edge_count": edge_count,
            "same_category_only": bool(same_category_only),
            "min_similarity_bucket": self._min_similarity_bucket(min_similarity),
            "source": source,
        }

    def replace_similarity_edges(
        self,
        db: Session,
        *,
        project: Project,
        results: list[dict[str, Any]],
        same_category_only: bool,
        min_similarity: float,
        source: str,
    ) -> int:
        """Replace one target/version/key's read-model edge rows."""
        if project.embedding_updated_at is None or not project.embedding_model:
            return 0

        filters = self._similarity_edge_filters(
            project,
            same_category_only=same_category_only,
            min_similarity=min_similarity,
        )
        db.query(ProjectSimilarityEdge).filter(*filters).delete(synchronize_session=False)
        computed_at = utc_now()
        for rank, result in enumerate(results, start=1):
            db.add(
                ProjectSimilarityEdge(
                    target_project_id=int(project.id),
                    candidate_project_id=int(result["project_id"]),
                    embedding_model=str(project.embedding_model),
                    target_embedding_updated_at=project.embedding_updated_at,
                    same_category_only=bool(same_category_only),
                    min_similarity_bucket=self._min_similarity_bucket(min_similarity),
                    rank=rank,
                    similarity_score=float(result["similarity_score"]),
                    source=source,
                    computed_at=computed_at,
                )
            )
        return len(results)

    def find_similar_projects(
        self,
        db: Session,
        project: Project,
        *,
        limit: int = 5,
        min_similarity: float = 0.0,
        same_category_only: bool = True,
        read_only: bool = False,
        stored_only: bool = False,
    ) -> dict[str, Any]:
        """Find projects closest to the target project's embedding.

        The pgvector path searches against *stored* embeddings via the HNSW
        index, so candidate freshness is the responsibility of the
        collection/backfill pipeline (deferred-embedding backfill,
        :meth:`rebuild_project_embeddings`) rather than this read path. Only the
        Python fallback (tests/in-memory, no pgvector) loads every candidate and
        refreshes in-memory embeddings, since it has no stored index to query.

        ``read_only=True`` (스캔 경로 전용) 은 target/candidate 임베딩을
        :meth:`resolve_embedding_without_persist` 로 해석해 세션에 아무것도
        쓰지 않는다 — 산출(점수·정렬)은 write 경로와 동일하고, 임베딩
        freshness 는 수집/backfill 파이프라인 소관이다 (설계 §5 PR-A-2 / S4).
        기본값 ``False`` 로 기존 호출자 동작은 불변이다.

        ``stored_only=True`` (UX GET 경로) 는 저장 embedding 만 사용하고
        missing/stale embedding 을 API 프로세스에서 재계산하지 않는다.
        """
        embedding_status = "ready"
        embedding_updated_at = project.embedding_updated_at
        refresh_required = False

        if stored_only:
            state = self.stored_embedding_state(project)
            target_embedding = state["vector"]
            target_model = state["model"]
            embedding_status = state["status"]
            embedding_updated_at = state["updated_at"]
            refresh_required = bool(state["refresh_required"])
            if not target_embedding:
                return {
                    "target_project_id": project.id,
                    "target_project_title": project.title,
                    "target_embedding_model": target_model,
                    "target_embedding_status": embedding_status,
                    "target_embedding_updated_at": embedding_updated_at,
                    "target_embedding_refresh_required": refresh_required,
                    "search_mode": "postgres_vector" if self._can_query_pgvector(db) else "python_fallback",
                    "same_category_only": same_category_only,
                    "min_similarity": round(min_similarity, 4),
                    "result_count": 0,
                    "results": [],
                }
            if embedding_status == "ready":
                read_model_results = self.load_similarity_edges(
                    db,
                    project,
                    limit=limit,
                    min_similarity=min_similarity,
                    same_category_only=same_category_only,
                )
                if read_model_results is not None:
                    return {
                        "target_project_id": project.id,
                        "target_project_title": project.title,
                        "target_embedding_model": target_model,
                        "target_embedding_status": embedding_status,
                        "target_embedding_updated_at": embedding_updated_at,
                        "target_embedding_refresh_required": refresh_required,
                        "search_mode": "read_model",
                        "same_category_only": same_category_only,
                        "min_similarity": round(min_similarity, 4),
                        "result_count": len(read_model_results),
                        "results": read_model_results,
                    }
        elif read_only:
            target_embedding, target_model = self.resolve_embedding_without_persist(project)
        else:
            target_embedding, target_model = self.refresh_project_embedding(db, project)

        if self._can_query_pgvector(db):
            results = self._search_with_postgres(
                db,
                project=project,
                query_embedding=target_embedding,
                limit=limit,
                min_similarity=min_similarity,
                same_category_only=same_category_only,
            )
            search_mode = "postgres_vector"
        else:
            candidate_query = db.query(Project).filter(Project.id != project.id)
            if same_category_only and project.category:
                candidate_query = candidate_query.filter(
                    Project.category == project.category
                )

            candidates = candidate_query.all()
            if read_only:
                def _resolve_candidate_embedding(candidate: Project) -> list[float]:
                    return self.resolve_embedding_without_persist(candidate)[0]

                embedding_resolver = _resolve_candidate_embedding
            elif stored_only:
                embedding_resolver = self._load_embedding
            else:
                self.refresh_project_embeddings(db, candidates)
                embedding_resolver = self._load_embedding

            results = self._search_with_python(
                candidates,
                query_embedding=target_embedding,
                limit=limit,
                min_similarity=min_similarity,
                embedding_resolver=embedding_resolver,
            )
            search_mode = "python_fallback"

        return {
            "target_project_id": project.id,
            "target_project_title": project.title,
            "target_embedding_model": target_model,
            "target_embedding_status": embedding_status,
            "target_embedding_updated_at": embedding_updated_at,
            "target_embedding_refresh_required": refresh_required,
            "search_mode": search_mode,
            "same_category_only": same_category_only,
            "min_similarity": round(min_similarity, 4),
            "result_count": len(results),
            "results": results,
        }

    def load_similarity_edges(
        self,
        db: Session,
        project: Project,
        *,
        limit: int,
        min_similarity: float,
        same_category_only: bool,
    ) -> list[dict[str, Any]] | None:
        """Load a fresh similarity read-model hit, or None when it is missing."""
        if project.embedding_updated_at is None or not project.embedding_model:
            return None
        rows = (
            db.query(ProjectSimilarityEdge, Project)
            .join(Project, Project.id == ProjectSimilarityEdge.candidate_project_id)
            .filter(
                *self._similarity_edge_filters(
                    project,
                    same_category_only=same_category_only,
                    min_similarity=min_similarity,
                )
            )
            .order_by(ProjectSimilarityEdge.rank.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        if not rows:
            return None
        return [
            self._serialize_result(candidate, float(edge.similarity_score))
            for edge, candidate in rows
        ]

    def _similarity_edge_filters(
        self,
        project: Project,
        *,
        same_category_only: bool,
        min_similarity: float,
    ) -> tuple[Any, ...]:
        return (
            ProjectSimilarityEdge.target_project_id == int(project.id),
            ProjectSimilarityEdge.embedding_model == str(project.embedding_model),
            ProjectSimilarityEdge.target_embedding_updated_at == project.embedding_updated_at,
            ProjectSimilarityEdge.same_category_only == bool(same_category_only),
            ProjectSimilarityEdge.min_similarity_bucket == self._min_similarity_bucket(min_similarity),
        )

    def _min_similarity_bucket(self, min_similarity: float) -> float:
        return round(float(min_similarity), 4)

    def build_semantic_text(self, project: Project) -> str:
        """Build a rich semantic description used for embeddings and retrieval."""
        parts = [self._classifier._build_project_semantic_text(project)]

        if project.notice_number:
            parts.append(f"공고번호 {project.notice_number}")
        if project.issuing_agency:
            parts.append(f"공고기관 {project.issuing_agency}")
        if project.demand_agency:
            parts.append(f"수요기관 {project.demand_agency}")

        if project.budget_estimate:
            parts.append(f"예산 {float(project.budget_estimate):.0f}")
        if project.budget_min or project.budget_max:
            budget_min = float(project.budget_min or 0.0)
            budget_max = float(project.budget_max or 0.0)
            parts.append(f"예산범위 {budget_min:.0f} {budget_max:.0f}")
        if project.status:
            parts.append(f"상태 {project.status}")
        if project.deadline:
            parts.append(f"마감 {project.deadline.isoformat()}")

        return " ".join(part.strip() for part in parts if part and part.strip())

    def _search_with_postgres(
        self,
        db: Session,
        *,
        project: Project,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
        same_category_only: bool,
    ) -> list[dict[str, Any]]:
        """Use PostgreSQL + pgvector cosine distance for nearest-neighbor search."""
        distance_expression = Project.embedding.cosine_distance(query_embedding)
        similarity_expression = (1 - distance_expression).label("similarity_score")

        query = db.query(Project, similarity_expression).filter(
            Project.id != project.id,
            Project.embedding.isnot(None),
        )

        if same_category_only and project.category:
            query = query.filter(Project.category == project.category)

        if min_similarity > 0:
            query = query.filter(distance_expression <= (1 - min_similarity))

        # pgvector HNSW can satisfy ORDER BY distance LIMIT, but adding a
        # secondary sort key (for example Project.id) pushes PostgreSQL back to
        # seq scan + top-N sort on large tables.
        rows = query.order_by(distance_expression.asc()).limit(limit).all()
        return [self._serialize_result(candidate, float(similarity_score)) for candidate, similarity_score in rows]

    def _search_with_python(
        self,
        candidates: Iterable[Project],
        *,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
        embedding_resolver: Callable[[Project], list[float]] | None = None,
    ) -> list[dict[str, Any]]:
        """Fallback similarity search using stored embedding payloads.

        ``embedding_resolver`` 은 candidate → embedding 벡터 조회를 갈아끼우는
        seam 이다. 기본값(``None``)은 저장본을 읽는 :meth:`_load_embedding` 이며,
        read-only 스캔은 세션 쓰기 없이 재해석하는 resolver 를 주입한다 —
        어느 쪽이든 같은 (정규화된) 벡터를 돌려주므로 산출은 동일하다.
        """
        resolver = embedding_resolver or self._load_embedding
        matches: list[dict[str, Any]] = []

        for candidate in candidates:
            candidate_embedding = resolver(candidate)
            if not candidate_embedding:
                continue

            similarity = self._cosine_similarity(query_embedding, candidate_embedding)
            if similarity < min_similarity:
                continue

            matches.append(self._serialize_result(candidate, similarity))

        matches.sort(key=lambda item: (-item["similarity_score"], item["project_id"]))
        return matches[:limit]

    def _serialize_result(self, project: Project, similarity_score: float) -> dict[str, Any]:
        """Shape a project row for the similarity API response."""
        return {
            "project_id": project.id,
            "title": project.title,
            "category": project.category,
            "status": project.status,
            "budget_estimate": float(project.budget_estimate or 0.0),
            "deadline": project.deadline,
            "created_at": project.created_at,
            "similarity_score": round(max(0.0, min(1.0, similarity_score)), 4),
            "embedding_model": project.embedding_model,
        }

    def _serialize_embedding_state(self, db: Session, project: Project, model_name: str) -> dict[str, Any]:
        """Shape one project's embedding refresh result for API and batch responses."""
        stored_embedding = self._load_embedding(project)
        return {
            "project_id": project.id,
            "title": project.title,
            "category": project.category,
            "embedding_model": model_name,
            "semantic_text_length": len(project.semantic_text or ""),
            "embedding_dimensions": len(stored_embedding),
            "embedding_updated_at": project.embedding_updated_at,
            "vector_storage_enabled": self._can_persist_pgvector(db),
            "vector_persisted": project.embedding is not None,
        }

    def _embed_text(self, semantic_text: str) -> tuple[list[float], str]:
        """Generate a normalized embedding, using sentence-transformers when available."""
        if settings.ENABLE_SEMANTIC_CLASSIFICATION and settings.ENVIRONMENT != "test":
            model = self._classifier._get_embedding_model()
            if model is not None:
                encoded = model.encode([semantic_text], normalize_embeddings=True)[0]
                vector = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
                return self._normalize_vector(vector), settings.CLASSIFIER_EMBEDDING_MODEL

        return self._build_fallback_embedding(semantic_text), FALLBACK_EMBEDDING_MODEL

    def _build_fallback_embedding(self, semantic_text: str) -> list[float]:
        """Build a deterministic hashed embedding for tests and offline environments."""
        tokens = self._classifier._tokenize_semantic_text(semantic_text)
        if not tokens:
            tokens = ["empty"]

        vector = [0.0] * PROJECT_VECTOR_DIMENSIONS
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            primary_index = int.from_bytes(digest[:4], "big") % PROJECT_VECTOR_DIMENSIONS
            secondary_index = int.from_bytes(digest[4:8], "big") % PROJECT_VECTOR_DIMENSIONS
            weight = 1.0 + (digest[8] / 255.0)
            signed_weight = weight if digest[9] % 2 == 0 else -weight
            vector[primary_index] += signed_weight
            vector[secondary_index] += signed_weight * 0.5

        return self._normalize_vector(vector)

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        """Pad or truncate vectors and normalize them for cosine similarity."""
        adjusted = list(vector[:PROJECT_VECTOR_DIMENSIONS])
        if len(adjusted) < PROJECT_VECTOR_DIMENSIONS:
            adjusted.extend([0.0] * (PROJECT_VECTOR_DIMENSIONS - len(adjusted)))

        magnitude = math.sqrt(sum(component * component for component in adjusted))
        if magnitude == 0:
            return adjusted
        return [component / magnitude for component in adjusted]

    def _load_embedding(self, project: Project) -> list[float]:
        """Load a project's persisted embedding payload."""
        if not project.embedding_payload:
            return []
        try:
            payload = json.loads(project.embedding_payload)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        if not payload:
            return []
        return self._normalize_vector([float(value) for value in payload])

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        """Compute cosine similarity between normalized vectors."""
        if not left or not right:
            return 0.0
        return max(0.0, min(1.0, sum(l * r for l, r in zip(left, right))))

    def _can_persist_pgvector(self, db: Session) -> bool:
        """Return whether native pgvector writes are available for this session."""
        return self._is_postgres(db) and PGVECTOR_SQLALCHEMY_AVAILABLE

    def _can_query_pgvector(self, db: Session) -> bool:
        """Return whether database-side vector distance queries are available."""
        return self._can_persist_pgvector(db) and hasattr(Project.embedding, "cosine_distance")

    def _is_postgres(self, db: Session) -> bool:
        """Return whether the session is currently bound to PostgreSQL."""
        return db.get_bind().dialect.name == "postgresql"
