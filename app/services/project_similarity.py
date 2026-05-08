"""Project embedding persistence and similar-project retrieval."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.core.vector import PGVECTOR_SQLALCHEMY_AVAILABLE
from app.models.models import Project
from app.services.classifier import NoticeClassifierService

PROJECT_VECTOR_DIMENSIONS = 384
FALLBACK_EMBEDDING_MODEL = "fallback-hash-v1"


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
        return self._serialize_embedding_state(db, project, model_name)

    def rebuild_project_embeddings(
        self,
        db: Session,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        project_status: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Refresh stored embeddings for a filtered batch of projects."""
        query = db.query(Project)

        if category:
            query = query.filter(Project.category == category)

        if project_status:
            query = query.filter(Project.status == project_status)

        projects = query.order_by(Project.id.asc()).offset(offset).limit(limit).all()
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
            "vector_storage_enabled": self._can_persist_pgvector(db),
            "project_ids": [result["project_id"] for result in results],
            "results": results,
        }

    def find_similar_projects(
        self,
        db: Session,
        project: Project,
        *,
        limit: int = 5,
        min_similarity: float = 0.0,
        same_category_only: bool = True,
    ) -> dict[str, Any]:
        """Find projects closest to the target project's embedding."""
        target_embedding, target_model = self.refresh_project_embedding(db, project)

        candidate_query = db.query(Project).filter(Project.id != project.id)
        if same_category_only and project.category:
            candidate_query = candidate_query.filter(Project.category == project.category)

        candidates = candidate_query.all()
        self.refresh_project_embeddings(db, candidates)

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
            results = self._search_with_python(
                candidates,
                query_embedding=target_embedding,
                limit=limit,
                min_similarity=min_similarity,
            )
            search_mode = "python_fallback"

        return {
            "target_project_id": project.id,
            "target_project_title": project.title,
            "target_embedding_model": target_model,
            "search_mode": search_mode,
            "same_category_only": same_category_only,
            "min_similarity": round(min_similarity, 4),
            "result_count": len(results),
            "results": results,
        }

    def build_semantic_text(self, project: Project) -> str:
        """Build a rich semantic description used for embeddings and retrieval."""
        parts = [self._classifier._build_project_semantic_text(project)]

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

        rows = query.order_by(distance_expression.asc(), Project.id.asc()).limit(limit).all()
        return [self._serialize_result(candidate, float(similarity_score)) for candidate, similarity_score in rows]

    def _search_with_python(
        self,
        candidates: Iterable[Project],
        *,
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[dict[str, Any]]:
        """Fallback similarity search using stored embedding payloads."""
        matches: list[dict[str, Any]] = []

        for candidate in candidates:
            candidate_embedding = self._load_embedding(candidate)
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