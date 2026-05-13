"""Operational endpoints for collection, classification, bid decision, and notifications."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.time import utc_now
from app.core.single_user import ensure_operator_account, get_operator_profile
from app.models.models import BidDecisionRecord, CompanyProfile, CrawlJob, Project
from app.schemas.schemas import (
    BackgroundJobResponse,
    BidDecisionDetailResponse,
    BidDecisionRecordResponse,
    BidDecisionRequest,
    BidDecisionResponse,
    BidDecisionSaveRequest,
    BidDecisionTimelineResponse,
    ClassificationRequest,
    ClassificationResponse,
    CrawlRequest,
    CrawlResponse,
    CrawlTaskResponse,
    CrawlTaskStatusResponse,
    OpportunityAnalysisRequest,
    OpportunityAnalysisResponse,
    TelegramActionResponse,
    TelegramCallbackUpdateRequest,
    TelegramNotificationRequest,
    TelegramStatusResponse,
    TelegramSyncResponse,
)
from app.services.allocation import BidDecisionService
from app.services.classifier import NoticeClassifierService
from app.services.koneps.collector import KonepsCollectorService, format_crawl_error_message
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.update_processor import TelegramSyncService, TelegramUpdateProcessor
from app.services.opportunity_analysis import OpportunityAnalysisService
from app.tasks.jobs import enqueue_koneps_notice_collection, get_koneps_notice_collection_task_status

router = APIRouter()


def _get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _create_queued_crawl_job(db: Session, request: CrawlRequest) -> CrawlJob:
    """Persist a queued crawl job before handing work to the task layer."""
    crawl_job = CrawlJob(
        source=(request.source or "koneps").strip().lower(),
        target_date=request.target_date,
        status="queued",
        result_count=0,
    )
    db.add(crawl_job)
    db.commit()
    db.refresh(crawl_job)
    return crawl_job


def _sync_crawl_job_from_task_status(db: Session, crawl_job: CrawlJob, task_status: dict) -> CrawlJob:
    """Keep the current request session aligned with eager/fallback task outcomes."""
    normalized_status = str(task_status.get("status", "queued"))
    result = task_status.get("result") if isinstance(task_status.get("result"), dict) else None

    if normalized_status not in {"completed", "failed"}:
        return crawl_job

    if normalized_status == "failed":
        crawl_job.status = "failed"
        crawl_job.error_message = str(task_status.get("error") or task_status.get("detail") or "")
        crawl_job.completed_at = utc_now()
    else:
        metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
        crawl_job.status = str(result.get("job_status", "completed")) if isinstance(result, dict) else "completed"
        crawl_job.result_count = int(result.get("collected_count", crawl_job.result_count or 0)) if isinstance(result, dict) else crawl_job.result_count
        crawl_job.error_message = format_crawl_error_message(metadata) if isinstance(metadata, dict) else None
        crawl_job.completed_at = utc_now()

    db.add(crawl_job)
    db.commit()
    db.refresh(crawl_job)
    return crawl_job


@router.post("/crawl", response_model=CrawlResponse)
def crawl_notices(request: CrawlRequest, db: Session = Depends(get_db)):
    """Execute a crawl immediately and persist the resulting crawl history."""
    service = KonepsCollectorService()
    crawl_job = service.create_crawl_job(db, request)

    try:
        response = service.collect_notices(request)
        crawl_job = service.persist_crawl_results(db, crawl_job, request, response)
        response.setdefault("metadata", {})["crawl_job_id"] = crawl_job.id
        return response
    except Exception as exc:
        service.mark_crawl_job_failed(db, crawl_job, str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"crawl failed: {exc}",
        ) from exc


@router.post("/crawl/async", response_model=CrawlTaskResponse)
def crawl_notices_async(request: CrawlRequest, db: Session = Depends(get_db)):
    """Queue a KONEPS crawl task and return a pollable task id."""
    crawl_job = _create_queued_crawl_job(db, request)

    try:
        async_result = enqueue_koneps_notice_collection(request=request, crawl_job_id=crawl_job.id)
    except Exception as exc:
        crawl_job.status = "failed"
        crawl_job.error_message = f"task enqueue failed: {exc}"
        crawl_job.completed_at = utc_now()
        db.add(crawl_job)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"crawl task enqueue failed: {exc}",
        ) from exc

    status_payload = get_koneps_notice_collection_task_status(async_result.id)
    crawl_job = _sync_crawl_job_from_task_status(db, crawl_job, status_payload)
    return {
        "task_id": async_result.id,
        "task_name": status_payload["task_name"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/operations/crawl/tasks/{async_result.id}",
        "crawl_job_id": crawl_job.id,
    }


@router.get("/crawl/tasks/{task_id}", response_model=CrawlTaskStatusResponse)
def get_crawl_notices_task_status(task_id: str):
    """Inspect the current status and result of a queued KONEPS crawl task."""
    return get_koneps_notice_collection_task_status(task_id)


@router.post("/classify", response_model=ClassificationResponse)
def classify_notice(request: ClassificationRequest, db: Session = Depends(get_db)):
    """Classify a project against the singleton operator's company profile."""
    project = _get_project_or_404(db, request.project_id)

    if request.user_id is not None:
        profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == request.user_id).first()
    else:
        profile = get_operator_profile(db)

    service = NoticeClassifierService()
    return service.classify(project=project, profile=profile)


