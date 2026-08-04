"""Monitor-specific decision reuse that preserves submitted real bids."""

from app.models.models import BidDecisionRecord

MONITOR_REUSABLE_STATUSES = ("planned", "reviewing", "skipped")


def find_reusable_monitor_decision(
    db, project_id: int, operator_id: int, monitor_run_id: int
):
    return (
        db.query(BidDecisionRecord)
        .filter(
            BidDecisionRecord.project_id == project_id,
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.monitor_run_id == monitor_run_id,
            BidDecisionRecord.decision_status.in_(MONITOR_REUSABLE_STATUSES),
        )
        .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
        .first()
    )
