"""Project CRUD, similarity search and embedding-refresh schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    title: str
    description: str
    requirements: str
    budget_estimate: float = Field(
        description=(
            "추정가격(부가세 별도 표기). 투찰율이 곱해지는 기초금액/사업금액과는 다른 "
            "금액이므로 이 값에 투찰율을 곱해 투찰금액을 검산하면 안 된다(#162)."
        )
    )
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


class ProjectDetailResponse(ProjectResponse):
    """공고 상세 — 목록 응답에 투찰 기준금액(기초금액)을 더한 형태.

    목록(``ProjectResponse``)에는 넣지 않는다. 기초금액 해석은 공고당 ``HistoricalData``
    조회를 한 번 더 요구하므로 목록 payload/쿼리를 그만큼 불리고, 운영자가 금액 basis 를
    확인해야 하는 자리는 상세 화면이기 때문이다.
    """

    bid_base_amount: float = Field(
        default=0.0,
        description=(
            "공고 투찰 기준금액(기초금액/사업금액, 과세 공고는 부가세 포함). 추천/제출 "
            "투찰금액이 곱해지는 금액이며 budget_estimate(추정가격)와 다르다."
        ),
    )
    bid_base_source: Optional[str] = Field(
        default=None,
        description=(
            "기초금액 출처(clean-base / reserve-estimate / base-fallback / "
            "budget-estimate-fallback). budget-estimate-fallback 은 기초금액을 확보하지 "
            "못해 추정가격을 그대로 기준금액으로 쓴 상태."
        ),
    )
    bid_base_to_estimate_ratio: Optional[float] = Field(
        default=None,
        description=(
            "기초금액 ÷ 추정가격. 1.0 이면 두 금액이 같고, 1.1 부근이면 기초금액이 "
            "추정가격에 없는 부가세를 포함한다는 뜻. 추정가격이 0 이면 null."
        ),
    )


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


class SimilarProjectSummary(BaseModel):
    """User-facing similar-project row without ML implementation metadata."""

    project_id: int
    title: str
    category: Optional[str] = None
    status: str
    budget_estimate: float
    deadline: Optional[datetime] = None
    created_at: datetime
    similarity_score: float = Field(ge=0.0, le=1.0)


class SimilarProjectsResponse(BaseModel):
    """User-facing similar-project results with storage details filtered out."""

    target_project_id: int
    target_project_title: str
    same_category_only: bool
    min_similarity: float = Field(ge=0.0, le=1.0)
    result_count: int
    results: List[SimilarProjectSummary] = Field(default_factory=list)


class ProjectSimilaritySearchResponse(BaseModel):
    """Internal/admin similarity result including diagnostic metadata."""

    target_project_id: int
    target_project_title: str
    target_embedding_model: Optional[str] = None
    target_embedding_status: Literal["ready", "pending", "stale"] = "ready"
    target_embedding_updated_at: Optional[datetime] = None
    target_embedding_refresh_required: bool = False
    projection_status: Literal["ready", "missing", "stale", "not_applicable"] = (
        "not_applicable"
    )
    search_mode: Literal[
        "read_model", "stored_missing", "postgres_vector", "python_fallback"
    ]
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


SimilarProjectsRefreshStatus = Literal[
    "accepted",
    "in_progress",
    "succeeded",
    "failed",
    "cancelled",
]


class SimilarProjectsRefreshOperationResponse(BaseModel):
    """User-facing handle for refreshing one project's similar-project results."""

    operation_id: str
    operation: Literal["refresh_similar_projects"] = "refresh_similar_projects"
    project_id: int
    status: SimilarProjectsRefreshStatus
    message: str
    poll_url: str


class SimilarProjectsRefreshOperationStatusResponse(BaseModel):
    """Infrastructure-neutral polling state for a similar-project refresh."""

    operation_id: str
    operation: Literal["refresh_similar_projects"] = "refresh_similar_projects"
    project_id: int
    status: SimilarProjectsRefreshStatus
    is_terminal: bool
    succeeded: bool
    message: str
    error: Optional[str] = None
