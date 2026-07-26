"""Paper-bidding backtest run and background-job schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BacktestDataAuditResponse(BaseModel):
    generated_at: str
    filters: dict = Field(default_factory=dict)
    table_counts: dict = Field(default_factory=dict)
    window_counts: dict = Field(default_factory=dict)
    date_range: dict = Field(default_factory=dict)
    category_breakdown: List[dict] = Field(default_factory=list)


class PaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=5000)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "local-backtest"
    model_version: str = "current"
    cutoff_hours_before_deadline: int = Field(default=2, ge=0, le=168)
    history_limit: int = Field(default=80, ge=1, le=500)
    settle_actions: List[Literal["bid_now", "review", "skip"]] = Field(
        default_factory=lambda: ["bid_now"]
    )
    persist: bool = False


class ForwardPaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "forward-paper"
    model_version: str = "current"
    history_limit: int = Field(default=80, ge=1, le=500)
    persist: bool = True


class PaperBiddingRunExecutionResponse(BaseModel):
    run_id: Optional[int] = None
    request: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    items: List[dict] = Field(default_factory=list)
    settlements: List[dict] = Field(default_factory=list)


class PaperBiddingRunListItem(BaseModel):
    id: int
    operator_id: int
    status: str
    mode: str
    scenario: str
    category_filter: Optional[str] = None
    strategy_version: str
    model_version: str
    target_start_at: Optional[datetime] = None
    target_end_at: Optional[datetime] = None
    data_cutoff_policy: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    candidate_count: int = Field(ge=0)
    paper_bid_count: int = Field(ge=0)
    settled_count: int = Field(ge=0)
    settlement_overview: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)


class PaperBiddingRunListResponse(BaseModel):
    operator_id: int
    returned_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: List[PaperBiddingRunListItem] = Field(default_factory=list)


class PaperBiddingRunDetailResponse(PaperBiddingRunListItem):
    request: dict = Field(default_factory=dict)
    paper_bids: List[dict] = Field(default_factory=list)
    settlements: List[dict] = Field(default_factory=list)


class PaperBiddingSummaryResponse(BaseModel):
    operator_id: int
    latest_run: Optional[PaperBiddingRunListItem] = None
    run_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    latest_summary: dict = Field(default_factory=dict)


class BackgroundJobResponse(BaseModel):
    task_name: str
    status: str
    detail: str
