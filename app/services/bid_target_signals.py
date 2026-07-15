"""Resolve per-notice signals for the bid target menu from historical outcomes.

MVP signal: dispersion (population stddev) of realized winning rates for the
same issuing agency + category over a recent window. Computed as a SQL
aggregate (never loading rows) to stay well under the bind-parameter limit
(see prediction_feedback chunking incident).

The population stddev is derived from ``avg(x^2) - avg(x)^2`` rather than a
dialect-specific ``stddev_pop`` so the same aggregate runs on both SQLite (test
DB) and PostgreSQL (production) with identical results.
"""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.bid_target import BidTargetSignals
from app.core.time import utc_now
from app.models.models import Project, TenderResult


def resolve_bid_target_signals(
    db: Session,
    *,
    agency_name: str | None,
    category: str | None,
    window_days: int = 365,
    min_samples: int = 8,
) -> BidTargetSignals:
    if not agency_name:
        return BidTargetSignals(win_rate_dispersion=None, data_sufficient=False)
    date_from = utc_now() - timedelta(days=window_days)
    query = (
        db.query(
            func.count(TenderResult.id),
            func.avg(TenderResult.winning_rate),
            func.avg(TenderResult.winning_rate * TenderResult.winning_rate),
        )
        .join(Project, Project.id == TenderResult.project_id)
        .filter(
            Project.issuing_agency == agency_name,
            TenderResult.winning_rate > 0,
            TenderResult.announced_at >= date_from,
        )
    )
    if category:
        query = query.filter(Project.category == category)
    sample_count, mean_rate, mean_square = query.one()
    if (
        sample_count is None
        or sample_count < min_samples
        or mean_rate is None
        or mean_square is None
    ):
        return BidTargetSignals(win_rate_dispersion=None, data_sufficient=False)
    variance = max(0.0, float(mean_square) - float(mean_rate) ** 2)
    return BidTargetSignals(win_rate_dispersion=math.sqrt(variance), data_sufficient=True)
