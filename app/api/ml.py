"""ML operation endpoints that only enqueue background work."""

from fastapi import APIRouter, Depends, Query, status

from app.core.config import settings
from app.core.security import require_privileged_operator
from app.schemas.schemas import MLTaskResponse, MLTaskStatusResponse, PricePredictionTrainingRequest
from app.tasks.jobs import (
    enqueue_decision_experiment_reevaluation,
    enqueue_price_predictor_training,
    enqueue_project_embedding_rebuild,
    get_decision_experiment_reevaluation_task_status,
    get_price_predictor_training_task_status,
    get_project_embedding_rebuild_task_status,
)

router = APIRouter(dependencies=[Depends(require_privileged_operator)])


@router.post(
    "/backfills/project-embeddings",
    response_model=MLTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_project_embedding_backfill(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    project_status: str | None = Query(default=None),
    force: bool = Query(default=False),
):
    """Queue a project embedding backfill on the ML backfill queue."""
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
        "queue": settings.CELERY_ML_BACKFILL_QUEUE,
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/admin/ml/backfills/project-embeddings/tasks/{async_result.id}",
    }


@router.get(
    "/backfills/project-embeddings/tasks/{task_id}",
    response_model=MLTaskStatusResponse,
)
def get_project_embedding_backfill_task_status(task_id: str):
    """Inspect the status of a queued project embedding backfill."""
    payload = get_project_embedding_rebuild_task_status(task_id)
    payload["queue"] = settings.CELERY_ML_BACKFILL_QUEUE
    return payload


@router.post(
    "/training/price-predictor",
    response_model=MLTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_price_predictor_training_job(request: PricePredictionTrainingRequest):
    """Queue price predictor training on the dedicated training queue."""
    async_result = enqueue_price_predictor_training(request_payload=request.model_dump(mode="json"))
    status_payload = get_price_predictor_training_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "queue": settings.CELERY_ML_TRAINING_QUEUE,
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/admin/ml/training/price-predictor/tasks/{async_result.id}",
    }


@router.get(
    "/training/price-predictor/tasks/{task_id}",
    response_model=MLTaskStatusResponse,
)
def get_price_predictor_training_job_status(task_id: str):
    """Inspect the status of a queued price predictor training job."""
    return get_price_predictor_training_task_status(task_id)


@router.post(
    "/reevaluations/decision-experiments/{experiment_run_id}",
    response_model=MLTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_decision_experiment_reevaluation_job(experiment_run_id: int):
    """Queue decision experiment re-evaluation on the ML re-evaluation queue."""
    async_result = enqueue_decision_experiment_reevaluation(experiment_run_id=experiment_run_id)
    status_payload = get_decision_experiment_reevaluation_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "queue": settings.CELERY_ML_REEVALUATION_QUEUE,
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": (
            "/api/v1/admin/ml/reevaluations/decision-experiments/tasks/"
            f"{async_result.id}"
        ),
    }


@router.get(
    "/reevaluations/decision-experiments/tasks/{task_id}",
    response_model=MLTaskStatusResponse,
)
def get_decision_experiment_reevaluation_job_status(task_id: str):
    """Inspect the status of a queued decision experiment re-evaluation."""
    return get_decision_experiment_reevaluation_task_status(task_id)
