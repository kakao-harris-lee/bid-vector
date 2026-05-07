"""Pydantic schemas for request/response"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr


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

    class Config:
        from_attributes = True


# Authentication Schemas
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


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

    class Config:
        from_attributes = True


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
    user_id: int
    status: str
    score: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


# Analytics Schemas
class AnalyticsEventRequest(BaseModel):
    event_type: str
    event_data: dict


class CrawlRequest(BaseModel):
    source: str = "koneps"
    category: Optional[str] = None
    target_date: Optional[str] = None
    keyword: Optional[str] = None


class CrawlResponse(BaseModel):
    job_status: str
    source: str
    collected_count: int
    items: List[dict]


class ClassificationRequest(BaseModel):
    project_id: int
    user_id: int


class ClassificationResponse(BaseModel):
    matched: bool
    score: float
    reasons: List[str]


class AllocationCandidate(BaseModel):
    user_id: int
    company_name: Optional[str] = None
    total_awards: int = 0
    weight: float = 0.0


class AllocationRequest(BaseModel):
    project_id: int
    recommended_amount: float
    probability_score: float
    candidates: List[AllocationCandidate]


class AllocationResponse(BaseModel):
    project_id: int
    assigned_user_id: int
    recommended_amount: float
    probability_score: float
    reasoning: str


class TelegramNotificationRequest(BaseModel):
    title: str
    message: str
    url: Optional[str] = None


class BackgroundJobResponse(BaseModel):
    task_name: str
    status: str
    detail: str
