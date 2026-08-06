"""Historical-data, market-insight, and recommended-amount helpers.

Blends stored bid history with caller what-if inputs, estimates market context
from similar-notice budgets, and clamps the bid recommendation into a
budget-aware range (deferring to the per-notice 투찰가 메뉴 when present).
Methods are moved verbatim from the original ``OpportunityAnalysisService`` body.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.price_prediction import get_price_insights
from app.domain.aggregates import average
from app.models.models import Bid, Project
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase
from app.utils.numeric import optional_float

# 기초금액/추정가격 비의 타당 상한. 부가세는 10% 이므로 정상 과세 공고는 ~1.10 이고,
# 면세 공고는 1.00 이다. 이 값을 넘는 비는 세금이 아니라 수집 오염이다(예: 예정가가
# base 자리에 덮인 이력 — #199/#220 계열). 오염된 base 를 예산 상한으로 삼으면 추천가가
# 함께 부풀므로, 그런 공고는 상한을 올리지 않고 추정가격에 머문다.
BUDGET_CAP_PLAUSIBLE_BASE_RATIO = 1.5


def resolve_budget_cap(reported_base: float | None, budget_estimate: float | None) -> float:
    """추천 투찰가의 예산 상한 — 투찰율이 곱해지는 기초금액과 같은 basis.

    이 상한은 predictor 가 낸 ``price_range``(기초금액-relative)를 자르므로 같은 basis
    여야 한다. 도입 시점(2026-05-10)에는 predictor 도 추정가격 기준이라 일치했으나,
    #162 가 predictor 만 기초금액으로 옮기고 이 상한을 남겨 두면서 자가 갈렸다 —
    설계가 아니라 부수 피해다.

    ``BUDGET_CAP_PLAUSIBLE_BASE_RATIO`` 를 넘는 base 는 오염으로 보고 추정가격을 쓴다.
    """
    base = optional_float(reported_base)
    estimate = optional_float(budget_estimate) or 0.0
    if base is None or base <= 0:
        return max(0.0, estimate)
    if estimate > 0 and base > estimate * BUDGET_CAP_PLAUSIBLE_BASE_RATIO:
        return estimate
    return base


class _MarketInputsMixin(_OpportunityAnalysisBase):
    """User-history, market-insight, and recommended-amount resolution."""

    def _build_user_historical_data(
        self,
        db: Session,
        *,
        operator_id: int,
        request_data: dict | None,
    ) -> dict:
        """Blend stored bid history with any caller-provided what-if inputs."""
        payload = dict(request_data or {})
        bids = db.query(Bid).filter(Bid.user_id == operator_id).all()
        if bids:
            payload.setdefault(
                "average_bid",
                average((float(bid.bid_amount or 0.0) for bid in bids), digits=2),
            )
            accepted_count = sum(1 for bid in bids if bid.status == "accepted")
            payload.setdefault("win_rate", round(accepted_count / len(bids), 4))
            payload.setdefault("bid_count", len(bids))
        return payload

    def _build_market_insights(self, project: Project, similar_projects: dict) -> dict:
        """Estimate market context from similar notice budgets."""
        historical_bids = [
            {"amount": float(item.get("budget_estimate") or 0.0)}
            for item in similar_projects.get("results", [])
            if float(item.get("budget_estimate") or 0.0) > 0
        ]

        if not historical_bids and float(project.budget_estimate or 0.0) > 0:
            historical_bids = [{"amount": float(project.budget_estimate or 0.0)}]

        insights = get_price_insights(historical_bids)
        insights["competitiveness_score"] = 0.0
        return insights

    def _resolve_recommended_amount(self, project: Project, price_prediction: dict, bid_recommendation: dict) -> float:
        """Clamp the bid recommendation into a sensible, budget-aware range.

        ★결정1: when a per-notice 투찰가 메뉴 was assembled (발주처 밴드가 있어
        메뉴가 존재), align the recommended amount to the menu's 'recommended'
        option 투찰가 (사업금액 base × 위치조정 rate) so the headline
        recommended_amount and the menu never disagree. The budget-aware clamping
        below is kept as the fallback when there is no menu.
        """
        menu = (price_prediction or {}).get("bid_target_menu")
        if menu:
            for option in menu.get("options", []):
                if option.get("label") == "recommended" and option.get("bid_price") is not None:
                    return float(option["bid_price"])

        budget_cap = resolve_budget_cap(
            price_prediction.get("bid_base"), project.budget_estimate
        )
        price_lower = float(price_prediction.get("price_range_min", 0.0) or 0.0)
        price_upper = float(price_prediction.get("price_range_max", 0.0) or 0.0)
        recommended_amount = float(bid_recommendation.get("recommended_bid", 0.0) or 0.0)

        if budget_cap > 0:
            price_upper = min(price_upper if price_upper > 0 else budget_cap, budget_cap)
            recommended_amount = min(recommended_amount or budget_cap, budget_cap)

        if price_upper > 0:
            recommended_amount = min(recommended_amount, price_upper)
        if price_lower > 0:
            # RED LINE: 예산 상한이 낙찰하한을 끌어내리지 않는다. 예전에는 여기서
            # ``min(price_lower, budget_cap)`` 으로 깎아, 하한×기초금액이 추정가격보다
            # 큰 과세 공고에서 **제출 즉시 실격**인 추천가를 만들었다(홀드아웃 78/400).
            recommended_amount = max(recommended_amount, price_lower)

        return round(max(0.0, recommended_amount), 2)
