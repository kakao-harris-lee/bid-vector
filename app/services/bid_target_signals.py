"""Resolve per-notice signals for the bid target menu from historical outcomes.

MVP signal: dispersion (population stddev) of realized winning rates for the
same issuing agency + category over a recent window. Computed as a SQL
aggregate (never loading rows) to stay well under the bind-parameter limit
(see prediction_feedback chunking incident).

``TenderResult.winning_rate`` is stored on mixed scales depending on the
collection path (scsbid persists a fraction e.g. ``0.875``; HTML parsing
persists a percentage e.g. ``87.5``). Every other consumer normalizes before
using it, so this resolver mirrors
``PredictionDatasetService._normalize_bid_rate_value``: values above the
percentage-scale threshold are divided by 100, and only rows whose *normalized*
value falls inside ``[VALID_BID_RATE_MIN, VALID_BID_RATE_MAX]`` are aggregated.
Normalizing in SQL keeps this a pure aggregate (no row loading).

The population stddev is derived from ``avg(x^2) - avg(x)^2`` rather than a
dialect-specific ``stddev_pop`` so the same aggregate runs on both SQLite (test
DB) and PostgreSQL (production) with identical results.
"""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.ai.bid_target import BidTargetSignals
from app.core.time import utc_now
from app.domain.rate_normalization import PERCENT_SCALE_THRESHOLD
from app.models.models import Project, TenderResult
from app.services.prediction_dataset import PredictionDatasetService

# Mirror PredictionDatasetService._normalize_bid_rate_value exactly. The
# percentage-scale threshold (rates above it are /100'd before the range gate) is
# single-sourced in app.domain.rate_normalization; the SQL CASE below references
# that constant since it cannot call the Python normalizer.
_VALID_BID_RATE_MIN = PredictionDatasetService.VALID_BID_RATE_MIN
_VALID_BID_RATE_MAX = PredictionDatasetService.VALID_BID_RATE_MAX


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
    # Per-row scale normalization (fraction vs percentage) done in SQL so the
    # aggregate never loads rows. Mirrors the >1.5 → /100 threshold used by the
    # prediction dataset normalizer.
    normalized_rate = case(
        (
            TenderResult.winning_rate > PERCENT_SCALE_THRESHOLD,
            TenderResult.winning_rate / 100.0,
        ),
        else_=TenderResult.winning_rate,
    )
    query = (
        db.query(
            func.count(TenderResult.id),
            func.avg(normalized_rate),
            func.avg(normalized_rate * normalized_rate),
        )
        .join(Project, Project.id == TenderResult.project_id)
        .filter(
            Project.issuing_agency == agency_name,
            # Gate on the NORMALIZED value so mixed-scale garbage (e.g. a stray
            # 200.0 → 2.0, or a 0.3 fraction) never inflates the dispersion.
            normalized_rate >= _VALID_BID_RATE_MIN,
            normalized_rate <= _VALID_BID_RATE_MAX,
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
