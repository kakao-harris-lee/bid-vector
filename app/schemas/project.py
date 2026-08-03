"""Project CRUD, similarity search and embedding-refresh schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    title: str
    description: str
    requirements: str
    budget_estimate: float
    category: str
    notice_number: Optional[str] = None
    source_url: Optional[str] = None
    issuing_agency: Optional[str] = None
    demand_agency: Optional[str] = None


class ProjectCreate(ProjectBase):
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    deadline: Optional[datetime] = None


class ProjectResponse(ProjectBase):
    id: int
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SimilarProjectItem(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    status: str
    budget_estimate: float
    deadline: Optional[datetime] = None
    created_at: datetime
    similarity_score: float = Field(ge=0.0, le=1.0)
    embedding_model: Optional[str] = None


class ProjectSimilaritySearchResponse(BaseModel):
    target_project_id: int
    target_project_title: str
    target_embedding_model: Optional[str] = None
    target_embedding_status: Literal["ready", "pending", "stale"] = "ready"
    target_embedding_updated_at: Optional[datetime] = None
    target_embedding_refresh_required: bool = False
    search_mode: Literal["read_model", "postgres_vector", "python_fallback"]
    same_category_only: bool
    min_similarity: float = Field(ge=0.0, le=1.0)
    result_count: int
    results: List[SimilarProjectItem] = Field(default_factory=list)


class ProjectEmbeddingRefreshResponse(BaseModel):
    project_id: int
    title: str
    category: Optional[str] = None
    embedding_model: Optional[str] = None
    semantic_text_length: int = Field(ge=0)
    embedding_dimensions: int = Field(ge=0)
    embedding_updated_at: Optional[datetime] = None
    vector_storage_enabled: bool
    vector_persisted: bool


class ProjectEmbeddingBatchRefreshResponse(BaseModel):
    processed_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    category: Optional[str] = None
    project_status: Optional[str] = None
    force: bool
    vector_storage_enabled: bool
    project_ids: List[int] = Field(default_factory=list)
    results: List[ProjectEmbeddingRefreshResponse] = Field(default_factory=list)


class ProjectEmbeddingBatchRefreshTaskResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class ProjectEmbeddingRefreshTaskResponse(ProjectEmbeddingBatchRefreshTaskResponse):
    project_id: int
    queue: str


class ProjectEmbeddingBatchRefreshTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[ProjectEmbeddingBatchRefreshResponse] = None
