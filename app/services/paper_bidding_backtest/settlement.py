"""Settlement scoring: win definition, eligibility gate, and reserve derivation.

The ``would_have_won_price_only`` / ``would_have_won_final`` verdicts in
:meth:`_build_settlement_item` are the single source of the backtest win
definition.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.ai.price_prediction import _resolve_floor_bid_rate
from app.domain.aggregates import error_rate
from app.models.models import HistoricalData, TenderResult
from app.schemas.paper_bidding_items import (
    PaperBiddingSettlementInput,
    PaperBiddingSettlementItem,
    WouldHaveWonFinal,
)
from app.services.bid_base import resolve_notice_bid_base
from app.services.paper_bidding_backtest.base import (
    PRICE_CLOSE_RATE_TOLERANCE,
    PRICE_COMPETITIVE_RATE_TOLERANCE,
    _PaperBiddingBase,
)

# JSON Text 컬럼(``reserve_prices``/``selected_numbers``)을 리스트로 해석하는 어댑터.
# ``json.loads`` 직접 호출 대신 pydantic 경로만 쓴다(원소 타입 강제는 호출부가
# 관용적으로 하므로 여기서는 "리스트인가"만 검증한다).
_JSON_LIST_ADAPTER: TypeAdapter[list[Any]] = TypeAdapter(list)


class _SettlementMixin(_PaperBiddingBase):
    """Score a paper bid against the realized award and the 낙찰하한가 gate."""

    def _build_settlement_item(
        self,
        db: Session,
        *,
        item: PaperBiddingSettlementInput,
        tender_result: TenderResult,
    ) -> PaperBiddingSettlementItem:
        winning_amount = float(tender_result.winning_amount or 0.0)
        winning_rate = self._normalize_rate(float(tender_result.winning_rate or 0.0))
        if winning_rate <= 0:
            # winning_rate 는 winning_amount / base_amount (기초금액; scsbid.py 와
            # 동일한 base-relative 정규화)이므로, 결측 시 유도값도 추정가격(ex-VAT)이
            # 아니라 base 로 나눠야 한다. base 로 나누지 않으면 과세 공고에서
            # winning_rate 가 ~10% 높게 잡혀 paper bid(이제 base 기준)와 비교가
            # 어긋난다. base 를 못 구하면 보고용 budget_estimate 로 폴백한다.
            bid_base = self._resolve_settlement_bid_base(
                db, tender_result=tender_result, item=item
            )
            if bid_base > 0:
                winning_rate = self._normalize_rate(winning_amount / bid_base)

        paper_bid_amount = float(item.paper_bid_amount or 0.0)
        paper_bid_rate = self._normalize_rate(float(item.paper_bid_rate or 0.0))
        amount_delta = paper_bid_amount - winning_amount
        # error_rate 는 winning_amount <= 0 이면 None → 기존 else 0.0 폴백을 or 0.0 로 흡수.
        absolute_error_rate = error_rate(paper_bid_amount, winning_amount) or 0.0
        bid_rate_delta = paper_bid_rate - winning_rate
        absolute_bid_rate_error = abs(bid_rate_delta)
        price_close = absolute_bid_rate_error <= PRICE_CLOSE_RATE_TOLERANCE
        price_competitive = absolute_bid_rate_error <= PRICE_COMPETITIVE_RATE_TOLERANCE
        would_have_won_price_only = (
            "plausible"
            if price_close
            else "competitive"
            if price_competitive
            else "unlikely"
        )

        # Eligibility gate. The 예정가격/낙찰하한가 come from the award's settled
        # reserve-price detail, so using them at settlement (scoring) time is not a
        # leak -- they are part of the tender *result*, not the prediction input.
        estimated_price, floor_price = self._resolve_settlement_floor(
            db,
            project_id=item.project_id,
            category=item.category,
        )
        would_have_won_final, eligibility_reason = self._score_eligibility_gate(
            our_amount=paper_bid_amount,
            winning_amount=winning_amount,
            estimated_price=estimated_price,
            floor_price=floor_price,
        )

        return PaperBiddingSettlementItem(
            project_id=item.project_id,
            # Breakdown keys (Phase 2 Experiment Lab). ``category``/``budget_estimate``
            # come straight from the candidate item so the settlement can be grouped
            # by category and budget band downstream without re-querying the project.
            category=item.category,
            budget_estimate=float(item.budget_estimate or 0.0),
            # Award announcement time (안내일/개찰일) -- used downstream to surface
            # per-category data freshness so thin/stale categories are visible.
            result_time=self._result_time(tender_result).isoformat(),
            tender_result_id=int(tender_result.id),
            result_status=str(tender_result.result_status or "awarded"),
            winning_company=tender_result.winning_company,
            winning_amount=round(winning_amount, 2),
            winning_rate=round(winning_rate, 6),
            amount_delta=round(amount_delta, 2),
            absolute_error_rate=round(absolute_error_rate, 6),
            bid_rate_delta=round(bid_rate_delta, 6),
            absolute_bid_rate_error=round(absolute_bid_rate_error, 6),
            price_close=price_close,
            price_competitive=price_competitive,
            would_have_won_price_only=would_have_won_price_only,
            would_have_won_final=would_have_won_final,
            estimated_price=(
                round(estimated_price, 2) if estimated_price is not None else None
            ),
            minimum_bid_price=(
                round(floor_price, 2) if floor_price is not None else None
            ),
            settlement_reason=eligibility_reason,
        )

    def _resolve_settlement_bid_base(
        self,
        db: Session,
        *,
        tender_result: TenderResult,
        item: PaperBiddingSettlementInput,
    ) -> float:
        """Resolve the base (기초금액/사업금액) to normalize a missing winning_rate against.

        ``winning_rate`` is base-relative (``winning_amount / base_amount``), so a
        derived fallback must divide by the notice base — not 추정가격(ex-VAT). Uses
        the live-path helper on the award's project (기초금액 is a pre-bid attribute,
        leak-safe) and falls back to the reported ``budget_estimate`` (면세 공고면
        두 값이 같아 무해) when the project/base is unavailable.
        """
        project = getattr(tender_result, "project", None)
        if project is not None:
            base = resolve_notice_bid_base(db, project)
            if base > 0:
                return base
        return float(item.budget_estimate or 0.0)

    def _score_eligibility_gate(
        self,
        *,
        our_amount: float,
        winning_amount: float,
        estimated_price: float | None,
        floor_price: float | None,
    ) -> tuple[WouldHaveWonFinal, str]:
        """Classify a paper bid against the 낙찰하한가 eligibility gate.

        Returns ``(would_have_won_final, settlement_reason)``. ``settlement_reason``
        is Korean and records the gate verdict for audit.

        * ``estimated_price``/``floor_price`` missing -> ``"unknown"`` (honest
          absence -- no reserve/planned data for the project).
        * ``our_amount < floor_price`` -> ``"disqualified"``: below the 낙찰하한가,
          so the bid is invalidated regardless of price proximity (price_close is
          NOT a win).
        * ``floor_price <= our_amount <= winning_amount`` -> ``"eligible_favorable"``:
          eligible and at/under the realized winning amount (lowest-price family).
        * otherwise (eligible but ``our_amount > winning_amount``) ->
          ``"eligible_but_outbid"``.
        """
        if estimated_price is None or floor_price is None:
            return (
                "unknown",
                "예가 정보가 없어 낙찰하한 기준 판정을 보류합니다(데이터 부재).",
            )
        if our_amount < floor_price:
            return (
                "disqualified",
                (
                    f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                    "미만이라 적격 미달로 탈락 처리합니다(가격 근접 여부와 무관)."
                ),
            )
        if our_amount <= winning_amount:
            return (
                "eligible_favorable",
                (
                    f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                    f"이상이며 실낙찰가 {winning_amount:,.0f}원 이하라 적격·유리합니다."
                ),
            )
        return (
            "eligible_but_outbid",
            (
                f"투찰가 {our_amount:,.0f}원이 낙찰하한가 {floor_price:,.0f}원 "
                f"이상으로 적격이나 실낙찰가 {winning_amount:,.0f}원보다 높아 패찰합니다."
            ),
        )

    def _resolve_settlement_floor(
        self,
        db: Session,
        *,
        project_id: int,
        category: str | None,
    ) -> tuple[float | None, float | None]:
        """Resolve ``(estimated_price, floor_price)`` for a settled project.

        ``estimated_price`` (예정가격) is derived from the project's settled
        ``HistoricalData`` (the award's reserve-price detail):

        1. mean of the *selected* 복수예비가격 (``reserve_prices[n-1]`` for each
           ``n`` in ``selected_numbers``) -- the KONEPS 예정가격 derivation, or
        2. the collected ``predicted_price`` (planned price) when no usable
           selected-reserve mean exists.

        ``floor_price`` (낙찰하한가) = ``estimated_price`` × the category
        minimum-bid-rate floor (``_resolve_floor_bid_rate``). Both are ``None``
        when no estimated price can be derived -- an honest "not enough data"
        signal the gate maps to ``"unknown"``.

        Uses the *category-only* floor (no group-calibrated floor): per §4.7 the
        category floor is the hard lower bound of any group floor, so scoring with
        the category floor never disqualifies a bid the predictor's (possibly
        higher group) floor would have allowed -- it errs on the safe side.

        ``project_id`` 는 :class:`PaperBiddingSettlementInput` 이 필수 ``int`` 로
        보장하므로 종전의 ``None`` 가드는 사라졌다(계약이 대신 막는다).
        """
        record = (
            db.query(HistoricalData)
            .filter(HistoricalData.project_id == int(project_id))
            .order_by(
                HistoricalData.opened_at.desc().nullslast(),
                HistoricalData.id.desc(),
            )
            .first()
        )
        if record is None:
            return (None, None)

        estimated_price = self._derive_estimated_price(record)
        if estimated_price is None or estimated_price <= 0:
            return (None, None)

        floor_rate = _resolve_floor_bid_rate(category)
        if floor_rate is None or floor_rate <= 0:
            # No configured floor for this category -> cannot apply the gate.
            return (estimated_price, None)

        return (estimated_price, estimated_price * float(floor_rate))

    def _derive_estimated_price(self, record: HistoricalData) -> float | None:
        """Derive 예정가격 from a settled ``HistoricalData`` row.

        Prefers the mean of the *selected* reserve prices (복수예비가격), falling
        back to the collected ``predicted_price``. Returns ``None`` when neither is
        usable.
        """
        reserve_prices = self._coerce_float_list(record.reserve_prices)
        selected_numbers = self._coerce_int_list(record.selected_numbers)
        picked = [
            reserve_prices[number - 1]
            for number in selected_numbers
            if 1 <= number <= len(reserve_prices)
        ]
        picked = [value for value in picked if value and value > 0]
        if picked:
            return sum(picked) / len(picked)

        predicted_price = float(record.predicted_price or 0.0)
        if predicted_price > 0:
            return predicted_price
        return None

    @staticmethod
    def _coerce_float_list(raw_value: Any) -> list[float]:
        """Parse a JSON-encoded numeric list (``reserve_prices`` Text column)."""
        # Sibling static helper now lives on this mixin; the original referenced it
        # as ``PaperBiddingBacktestService._coerce_json_list`` (the composed class),
        # which is not importable here without a circular import. Only necessary
        # seam change of the decomposition -- behaviour is identical.
        values = _SettlementMixin._coerce_json_list(raw_value)
        result: list[float] = []
        for value in values:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _coerce_int_list(raw_value: Any) -> list[int]:
        """Parse a JSON-encoded integer list (``selected_numbers`` Text column)."""
        values = _SettlementMixin._coerce_json_list(raw_value)
        result: list[int] = []
        for value in values:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _coerce_json_list(raw_value: Any) -> list[Any]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple)):
            return list(raw_value)
        text = str(raw_value).strip()
        if not text:
            return []
        try:
            return _JSON_LIST_ADAPTER.validate_json(text)
        except ValidationError:
            # 깨진 JSON 이거나 리스트가 아닌 값(스칼라/오브젝트) -> 표본 없음.
            return []
