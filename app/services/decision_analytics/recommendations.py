"""Funnel-derived tuning recommendations and their experiment plans."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.models.models import User
from app.services.decision_analytics.base import _DecisionAnalyticsBase


class _RecommendationsMixin(_DecisionAnalyticsBase):
    """Threshold/workload/category recommendations plus experiment plans."""

    def build_recommendations(
        self,
        db: Session,
        *,
        days: int = 30,
        breakdown_limit: int = 5,
        trend_bucket_days: int = 7,
        recommendation_limit: int = 5,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Convert funnel analytics into actionable tuning recommendations."""
        if operator is None:
            operator = ensure_operator_account(db)
        funnel = self.build_funnel(
            db,
            days=days,
            limit=max(3, recommendation_limit),
            breakdown_limit=breakdown_limit,
            trend_bucket_days=trend_bucket_days,
            operator=operator,
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
        experiment_history = self._build_recommendation_experiment_history(
            db,
            operator_id=int(funnel["operator_id"]),
            days=days,
        )
        recommendations = self._apply_experiment_history_to_recommendations(
            recommendations,
            experiment_history=experiment_history,
        )

        if not recommendations:
            recommendations.append(self._with_default_recommendation_priority({
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
            }))

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
            "experiment_history": experiment_history,
            "recommended_next_experiment": experiments[0] if experiments else None,
            "experiments": experiments,
            "recommendations": enriched_recommendations,
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

    def _build_experiment_plan(self, recommendation: dict[str, Any], *, priority_rank: int) -> dict[str, Any] | None:
        """Translate one tuning recommendation into a lightweight experiment plan."""
        key = str(recommendation.get("key") or "")
        metrics = recommendation.get("supporting_metrics") or {}
        parameter_recommendation = recommendation.get("parameter_recommendation")
        if not isinstance(parameter_recommendation, dict):
            parameter_recommendation = {}

        if key == "review-threshold-tighten":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.04)
            return {
                "experiment_key": "exp-review-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 상향 실험",
                "hypothesis": "review 후보 진입 기준을 높이면 review 대기열 품질이 개선되어 제출 전환율이 올라갑니다.",
                "suggested_change": f"`REVIEW_THRESHOLD`를 {delta:.3f} 높이고, 복잡도 상위 케이스는 review 진입에서 한 단계 더 엄격하게 거릅니다.",
                "target_metric": "review_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "review_submission_rate가 최소 +0.10p 개선되고 active_pending_count가 증가하지 않으면 성공으로 간주합니다.",
                "guardrail_metric": "overall_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "overall_submission_rate가 0.05p 이상 하락하거나 review 후보 수가 절반 이하로 줄면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        if key == "review-threshold-relax":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.03)
            return {
                "experiment_key": "exp-review-threshold-relax",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 완화 실험",
                "hypothesis": "review 기준을 조금 낮추면 제출 품질을 크게 해치지 않으면서 더 많은 유의미 후보를 검토 큐에 올릴 수 있습니다.",
                "suggested_change": f"`REVIEW_THRESHOLD`를 {delta:.3f} 낮추고 review 후보 수 변화와 전환율을 함께 추적합니다.",
                "target_metric": "decision_count",
                "expected_direction": "increase",
                "success_criteria": "review 후보 수가 늘면서 review_submission_rate가 0.60 이상 유지되면 성공으로 간주합니다.",
                "guardrail_metric": "review_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "review_submission_rate가 0.15p 이상 하락하면 즉시 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        if key == "bid-now-threshold-tighten":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.03)
            return {
                "experiment_key": "exp-bid-now-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "즉시 투찰 기준 상향 실험",
                "hypothesis": "bid_now 기준을 높이면 즉시 추진 후보의 질이 개선되어 제출 완료 비율이 높아집니다.",
                "suggested_change": f"`BID_NOW_THRESHOLD`를 {delta:.3f} 높이고 복잡도 상위 케이스는 review로 유도합니다.",
                "target_metric": "bid_now_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "bid_now_submission_rate가 +0.10p 이상 개선되고 submitted_count가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "submitted_count",
                "minimum_decision_sample": max(4, int(metrics.get("entry_bid_now_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "submitted_count가 20% 이상 감소하면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
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
            best_delta = float(parameter_recommendation.get("best_category_delta") or 0.03)
            worst_delta = float(parameter_recommendation.get("worst_category_delta") or -0.03)
            best_category = parameter_recommendation.get("best_category") or metrics.get("best_category") or "고성과 카테고리"
            worst_category = parameter_recommendation.get("worst_category") or metrics.get("worst_category") or "저성과 카테고리"
            return {
                "experiment_key": "exp-category-focus-shift",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "카테고리별 기준 차등화 실험",
                "hypothesis": "전환이 약한 카테고리에 더 엄격한 기준을 적용하고 강한 카테고리에 더 많은 탐색 용량을 주면 전체 제출 효율이 개선됩니다.",
                "suggested_change": f"`{best_category}` category override를 {best_delta:+.3f}, `{worst_category}` override를 {worst_delta:+.3f} 조정합니다.",
                "target_metric": "worst_category_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "저성과 카테고리 제출 전환율이 +0.10p 이상 개선되거나 전체 submission_rate가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "best_category_submission_rate",
                "minimum_decision_sample": 4,
                "duration_days": 21,
                "rollback_trigger": "고성과 카테고리 제출 전환율이 0.10p 이상 하락하면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        return None
