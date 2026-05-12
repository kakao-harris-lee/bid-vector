"""Decision analytics summaries for operator workflow tuning."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord


class DecisionAnalyticsService:
    """Build operator-facing analytics from persisted bid decision records."""

    ACTIVE_DECISION_STATUSES = {"planned", "reviewing"}
    UNKNOWN_CATEGORY = "uncategorized"
    UNKNOWN_AGENCY = "unknown"
    DEFAULT_WORKLOAD_SOURCE = "provided"
    REVIEW_RATE_TIGHTEN_THRESHOLD = 0.35
    REVIEW_RATE_RELAX_THRESHOLD = 0.75
    BID_NOW_RATE_TIGHTEN_THRESHOLD = 0.5
    WORKLOAD_GAP_ACTION_THRESHOLD = 0.35
    CATEGORY_GAP_ACTION_THRESHOLD = 0.3

    def build_insights(self, db: Session, *, days: int = 30, limit: int = 10) -> dict[str, Any]:
        """Summarize persisted bid-decision signals for tuning and operator review."""
        operator = ensure_operator_account(db)
        decisions = self._load_recent_decisions(db, operator_id=operator.id, days=days)

        status_breakdown: dict[str, int] = {}
        action_breakdown: dict[str, int] = {}
        for decision in decisions:
            status_breakdown[str(decision.decision_status)] = status_breakdown.get(str(decision.decision_status), 0) + 1
            action_breakdown[str(decision.action)] = action_breakdown.get(str(decision.action), 0) + 1

        return {
            "operator_id": operator.id,
            "period_days": days,
            "result_count": len(decisions),
            "high_priority_count": sum(1 for decision in decisions if float(decision.priority_score or 0.0) >= 0.7),
            "bid_now_count": action_breakdown.get("bid_now", 0),
            "review_count": action_breakdown.get("review", 0),
            "skip_count": action_breakdown.get("skip", 0),
            "submitted_count": status_breakdown.get("submitted", 0),
            "auto_workload_count": sum(1 for decision in decisions if str(decision.workload_source or "provided") == "auto"),
            "provided_workload_count": sum(1 for decision in decisions if str(decision.workload_source or "provided") == "provided"),
            "average_priority_score": self._average([float(decision.priority_score or 0.0) for decision in decisions]),
            "average_expected_margin_score": self._average([float(decision.expected_margin_score or 0.0) for decision in decisions]),
            "average_execution_complexity_score": self._average([float(decision.execution_complexity_score or 0.0) for decision in decisions]),
            "average_competitiveness_score": self._average([float(decision.competitiveness_score or 0.0) for decision in decisions]),
            "average_budget_capture_score": self._average([float(decision.budget_capture_score or 0.0) for decision in decisions]),
            "status_breakdown": status_breakdown,
            "action_breakdown": action_breakdown,
            "recent_decisions": [
                {
                    "decision_record_id": int(decision.id),
                    "project_id": int(decision.project_id),
                    "action": str(decision.action),
                    "decision_status": str(decision.decision_status),
                    "priority_score": float(decision.priority_score or 0.0),
                    "expected_margin_score": float(decision.expected_margin_score or 0.0),
                    "execution_complexity_score": float(decision.execution_complexity_score or 0.0),
                    "competitiveness_score": float(decision.competitiveness_score or 0.0),
                    "budget_capture_score": float(decision.budget_capture_score or 0.0),
                    "updated_at": decision.updated_at,
                }
                for decision in decisions[:limit]
            ],
        }

    def build_funnel(
        self,
        db: Session,
        *,
        days: int = 30,
        limit: int = 10,
        breakdown_limit: int = 5,
        trend_bucket_days: int = 7,
    ) -> dict[str, Any]:
        """Summarize how initial bid decisions progress toward submitted workflow states."""
        operator = ensure_operator_account(db)
        now = utc_now()
        current_period_start = now - timedelta(days=days)
        previous_period_start = current_period_start - timedelta(days=days)
        current_decisions = self._load_decisions_in_range(
            db,
            operator_id=operator.id,
            start_at=current_period_start,
            end_at=None,
        )
        previous_decisions = self._load_decisions_in_range(
            db,
            operator_id=operator.id,
            start_at=previous_period_start,
            end_at=current_period_start,
        )
        current_summary = self._build_funnel_summary(current_decisions)
        previous_summary = self._build_funnel_summary(previous_decisions)
        recent_submissions = self._build_recent_submissions(current_decisions, limit=limit)

        return {
            "operator_id": operator.id,
            "period_days": days,
            **current_summary,
            "current_period_start": current_period_start,
            "current_period_end": now,
            "previous_period": {
                "period_start": previous_period_start,
                "period_end": current_period_start,
                **previous_summary,
            },
            "comparison": self._build_funnel_comparison(
                current_summary,
                previous_summary,
                current_period_start=current_period_start,
                current_period_end=now,
                previous_period_start=previous_period_start,
                previous_period_end=current_period_start,
            ),
            "trend_bucket_days": trend_bucket_days,
            "breakdown_limit_applied": breakdown_limit,
            "trend": self._build_trend(current_decisions, days=days, bucket_days=trend_bucket_days),
            "category_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=self._resolve_category_segment,
                limit=breakdown_limit,
            ),
            "workload_source_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=lambda decision: str(decision.workload_source or self.DEFAULT_WORKLOAD_SOURCE),
                limit=breakdown_limit,
            ),
            "agency_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=self._resolve_agency_segment,
                limit=breakdown_limit,
            ),
            "recent_submissions": recent_submissions,
        }

    def build_recommendations(
        self,
        db: Session,
        *,
        days: int = 30,
        breakdown_limit: int = 5,
        trend_bucket_days: int = 7,
        recommendation_limit: int = 5,
    ) -> dict[str, Any]:
        """Convert funnel analytics into actionable tuning recommendations."""
        funnel = self.build_funnel(
            db,
            days=days,
            limit=max(3, recommendation_limit),
            breakdown_limit=breakdown_limit,
            trend_bucket_days=trend_bucket_days,
        )

        recommendations = [
            recommendation
            for recommendation in [
                self._recommend_review_threshold(funnel),
                self._recommend_bid_now_threshold(funnel),
                self._recommend_workload_calibration(funnel),
                self._recommend_category_focus(funnel),
            ]
            if recommendation is not None
        ]

        if not recommendations:
            recommendations.append({
                "key": "stable-funnel",
                "severity": "info",
                "title": "전환 퍼널이 비교적 안정적입니다.",
                "summary": "현재 기간에는 임계값을 즉시 조정해야 할 강한 하락 신호가 보이지 않습니다. 현재 추세를 유지하며 추가 표본을 모으는 편이 안전합니다.",
                "suggested_adjustment": "기존 `BID_NOW_THRESHOLD`/`REVIEW_THRESHOLD`를 유지하고 다음 기간 비교를 확인하세요.",
                "supporting_metrics": {
                    "overall_submission_rate": funnel.get("overall_submission_rate"),
                    "review_submission_rate": funnel.get("review_submission_rate"),
                    "bid_now_submission_rate": funnel.get("bid_now_submission_rate"),
                },
            })

        recommendations = recommendations[:recommendation_limit]
        experiments: list[dict[str, Any]] = []
        enriched_recommendations: list[dict[str, Any]] = []
        for priority_rank, recommendation in enumerate(recommendations, start=1):
            experiment_plan = self._build_experiment_plan(recommendation, priority_rank=priority_rank)
            enriched_recommendations.append({
                **recommendation,
                "experiment_plan": experiment_plan,
            })
            if experiment_plan is not None:
                experiments.append(experiment_plan)

        return {
            "operator_id": funnel["operator_id"],
            "period_days": funnel["period_days"],
            "decision_count": funnel["decision_count"],
            "submitted_count": funnel["submitted_count"],
            "active_pending_count": funnel["active_pending_count"],
            "overall_submission_rate": funnel.get("overall_submission_rate"),
            "workflow_submission_rate": funnel.get("workflow_submission_rate"),
            "bid_now_submission_rate": funnel.get("bid_now_submission_rate"),
            "review_submission_rate": funnel.get("review_submission_rate"),
            "recommendation_count": len(enriched_recommendations),
            "recommendation_limit_applied": recommendation_limit,
            "experiment_count": len(experiments),
            "headline": self._build_recommendation_headline(funnel, recommendations),
            "comparison": funnel["comparison"],
            "recommended_next_experiment": experiments[0] if experiments else None,
            "experiments": experiments,
            "recommendations": enriched_recommendations,
        }

    def _load_recent_decisions(self, db: Session, *, operator_id: int, days: int) -> list[BidDecisionRecord]:
        """Return recent decision rows ordered newest first."""
        date_from = utc_now() - timedelta(days=days)
        return self._load_decisions_in_range(db, operator_id=operator_id, start_at=date_from, end_at=None)

    def _load_decisions_in_range(
        self,
        db: Session,
        *,
        operator_id: int,
        start_at,
        end_at,
    ) -> list[BidDecisionRecord]:
        """Return decision rows for one operator within a bounded updated-at window."""
        query = (
            db.query(BidDecisionRecord)
            .options(selectinload(BidDecisionRecord.project))
            .filter(
                BidDecisionRecord.operator_id == operator_id,
                BidDecisionRecord.updated_at >= start_at,
            )
        )
        if end_at is not None:
            query = query.filter(BidDecisionRecord.updated_at < end_at)

        return (
            query
            .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
            .all()
        )

    def _build_funnel_summary(self, decisions: list[BidDecisionRecord]) -> dict[str, Any]:
        """Compute reusable top-level funnel metrics for one period."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        active_pending_decisions = [decision for decision in decisions if self._is_active_pending(decision)]
        skipped_decisions = [decision for decision in decisions if self._is_skipped(decision)]
        workflow_entries = [decision for decision in decisions if self._entry_status(decision) != "submitted"]
        bid_now_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "bid_now"]
        review_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "review"]
        skip_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "skip"]
        direct_submitted = [decision for decision in submitted_decisions if self._entry_status(decision) == "submitted"]
        submitted_after_bid_now = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "bid_now"
        ]
        submitted_after_review = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "review"
        ]
        submitted_after_skip = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "skip"
        ]
        submission_durations = [
            hours_to_submit
            for hours_to_submit in (self._compute_hours_to_submit(decision) for decision in submitted_decisions)
            if hours_to_submit is not None
        ]

        return {
            "decision_count": len(decisions),
            "project_count": len({int(decision.project_id) for decision in decisions if decision.project_id is not None}),
            "active_pending_count": len(active_pending_decisions),
            "submitted_count": len(submitted_decisions),
            "skipped_count": len(skipped_decisions),
            "entry_bid_now_count": len(bid_now_entries),
            "entry_review_count": len(review_entries),
            "entry_skip_count": len(skip_entries),
            "direct_submitted_count": len(direct_submitted),
            "submitted_after_bid_now_count": len(submitted_after_bid_now),
            "submitted_after_review_count": len(submitted_after_review),
            "submitted_after_skip_count": len(submitted_after_skip),
            "overall_submission_rate": self._rate(len(submitted_decisions), len(decisions)),
            "workflow_submission_rate": self._rate(
                len(submitted_after_bid_now) + len(submitted_after_review) + len(submitted_after_skip),
                len(workflow_entries),
            ),
            "bid_now_submission_rate": self._rate(len(submitted_after_bid_now), len(bid_now_entries)),
            "review_submission_rate": self._rate(len(submitted_after_review), len(review_entries)),
            "average_hours_to_submit": self._average(submission_durations),
        }

    def _build_recent_submissions(self, decisions: list[BidDecisionRecord], *, limit: int) -> list[dict[str, Any]]:
        """Serialize recent submitted decisions for the current analysis window."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        return [
            {
                "decision_record_id": int(decision.id),
                "project_id": int(decision.project_id),
                "project_title": str(decision.project.title) if decision.project is not None else f"Project {decision.project_id}",
                "initial_action": self._entry_action(decision),
                "initial_decision_status": self._entry_status(decision),
                "current_action": str(decision.action or self._entry_action(decision)),
                "current_decision_status": str(decision.decision_status or "submitted"),
                "priority_score": float(decision.priority_score or 0.0),
                "recommended_amount": float(decision.recommended_amount or 0.0),
                "first_decided_at": self._entry_datetime(decision),
                "submitted_at": decision.updated_at,
                "hours_to_submit": self._compute_hours_to_submit(decision),
            }
            for decision in submitted_decisions[:limit]
        ]

    def _build_funnel_comparison(
        self,
        current_summary: dict[str, Any],
        previous_summary: dict[str, Any],
        *,
        current_period_start,
        current_period_end,
        previous_period_start,
        previous_period_end,
    ) -> dict[str, Any]:
        """Compute period-over-period deltas for the most actionable funnel metrics."""
        return {
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "previous_period_start": previous_period_start,
            "previous_period_end": previous_period_end,
            "decision_count_delta": int(current_summary["decision_count"] - previous_summary["decision_count"]),
            "project_count_delta": int(current_summary["project_count"] - previous_summary["project_count"]),
            "submitted_count_delta": int(current_summary["submitted_count"] - previous_summary["submitted_count"]),
            "active_pending_count_delta": int(current_summary["active_pending_count"] - previous_summary["active_pending_count"]),
            "skipped_count_delta": int(current_summary["skipped_count"] - previous_summary["skipped_count"]),
            "overall_submission_rate_delta": self._delta(current_summary["overall_submission_rate"], previous_summary["overall_submission_rate"]),
            "workflow_submission_rate_delta": self._delta(current_summary["workflow_submission_rate"], previous_summary["workflow_submission_rate"]),
            "bid_now_submission_rate_delta": self._delta(current_summary["bid_now_submission_rate"], previous_summary["bid_now_submission_rate"]),
            "review_submission_rate_delta": self._delta(current_summary["review_submission_rate"], previous_summary["review_submission_rate"]),
            "average_hours_to_submit_delta": self._delta(current_summary["average_hours_to_submit"], previous_summary["average_hours_to_submit"]),
        }

    def _recommend_review_threshold(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend tightening or relaxing the review threshold based on review conversion."""
        review_entry_count = int(funnel.get("entry_review_count") or 0)
        current_rate = funnel.get("review_submission_rate")
        delta = (funnel.get("comparison") or {}).get("review_submission_rate_delta")
        if review_entry_count < 3 or current_rate is None:
            return None

        if current_rate < self.REVIEW_RATE_TIGHTEN_THRESHOLD:
            severity = "action" if delta is not None and delta <= -0.1 else "watch"
            return {
                "key": "review-threshold-tighten",
                "severity": severity,
                "title": "검토 대기열 품질을 더 엄격하게 관리하세요.",
                "summary": f"현재 기간의 review 진입 {review_entry_count}건 중 제출 전환율이 {current_rate:.2f}로 낮습니다. 약한 후보가 review 큐에 과하게 남아 있을 가능성이 큽니다.",
                "suggested_adjustment": "`REVIEW_THRESHOLD`를 0.03~0.07 상향하거나 review 후보 생성 시 예상 수익성/복잡도 조건을 더 엄격하게 두는 실험을 권장합니다.",
                "supporting_metrics": {
                    "entry_review_count": review_entry_count,
                    "current_review_submission_rate": current_rate,
                    "review_submission_rate_delta": delta,
                },
            }

        if current_rate > self.REVIEW_RATE_RELAX_THRESHOLD and review_entry_count >= 4:
            return {
                "key": "review-threshold-relax",
                "severity": "info",
                "title": "검토 후보를 조금 더 넓게 받아볼 여지가 있습니다.",
                "summary": f"review 진입 {review_entry_count}건의 제출 전환율이 {current_rate:.2f}로 높습니다. 현재 검토 기준이 다소 보수적일 수 있습니다.",
                "suggested_adjustment": "`REVIEW_THRESHOLD`를 0.02~0.04 낮추고 추가 후보 유입 시 품질이 유지되는지 확인해 보세요.",
                "supporting_metrics": {
                    "entry_review_count": review_entry_count,
                    "current_review_submission_rate": current_rate,
                    "review_submission_rate_delta": delta,
                },
            }
        return None

    def _recommend_bid_now_threshold(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend tightening the immediate-bid threshold when direct submissions underperform."""
        bid_now_entry_count = int(funnel.get("entry_bid_now_count") or 0)
        current_rate = funnel.get("bid_now_submission_rate")
        delta = (funnel.get("comparison") or {}).get("bid_now_submission_rate_delta")
        if bid_now_entry_count < 3 or current_rate is None:
            return None
        if current_rate >= self.BID_NOW_RATE_TIGHTEN_THRESHOLD:
            return None

        return {
            "key": "bid-now-threshold-tighten",
            "severity": "action" if delta is not None and delta < 0 else "watch",
            "title": "즉시 투찰 기준을 다소 보수적으로 조정하세요.",
            "summary": f"bid_now 진입 {bid_now_entry_count}건의 제출 전환율이 {current_rate:.2f}입니다. 즉시 추진으로 분류되는 후보의 질이 낮아졌을 수 있습니다.",
            "suggested_adjustment": "`BID_NOW_THRESHOLD`를 0.02~0.05 상향하고, 높은 실행 복잡도 케이스의 즉시 승격을 줄이는 편이 안전합니다.",
            "supporting_metrics": {
                "entry_bid_now_count": bid_now_entry_count,
                "current_bid_now_submission_rate": current_rate,
                "bid_now_submission_rate_delta": delta,
            },
        }

    def _recommend_workload_calibration(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend reviewing auto workload calibration when auto-tagged cases underperform."""
        auto_segment = self._find_segment(funnel.get("workload_source_breakdown", []), "auto")
        provided_segment = self._find_segment(funnel.get("workload_source_breakdown", []), self.DEFAULT_WORKLOAD_SOURCE)
        if auto_segment is None or provided_segment is None:
            return None
        if int(auto_segment.get("decision_count") or 0) < 2 or int(provided_segment.get("decision_count") or 0) < 2:
            return None

        auto_rate = auto_segment.get("submission_rate")
        provided_rate = provided_segment.get("submission_rate")
        if auto_rate is None or provided_rate is None:
            return None

        gap = round(float(provided_rate) - float(auto_rate), 4)
        if gap < 0.2:
            return None

        return {
            "key": "workload-auto-calibration",
            "severity": "action" if gap >= self.WORKLOAD_GAP_ACTION_THRESHOLD else "watch",
            "title": "자동 업무부하 산정 로직을 재점검하세요.",
            "summary": f"auto workload 케이스의 제출 전환율이 {auto_rate:.2f}, provided 케이스는 {provided_rate:.2f}입니다. 자동 부하 점수가 후보를 과하게 눌러서 전환을 저해할 수 있습니다.",
            "suggested_adjustment": "auto workload 계산식의 상한을 낮추거나 `load_penalty` 가중치를 축소하는 실험을 권장합니다.",
            "supporting_metrics": {
                "auto_decision_count": auto_segment.get("decision_count"),
                "auto_submission_rate": auto_rate,
                "provided_decision_count": provided_segment.get("decision_count"),
                "provided_submission_rate": provided_rate,
                "submission_rate_gap": gap,
            },
        }

    def _recommend_category_focus(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend shifting category focus when one category clearly outperforms another."""
        segments = [
            segment
            for segment in funnel.get("category_breakdown", [])
            if int(segment.get("decision_count") or 0) >= 2 and segment.get("submission_rate") is not None
        ]
        if len(segments) < 2:
            return None

        best_segment = max(segments, key=lambda item: (float(item.get("submission_rate") or 0.0), int(item.get("decision_count") or 0)))
        worst_segment = min(segments, key=lambda item: (float(item.get("submission_rate") or 0.0), -int(item.get("decision_count") or 0)))
        gap = round(float(best_segment.get("submission_rate") or 0.0) - float(worst_segment.get("submission_rate") or 0.0), 4)
        if gap < 0.25:
            return None

        return {
            "key": "category-focus-shift",
            "severity": "action" if gap >= self.CATEGORY_GAP_ACTION_THRESHOLD else "watch",
            "title": "카테고리별 우선순위를 다시 배분하세요.",
            "summary": f"`{best_segment['segment']}` 카테고리 제출 전환율은 {best_segment['submission_rate']:.2f}인데 반해 `{worst_segment['segment']}`는 {worst_segment['submission_rate']:.2f}입니다. 카테고리별 필터 강도를 다르게 가져갈 필요가 있습니다.",
            "suggested_adjustment": f"`{worst_segment['segment']}` 카테고리에는 더 높은 확률/적합도 기준을 적용하고, `{best_segment['segment']}`에는 모니터링 우선순위를 높이는 편이 유리합니다.",
            "supporting_metrics": {
                "best_category": best_segment.get("segment"),
                "best_category_submission_rate": best_segment.get("submission_rate"),
                "worst_category": worst_segment.get("segment"),
                "worst_category_submission_rate": worst_segment.get("submission_rate"),
                "submission_rate_gap": gap,
            },
        }

    def _find_segment(self, segments: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
        """Return one breakdown segment by exact label."""
        for segment in segments:
            if str(segment.get("segment")) == label:
                return segment
        return None

    def _build_experiment_plan(self, recommendation: dict[str, Any], *, priority_rank: int) -> dict[str, Any] | None:
        """Translate one tuning recommendation into a lightweight experiment plan."""
        key = str(recommendation.get("key") or "")
        metrics = recommendation.get("supporting_metrics") or {}

        if key == "review-threshold-tighten":
            return {
                "experiment_key": "exp-review-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 상향 실험",
                "hypothesis": "review 후보 진입 기준을 높이면 review 대기열 품질이 개선되어 제출 전환율이 올라갑니다.",
                "suggested_change": "`REVIEW_THRESHOLD`를 0.03~0.05 높이고, 복잡도 상위 케이스는 review 진입에서 한 단계 더 엄격하게 거릅니다.",
                "target_metric": "review_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "review_submission_rate가 최소 +0.10p 개선되고 active_pending_count가 증가하지 않으면 성공으로 간주합니다.",
                "guardrail_metric": "overall_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "overall_submission_rate가 0.05p 이상 하락하거나 review 후보 수가 절반 이하로 줄면 롤백합니다.",
            }

        if key == "review-threshold-relax":
            return {
                "experiment_key": "exp-review-threshold-relax",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 완화 실험",
                "hypothesis": "review 기준을 조금 낮추면 제출 품질을 크게 해치지 않으면서 더 많은 유의미 후보를 검토 큐에 올릴 수 있습니다.",
                "suggested_change": "`REVIEW_THRESHOLD`를 0.02~0.04 낮추고 review 후보 수 변화와 전환율을 함께 추적합니다.",
                "target_metric": "decision_count",
                "expected_direction": "increase",
                "success_criteria": "review 후보 수가 늘면서 review_submission_rate가 0.60 이상 유지되면 성공으로 간주합니다.",
                "guardrail_metric": "review_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "review_submission_rate가 0.15p 이상 하락하면 즉시 롤백합니다.",
            }

        if key == "bid-now-threshold-tighten":
            return {
                "experiment_key": "exp-bid-now-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "즉시 투찰 기준 상향 실험",
                "hypothesis": "bid_now 기준을 높이면 즉시 추진 후보의 질이 개선되어 제출 완료 비율이 높아집니다.",
                "suggested_change": "`BID_NOW_THRESHOLD`를 0.02~0.05 높이고 복잡도 상위 케이스는 review로 유도합니다.",
                "target_metric": "bid_now_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "bid_now_submission_rate가 +0.10p 이상 개선되고 submitted_count가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "submitted_count",
                "minimum_decision_sample": max(4, int(metrics.get("entry_bid_now_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "submitted_count가 20% 이상 감소하면 롤백합니다.",
            }

        if key == "workload-auto-calibration":
            return {
                "experiment_key": "exp-workload-auto-calibration",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "자동 workload 보정 실험",
                "hypothesis": "auto workload 계산 상한과 감점 강도를 낮추면 자동 추정 케이스의 과도한 감점이 줄어 제출 전환율이 개선됩니다.",
                "suggested_change": "auto workload 상한을 0.10~0.15 낮추거나 `load_penalty` 가중치를 축소해 auto 케이스를 재평가합니다.",
                "target_metric": "auto_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "auto_submission_rate가 +0.10p 이상 개선되고 active_pending_count가 20% 이상 늘지 않으면 성공으로 간주합니다.",
                "guardrail_metric": "active_pending_count",
                "minimum_decision_sample": max(4, int(metrics.get("auto_decision_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "active_pending_count가 20% 이상 늘거나 overall_submission_rate가 하락하면 롤백합니다.",
            }

        if key == "category-focus-shift":
            return {
                "experiment_key": "exp-category-focus-shift",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "카테고리별 기준 차등화 실험",
                "hypothesis": "전환이 약한 카테고리에 더 엄격한 기준을 적용하고 강한 카테고리에 더 많은 탐색 용량을 주면 전체 제출 효율이 개선됩니다.",
                "suggested_change": "저성과 카테고리에는 확률/적합도 기준을 0.03~0.05 높이고, 고성과 카테고리는 모니터링 우선순위를 1단계 올립니다.",
                "target_metric": "worst_category_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "저성과 카테고리 제출 전환율이 +0.10p 이상 개선되거나 전체 submission_rate가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "best_category_submission_rate",
                "minimum_decision_sample": 4,
                "duration_days": 21,
                "rollback_trigger": "고성과 카테고리 제출 전환율이 0.10p 이상 하락하면 롤백합니다.",
            }

        return None

    def _build_recommendation_headline(self, funnel: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        """Create a compact narrative headline for dashboard summaries."""
        action_count = sum(1 for item in recommendations if item.get("severity") == "action")
        watch_count = sum(1 for item in recommendations if item.get("severity") == "watch")
        overall_delta = (funnel.get("comparison") or {}).get("overall_submission_rate_delta")
        if action_count:
            if overall_delta is not None:
                return f"즉시 점검이 필요한 항목 {action_count}건이 있습니다. 전체 제출 전환율은 직전 기간 대비 {overall_delta:+.2f}p 변화했습니다."
            return f"즉시 점검이 필요한 항목 {action_count}건이 있습니다."
        if watch_count:
            if overall_delta is not None:
                return f"주의 깊게 볼 최적화 포인트 {watch_count}건이 있습니다. 전체 제출 전환율은 직전 기간 대비 {overall_delta:+.2f}p 변화했습니다."
            return f"주의 깊게 볼 최적화 포인트 {watch_count}건이 있습니다."
        return "현재 기간에는 강한 하락 신호가 없어 기존 임계값을 유지해도 괜찮아 보입니다."

    def _build_trend(self, decisions: list[BidDecisionRecord], *, days: int, bucket_days: int) -> list[dict[str, Any]]:
        """Build conversion trend cohorts grouped by first decision date."""
        if not decisions:
            return []

        now = utc_now()
        period_start = (now - timedelta(days=days)).date()
        period_end = now.date()
        buckets: dict[date, list[BidDecisionRecord]] = {}

        for decision in decisions:
            entry_date = self._entry_datetime(decision).date()
            offset_days = max((entry_date - period_start).days, 0)
            bucket_index = offset_days // max(bucket_days, 1)
            bucket_start = period_start + timedelta(days=bucket_index * max(bucket_days, 1))
            buckets.setdefault(bucket_start, []).append(decision)

        trend: list[dict[str, Any]] = []
        for bucket_start in sorted(buckets.keys()):
            bucket_decisions = buckets[bucket_start]
            trend.append({
                "bucket_start": bucket_start,
                "bucket_end": min(bucket_start + timedelta(days=max(bucket_days, 1) - 1), period_end),
                **self._build_segment_metrics(bucket_decisions),
            })
        return trend

    def _build_segment_breakdown(
        self,
        decisions: list[BidDecisionRecord],
        *,
        segment_resolver,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Build top-N conversion summaries for a chosen segmentation dimension."""
        grouped: dict[str, list[BidDecisionRecord]] = {}
        for decision in decisions:
            label = str(segment_resolver(decision) or self.UNKNOWN_AGENCY)
            grouped.setdefault(label, []).append(decision)

        summaries = [
            {
                "segment": label,
                **self._build_segment_metrics(items),
            }
            for label, items in grouped.items()
        ]
        summaries.sort(key=lambda item: (-int(item["decision_count"]), -int(item["submitted_count"]), str(item["segment"])))
        return summaries[:limit]

    def _build_segment_metrics(self, decisions: list[BidDecisionRecord]) -> dict[str, Any]:
        """Compute reusable funnel metrics for a subset of decision records."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        active_pending_decisions = [decision for decision in decisions if self._is_active_pending(decision)]
        skipped_decisions = [decision for decision in decisions if self._is_skipped(decision)]

        workflow_entries = [decision for decision in decisions if self._entry_status(decision) != "submitted"]
        bid_now_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "bid_now"]
        review_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "review"]
        skip_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "skip"]

        submitted_after_bid_now = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "bid_now"]
        submitted_after_review = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "review"]
        submitted_after_skip = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "skip"]

        submission_durations = [
            hours_to_submit
            for hours_to_submit in (self._compute_hours_to_submit(decision) for decision in submitted_decisions)
            if hours_to_submit is not None
        ]

        return {
            "decision_count": len(decisions),
            "project_count": len({int(decision.project_id) for decision in decisions if decision.project_id is not None}),
            "submitted_count": len(submitted_decisions),
            "active_pending_count": len(active_pending_decisions),
            "skipped_count": len(skipped_decisions),
            "entry_bid_now_count": len(bid_now_entries),
            "entry_review_count": len(review_entries),
            "entry_skip_count": len(skip_entries),
            "submitted_after_bid_now_count": len(submitted_after_bid_now),
            "submitted_after_review_count": len(submitted_after_review),
            "submitted_after_skip_count": len(submitted_after_skip),
            "submission_rate": self._rate(len(submitted_decisions), len(decisions)),
            "bid_now_submission_rate": self._rate(len(submitted_after_bid_now), len(bid_now_entries)),
            "review_submission_rate": self._rate(len(submitted_after_review), len(review_entries)),
            "average_priority_score": self._average([float(decision.priority_score or 0.0) for decision in decisions]),
            "average_expected_margin_score": self._average([float(decision.expected_margin_score or 0.0) for decision in decisions]),
            "average_hours_to_submit": self._average(submission_durations),
        }

    def _resolve_category_segment(self, decision: BidDecisionRecord) -> str:
        """Return a stable category label for segment analysis."""
        if decision.project is None or not decision.project.category:
            return self.UNKNOWN_CATEGORY
        return str(decision.project.category)

    def _resolve_agency_segment(self, decision: BidDecisionRecord) -> str:
        """Use demand agency first, then issuing agency, for segment analysis."""
        if decision.project is None:
            return self.UNKNOWN_AGENCY
        return str(decision.project.demand_agency or decision.project.issuing_agency or self.UNKNOWN_AGENCY)

    def _entry_datetime(self, decision: BidDecisionRecord):
        """Resolve the timestamp that represents entry into the decision workflow."""
        return decision.first_decided_at or decision.created_at or decision.updated_at or utc_now()

    def _is_submitted(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision has reached submitted state."""
        return str(decision.decision_status or "") == "submitted"

    def _is_active_pending(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision is still pending active handling."""
        return str(decision.decision_status or "") in self.ACTIVE_DECISION_STATUSES

    def _is_skipped(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision has been skipped."""
        return str(decision.decision_status or "") == "skipped"

    def _entry_action(self, decision: BidDecisionRecord) -> str:
        """Resolve the original action that first introduced the record into the workflow."""
        return str(decision.initial_action or decision.action or "skip")

    def _entry_status(self, decision: BidDecisionRecord) -> str:
        """Resolve the original workflow status captured when the record was first created."""
        return str(decision.initial_decision_status or decision.decision_status or "planned")

    def _compute_hours_to_submit(self, decision: BidDecisionRecord) -> float | None:
        """Measure elapsed hours from first decision creation to submitted state."""
        first_decided_at = self._entry_datetime(decision)
        submitted_at = decision.updated_at
        if first_decided_at is None or submitted_at is None:
            return None
        delta_seconds = max((submitted_at - first_decided_at).total_seconds(), 0.0)
        return round(delta_seconds / 3600, 4)

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average for summary metrics."""
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def _rate(self, numerator: int, denominator: int) -> float | None:
        """Return a safe rounded ratio for funnel conversion metrics."""
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    def _delta(self, current_value: float | None, previous_value: float | None) -> float | None:
        """Return a rounded period-over-period delta when both values exist."""
        if current_value is None or previous_value is None:
            return None
        return round(float(current_value) - float(previous_value), 4)
