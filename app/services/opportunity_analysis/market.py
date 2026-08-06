"""Historical-data, market-insight, and recommended-amount helpers.

Blends stored bid history with caller what-if inputs, estimates market context
from similar-notice budgets, and clamps the bid recommendation into a
budget-aware range (deferring to the per-notice 투찰가 메뉴 when present).
Methods are moved verbatim from the original ``OpportunityAnalysisService`` body.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.ai.price_prediction import get_price_insights
from app.domain.aggregates import average
from app.models.models import Bid, Project
from app.utils.numeric import optional_float
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase


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

        budget_cap = float(project.budget_estimate or 0.0)
        price_lower = float(price_prediction.get("price_range_min", 0.0) or 0.0)
        price_upper = float(price_prediction.get("price_range_max", 0.0) or 0.0)
        recommended_amount = float(bid_recommendation.get("recommended_bid", 0.0) or 0.0)

        if budget_cap > 0:
            price_lower = min(price_lower, budget_cap)
            price_upper = min(price_upper if price_upper > 0 else budget_cap, budget_cap)
            recommended_amount = min(recommended_amount or budget_cap, budget_cap)

        if price_upper > 0:
            recommended_amount = min(recommended_amount, price_upper)
        if price_lower > 0:
            recommended_amount = max(recommended_amount, min(price_lower, budget_cap or price_lower))

        # RED LINE: 낙찰하한만은 예산 상한을 넘어서라도 지킨다.
        #
        # 위 ``price_lower``(=``price_range_min``)는 후보 예측가의 최솟값, 즉 시나리오
        # 범위의 하단일 뿐 법정 하한이 아니다. 그것까지 상한 위로 풀면 하한 여유가 있는
        # 공고의 추천가만 올라가 과추천이 된다(홀드아웃: 그 구간 34건 중 28건이 낙찰가에서
        # 멀어짐). 반대로 guardrail 이 낸 ``floor_price`` 는 법정 하한을 max() 로 접어
        # 넣은 **하한 그 자체**라, 이것이 상한에 깎이면 그 추천가는 하한 미달이 된다.
        # 그래서 상한을 넘길 권한은 ``floor_price`` 가 보고된 경우에만 준다.
        legal_floor_price = optional_float(price_prediction.get("floor_price")) or 0.0
        if legal_floor_price > 0:
            recommended_amount = max(recommended_amount, legal_floor_price)

        amount = round(max(0.0, recommended_amount), 2)
        # 반올림이 하한을 깨지 못하게 한다. ``round`` 는 내림이 될 수 있어 정확히 하한에
        # 착지한 추천가가 1원 미만으로 하한을 밑도는 잔차가 생긴다(홀드아웃 1/743).
        # 하한 미달은 정도의 문제가 아니라 실격 여부라 올림으로 막는다.
        if legal_floor_price > 0 and amount < legal_floor_price:
            amount = math.ceil(legal_floor_price * 100.0) / 100.0
        return amount
