"""Pydantic schemas for request/response"""
from datetime import datetime
from typing import List, Literal, Optional

try:  # pragma: no cover - optional dependency fallback
    import email_validator  # noqa: F401
    from pydantic import BaseModel, ConfigDict, EmailStr, Field
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    from pydantic import BaseModel, ConfigDict, Field

    EmailStr = str


# User Schemas
class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: str
    company: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    company: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OperatorLoginRequest(BaseModel):
    username: str
    password: str


class OperatorProfileUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    company: Optional[str] = None
    business_type: Optional[str] = None
    license_codes: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None
    annual_revenue: Optional[float] = Field(default=None, ge=0.0)
    capacity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    total_awards: Optional[int] = Field(default=None, ge=0)


class OperatorProfileResponse(BaseModel):
    operator_id: int
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    company: Optional[str] = None
    is_active: bool
    created_at: datetime
    business_type: str
    license_codes: List[str] = Field(default_factory=list)
    region_codes: List[str] = Field(default_factory=list)
    annual_revenue: float
    capacity_score: float
    total_awards: int
    profile_configured: bool


class OperatorOverviewResponse(BaseModel):
    operator_id: int
    project_count: int
    bid_count: int
    active_bid_count: int
    prediction_count: int
    unread_notification_count: int
    recent_event_count: int
    profile_configured: bool


# Authentication Schemas
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    operator_id: Optional[int] = None
    username: Optional[str] = None


# Project Schemas
class ProjectBase(BaseModel):
    title: str
    description: str
    requirements: str
    budget_estimate: float
    category: str


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
    search_mode: Literal["postgres_vector", "python_fallback"]
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


# Bid Schemas
class BidBase(BaseModel):
    bid_amount: float
    proposed_timeline: int


class BidCreate(BidBase):
    project_id: int
    description: str


class BidUpdate(BaseModel):
    bid_amount: Optional[float] = None
    description: Optional[str] = None


class BidResponse(BidBase):
    id: int
    project_id: int
    operator_id: int
    user_id: int
    status: str
    decision_record_id: Optional[int] = None
    decision_status: Optional[Literal["planned", "reviewing", "submitted", "skipped"]] = None
    score: Optional[float] = None
    created_at: datetime


# Price Prediction Schemas
class PricePredictionRequest(BaseModel):
    project_id: int
    budget_estimate: float
    category: str
    description: str


class PricePredictionResponse(BaseModel):
    predicted_price: float
    price_range_min: float
    price_range_max: float
    confidence_score: float
    model_version: str


# Bid Recommendation Schemas
class BidRecommendationRequest(BaseModel):
    project_id: int
    user_historical_data: Optional[dict] = None


class BidRecommendationResponse(BaseModel):
    recommended_bid: float
    confidence_score: float
    reasoning: str
    market_analysis: Optional[dict] = None


# Document Analysis Schemas
class DocumentAnalysisRequest(BaseModel):
    project_id: int
    document_content: str
    document_type: str


class DocumentAnalysisResponse(BaseModel):
    key_requirements: List[str]
    complexity_score: float
    estimated_effort: float
    risks: List[str]


# Notification Schemas
class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Analytics Schemas
class AnalyticsEventRequest(BaseModel):
    event_type: str
    event_data: dict


class AnalyticsSummaryResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_projects: int
    total_events: int
    mode: Literal["single_operator"] = "single_operator"


class OperatorStatsResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_events: int
    bids_count: int
    requested_user_id: Optional[int] = None
    mode: Literal["single_operator"] = "single_operator"


class LegacyAdminStatsResponse(BaseModel):
    operator_id: int
    total_users: int
    active_users: int
    total_projects: int
    total_bids: int
    mode: Literal["single_operator"] = "single_operator"


class LegacyAdminActionResponse(BaseModel):
    status: str
    operator_id: int
    requested_user_id: int
    mode: Literal["single_operator"] = "single_operator"


