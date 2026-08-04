"""Versioned, corpus-aware read model for project similarity UX reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import (
    Project,
    ProjectSimilarityEdge,
    ProjectSimilaritySnapshot,
)
from app.schemas.project import ProjectSimilaritySearchResponse, SimilarProjectItem
from app.schemas.similarity_runtime import (
    ProjectEmbeddingInputState,
    SimilarityCorpusWatermark,
    SimilarityProjectionResult,
    SimilaritySearchPreparation,
    StoredEmbeddingState,
)

if TYPE_CHECKING:
    from app.services.project_similarity import ProjectSimilarityService


SIMILARITY_READ_MODEL_LIMIT = 20
SIMILARITY_READ_MODEL_MIN_SIMILARITY = 0.15
SIMILARITY_READ_MODEL_SOURCE_PGVECTOR = "pgvector_hnsw"
SIMILARITY_READ_MODEL_SOURCE_PYTHON = "python_fallback"


class SimilarityCorpusChangedError(RuntimeError):
    """The embedding corpus changed while a projection was being computed."""


def invalidate_project_embedding(project: Project) -> None:
    """Remove a stale vector immediately so reads cannot reuse it while refresh is queued."""
    project.embedding_payload = "[]"
    project.embedding_model = None
    project.embedding_updated_at = None
    project.embedding = None


def project_embedding_input_state(project: Project) -> ProjectEmbeddingInputState:
    """Capture fields that contribute to semantic text or category filtering."""
    return ProjectEmbeddingInputState(
        title=project.title,
        description=project.description,
        requirements=project.requirements,
        category=project.category,
        notice_number=project.notice_number,
        issuing_agency=project.issuing_agency,
        demand_agency=project.demand_agency,
        budget_estimate=project.budget_estimate,
        budget_min=project.budget_min,
        budget_max=project.budget_max,
        status=project.status,
        deadline=project.deadline,
    )


class ProjectSimilarityReadModelService:
    """Materialize and serve similarity snapshots with corpus freshness checks."""

    def __init__(self, similarity: ProjectSimilarityService) -> None:
        self._similarity = similarity

    def embedding_state(self, project: Project) -> StoredEmbeddingState:
        semantic_text = self._similarity.build_semantic_text(project)
        vector = self._similarity._load_embedding(project)
        stored_text = (project.semantic_text or "").strip()
        status = (
            "pending"
            if not vector
            else "stale"
            if semantic_text != stored_text
            else "ready"
        )
        return StoredEmbeddingState(
            vector=vector,
            status=status,
            model=project.embedding_model if vector else None,
            updated_at=project.embedding_updated_at,
            refresh_required=status != "ready",
        )

    def prepare_search(
        self,
        db: Session,
        project: Project,
        *,
        limit: int,
        min_similarity: float,
        same_category_only: bool,
    ) -> SimilaritySearchPreparation:
        state = self.embedding_state(project)
        response = None
        if state.status != "ready":
            response = self._build_response(
                project,
                state,
                [],
                min_similarity,
                same_category_only,
                projection_status="missing",
            )
        else:
            response = self._projected_search_response(
                db, project, state, limit, min_similarity, same_category_only
            )
        return SimilaritySearchPreparation(
            vector=state.vector,
            model=state.model,
            status=state.status,
            updated_at=state.updated_at,
            refresh_required=state.refresh_required,
            response=response,
        )

    def _projected_search_response(
        self, db, project, state, limit, min_similarity, same_category_only
    ):
        snapshot = self._load_snapshot(db, project, min_similarity, same_category_only)
        projection_status = (
            "missing"
            if snapshot is None
            else "ready"
            if self._snapshot_is_current(db, snapshot)
            else "stale"
        )
        items = None
        if projection_status == "ready":
            items = self.load(
                db,
                project,
                limit=limit,
                min_similarity=min_similarity,
                same_category_only=same_category_only,
            )
            if items is None:
                projection_status = "stale"
        return self._build_response(
            project,
            state,
            items or [],
            min_similarity,
            same_category_only,
            projection_status=projection_status,
        )

    def recompute(
        self,
        db: Session,
        *,
        project_id: int,
        same_category_only: bool = True,
        min_similarity: float = SIMILARITY_READ_MODEL_MIN_SIMILARITY,
        limit: int = SIMILARITY_READ_MODEL_LIMIT,
    ) -> SimilarityProjectionResult:
        project = db.query(Project).filter(Project.id == int(project_id)).first()
        if project is None:
            return self._skipped(project_id, "project_not_found")
        state = self.embedding_state(project)
        if state.status != "ready" or not state.vector:
            return self._skipped(project.id, f"target_embedding_{state.status}")
        before = self.corpus_watermark(db)
        items, source = self._compute_results(
            db, project, state, limit, min_similarity, same_category_only
        )
        if before != self.corpus_watermark(db):
            raise SimilarityCorpusChangedError(
                "embedding corpus changed during projection"
            )
        self._replace_snapshot(
            db, project, items, before, source, same_category_only, min_similarity
        )
        return SimilarityProjectionResult(
            status="completed",
            project_id=int(project.id),
            edge_count=len(items),
            same_category_only=same_category_only,
            min_similarity_bucket=self.min_similarity_bucket(min_similarity),
            source=source,
        )

    def load(
        self,
        db: Session,
        project: Project,
        *,
        limit: int,
        min_similarity: float,
        same_category_only: bool,
    ) -> list[SimilarProjectItem] | None:
        snapshot = self._load_snapshot(db, project, min_similarity, same_category_only)
        if snapshot is None or not self._snapshot_is_current(db, snapshot):
            return None
        query = (
            db.query(ProjectSimilarityEdge, Project)
            .join(Project, Project.id == ProjectSimilarityEdge.candidate_project_id)
            .filter(ProjectSimilarityEdge.snapshot_id == int(snapshot.id))
        )
        if same_category_only and project.category:
            query = query.filter(Project.category == project.category)
        rows = (
            query.order_by(ProjectSimilarityEdge.rank.asc())
            .limit(max(1, int(limit)))
            .all()
        )
        if len(rows) != min(int(snapshot.edge_count), max(1, int(limit))):
            return None
        return [
            SimilarProjectItem.model_validate(
                self._similarity._serialize_result(
                    candidate, float(edge.similarity_score)
                )
            )
            for edge, candidate in rows
        ]

    def corpus_watermark(self, db: Session) -> SimilarityCorpusWatermark:
        count, updated_at = db.query(
            func.count(Project.embedding_updated_at),
            func.max(Project.embedding_updated_at),
        ).one()
        return SimilarityCorpusWatermark(
            embedding_count=int(count or 0),
            embedding_updated_at=updated_at,
        )

    def min_similarity_bucket(self, min_similarity: float) -> float:
        return round(float(min_similarity), 4)

    def _compute_results(
        self,
        db: Session,
        project: Project,
        state: StoredEmbeddingState,
        limit: int,
        min_similarity: float,
        same_category_only: bool,
    ) -> tuple[list[SimilarProjectItem], str]:
        if self._similarity._can_query_pgvector(db):
            raw = self._similarity._search_with_postgres(
                db,
                project=project,
                query_embedding=state.vector,
                limit=max(1, int(limit)),
                min_similarity=min_similarity,
                same_category_only=same_category_only,
            )
            source = SIMILARITY_READ_MODEL_SOURCE_PGVECTOR
        else:
            query = db.query(Project).filter(Project.id != project.id)
            if same_category_only and project.category:
                query = query.filter(Project.category == project.category)
            raw = self._similarity._search_with_python(
                query.all(),
                query_embedding=state.vector,
                limit=max(1, int(limit)),
                min_similarity=min_similarity,
                embedding_resolver=self._similarity._load_embedding,
            )
            source = SIMILARITY_READ_MODEL_SOURCE_PYTHON
        return [SimilarProjectItem.model_validate(item) for item in raw], source

    def _replace_snapshot(
        self,
        db: Session,
        project: Project,
        items: list[SimilarProjectItem],
        watermark: SimilarityCorpusWatermark,
        source: str,
        same_category_only: bool,
        min_similarity: float,
    ) -> None:
        snapshot = self._load_snapshot(db, project, min_similarity, same_category_only)
        if snapshot is None:
            snapshot = self._new_snapshot(project, min_similarity, same_category_only)
            db.add(snapshot)
            db.flush()
        db.query(ProjectSimilarityEdge).filter(
            ProjectSimilarityEdge.snapshot_id == int(snapshot.id)
        ).delete(synchronize_session=False)
        self._update_snapshot(snapshot, watermark, source, len(items))
        for rank, item in enumerate(items, start=1):
            db.add(
                ProjectSimilarityEdge(
                    snapshot_id=int(snapshot.id),
                    candidate_project_id=item.project_id,
                    rank=rank,
                    similarity_score=item.similarity_score,
                )
            )
        self._delete_obsolete_snapshots(db, snapshot)
        db.flush()

    def _load_snapshot(
        self,
        db: Session,
        project: Project,
        min_similarity: float,
        same_category_only: bool,
    ) -> ProjectSimilaritySnapshot | None:
        if project.embedding_updated_at is None or not project.embedding_model:
            return None
        return (
            db.query(ProjectSimilaritySnapshot)
            .filter(
                ProjectSimilaritySnapshot.target_project_id == int(project.id),
                ProjectSimilaritySnapshot.embedding_model
                == str(project.embedding_model),
                ProjectSimilaritySnapshot.target_embedding_updated_at
                == project.embedding_updated_at,
                ProjectSimilaritySnapshot.same_category_only
                == bool(same_category_only),
                ProjectSimilaritySnapshot.min_similarity_bucket
                == self.min_similarity_bucket(min_similarity),
            )
            .first()
        )

    def _snapshot_is_current(
        self,
        db: Session,
        snapshot: ProjectSimilaritySnapshot,
    ) -> bool:
        watermark = self.corpus_watermark(db)
        return (
            int(snapshot.corpus_embedding_count) == watermark.embedding_count
            and snapshot.corpus_embedding_updated_at == watermark.embedding_updated_at
        )

    def _new_snapshot(
        self,
        project: Project,
        min_similarity: float,
        same_category_only: bool,
    ) -> ProjectSimilaritySnapshot:
        return ProjectSimilaritySnapshot(
            target_project_id=int(project.id),
            embedding_model=str(project.embedding_model),
            target_embedding_updated_at=project.embedding_updated_at,
            same_category_only=bool(same_category_only),
            min_similarity_bucket=self.min_similarity_bucket(min_similarity),
            corpus_embedding_count=0,
            edge_count=0,
            source=SIMILARITY_READ_MODEL_SOURCE_PYTHON,
            computed_at=utc_now(),
        )

    def _update_snapshot(
        self,
        snapshot: ProjectSimilaritySnapshot,
        watermark: SimilarityCorpusWatermark,
        source: str,
        edge_count: int,
    ) -> None:
        snapshot.corpus_embedding_count = watermark.embedding_count
        snapshot.corpus_embedding_updated_at = watermark.embedding_updated_at
        snapshot.edge_count = edge_count
        snapshot.source = source
        snapshot.computed_at = utc_now()

    def _delete_obsolete_snapshots(
        self,
        db: Session,
        current: ProjectSimilaritySnapshot,
    ) -> None:
        stale = (
            db.query(ProjectSimilaritySnapshot.id)
            .filter(
                ProjectSimilaritySnapshot.target_project_id
                == current.target_project_id,
                ProjectSimilaritySnapshot.same_category_only
                == current.same_category_only,
                ProjectSimilaritySnapshot.min_similarity_bucket
                == current.min_similarity_bucket,
                ProjectSimilaritySnapshot.id != current.id,
            )
            .all()
        )
        stale_ids = [int(row[0]) for row in stale]
        if not stale_ids:
            return
        db.query(ProjectSimilarityEdge).filter(
            ProjectSimilarityEdge.snapshot_id.in_(stale_ids)
        ).delete(synchronize_session=False)
        db.query(ProjectSimilaritySnapshot).filter(
            ProjectSimilaritySnapshot.id.in_(stale_ids)
        ).delete(synchronize_session=False)

    def _build_response(
        self,
        project: Project,
        state: StoredEmbeddingState,
        items: list[SimilarProjectItem],
        min_similarity: float,
        same_category_only: bool,
        *,
        projection_status: str,
    ) -> ProjectSimilaritySearchResponse:
        return ProjectSimilaritySearchResponse(
            target_project_id=int(project.id),
            target_project_title=project.title,
            target_embedding_model=state.model,
            target_embedding_status=state.status,
            target_embedding_updated_at=state.updated_at,
            target_embedding_refresh_required=state.refresh_required,
            projection_status=projection_status,
            search_mode=(
                "read_model" if projection_status == "ready" else "stored_missing"
            ),
            same_category_only=same_category_only,
            min_similarity=round(min_similarity, 4),
            result_count=len(items),
            results=items,
        )

    def _skipped(self, project_id: int, reason: str) -> SimilarityProjectionResult:
        return SimilarityProjectionResult(
            status="skipped",
            project_id=int(project_id),
            reason=reason,
        )
