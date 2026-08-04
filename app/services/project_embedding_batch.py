"""Batch persistence for project embeddings and ready events."""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.models import Project


class ProjectEmbeddingBatchMixin:
    def refresh_project_embedding_details(
        self, db: Session, project: Project, *, force: bool = False
    ):
        _, model_name = self.refresh_project_embedding(db, project, force=force)
        db.flush()
        result = self._serialize_embedding_state(db, project, model_name)
        event = self._outbox.append_embedding_ready_event(db, project)
        if event is not None:
            db.flush()
            result["outbox_event_id"] = int(event.id)
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
    ):
        query = db.query(Project)
        normalized_ids = (
            sorted({int(project_id) for project_id in project_ids})
            if project_ids is not None
            else None
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
