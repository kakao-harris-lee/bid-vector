"""Analytics, document-analysis and notification response schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict


class DocumentAnalysisRequest(BaseModel):
    project_id: int
    document_content: str
    document_type: str


class DocumentAnalysisResponse(BaseModel):
    key_requirements: List[str]
    complexity_score: float
    estimated_effort: float
    risks: List[str]


class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


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