class CrawlRequest(BaseModel):
    source: str = "koneps"
    category: Optional[str] = None
    target_date: Optional[str] = None
    keyword: Optional[str] = None
    execution_mode: Literal["mock", "live", "auto"] = "mock"
    max_items: int = Field(default=10, ge=1, le=100)


class CrawlNoticeItem(BaseModel):
    notice_number: str
    title: str
    base_amount: float
    estimated_amount: Optional[float] = None
    closing_at: Optional[datetime] = None
    business_type: Optional[str] = None
    region: Optional[str] = None
    license_codes: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class CrawlResponse(BaseModel):
    job_status: str
    source: str
    collected_count: int
    items: List[CrawlNoticeItem]
    metadata: dict = Field(default_factory=dict)


class CrawlTaskResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str
    crawl_job_id: int


class CrawlTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    crawl_job_id: Optional[int] = None
    error: Optional[str] = None
    result: Optional[CrawlResponse] = None


class ClassificationRequest(BaseModel):
    project_id: int
    user_id: Optional[int] = Field(default=None, description="Legacy compatibility field; omit in single-user mode.")


class ClassificationResponse(BaseModel):
    matched: bool
    score: float
    reasons: List[str]


class BidDecisionRequest(BaseModel):
    project_id: int
    recommended_amount: float
    probability_score: float
    matched_score: float = Field(default=0.0, ge=0.0, le=1.0)
    deadline_hours_remaining: Optional[int] = Field(default=None, ge=0)
    current_active_bids: int = Field(default=0, ge=0)
    max_active_bids: int = Field(default=3, ge=1)
    current_workload_score: float = Field(default=0.0, ge=0.0, le=1.0)


class BidDecisionResponse(BaseModel):
    project_id: int
    pursue_bid: bool
    action: Literal["bid_now", "review", "skip"]
    priority_score: float
    recommended_amount: float
    probability_score: float
    reasoning: str


class BidDecisionSaveRequest(BidDecisionRequest):
    decision_status: Optional[Literal["planned", "reviewing", "submitted", "skipped"]] = None


class BidDecisionRecordResponse(BaseModel):
    id: int
    project_id: int
    operator_id: int
    pursue_bid: bool
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float
    recommended_amount: float
    probability_score: float
    matched_score: float
    deadline_hours_remaining: Optional[int] = None
    current_active_bids: int
    max_active_bids: int
    current_workload_score: float
    reasoning: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Backward-compatible aliases while the domain language migrates away from multi-user allocation.
AllocationRequest = BidDecisionRequest
AllocationResponse = BidDecisionResponse


class TelegramNotificationRequest(BaseModel):
    title: str
    message: str
    url: Optional[str] = None


class TelegramCallbackChat(BaseModel):
    id: int


class TelegramCallbackMessage(BaseModel):
    message_id: int
    chat: TelegramCallbackChat


class TelegramCallbackQuery(BaseModel):
    id: str
    data: str
    message: Optional[TelegramCallbackMessage] = None


class TelegramCallbackUpdateRequest(BaseModel):
    update_id: Optional[int] = None
    callback_query: TelegramCallbackQuery


class TelegramActionResponse(BaseModel):
    status: str
    detail: str
    decision_record_id: int
    action: Literal["bid_now", "review", "skip"]
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]


class TelegramSyncResponse(BaseModel):
    status: str
    detail: str
    processed_count: int
    processed_update_ids: List[int] = Field(default_factory=list)
    known_chat_ids: List[int] = Field(default_factory=list)


class TelegramStatusResponse(BaseModel):
    configured: bool
    delivery_chat_id: Optional[str] = None
    pending_update_count: int = 0
    webhook_url: str = ""
    has_custom_certificate: bool = False
    known_chat_ids: List[int] = Field(default_factory=list)


class BackgroundJobResponse(BaseModel):
    task_name: str
    status: str
    detail: str
