"""HTTP endpoints for synthetic-operator seed + per-operator backtest comparison."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.schemas import (
    CustomOperatorCloneRequest,
    CustomOperatorCreate,
    CustomOperatorDeleteResponse,
    CustomOperatorDetail,
    CustomOperatorUpdate,
    SyntheticExperimentCompareResponse,
    SyntheticExperimentCreate,
    SyntheticExperimentPresetListResponse,
    SyntheticExperimentResponse,
    SyntheticExperimentRunResponse,
)
from app.services.synthetic_backtest import SyntheticBacktestService
from app.services.synthetic_custom_operator import (
    CustomOperatorError,
    SyntheticCustomOperatorService,
)
from app.services.synthetic_experiment import SyntheticExperimentService
from app.tasks.jobs import (
    enqueue_synthetic_operator_backtest,
    get_synthetic_backtest_task_status,
)

router = APIRouter(prefix="/synthetic", tags=["synthetic"])


# --- schemas -------------------------------------------------------------------


class SyntheticOperatorItem(BaseModel):
    user_id: int
    username: str
    slug: str
    # True iff this is a web-defined custom company (slug carries ``custom-``);
    # presets (12 archetypes) and any future built-ins are False.
    is_custom: bool = False
    display_name: str
    company: Optional[str] = None
    business_type: Optional[str] = None
    annual_revenue: float = 0.0
    capacity_score: float = 0.0
    bid_now_threshold: float = 0.0
    review_threshold: float = 0.0


class SyntheticOperatorListResponse(BaseModel):
    operator_count: int = Field(ge=0)
    operators: List[SyntheticOperatorItem] = Field(default_factory=list)


class SyntheticSeedResponse(BaseModel):
    seeded_count: int = Field(ge=0)
    purged_count: int = Field(default=0, ge=0)
    operators: List[SyntheticOperatorItem] = Field(default_factory=list)


class SyntheticBacktestRunRequest(BaseModel):
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=1000)
    scenario: str = "base"
    slugs: Optional[List[str]] = None


class SyntheticBacktestSettlementItem(BaseModel):
    project_id: Optional[int] = None
    project_title: str = ""
    category: Optional[str] = None
    paper_bid_id: Optional[int] = None
    decision_action: Optional[str] = None
    bid_amount: Optional[float] = None
    winning_amount: Optional[float] = None
    absolute_bid_rate_error: Optional[float] = None
    would_have_won: bool = False
    settled_at: Optional[datetime] = None


class SyntheticBacktestOperatorResult(SyntheticOperatorItem):
    candidate_count: int = 0
    paper_bid_count: int = 0
    settled_count: int = 0
    would_have_won_count: int = 0
    win_rate_on_settled: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_submission_rate: Optional[float] = None
    average_absolute_bid_rate_error: Optional[float] = None
    settlement_sample_count: int = 0
    settlement_items: List[SyntheticBacktestSettlementItem] = Field(
        default_factory=list
    )


class SyntheticBacktestRunResponse(BaseModel):
    operator_count: int
    category: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    limit: int
    scenario: str
    results: List[SyntheticBacktestOperatorResult] = Field(default_factory=list)


class SyntheticSeedRequest(BaseModel):
    purge: bool = False


# --- routes --------------------------------------------------------------------


@router.get("/operators", response_model=SyntheticOperatorListResponse)
def list_synthetic_operators_endpoint(db: Session = Depends(get_db)):
    """Return seeded synthetic operators (`synthetic-*`)."""
    service = SyntheticBacktestService()
    operators = service.list_operators(db)
    return {"operator_count": len(operators), "operators": operators}


@router.post(
    "/operators/seed",
    response_model=SyntheticSeedResponse,
    status_code=status.HTTP_200_OK,
)
def seed_synthetic_operators_endpoint(
    request: SyntheticSeedRequest,
    db: Session = Depends(get_db),
):
    """Idempotent seed/upsert of the 12-archetype synthetic operator catalogue.

    Optionally purges existing synthetic rows first (`purge=true`).
    """
    # Imports are local to avoid Pydantic 'model_*' field conflicts during app import.
    from scripts.seed_synthetic_operators import (
        ARCHETYPES,
        purge_synthetic_operators,
        upsert_synthetic_operator,
    )

    purged_count = 0
    if request.purge:
        purge_result = purge_synthetic_operators(db)
        purged_count = int(purge_result.get("removed_count") or 0)

    seeded_count = 0
    for archetype in ARCHETYPES:
        upsert_synthetic_operator(db, archetype)
        seeded_count += 1

    db.commit()
    service = SyntheticBacktestService()
    operators = service.list_operators(db)
    return {
        "seeded_count": seeded_count,
        "purged_count": purged_count,
        "operators": operators,
    }


# --- Custom synthetic companies (Phase 3) --------------------------------------


def _custom_operator_http_error(exc: CustomOperatorError) -> HTTPException:
    """Map a custom-operator domain error to its HTTP status."""
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post(
    "/custom-operators",
    response_model=CustomOperatorDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_operator_endpoint(
    request: CustomOperatorCreate,
    db: Session = Depends(get_db),
):
    """Create a custom synthetic company (`synthetic-custom-<slug>`).

    Slug collisions return 409. The new company is automatically picked up by
    `list_operators` (slug `custom-*`), so it appears in subsequent backtests.
    """
    service = SyntheticCustomOperatorService(db)
    try:
        return service.create(request.model_dump(exclude_unset=True))
    except CustomOperatorError as exc:
        raise _custom_operator_http_error(exc)


@router.get(
    "/custom-operators/{slug}",
    response_model=CustomOperatorDetail,
)
def get_custom_operator_endpoint(
    slug: str,
    db: Session = Depends(get_db),
):
    """Fetch a single custom company's full profile+strategy (edit-form prefill).

    Presets/operator are protected (400); a missing custom company returns 404.
    """
    service = SyntheticCustomOperatorService(db)
    try:
        return service.get(slug)
    except CustomOperatorError as exc:
        raise _custom_operator_http_error(exc)


@router.put(
    "/custom-operators/{slug}",
    response_model=CustomOperatorDetail,
)
def update_custom_operator_endpoint(
    slug: str,
    request: CustomOperatorUpdate,
    db: Session = Depends(get_db),
):
    """Partial-update a custom company. Presets/operator are protected (400)."""
    service = SyntheticCustomOperatorService(db)
    try:
        return service.update(slug, request.model_dump(exclude_unset=True))
    except CustomOperatorError as exc:
        raise _custom_operator_http_error(exc)


@router.post(
    "/custom-operators/{slug}/clone",
    response_model=CustomOperatorDetail,
    status_code=status.HTTP_201_CREATED,
)
def clone_custom_operator_endpoint(
    slug: str,
    request: CustomOperatorCloneRequest,
    db: Session = Depends(get_db),
):
    """Clone a preset OR custom company (`slug`) into a new custom company.

    The source is read-only; body fields override the copied values. Missing
    source returns 404.
    """
    service = SyntheticCustomOperatorService(db)
    try:
        return service.clone(slug, request.model_dump(exclude_unset=True))
    except CustomOperatorError as exc:
        raise _custom_operator_http_error(exc)


@router.delete(
    "/custom-operators/{slug}",
    response_model=CustomOperatorDeleteResponse,
)
def delete_custom_operator_endpoint(
    slug: str,
    db: Session = Depends(get_db),
):
    """Delete a custom company. Presets/operator are protected (400); 404 if absent."""
    service = SyntheticCustomOperatorService(db)
    try:
        return service.delete(slug)
    except CustomOperatorError as exc:
        raise _custom_operator_http_error(exc)


class SyntheticBacktestTaskResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: str
    detail: str
    poll_url: str


class SyntheticBacktestTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: str
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[SyntheticBacktestRunResponse] = None


@router.post(
    "/backtests/run-async",
    response_model=SyntheticBacktestTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_synthetic_backtest_async_endpoint(
    request: SyntheticBacktestRunRequest,
    db: Session = Depends(get_db),
):
    """Queue the synthetic backtest in a Celery worker and return a pollable task id.

    Use when the synchronous path could exceed the API request timeout
    (12 operators × large `limit`, slow predictor warm-up, etc.).
    """
    service = SyntheticBacktestService()
    operators = service.list_operators(db)
    if not operators:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No synthetic operators seeded. Seed via POST /operators/seed first.",
        )

    payload = {
        "start_at": request.start_at.isoformat() if request.start_at else None,
        "end_at": request.end_at.isoformat() if request.end_at else None,
        "category": request.category,
        "limit": request.limit,
        "scenario": request.scenario,
        "slugs": request.slugs,
    }
    async_result = enqueue_synthetic_operator_backtest(payload=payload)
    status_payload = get_synthetic_backtest_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "queue": status_payload["queue"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/synthetic/backtests/tasks/{async_result.id}",
    }


@router.get(
    "/backtests/tasks/{task_id}",
    response_model=SyntheticBacktestTaskStatusResponse,
)
def get_synthetic_backtest_task_status_endpoint(task_id: str):
    """Inspect the status (+ result) of a queued synthetic backtest task."""
    return get_synthetic_backtest_task_status(task_id)


@router.post("/backtests/run", response_model=SyntheticBacktestRunResponse)
def run_synthetic_backtest_endpoint(
    request: SyntheticBacktestRunRequest,
    db: Session = Depends(get_db),
):
    """Run the historical paper-bidding backtest for every synthetic operator.

    Synchronous for now (small operator count + bounded `limit`). Frontend can
    show a loading state; a Celery wrapper can wrap this later if needed.
    """
    service = SyntheticBacktestService()
    operators = service.list_operators(db)
    if not operators:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No synthetic operators seeded. Seed via POST /operators/seed first.",
        )
    return service.run_for_all(
        db,
        start_at=request.start_at,
        end_at=request.end_at,
        category=request.category,
        limit=request.limit,
        scenario=request.scenario,
        slugs=request.slugs,
    )


# --- Experiment Lab (Phase 1): persist definitions + async run/poll ------------


@router.post(
    "/experiments",
    response_model=SyntheticExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_experiment_endpoint(
    request: SyntheticExperimentCreate,
    db: Session = Depends(get_db),
):
    """Save a synthetic experiment definition."""
    service = SyntheticExperimentService(db)
    experiment = service.create_experiment(
        name=request.name,
        description=request.description,
        params=request.params.model_dump(mode="json"),
        operator_slugs=request.operator_slugs,
    )
    return service.serialize_experiment(experiment)


@router.get("/experiments", response_model=List[SyntheticExperimentResponse])
def list_experiments_endpoint(db: Session = Depends(get_db)):
    """List saved synthetic experiments (most recent first)."""
    service = SyntheticExperimentService(db)
    return [
        service.serialize_experiment(experiment)
        for experiment in service.list_experiments()
    ]


@router.get(
    "/experiments/presets",
    response_model=SyntheticExperimentPresetListResponse,
)
def list_experiment_presets_endpoint(db: Session = Depends(get_db)):
    """Return fixed G-1 experiment presets and their saved experiment state."""
    service = SyntheticExperimentService(db)
    return {"presets": service.list_presets()}


@router.post(
    "/experiments/presets/{preset_name}",
    response_model=SyntheticExperimentResponse,
)
def ensure_experiment_preset_endpoint(
    preset_name: str,
    db: Session = Depends(get_db),
):
    """Create/update the saved experiment backing a fixed G-1 preset."""
    service = SyntheticExperimentService(db)
    experiment = service.ensure_preset(preset_name)
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment preset not found.",
        )
    return service.serialize_experiment(experiment)


@router.get(
    "/experiments/compare",
    response_model=SyntheticExperimentCompareResponse,
)
def compare_experiment_runs_endpoint(
    run_a: int = Query(..., description="Run id for the A (baseline) side."),
    run_b: int = Query(..., description="Run id for the B (candidate) side."),
    db: Session = Depends(get_db),
):
    """A/B compare two runs' per-operator metrics, joined by ``operator_slug``.

    ``delta`` is ``B - A`` (positive => B higher). The two runs may belong to
    different experiments -- the join is purely on the slug intersection.
    Returns 404 when either run id is unknown. ``win_rate_*`` values stay
    price-only estimates (NOT actual awards).
    """
    service = SyntheticExperimentService(db)
    comparison = service.compare_runs(run_a, run_b)
    if comparison is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both runs not found.",
        )
    return comparison


@router.get(
    "/experiments/{experiment_id}",
    response_model=SyntheticExperimentResponse,
)
def get_experiment_endpoint(experiment_id: int, db: Session = Depends(get_db)):
    """Fetch a synthetic experiment detail including its run history."""
    service = SyntheticExperimentService(db)
    experiment = service.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found.",
        )
    return service.serialize_experiment(experiment)


@router.post(
    "/experiments/{experiment_id}/runs",
    response_model=SyntheticExperimentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_experiment_run_endpoint(
    experiment_id: int,
    db: Session = Depends(get_db),
):
    """Trigger an asynchronous run of a saved experiment."""
    service = SyntheticExperimentService(db)
    experiment = service.get_experiment(experiment_id)
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found.",
        )
    run = service.create_run(experiment)
    return service.serialize_run_detail(run)


@router.get(
    "/experiments/{experiment_id}/runs/{run_id}",
    response_model=SyntheticExperimentRunResponse,
)
def get_experiment_run_endpoint(
    experiment_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Poll the status/results of a synthetic experiment run."""
    service = SyntheticExperimentService(db)
    run = service.get_run(experiment_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found.",
        )
    return service.serialize_run_detail(run)


@router.get(
    "/experiments/{experiment_id}/runs/{run_id}/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_experiment_run_csv_endpoint(
    experiment_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Download a run's per-operator metrics as a CSV attachment.

    A run with no results (e.g. not yet completed) yields a header-only CSV with
    HTTP 200. Returns 404 when the run is unknown. ``win_rate_*`` columns remain
    price-only estimates (NOT actual awards).
    """
    service = SyntheticExperimentService(db)
    run = service.get_run(experiment_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found.",
        )
    csv_body = service.export_run_csv(run)
    filename = f"experiment-{experiment_id}-run-{run_id}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
