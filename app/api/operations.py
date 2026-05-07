"""Operational skeleton endpoints for collection, classification, allocation and notifications."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import CompanyProfile, Project
from app.schemas.schemas import (
    AllocationRequest,
    AllocationResponse,
    BackgroundJobResponse,
    ClassificationRequest,
    ClassificationResponse,
    CrawlRequest,
    CrawlResponse,
    TelegramNotificationRequest,
)
from app.services.allocation import AllocationService
from app.services.classifier import NoticeClassifierService
from app.services.koneps.collector import KonepsCollectorService
from app.services.notifications.telegram import TelegramNotificationService

router = APIRouter()


@router.post("/crawl", response_model=CrawlResponse)
def crawl_notices(request: CrawlRequest):
    """Queue or execute a basic crawl skeleton response."""
    service = KonepsCollectorService()
    return service.collect_notices(request)


@router.post("/classify", response_model=ClassificationResponse)
def classify_notice(request: ClassificationRequest, db: Session = Depends(get_db)):
    """Classify a project against a company profile."""
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == request.user_id).first()
    service = NoticeClassifierService()
    return service.classify(project=project, profile=profile)


@router.post("/allocate", response_model=AllocationResponse)
def allocate_bid(request: AllocationRequest):
    """Allocate a recommended bid amount to the fairest candidate."""
    service = AllocationService()
    selected = service.select_candidate(request.candidates)
    return {
        "project_id": request.project_id,
        "assigned_user_id": selected.user_id,
        "recommended_amount": request.recommended_amount,
        "probability_score": request.probability_score,
        "reasoning": "낙찰 이력이 적고 가중치가 높은 후보를 우선 선택했습니다.",
    }


@router.post("/notify/telegram", response_model=BackgroundJobResponse)
def notify_telegram(request: TelegramNotificationRequest):
    """Build a Telegram notification payload and return configuration status."""
    service = TelegramNotificationService()
    message = service.build_message(request.title, request.message, request.url)
    detail = message if service.is_configured() else "Telegram is not configured yet. Skeleton message created."
    return {
        "task_name": "send_telegram_notification",
        "status": "ready" if service.is_configured() else "pending_configuration",
        "detail": detail,
    }