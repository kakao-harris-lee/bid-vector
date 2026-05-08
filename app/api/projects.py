"""Project routes"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Project
from app.schemas.schemas import (
    ProjectCreate,
    ProjectEmbeddingBatchRefreshResponse,
    ProjectEmbeddingBatchRefreshTaskResponse,
    ProjectEmbeddingBatchRefreshTaskStatusResponse,
    ProjectEmbeddingRefreshResponse,
    ProjectResponse,
    ProjectSimilaritySearchResponse,
)
from app.services.project_similarity import ProjectSimilarityService
from app.tasks.jobs import (
    enqueue_project_embedding_rebuild,
    get_project_embedding_rebuild_task_status,
)

router = APIRouter()


@router.post("/", response_model=ProjectResponse)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """Create a new project"""
    db_project = Project(
        title=project.title,
        description=project.description,
        requirements=project.requirements,
        budget_estimate=project.budget_estimate,
        budget_min=project.budget_min,
        budget_max=project.budget_max,
        category=project.category,
        deadline=project.deadline,
    )
    db.add(db_project)
    db.flush()
    ProjectSimilarityService().refresh_project_embedding(db, db_project)
    db.commit()
    db.refresh(db_project)

    return db_project


@router.get("/", response_model=List[ProjectResponse])
def list_projects(
    skip: int = 0,
    limit: int = 100,
    category: str = None,
    status: str = None,
    db: Session = Depends(get_db)
):
    """List all projects"""
    query = db.query(Project)

    if category:
        query = query.filter(Project.category == category)

    if status:
        query = query.filter(Project.status == status)

    projects = query.offset(skip).limit(limit).all()
    return projects


@router.post("/embeddings/rebuild", response_model=ProjectEmbeddingBatchRefreshResponse)
def rebuild_project_embeddings(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    project_status: str | None = Query(default=None),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Rebuild semantic embeddings for a filtered batch of projects."""
    response = ProjectSimilarityService().rebuild_project_embeddings(
        db,
        limit=limit,
        offset=offset,
        category=category,
        project_status=project_status,
        force=force,
    )
    db.commit()
    return response


@router.post("/embeddings/rebuild/async", response_model=ProjectEmbeddingBatchRefreshTaskResponse)
def rebuild_project_embeddings_async(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    project_status: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    """Queue a batch embedding rebuild task and return a pollable task id."""
    async_result = enqueue_project_embedding_rebuild(
        limit=limit,
        offset=offset,
        category=category,
        project_status=project_status,
        force=force,
    )
    status_payload = get_project_embedding_rebuild_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/projects/embeddings/rebuild/tasks/{async_result.id}",
    }


@router.get("/embeddings/rebuild/tasks/{task_id}", response_model=ProjectEmbeddingBatchRefreshTaskStatusResponse)
def get_rebuild_project_embeddings_task_status(task_id: str):
    """Inspect the current status and result of a queued embedding rebuild task."""
    return get_project_embedding_rebuild_task_status(task_id)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Get project details"""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    return project


@router.get("/{project_id}/similar", response_model=ProjectSimilaritySearchResponse)
def get_similar_projects(
    project_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    min_similarity: float = Query(default=0.15, ge=0.0, le=1.0),
    same_category_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Return semantically similar procurement notices for a given project."""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    response = ProjectSimilarityService().find_similar_projects(
        db,
        project,
        limit=limit,
        min_similarity=min_similarity,
        same_category_only=same_category_only,
    )
    db.commit()
    return response


@router.post("/{project_id}/embedding/refresh", response_model=ProjectEmbeddingRefreshResponse)
def refresh_project_embedding(
    project_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Rebuild one project's semantic embedding and persist the latest vector metadata."""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    response = ProjectSimilarityService().refresh_project_embedding_details(db, project, force=force)
    db.commit()
    return response


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_update: ProjectCreate,
    db: Session = Depends(get_db)
):
    """Update project"""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Update fields
    for key, value in project_update.dict(exclude_unset=True).items():
        setattr(project, key, value)

    ProjectSimilarityService().refresh_project_embedding(db, project, force=True)
    db.commit()
    db.refresh(project)

    return project
