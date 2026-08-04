"""Current tender result snapshot persistence."""

from sqlalchemy.orm import Session

from app.models.models import TenderResult
from app.schemas.koneps_items import CrawlItemMetadataFacts
from app.services.koneps import parsing
from app.services.tender_result_events import stage_tender_result_event


def _current_result(db: Session, project_id: int | None) -> TenderResult | None:
    if project_id is None:
        return None
    return (
        db.query(TenderResult)
        .filter(
            TenderResult.project_id == project_id,
            TenderResult.is_current.is_(True),
        )
        .order_by(TenderResult.id.desc())
        .first()
    )


def _apply_observation(
    result: TenderResult,
    *,
    project_id: int | None,
    company: str,
    amount: float,
    rate: float,
    status: str,
    announced_at,
) -> None:
    result.project_id = project_id
    result.is_current = True
    if company:
        result.winning_company = company
    if amount > 0:
        result.winning_amount = amount
    if rate > 0:
        result.winning_rate = rate
    existing_status = str(result.result_status or "").strip().lower()
    if status and (
        existing_status in {"", "pending"} or company or amount > 0 or rate > 0
    ):
        result.result_status = status
    if announced_at is not None:
        result.announced_at = announced_at


def resolve_tender_result(
    db: Session,
    *,
    project_id: int | None,
    facts: CrawlItemMetadataFacts,
    crawl_job_status: str,
) -> TenderResult:
    """Merge one source observation into the project's current snapshot."""
    announced_at = parsing.coerce_datetime(facts.opening_announced_at)
    company = facts.winning_company or ""
    amount = facts.winning_amount or 0.0
    rate = facts.winning_rate or 0.0
    status = facts.opening_status or crawl_job_status
    result = _current_result(db, project_id)
    if result is None:
        result = TenderResult(
            project_id=project_id, is_current=True, winning_company=company
        )
        db.add(result)
        db.flush()
    stage_tender_result_event(
        db,
        tender_result=result,
        project_id=project_id,
        payload={
            "winning_company": company,
            "winning_amount": amount,
            "winning_rate": rate,
            "result_status": status,
            "announced_at": announced_at.isoformat() if announced_at else None,
        },
        observed_at=announced_at,
    )
    _apply_observation(
        result,
        project_id=project_id,
        company=company,
        amount=amount,
        rate=rate,
        status=status,
        announced_at=announced_at,
    )
    return result
