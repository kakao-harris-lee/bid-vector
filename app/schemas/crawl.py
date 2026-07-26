"""KONEPS crawl request-response and classification schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class CrawlRequest(BaseModel):
    source: str = "koneps"
    category: Optional[str] = None
    target_date: Optional[str] = None
    keyword: Optional[str] = None
    execution_mode: Literal["mock", "live", "auto"] = "mock"
    max_items: int = Field(default=10, ge=1, le=500)
    # scsbid award coverage — all optional, fully backward compatible.
    categories: Optional[List[str]] = Field(
        default=None,
        description="scsbid multi-category sweep. None falls back to the single `category`.",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="scsbid award date-window start (YYYYMMDD or ISO).",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="scsbid award date-window end (YYYYMMDD or ISO).",
    )
    lookback_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="scsbid rolling window: end=today, start=today-lookback_days.",
    )
    page_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=999,
        description="scsbid numOfRows per page. Defaults to 100.",
    )
    max_pages: Optional[int] = Field(
        default=None,
        ge=1,
        description="scsbid per-category page ceiling. Defaults to 30.",
    )
    collect_reserve_detail: bool = Field(
        default=True,
        description="When False, skip per-item reserve-price detail fetches.",
    )


class CrawlNoticeItem(BaseModel):
    notice_number: str
    title: str
    base_amount: float
    estimated_amount: Optional[float] = None
    closing_at: Optional[datetime] = None
    business_type: Optional[str] = None
    business_type_code: Optional[str] = None
    business_type_label: Optional[str] = None
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
    user_id: Optional[int] = Field(
        default=None,
        description="Legacy compatibility field; omit in single-user mode.",
    )


class ClassificationResponse(BaseModel):
    matched: bool
    score: float
    reasons: List[str]
    criteria: dict = Field(default_factory=dict)
    score_breakdown: dict = Field(default_factory=dict)
