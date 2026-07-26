"""Recommendation-feedback false-positive/negative labels for training data."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, User
from app.schemas.schemas import _extract_decision_reasons
from app.services.decision_analytics.base import _DecisionAnalyticsBase


class _FeedbackLabelsMixin(_DecisionAnalyticsBase):
    """Operator verdict labels joined back to their decision records."""

    def build_recommendation_feedback_labels(
        self,
        db: Session,
        *,
        days: int = 90,
        limit: int = 100,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return false-positive/negative labels collected via recommendation feedback.

        Joins ``recommendation_feedback`` analytics events to their persisted
        ``BidDecisionRecord`` (and ``Project``) so the operator's verdicts become
        analyzable training data. Verdicts are deduped per ``decision_record_id``
        via the shared :meth:`_dedupe_latest_feedback_verdicts` helper. Orphan
        verdicts whose decision record is missing are skipped.
        """
        if operator is None:
            operator = ensure_operator_account(db)
        start_at = utc_now() - timedelta(days=days)
        feedback_events = self._load_events_in_range(
            db,
            operator_id=operator.id,
            event_type="recommendation_feedback",
            start_at=start_at,
        )
        latest_by_decision = self._dedupe_latest_feedback_verdicts(feedback_events)

        if not latest_by_decision:
            return {
                "operator_id": operator.id,
                "period_days": days,
                "label_count": 0,
                "useful_count": 0,
                "not_useful_count": 0,
                "review_value_rate": None,
                "by_category": {},
                "by_action": {},
                "items": [],
            }

        decision_ids = list(latest_by_decision.keys())
        decisions = (
            db.query(BidDecisionRecord)
            .options(selectinload(BidDecisionRecord.project))
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.id.in_(decision_ids),
            )
            .all()
        )
        decisions_by_id = {int(record.id): record for record in decisions}

        (
            items,
            useful_count,
            not_useful_count,
            category_counters,
            action_counters,
        ) = self._build_feedback_label_items(
            latest_by_decision=latest_by_decision,
            decisions_by_id=decisions_by_id,
        )

        # Newest feedback first; missing timestamps sort last deterministically by id.
        items.sort(
            key=lambda item: (
                item["feedback_at"] is None,
                -(item["feedback_at"].timestamp() if item["feedback_at"] is not None else 0.0),
                -int(item["decision_record_id"]),
            )
        )
        clamped_items = items[: max(limit, 0)]

        label_count = useful_count + not_useful_count
        return {
            "operator_id": operator.id,
            "period_days": days,
            "label_count": label_count,
            "useful_count": useful_count,
            "not_useful_count": not_useful_count,
            "review_value_rate": self._rate(useful_count, label_count),
            "by_category": self._finalize_label_breakdown(category_counters),
            "by_action": self._finalize_label_breakdown(action_counters),
            "items": clamped_items,
        }

    def _build_feedback_label_items(
        self,
        *,
        latest_by_decision: dict[int, dict[str, Any]],
        decisions_by_id: dict[int, BidDecisionRecord],
    ) -> tuple[list[dict[str, Any]], int, int, dict[str, dict[str, int]], dict[str, dict[str, int]]]:
        items: list[dict[str, Any]] = []
        useful_count = 0
        not_useful_count = 0
        category_counters: dict[str, dict[str, int]] = {}
        action_counters: dict[str, dict[str, int]] = {}
        for decision_id, entry in latest_by_decision.items():
            decision = decisions_by_id.get(decision_id)
            if decision is None:
                continue
            verdict = entry["verdict"]
            useful_count += 1 if verdict == "useful" else 0
            not_useful_count += 1 if verdict != "useful" else 0
            items.append(
                self._build_feedback_label_item(
                    decision,
                    verdict=verdict,
                    feedback_at=entry["feedback_at"],
                    category_counters=category_counters,
                    action_counters=action_counters,
                )
            )
        return items, useful_count, not_useful_count, category_counters, action_counters

    def _build_feedback_label_item(
        self,
        decision: BidDecisionRecord,
        *,
        verdict: str,
        feedback_at,
        category_counters: dict[str, dict[str, int]],
        action_counters: dict[str, dict[str, int]],
    ) -> dict[str, Any]:
        project = decision.project
        project_category = (
            str(project.category) if project is not None and project.category else None
        )
        project_business_type_code = (
            str(project.business_type_code)
            if project is not None and project.business_type_code
            else None
        )
        project_title = (
            str(project.title)
            if project is not None and project.title
            else f"Project {decision.project_id}"
        )
        self._increment_label_breakdown(
            category_counters,
            key=project_category or self.UNKNOWN_CATEGORY,
            verdict=verdict,
        )
        self._increment_label_breakdown(
            action_counters,
            key=str(decision.action or "skip"),
            verdict=verdict,
        )
        strengths, risk_flags = _extract_decision_reasons(decision.score_breakdown)
        return {
            "decision_record_id": int(decision.id),
            "project_id": int(decision.project_id) if decision.project_id is not None else 0,
            "project_title": project_title,
            "project_category": project_category,
            "project_business_type_code": project_business_type_code,
            "action": str(decision.action or "skip"),
            "decision_status": str(decision.decision_status or "planned"),
            "priority_score": float(decision.priority_score or 0.0),
            "verdict": verdict,
            "feedback_at": feedback_at,
            "reasoning": self._reasoning_excerpt(decision.reasoning),
            "strengths": strengths,
            "risk_flags": risk_flags,
        }

    def _increment_label_breakdown(
        self,
        counters: dict[str, dict[str, int]],
        *,
        key: str,
        verdict: str,
    ) -> None:
        """Accumulate useful/not_useful counts for one breakdown key."""
        bucket = counters.setdefault(key, {"useful": 0, "not_useful": 0, "total": 0})
        bucket[verdict] = bucket.get(verdict, 0) + 1
        bucket["total"] = bucket.get("total", 0) + 1

    def _finalize_label_breakdown(
        self, counters: dict[str, dict[str, int]]
    ) -> dict[str, dict[str, Any]]:
        """Attach the useful-share rate to each breakdown bucket."""
        finalized: dict[str, dict[str, Any]] = {}
        for key, bucket in counters.items():
            total = int(bucket.get("total", 0))
            useful = int(bucket.get("useful", 0))
            not_useful = int(bucket.get("not_useful", 0))
            finalized[key] = {
                "useful": useful,
                "not_useful": not_useful,
                "total": total,
                "rate": self._rate(useful, total),
            }
        return finalized
