"""Project embedding persistence and similar-project retrieval."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.core.vector import PGVECTOR_SQLALCHEMY_AVAILABLE
from app.models.models import (
    Project,
)
from app.schemas.project import ProjectSimilaritySearchResponse, SimilarProjectItem
from app.schemas.similarity_runtime import (
    InferenceOutboxProcessResult,
    SimilarityProjectionBackfillResult,
    SimilarityProjectionResult,
    SimilaritySearchExecution,
    SimilaritySearchPreparation,
)
from app.services.classifier import NoticeClassifierService
from app.services.inference_outbox import InferenceOutboxService
from app.services.project_embedding_batch import ProjectEmbeddingBatchMixin
from app.services.project_similarity_constants import (
    FALLBACK_EMBEDDING_MODEL,
    PROJECT_VECTOR_DIMENSIONS,
)
from app.services.project_similarity_schema import (
    ensure_project_metadata_schema as ensure_project_metadata_schema,
    ensure_project_vector_schema as ensure_project_vector_schema,
)
from app.services.similarity_read_model import ProjectSimilarityReadModelService
from app.services.similarity_projection_backfill import (
    stage_active_similarity_projection_backfill,
)


class ProjectSimilarityService(ProjectEmbeddingBatchMixin):
    """Persist project embeddings and query similar projects."""

    def __init__(self) -> None:
        self._classifier = NoticeClassifierService()
        self._read_model = ProjectSimilarityReadModelService(self)
        self._outbox = InferenceOutboxService(
            self._read_model,
            embedding_refresher=self.refresh_project_embedding,
        )

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

    def refresh_project_embeddings(self, db: Session, projects: Iterable[Project]) -> None:
        """Refresh embeddings for a batch of projects within the current transaction."""
        touched = False
        for project in projects:
            self.refresh_project_embedding(db, project)
            touched = True

        if touched:
            db.flush()

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
        """Find closest projects; stored-only UX reads never run inference inline."""
        prepared = self._prepare_similarity_search(
            db,
            project,
            limit,
            min_similarity,
            same_category_only,
            read_only,
            stored_only,
        )
        if prepared.response is not None:
            return prepared.response.model_dump(mode="python")
        executed = self._execute_similarity_search(
            db,
            project,
            prepared.vector,
            limit,
            min_similarity,
            same_category_only,
            read_only,
            stored_only,
        )
        response = ProjectSimilaritySearchResponse(
            target_project_id=int(project.id),
            target_project_title=project.title,
            target_embedding_model=prepared.model,
            target_embedding_status=prepared.status,
            target_embedding_updated_at=prepared.updated_at,
            target_embedding_refresh_required=prepared.refresh_required,
            projection_status="not_applicable",
            search_mode=executed.search_mode,
            same_category_only=same_category_only,
            min_similarity=round(min_similarity, 4),
            result_count=len(executed.items),
            results=executed.items,
        )
        return response.model_dump(mode="python")

    def recompute_similarity_read_model(
        self,
        db: Session,
        *,
        project_id: int,
        same_category_only: bool = True,
        min_similarity: float = 0.15,
        limit: int = 20,
    ) -> SimilarityProjectionResult:
        """Rebuild one versioned similarity projection."""
        return self._read_model.recompute(
            db,
            project_id=project_id,
            same_category_only=same_category_only,
            min_similarity=min_similarity,
            limit=limit,
        )

    def process_inference_outbox_events(self, db: Session, *, limit: int = 50) -> InferenceOutboxProcessResult:
        """Process recoverable similarity-projection events."""
        return self._outbox.process(db, limit=limit)

    def stage_active_similarity_projection_backfill(
        self,
        db: Session,
        *,
        limit: int = 100,
    ) -> SimilarityProjectionBackfillResult:
        """Stage bounded durable work for active targets with no current projection."""
        return stage_active_similarity_projection_backfill(
            db, read_model=self._read_model, outbox=self._outbox, limit=limit
        )

    def _prepare_similarity_search(
        self,
        db: Session,
        project: Project,
        limit: int,
        min_similarity: float,
        same_category_only: bool,
        read_only: bool,
        stored_only: bool,
    ) -> SimilaritySearchPreparation:
        if stored_only:
            return self._read_model.prepare_search(
                db,
                project,
                limit=limit,
                min_similarity=min_similarity,
                same_category_only=same_category_only,
            )
        if read_only:
            vector, model = self.resolve_embedding_without_persist(project)
        else:
            vector, model = self.refresh_project_embedding(db, project)
        return SimilaritySearchPreparation(
            vector=vector,
            model=model,
            status="ready",
            updated_at=project.embedding_updated_at,
            refresh_required=False,
        )

    def _execute_similarity_search(
        self,
        db: Session,
        project: Project,
        vector: list[float],
        limit: int,
        min_similarity: float,
        same_category_only: bool,
        read_only: bool,
        stored_only: bool,
    ) -> SimilaritySearchExecution:
        if stored_only:
            raise RuntimeError(
                "stored-only similarity search reached inline execution"
            )
        if self._can_query_pgvector(db):
            raw = self._search_with_postgres(
                db,
                project=project,
                query_embedding=vector,
                limit=limit,
                min_similarity=min_similarity,
                same_category_only=same_category_only,
            )
            return SimilaritySearchExecution(
                items=[SimilarProjectItem.model_validate(item) for item in raw],
                search_mode="postgres_vector",
            )
        candidates = self._load_similarity_candidates(db, project, same_category_only)
        resolver = self._candidate_embedding_resolver(read_only, stored_only)
        if not read_only and not stored_only:
            self.refresh_project_embeddings(db, candidates)
        raw = self._search_with_python(
            candidates,
            query_embedding=vector,
            limit=limit,
            min_similarity=min_similarity,
            embedding_resolver=resolver,
        )
        return SimilaritySearchExecution(
            items=[SimilarProjectItem.model_validate(item) for item in raw],
            search_mode="python_fallback",
        )

    def _load_similarity_candidates(self, db: Session, project: Project, same_category_only: bool) -> list[Project]:
        query = db.query(Project).filter(Project.id != project.id)
        if same_category_only and project.category:
            query = query.filter(Project.category == project.category)
        return query.all()

    def _candidate_embedding_resolver(self, read_only: bool, stored_only: bool) -> Callable[[Project], list[float]]:
        if read_only:
            return lambda candidate: self.resolve_embedding_without_persist(candidate)[0]
        return self._load_embedding

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
            Project.embedding_model == project.embedding_model,
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
