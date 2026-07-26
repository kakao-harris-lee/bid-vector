"""Compact value normalizers and numeric helpers for dashboard serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.time import ensure_utc
from app.domain.aggregates import error_rate

from .constants import _BID_STATUSES, _OPPORTUNITY_STATUSES, _PAPER_ACTION_STATUS

if TYPE_CHECKING:
    from app.models.models import Project


def _project_brief(project: Project | None) -> dict:
    """Serialize the shared compact project shape used by all dashboard tabs."""
    return {
        "project_id": (
            int(project.id) if project is not None and project.id is not None else 0
        ),
        "title": (
            project.title
            if project is not None and project.title
            else "Untitled project"
        ),
        "category": project.category if project is not None else None,
        "notice_number": project.notice_number if project is not None else None,
        "issuing_agency": project.issuing_agency if project is not None else None,
        "demand_agency": project.demand_agency if project is not None else None,
        "budget_estimate": (
            float(project.budget_estimate or 0.0) if project is not None else 0.0
        ),
        "deadline": project.deadline if project is not None else None,
        "status": str(project.status or "open") if project is not None else "unknown",
    }


def _normalize_opportunity_status(status_value: str | None) -> str:
    normalized = str(status_value or "planned")
    if normalized in _OPPORTUNITY_STATUSES:
        return normalized
    return "reviewing"


def _normalize_action(
    action_value: str | None, *, pursue_bid: bool | None = None
) -> str:
    normalized = str(action_value or "")
    if normalized in {"bid_now", "review", "skip"}:
        return normalized
    return "review" if pursue_bid else "skip"


def _normalize_bid_status(status_value: str | None) -> str:
    normalized = str(status_value or "submitted")
    if normalized in _BID_STATUSES:
        return normalized
    return "submitted"


def _paper_status_from_action(action_value: str | None) -> str:
    return _PAPER_ACTION_STATUS.get(str(action_value or ""), "reviewing")


def _hours_until(deadline, *, now) -> int | None:
    if deadline is None:
        return None
    return int((ensure_utc(deadline) - now).total_seconds() // 3600)


def _round_optional(value: float | None, *, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _compute_delta(
    candidate_amount: float | None, winning_amount: float
) -> float | None:
    if candidate_amount is None or winning_amount <= 0:
        return None
    return candidate_amount - winning_amount


def _compute_error_rate(
    candidate_amount: float | None, winning_amount: float
) -> float | None:
    return error_rate(candidate_amount, winning_amount)