@router.post("/opportunity-analysis", response_model=OpportunityAnalysisResponse)
def analyze_opportunity(request: OpportunityAnalysisRequest, db: Session = Depends(get_db)):
    """Run a multi-angle analysis and return the recommended bid action for one project."""
    project = _get_project_or_404(db, request.project_id)
    return OpportunityAnalysisService().analyze_project(db, project, request)


@router.post("/allocate", response_model=BidDecisionResponse, deprecated=True)
@router.post("/bid-decision", response_model=BidDecisionResponse)
def decide_bid_pursuit(request: BidDecisionRequest, db: Session = Depends(get_db)):
    """Prioritize whether the single user should pursue a bid opportunity now."""
    service = BidDecisionService()
    return service.evaluate_opportunity(request, db=db)


@router.post("/bid-decisions", response_model=BidDecisionRecordResponse)
def save_bid_decision(request: BidDecisionSaveRequest, db: Session = Depends(get_db)):
    """Evaluate and persist a single-operator bid decision record."""
    project = _get_project_or_404(db, request.project_id)
    service = BidDecisionService()
    record = service.save_decision(db, request)
    OperatorNotificationService().create_bid_decision_notification(
        db,
        operator_id=record.operator_id,
        project=project,
        decision_record=record,
    )
    return record


