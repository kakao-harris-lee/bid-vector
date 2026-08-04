"""Typed internal contracts for similarity projections and inference outbox work."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.project import ProjectSimilaritySearchResponse, SimilarProjectItem


class StoredEmbeddingState(BaseModel):
    vector: list[float] = Field(default_factory=list)
    status: Literal["ready", "pending", "stale"]
    model: str | None = None
    updated_at: datetime | None = None
    refresh_required: bool


class ProjectEmbeddingInputState(BaseModel):
    title: str | None = None
    description: str | None = None
    requirements: str | None = None
    category: str | None = None
    notice_number: str | None = None
    issuing_agency: str | None = None
    demand_agency: str | None = None
    budget_estimate: float | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    status: str | None = None
    deadline: datetime | None = None


class SimilarityCorpusWatermark(BaseModel):
    embedding_count: int = Field(ge=0)
    embedding_updated_at: datetime | None = None


class SimilaritySearchPreparation(BaseModel):
    vector: list[float] = Field(default_factory=list)
    model: str | None = None
    status: Literal["ready", "pending", "stale"]
    updated_at: datetime | None = None
    refresh_required: bool
    response: ProjectSimilaritySearchResponse | None = None


class SimilaritySearchExecution(BaseModel):
    items: list[SimilarProjectItem] = Field(default_factory=list)
    search_mode: Literal["postgres_vector", "python_fallback"]


class SimilarityProjectionResult(BaseModel):
    status: Literal["completed", "skipped"]
    project_id: int
    edge_count: int = Field(default=0, ge=0)
    same_category_only: bool = True
    min_similarity_bucket: float = Field(default=0.15, ge=0.0, le=1.0)
    source: str | None = None
    reason: str | None = None


class InferenceOutboxProcessedEvent(BaseModel):
    event_id: int
    result: SimilarityProjectionResult


class InferenceOutboxFailure(BaseModel):
    event_id: int
    error: str


class InferenceOutboxProcessResult(BaseModel):
    processed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    recovered_count: int = Field(default=0, ge=0)
    processed_event_id_first: int | None = None
    processed_event_id_last: int | None = None
    result_sample_count: int = Field(default=0, ge=0)
    result_sample_truncated: bool = False
    event_ids: list[int] = Field(default_factory=list)
    failed_event_ids: list[int] = Field(default_factory=list)
    results: list[InferenceOutboxProcessedEvent] = Field(default_factory=list)
    failures: list[InferenceOutboxFailure] = Field(default_factory=list)


class SimilarityProjectionBackfillResult(BaseModel):
    selected_count: int = Field(ge=0)
    staged_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    project_ids: list[int] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)


class EmbeddingRebuildDispatchInput(BaseModel):
    outbox_event_ids: list[int] = Field(default_factory=list)


class EmbeddingRebuildDispatchResult(BaseModel):
    task_id: str | None = None
    queue: str | None = None
