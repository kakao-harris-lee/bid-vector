"""API route aggregator"""
from fastapi import APIRouter

from app.api import (
    admin,
    analytics,
    auth,
    backtests,
    bid_form_draft,
    bid_report_email,
    bid_summary,
    bids,
    business_verification,
    dashboard,
    decision_samples,
    ml,
    onboarding,
    operations,
    operator,
    predictions,
    projects,
    real_bids,
    realtime,
    synthetic,
)

router = APIRouter()

# Include route modules
router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(backtests.router, prefix="/backtests", tags=["Backtests"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
router.include_router(operator.router, prefix="/operator", tags=["Operator"])
router.include_router(onboarding.router, prefix="/operator", tags=["Onboarding"])
router.include_router(
    business_verification.router, prefix="/operator", tags=["Business Verification"]
)
router.include_router(projects.router, prefix="/projects", tags=["Projects"])
router.include_router(bids.router, prefix="/bids", tags=["Bids"])
router.include_router(predictions.router, prefix="/predictions", tags=["AI Predictions"])
router.include_router(ml.router, prefix="/ml", tags=["ML Jobs"])
router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
router.include_router(admin.router, prefix="/admin", tags=["Legacy Admin"])
router.include_router(operations.router, prefix="/operations", tags=["Operations"])
router.include_router(bid_summary.router, prefix="/operations", tags=["Bid Summary"])
router.include_router(
    bid_form_draft.router, prefix="/operations", tags=["Bid Form Draft"]
)
router.include_router(
    bid_report_email.router, prefix="/operations", tags=["Bid Report Email"]
)
router.include_router(
    decision_samples.router, prefix="/operations", tags=["Decision Samples"]
)
router.include_router(real_bids.router, prefix="/operations", tags=["Real Bids"])
router.include_router(realtime.router, prefix="/realtime", tags=["Realtime"])
router.include_router(synthetic.router, tags=["Synthetic"])
