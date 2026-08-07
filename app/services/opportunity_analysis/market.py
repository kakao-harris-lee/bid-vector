"""Historical-data, market-insight, and recommended-amount helpers.

Blends stored bid history with caller what-if inputs, estimates market context
from similar-notice budgets, and clamps the bid recommendation into a
budget-aware range (deferring to the per-notice 투찰가 메뉴 when present).

CONTRACT CHANGE (#356): "budget-aware" 는 더 이상 무조건이 아니다. 공고가 published
법정하한을 보고했고 그 하한이 신뢰할 수 있는 base 위에서 계산됐을 때는 추천가가
추정가격(예산 상한)을 **넘을 수 있다** — 하한 미달 추천을 내지 않기 위해서다.
:func:`enforceable_floor_price` 가 그 조건을 판정한다.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.ai.price_prediction import get_price_insights
from app.core.constants import BID_BASE_TRUST_RATIO_MAX
from app.domain.aggregates import average
from app.models.models import Bid, Project
from app.utils.numeric import optional_float
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase


def enforceable_floor_price(
    legal_floor_bid_rate: float | None,
    floor_price: float | None,
    bid_base: float | None,
    budget_cap: float,
) -> float:
    """예산 상한을 넘어서라도 지킬 하한. 지킬 수 없으면 ``0.0``.

    상한 초과는 **하한을 믿을 수 있을 때만** 허용한다: ① 공고가 published 법정하한을
    실제로 보고했고(``legal_floor_bid_rate``) ② 기초금액이 추정가격의
    :data:`BID_BASE_TRUST_RATIO_MAX` 배 이내일 것. 두 게이트의 실측 근거(미보고 하한
    경로의 상승 12/12 악화, 오염 base 임계에서 실낙찰자 전원이 임계 미만 낙찰)는 PR #356
    본문에 있다.

    ``budget_cap`` 이 0 이면 넘을 상한이 없어 비율 검사를 건너뛰고, ``bid_base`` 를 못
    받으면 비율을 검증할 수 없어 **보수적으로 닫는다**(legacy bound 유지).
    """
    if legal_floor_bid_rate is None:
        return 0.0
    floor = optional_float(floor_price) or 0.0
    if floor <= 0:
        return 0.0
    if budget_cap <= 0:
        return floor
    base = optional_float(bid_base) or 0.0
    # 곱셈형(base > cap × 임계)이다. 같은 임계를 쓰는 분류기
    # (``app/services/base_amount_basis.py::_is_suspect_ratio``)는 나눗셈형
    # (base ÷ est > 임계)이라, 경계에 정확히 걸리는 값에서 부동소수 반올림이 갈릴 수 있다.
    # 형태를 통일하지 않는 이유: 두 지점은 다른 질문에 답하고(라이브 게이트 vs 저장 라벨)
    # 어느 쪽도 상대의 판정을 읽지 않으므로, 갈려도 한쪽이 다른 쪽을 틀리게 만들지 않는다.
    # 통일은 두 경로의 수치를 동시에 움직이므로 별도 검증이 필요하다.
    if base <= 0 or base > budget_cap * BID_BASE_TRUST_RATIO_MAX:
        return 0.0
    return floor


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

        ★결정2 (#356): 예산 상한은 절대 상한이 아니다 — 신뢰할 수 있는 published
        법정하한이 그 위에 있으면 하한을 따른다(:func:`enforceable_floor_price`).
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

        # 예산 상한을 넘길 권한은 **믿을 수 있는 하한에만** 준다(아래 게이트 참조).
        enforced_floor = enforceable_floor_price(
            price_prediction.get("legal_floor_bid_rate"),
            price_prediction.get("floor_price"),
            price_prediction.get("bid_base"),
            budget_cap,
        )
        if enforced_floor > 0:
            recommended_amount = max(recommended_amount, enforced_floor)

        amount = round(max(0.0, recommended_amount), 2)
        # 반올림 내림이 하한을 서브센트만큼 깨던 잔차(반사실 1/743, 0.005원)를 올림으로
        # 막는다 — 하한 미달은 정도가 아니라 실격 여부의 문제다.
        if enforced_floor > 0 and amount < enforced_floor:
            amount = math.ceil(enforced_floor * 100.0) / 100.0
        return amount
