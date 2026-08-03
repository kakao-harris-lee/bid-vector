"""Project routes"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    get_current_operator_from_bearer,
    require_privileged_operator,
)
from app.models.models import Project, User
from app.schemas.schemas import (
    ProjectCreate,
    ProjectEmbeddingBatchRefreshTaskResponse,
    ProjectEmbeddingBatchRefreshTaskStatusResponse,
    ProjectEmbeddingRefreshTaskResponse,
    ProjectResponse,
    SimilarProjectsRefreshOperationResponse,
    SimilarProjectsRefreshOperationStatusResponse,
)
from app.schemas.project import SimilarProjectsResponse
from app.services.project_similarity import ProjectSimilarityService
from app.services.project_writes import (
    ProjectWriteNotFound,
    ProjectWriteService,
    get_project_write_service,
)
from app.services.similar_projects_refresh import (
    SimilarProjectsRefreshOperationNotFound,
    SimilarProjectsRefreshService,
    get_similar_projects_refresh_service,
)
from app.tasks.jobs import (
    enqueue_project_embedding_refresh,
    enqueue_project_embedding_rebuild,
    get_project_embedding_rebuild_task_status,
)

router = APIRouter()


@router.post("/", response_model=ProjectResponse)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    write_service: ProjectWriteService = Depends(get_project_write_service),
):
    """Create a new project"""
    return write_service.create(db, project)


@router.get("/", response_model=List[ProjectResponse])
def list_projects(
    response: Response,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    q: Optional[str] = Query(
        default=None,
        description="제목/공고번호 부분 일치(LIKE) 검색어",
    ),
    agency: Optional[str] = Query(
        default=None,
        description="발주/수요 기관명 부분 일치 검색어",
    ),
    budget_min: Optional[float] = Query(
        default=None,
        ge=0.0,
        description="budget_estimate >= 값으로 필터",
    ),
    budget_max: Optional[float] = Query(
        default=None,
        ge=0.0,
        description="budget_estimate <= 값으로 필터",
    ),
    db: Session = Depends(get_db),
):
    """List projects with optional text/agency/budget filters.

    The response payload stays a list (backward compatible). Total matching
    count is exposed via the `X-Total-Count` response header so the frontend
    can drive pagination without changing the response envelope.
    """
    query = db.query(Project)

    if category:
        query = query.filter(Project.category == category)

    if status:
        query = query.filter(Project.status == status)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Project.title.ilike(like), Project.notice_number.ilike(like))
        )

    if agency:
        agency_like = f"%{agency}%"
        query = query.filter(
            or_(
                Project.issuing_agency.ilike(agency_like),
                Project.demand_agency.ilike(agency_like),
            )
        )

    if budget_min is not None:
        query = query.filter(Project.budget_estimate >= budget_min)

    if budget_max is not None:
        query = query.filter(Project.budget_estimate <= budget_max)

    total = query.with_entities(func.count(Project.id)).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    projects = (
        query.order_by(Project.created_at.desc(), Project.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return projects


def _enqueue_project_embedding_rebuild_response(
    *,
    limit: int,
    offset: int,
    category: str | None,
    project_status: str | None,
    force: bool,
) -> dict:
    """Queue a project embedding rebuild and shape the pollable API response."""
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


@router.post(
    "/embeddings/rebuild",
    response_model=ProjectEmbeddingBatchRefreshTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    dependencies=[Depends(require_privileged_operator)],
)
def rebuild_project_embeddings(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    project_status: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    """Queue a semantic embedding backfill without running it inside the API request."""
    return _enqueue_project_embedding_rebuild_response(
        limit=limit,
        offset=offset,
        category=category,
        project_status=project_status,
        force=force,
    )


@router.post(
    "/embeddings/rebuild/async",
    response_model=ProjectEmbeddingBatchRefreshTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    dependencies=[Depends(require_privileged_operator)],
)
def rebuild_project_embeddings_async(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    project_status: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    """Queue a batch embedding rebuild task and return a pollable task id."""
    return _enqueue_project_embedding_rebuild_response(
        limit=limit,
        offset=offset,
        category=category,
        project_status=project_status,
        force=force,
    )


@router.get(
    "/embeddings/rebuild/tasks/{task_id}",
    response_model=ProjectEmbeddingBatchRefreshTaskStatusResponse,
    deprecated=True,
    dependencies=[Depends(require_privileged_operator)],
)
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


@router.get("/{project_id}/similar", response_model=SimilarProjectsResponse)
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
        stored_only=True,
    )
    return response


@router.post(
    "/{project_id}/similar/refresh",
    response_model=SimilarProjectsRefreshOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_similar_projects(
    project_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
    refresh_service: SimilarProjectsRefreshService = Depends(
        get_similar_projects_refresh_service
    ),
):
    """Queue a user-facing refresh without exposing ML infrastructure details."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return refresh_service.start(
        db,
        project=project,
        operator=operator,
        force=force,
    )


@router.get(
    "/{project_id}/similar/refresh/operations/{operation_id}",
    response_model=SimilarProjectsRefreshOperationStatusResponse,
)
def get_similar_projects_refresh_status(
    project_id: int,
    operation_id: str,
    db: Session = Depends(get_db),
    operator: User = Depends(get_current_operator_from_bearer),
    refresh_service: SimilarProjectsRefreshService = Depends(
        get_similar_projects_refresh_service
    ),
):
    """Return the domain lifecycle state for a similar-project refresh."""
    try:
        return refresh_service.get_status(
            db,
            operation_id=operation_id,
            project_id=project_id,
            operator=operator,
        )
    except SimilarProjectsRefreshOperationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Refresh operation not found",
        ) from exc


@router.post(
    "/{project_id}/embedding/refresh",
    response_model=ProjectEmbeddingRefreshTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    dependencies=[Depends(require_privileged_operator)],
)
def refresh_project_embedding(
    project_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Queue one project's semantic embedding refresh without running ML inline."""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    async_result = enqueue_project_embedding_refresh(project_id=project.id, force=force)
    status_payload = get_project_embedding_rebuild_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "project_id": project.id,
        "queue": settings.CELERY_ML_INFERENCE_QUEUE,
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/projects/embeddings/rebuild/tasks/{async_result.id}",
    }


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_update: ProjectCreate,
    db: Session = Depends(get_db),
    write_service: ProjectWriteService = Depends(get_project_write_service),
):
    """Update project"""
    try:
        return write_service.update(
            db,
            project_id=project_id,
            project_update=project_update,
        )
    except ProjectWriteNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from exc