@router.get("/bid-decisions", response_model=List[BidDecisionRecordResponse])
def list_bid_decisions(
    decision_status: Optional[str] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List persisted bid decision records for the singleton operator."""
    operator = ensure_operator_account(db)
    query = db.query(BidDecisionRecord).filter(BidDecisionRecord.operator_id == operator.id)

    if decision_status:
        query = query.filter(BidDecisionRecord.decision_status == decision_status)

    if project_id:
        query = query.filter(BidDecisionRecord.project_id == project_id)

    return (
        query.order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
        .limit(limit)
        .all()
    )


@router.get("/projects/{project_id}/bid-decision-timeline", response_model=BidDecisionTimelineResponse)
def get_project_bid_decision_timeline(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return recent persisted bid-decision history for one project."""
    project = _get_project_or_404(db, project_id)
    return BidDecisionService().get_project_timeline(db, project=project, limit=limit)


@router.get("/bid-decisions/{decision_record_id}", response_model=BidDecisionDetailResponse)
def get_bid_decision_detail(
    decision_record_id: int,
    timeline_limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return one persisted bid decision with project context and same-project history."""
    try:
        return BidDecisionService().get_decision_detail(db, decision_record_id=decision_record_id, timeline_limit=timeline_limit)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/notify/telegram", response_model=BackgroundJobResponse)
def notify_telegram(request: TelegramNotificationRequest):
    """Build and best-effort send a Telegram notification payload."""
    service = TelegramNotificationService()
    message = service.build_message(request.title, request.message, request.url)

    if not service.is_configured():
        return {
            "task_name": "send_telegram_notification",
            "status": "pending_configuration",
            "detail": "Telegram is not configured yet. Skeleton message created.",
        }

    try:
        delivery = service.send_message(message)
        detail_prefix = str(delivery.get("detail", "Telegram delivery attempted."))
    except RuntimeError as exc:
        detail_prefix = f"Telegram delivery failed: {exc}"

    detail = f"{detail_prefix}\n\n{message}"
    return {
        "task_name": "send_telegram_notification",
        "status": "ready",
        "detail": detail,
    }


@router.post("/telegram/callback", response_model=TelegramActionResponse)
def handle_telegram_callback(update: TelegramCallbackUpdateRequest, db: Session = Depends(get_db)):
    """Handle Telegram inline button callbacks for bid-decision actions."""
    try:
        result = TelegramUpdateProcessor().process_update(db, update.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return {
        "status": str(result["status"]),
        "detail": str(result["detail"]),
        "decision_record_id": int(result["decision_record_id"]),
        "action": str(result["action"]),
        "decision_status": str(result["decision_status"]),
    }


@router.post("/telegram/webhook", response_model=TelegramSyncResponse)
def handle_telegram_webhook(
    update: dict,
    db: Session = Depends(get_db),
    telegram_secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    """Process raw Telegram webhook updates for messages and inline callbacks."""
    service = TelegramNotificationService()
    if service.is_configured() and service.get_configured_chat_id() and service.get_configured_chat_id() != "":
        from app.core.config import settings

        if settings.TELEGRAM_WEBHOOK_SECRET and telegram_secret != settings.TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telegram webhook secret")

    result = TelegramUpdateProcessor(service).process_update(db, update)
    update_id = update.get("update_id")
    processed_update_ids = [int(update_id)] if isinstance(update_id, int) else []
    chat_id = result.get("chat_id")
    known_chat_ids = [int(chat_id)] if isinstance(chat_id, int) else []
    processed = 1 if result.get("status") == "processed" else 0
    return {
        "status": str(result.get("status", "ignored")),
        "detail": str(result.get("detail", "Telegram webhook update handled.")),
        "processed_count": processed,
        "processed_update_ids": processed_update_ids,
        "known_chat_ids": known_chat_ids,
    }


@router.post("/telegram/sync", response_model=TelegramSyncResponse)
def sync_telegram_updates(
    limit: int = Query(default=20, ge=1, le=100),
    timeout_seconds: int = Query(default=0, ge=0, le=60),
    db: Session = Depends(get_db),
):
    """Fetch pending Telegram updates manually and process them immediately."""
    return TelegramSyncService().sync_updates(db, limit=limit, timeout_seconds=timeout_seconds)


@router.get("/telegram/status", response_model=TelegramStatusResponse)
def get_telegram_status():
    """Expose Telegram delivery and webhook diagnostics for local debugging."""
    service = TelegramNotificationService()
    updates = service.get_updates(limit=10, timeout_seconds=0)
    webhook_info = service.get_webhook_info().get("result", {})
    return {
        "configured": service.is_configured(),
        "delivery_chat_id": service.get_configured_chat_id() or None,
        "pending_update_count": int(webhook_info.get("pending_update_count", 0) or 0),
        "webhook_url": str(webhook_info.get("url", "") or ""),
        "has_custom_certificate": bool(webhook_info.get("has_custom_certificate", False)),
        "known_chat_ids": service.extract_chat_ids(updates),
    }
