"""Historical dataset helpers for price prediction."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session, joinedload

from app.ai.business_group import resolve_business_group
from app.core.time import utc_now
from app.domain.rate_normalization import BID_RATE_PLAUSIBLE_MAX, BID_RATE_PLAUSIBLE_MIN, to_bid_rate_fraction
from app.domain.reliable_base import get_reliable_base
from app.models.models import (
    HistoricalData,
    PaperBid,
    PaperBidSettlement,
    TenderResult,
)
from app.services.prediction_label_basis import award_rate_label_for
from app.services.query_predicates import settled_any_signal
from app.utils.sequence_coercion import (
    coerce_integer_list,
    coerce_numeric_list,
)
from app.utils.textfmt import normalize_lookup_key

_CATEGORY_ALIASES = {
    "general-service": "service",
    "일반용역": "service",
    "service": "service",
    "technical-service": "technical-service",
    "기술용역": "technical-service",
    "construction": "construction",
    "공사": "construction",
    "software": "software",
    "소프트웨어": "software",
    "goods": "goods",
    "물품": "goods",
}

_RELATED_PRICE_HISTORY_CATEGORIES = {
    "technical-service": ("service",),
    "general-service": ("service",),
    "service": ("technical-service", "general-service"),
    "software": ("service", "technical-service"),
}


class PredictionDatasetService:
    """Build normalized historical bid-rate series for prediction logic."""

    VALID_BID_RATE_MIN = BID_RATE_PLAUSIBLE_MIN  # 창의 단일 출처는 rate_normalization
    VALID_BID_RATE_MAX = BID_RATE_PLAUSIBLE_MAX  # (값 동일 — 이 두 줄은 이름 재노출)
    RESERVE_CONTEXT_BACKFILL_LIMIT = 60

    def load_historical_series(
        self,
        db: Session,
        *,
        category: str | None = None,
        agency_name: str | None = None,
        limit: int = 120,
        explicit_bid_rate_only: bool = False,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Load a normalized bid-rate series from stored historical rows."""
        category_scope = self._category_scope(category)
        effective_as_of = as_of or utc_now()
        records: list[HistoricalData] = []
        seen_historical_ids: set[int] = set()

        scopes: list[tuple[list[str], str | None]] = []
        if agency_name:
            scopes.append((category_scope[:1], agency_name))
        scopes.append((category_scope[:1], None))
        if len(category_scope) > 1:
            scopes.append((category_scope[1:], None))
        if not scopes:
            scopes.append(([], agency_name))

        for categories, scoped_agency_name in scopes:
            if len(records) >= limit:
                break
            for record in self._load_records(
                db,
                categories=categories,
                agency_name=scoped_agency_name,
                explicit_bid_rate_only=explicit_bid_rate_only,
                as_of=effective_as_of,
                exclude_historical_ids=seen_historical_ids,
                limit=max(1, int(limit or 1)) - len(records),
            ):
                record_id = int(record.id or 0)
                if record_id in seen_historical_ids:
                    continue
                records.append(record)
                seen_historical_ids.add(record_id)

        latest_results = self._load_latest_tender_results(
            db,
            project_ids={
                int(record.project_id)
                for record in records
                if record.project_id is not None
            },
        )

        series: list[dict[str, Any]] = []
        for record in records:
            tender_result = (
                latest_results.get(int(record.project_id))
                if record.project_id is not None
                else None
            )
            normalized = self._serialize_series_point(
                record,
                tender_result=tender_result,
                explicit_bid_rate_only=explicit_bid_rate_only,
            )
            if normalized is not None:
                series.append(normalized)

        if series and not any(item.get("reserve_prices") for item in series):
            supplemental_records = self._load_reserve_context_backfill_records(
                db,
                category_scope=category_scope,
                agency_name=agency_name,
                explicit_bid_rate_only=explicit_bid_rate_only,
                as_of=effective_as_of,
                limit=self.RESERVE_CONTEXT_BACKFILL_LIMIT,
                exclude_historical_ids=seen_historical_ids,
            )
            if supplemental_records:
                supplemental_results = self._load_latest_tender_results(
                    db,
                    project_ids={
                        int(record.project_id)
                        for record in supplemental_records
                        if record.project_id is not None
                    },
                )
                for record in supplemental_records:
                    tender_result = (
                        supplemental_results.get(int(record.project_id))
                        if record.project_id is not None
                        else None
                    )
                    normalized = self._serialize_series_point(
                        record,
                        tender_result=tender_result,
                        explicit_bid_rate_only=explicit_bid_rate_only,
                    )
                    if normalized is None:
                        continue
                    series.append(normalized)
                    seen_historical_ids.add(int(record.id or 0))
        return series

    def _load_records(
        self,
        db: Session,
        *,
        categories: list[str],
        agency_name: str | None,
        explicit_bid_rate_only: bool,
        as_of: datetime,
        exclude_historical_ids: set[int],
        limit: int,
    ) -> list[HistoricalData]:
        """Load one prediction-history scope."""
        query = db.query(HistoricalData).options(joinedload(HistoricalData.project))
        if categories:
            query = query.filter(HistoricalData.category.in_(categories))
        if agency_name:
            query = query.filter(
                HistoricalData.agency_name.ilike(f"%{agency_name.strip()}%")
            )
        query = self._apply_as_of_filter(query, as_of=as_of)
        if explicit_bid_rate_only:
            query = query.filter(self._has_explicit_bid_rate_or_result())
        if exclude_historical_ids:
            query = query.filter(HistoricalData.id.notin_(exclude_historical_ids))

        return (
            query.order_by(
                HistoricalData.opened_at.desc(), HistoricalData.created_at.desc()
            )
            .limit(limit)
            .all()
        )

    def _load_reserve_context_backfill_records(
        self,
        db: Session,
        *,
        category_scope: list[str],
        agency_name: str | None,
        explicit_bid_rate_only: bool,
        as_of: datetime,
        exclude_historical_ids: set[int],
        limit: int,
    ) -> list[HistoricalData]:
        """Load additional rows with reserve metadata when recent slices do not contain any."""
        query = db.query(HistoricalData).options(joinedload(HistoricalData.project))
        if category_scope:
            query = query.filter(HistoricalData.category.in_(category_scope))
        if agency_name:
            query = query.filter(
                HistoricalData.agency_name.ilike(f"%{agency_name.strip()}%")
            )
        query = self._apply_as_of_filter(query, as_of=as_of)
        if explicit_bid_rate_only:
            query = query.filter(self._has_explicit_bid_rate_or_result())
        if exclude_historical_ids:
            query = query.filter(HistoricalData.id.notin_(exclude_historical_ids))

        query = query.filter(HistoricalData.reserve_prices.isnot(None))
        query = query.filter(HistoricalData.reserve_prices != "")
        query = query.filter(HistoricalData.reserve_prices != "[]")

        return (
            query.order_by(
                HistoricalData.opened_at.desc(), HistoricalData.created_at.desc()
            )
            .limit(max(0, int(limit or 0)))
            .all()
        )

    def _category_scope(self, category: str | None) -> list[str]:
        normalized_category = self._normalize_category(category)
        if not normalized_category:
            return []
        categories = [normalized_category]
        for related_category in _RELATED_PRICE_HISTORY_CATEGORIES.get(
            normalized_category, ()
        ):
            if related_category not in categories:
                categories.append(related_category)
        return categories

    def _normalize_category(self, category: str | None) -> str:
        return normalize_lookup_key(category, _CATEGORY_ALIASES)

    def build_training_dataset(
        self,
        db: Session,
        *,
        category: str | None = None,
        agency_name: str | None = None,
        limit: int = 120,
        explicit_bid_rate_only: bool = False,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a normalized training dataset plus lightweight quality summary."""
        series = self.load_historical_series(
            db,
            category=category,
            agency_name=agency_name,
            limit=limit,
            explicit_bid_rate_only=explicit_bid_rate_only,
            as_of=as_of,
        )
        opened_at_values = [
            item["opened_at"] for item in series if item.get("opened_at") is not None
        ]
        linked_result_count = sum(
            1 for item in series if item.get("tender_result_status")
        )
        reserve_pattern_sample_count = sum(
            1 for item in series if item.get("reserve_prices")
        )

        return {
            "category": category,
            "agency_name": agency_name,
            "summary": {
                "sample_count": len(series),
                "project_count": len(
                    {
                        item.get("project_id")
                        for item in series
                        if item.get("project_id") is not None
                    }
                ),
                "agency_count": len(
                    {
                        item.get("agency_name")
                        for item in series
                        if item.get("agency_name")
                    }
                ),
                "linked_result_count": linked_result_count,
                "reserve_pattern_sample_count": reserve_pattern_sample_count,
                "started_at": min(opened_at_values) if opened_at_values else None,
                "ended_at": max(opened_at_values) if opened_at_values else None,
            },
            "series": series,
        }

    def build_probability_calibration_dataset(
        self,
        db: Session,
        *,
        category: str | None = None,
        limit: int = 2000,
    ) -> dict[str, Any]:
        """Build a labeled dataset for calibrating the낙찰-가능성 (probability) score.

        Each row pairs the cheap, *inference-time* signals the Platt calibration
        consumes (``confidence_score``, ``matched_score``, ``business_group``,
        ``category``) with a settlement-derived binary label.

        ``historical_sample_size`` is deliberately NOT a feature: it is not persisted
        on ``PaperBid``, so it would always be 0 here while inference sees a real
        count — a train/serve skew. The calibration raw therefore uses only
        confidence/matched (see ``historical.calibration_raw_signal``).

        **Leakage guard.** The settlement fields (``would_have_won_final``,
        ``would_have_won_price_only``) are LABELS only. They are never copied into
        the ``features`` block, so a model trained on this dataset can never see a
        settled outcome as an input. Only columns persisted on ``PaperBid`` at
        decision time (before settlement) populate ``features``.

        Rows whose ``would_have_won_final`` is ``"unknown"`` (no eligibility verdict
        yet — typically pre-backfill or missing 예정가격) are excluded from the
        primary label set to avoid class pollution. A ``price_close`` fallback label
        is exposed alongside so the trainer can fall back when the eligibility-gated
        positives are too few.
        """
        safe_limit = max(1, min(int(limit or 2000), 20000))
        query = (
            db.query(PaperBid, PaperBidSettlement)
            .join(PaperBidSettlement, PaperBidSettlement.paper_bid_id == PaperBid.id)
            .options(joinedload(PaperBid.project))
        )
        if category:
            normalized = self._normalize_category(category)
            scope = self._category_scope(category) or [normalized]
            query = query.filter(PaperBid.project_id.isnot(None))
            # Filter on the stored paper_bid category proxy via the linked project
            # category is not persisted on PaperBid; we filter post-hoc below.
            scope_set = {value for value in scope if value}
        else:
            scope_set = None
        rows = query.order_by(PaperBid.id.desc()).limit(safe_limit).all()

        items: list[dict[str, Any]] = []
        eligibility_labeled = 0
        positive_count = 0
        unknown_count = 0
        for paper_bid, settlement in rows:
            business_group, _ = self._resolve_paper_bid_business_group(paper_bid)
            if scope_set is not None and business_group not in scope_set:
                continue

            would_have_won_final = str(settlement.would_have_won_final or "unknown")
            would_have_won_price_only = str(
                settlement.would_have_won_price_only or "unknown"
            )
            # Primary label: eligibility-gated favorable outcome. "unknown" rows are
            # dropped from the primary label (None), keeping them out of training.
            if would_have_won_final == "unknown":
                primary_label: int | None = None
                unknown_count += 1
            elif would_have_won_final == "eligible_favorable":
                primary_label = 1
                eligibility_labeled += 1
                positive_count += 1
            else:
                primary_label = 0
                eligibility_labeled += 1
            # Fallback label (price-only): used only if the trainer lacks enough
            # eligibility-gated positives. Never mixed silently with the primary.
            price_only_label = 1 if would_have_won_price_only == "plausible" else 0

            items.append(
                {
                    "paper_bid_id": int(paper_bid.id),
                    "project_id": paper_bid.project_id,
                    # --- features (inference-time signals ONLY) ---
                    "features": {
                        "confidence_score": float(paper_bid.confidence_score or 0.0),
                        "matched_score": float(paper_bid.matched_score or 0.0),
                        "business_group": business_group,
                        "category": business_group,
                    },
                    # --- labels (settled outcomes — never features) ---
                    "label": primary_label,
                    "would_have_won_final": would_have_won_final,
                    "price_close_label": price_only_label,
                    "would_have_won_price_only": would_have_won_price_only,
                }
            )

        return {
            "category": category,
            "summary": {
                "sample_count": len(items),
                "eligibility_labeled_count": eligibility_labeled,
                "positive_count": positive_count,
                "unknown_excluded_count": unknown_count,
            },
            "items": items,
        }

    def _resolve_paper_bid_category(self, paper_bid: PaperBid) -> str | None:
        """Best-effort category for a paper bid via its linked project."""
        project = getattr(paper_bid, "project", None)
        if project is not None:
            return getattr(project, "category", None)
        return None

    def _resolve_paper_bid_business_group(
        self, paper_bid: PaperBid
    ) -> tuple[str | None, str | None]:
        """Resolve the business group for a paper bid, preferring project 업종코드."""
        project = getattr(paper_bid, "project", None)
        if project is not None:
            business_type_code = getattr(project, "business_type_code", None)
            code_group = resolve_business_group(business_type_code)
            if code_group:
                return code_group, "business_type_code"
            category_group = (
                self._normalize_category(getattr(project, "category", None)) or None
            )
            if category_group:
                return category_group, "category"
        return None, None

    def _reliable_base_amount(self, record: HistoricalData) -> float:
        """basis-aware 기초금액 — #199 오염 태그를 보고 신뢰 base를 고른다(순수 접근자 재사용).

        raw ``base_amount`` 를 그대로 읽으면 개찰 후 settled 스냅샷의 예정가-basis 오염
        (``win ÷ winning_rate`` 역산, #199 실측 settled 66.2%)을 학습·데이터셋 조립이
        그대로 소비한다. ``get_reliable_base`` (bid_base 가격 경로와 동일한 프리미티브,
        #225)로 record별 신뢰 base를 고른다: clean/basis-미상 행은 ``base_amount`` 그대로
        (회귀 0), 명시적 non-clean 행만 복수예비가격 midpoint 복구 추정치
        (``base_amount_estimated``)로 대체한다. 양수 base가 없으면 0.0(기존 폴백 의미 보존).
        """
        reliable = get_reliable_base(
            base_amount=record.base_amount,
            basis=record.base_amount_basis,
            base_amount_estimated=record.base_amount_estimated,
        )
        value = reliable.value
        return float(value) if value is not None and value > 0 else 0.0

    def _serialize_series_point(
        self,
        record: HistoricalData,
        *,
        tender_result: TenderResult | None,
        explicit_bid_rate_only: bool = False,
    ) -> dict[str, Any] | None:
        """Normalize one historical row into a predictor-friendly dictionary."""
        bid_rate, bid_rate_source = self._resolve_bid_rate(
            record,
            tender_result=tender_result,
            allow_predicted_price_fallback=not explicit_bid_rate_only,
        )
        if bid_rate is None:
            return None
        business_group, business_group_source = self._resolve_record_business_group(
            record
        )
        project = getattr(record, "project", None)

        return {
            "historical_data_id": record.id,
            "project_id": record.project_id,
            "notice_number": record.notice_number,
            "category": record.category,
            "business_group": business_group,
            "business_group_source": business_group_source,
            "business_type_code": getattr(project, "business_type_code", None)
            if project is not None
            else None,
            "business_type_label": getattr(project, "business_type_label", None)
            if project is not None
            else None,
            "agency_name": record.agency_name,
            "base_amount": self._reliable_base_amount(record),
            # raw 오염 태그(#199) — distribution predictor 의 clean 필터 입력. base 는
            # 위에서 basis-aware 로 치환되므로, 태그 없이 실으면 non-clean 행의
            # 자기참조 비율(예비가/중점)이 사정률 관측을 오염시킨다(리뷰 K2).
            "base_amount_basis": record.base_amount_basis,
            "predicted_price": float(record.predicted_price or 0.0),
            "bid_rate": bid_rate,
            "bid_rate_source": bid_rate_source,
            "award_rate_label": award_rate_label_for(record, tender_result),
            "reserve_prices": coerce_numeric_list(record.reserve_prices),
            "selected_numbers": coerce_integer_list(record.selected_numbers),
            "opened_at": record.opened_at or record.created_at,
            "tender_result_id": tender_result.id if tender_result is not None else None,
            "winning_amount": float(tender_result.winning_amount or 0.0)
            if tender_result is not None
            else None,
            "winning_rate": float(tender_result.winning_rate or 0.0)
            if tender_result is not None
            else None,
            "tender_result_status": tender_result.result_status
            if tender_result is not None
            else None,
            "tender_result_announced_at": tender_result.announced_at
            if tender_result is not None
            else None,
        }

    def _resolve_bid_rate(
        self,
        record: HistoricalData,
        *,
        tender_result: TenderResult | None = None,
        allow_predicted_price_fallback: bool = True,
    ) -> tuple[float | None, str | None]:
        """Resolve a usable bid rate from the row's explicit or derived values."""
        bid_rate = float(record.bid_rate or 0.0)
        if self.VALID_BID_RATE_MIN <= bid_rate <= self.VALID_BID_RATE_MAX:
            return round(float(bid_rate), 6), "historical_data"

        if tender_result is not None:
            tender_rate = self._normalize_bid_rate_value(tender_result.winning_rate)
            if tender_rate is not None:
                return tender_rate, "tender_result_winning_rate"
            winning_amount = float(tender_result.winning_amount or 0.0)
            base_amount = self._reliable_base_amount(record)
            if winning_amount > 0 and base_amount > 0:
                derived_rate = winning_amount / base_amount
                if self.VALID_BID_RATE_MIN <= derived_rate <= self.VALID_BID_RATE_MAX:
                    return round(float(derived_rate), 6), "tender_result_winning_amount"

        if allow_predicted_price_fallback:
            predicted_price = float(record.predicted_price or 0.0)
            base_amount = self._reliable_base_amount(record)
            if predicted_price > 0 and base_amount > 0:
                bid_rate = predicted_price / base_amount
                if self.VALID_BID_RATE_MIN <= bid_rate <= self.VALID_BID_RATE_MAX:
                    return round(float(bid_rate), 6), "predicted_price"
        return None, None

    def _resolve_record_business_group(
        self, record: HistoricalData
    ) -> tuple[str | None, str | None]:
        """Resolve business group for price training, preferring KONEPS 업종코드."""
        project = getattr(record, "project", None)
        if project is not None:
            code_group = resolve_business_group(
                getattr(project, "business_type_code", None)
            )
            if code_group:
                return code_group, "business_type_code"
        category_group = self._normalize_category(record.category) or None
        if category_group:
            return category_group, "category"
        return None, None

    def _normalize_bid_rate_value(self, value: Any) -> float | None:
        """Normalize ratio/percentage-style tender result rates into bid-rate ratios."""
        try:
            numeric = float(value or 0.0)
        except (TypeError, ValueError):
            return None
        if numeric <= 0:
            return None
        numeric = to_bid_rate_fraction(numeric)
        if not (self.VALID_BID_RATE_MIN <= numeric <= self.VALID_BID_RATE_MAX):
            return None
        return round(float(numeric), 6)

    def _apply_as_of_filter(self, query, *, as_of: datetime):
        """Keep training/history slices causal by excluding rows opened after as_of."""
        return query.filter(
            or_(
                and_(
                    HistoricalData.opened_at.isnot(None),
                    HistoricalData.opened_at <= as_of,
                ),
                and_(
                    HistoricalData.opened_at.is_(None),
                    HistoricalData.created_at <= as_of,
                ),
            )
        )

    def _has_explicit_bid_rate_or_result(self):
        """Return rows with a direct bid-rate label or a linked usable tender result."""
        usable_result_exists = exists().where(
            and_(
                TenderResult.project_id == HistoricalData.project_id,
                TenderResult.project_id.isnot(None),
                settled_any_signal(),
            )
        )
        return or_(HistoricalData.bid_rate > 0, usable_result_exists)

    def _load_latest_tender_results(
        self,
        db: Session,
        *,
        project_ids: set[int],
    ) -> dict[int, TenderResult]:
        """Load the latest tender result for each linked project."""
        if not project_ids:
            return {}

        results = (
            db.query(TenderResult)
            .filter(
                TenderResult.project_id.in_(sorted(project_ids)),
                TenderResult.is_current.is_(True),
            )
            .order_by(TenderResult.project_id.asc(), TenderResult.id.desc())
            .all()
        )
        latest_by_project: dict[int, TenderResult] = {}
        for result in results:
            project_id = int(result.project_id or 0)
            current = latest_by_project.get(project_id)
            if current is None or self._is_better_result(result, current):
                latest_by_project[project_id] = result
        return latest_by_project

    def _is_better_result(self, candidate: TenderResult, current: TenderResult) -> bool:
        """Prefer usable award rows, then the newest announced result."""
        candidate_usable = self._has_usable_tender_result(candidate)
        current_usable = self._has_usable_tender_result(current)
        if candidate_usable != current_usable:
            return candidate_usable
        return self._is_newer_result(candidate, current)

    def _has_usable_tender_result(self, result: TenderResult) -> bool:
        """Whether a tender result can provide an actual award label."""
        return (
            float(result.winning_amount or 0.0) > 0
            or self._normalize_bid_rate_value(result.winning_rate) is not None
        )

    def _is_newer_result(self, candidate: TenderResult, current: TenderResult) -> bool:
        """Prefer the latest announced result, with created id as a fallback."""
        candidate_announced_at = candidate.announced_at or candidate.created_at
        current_announced_at = current.announced_at or current.created_at
        if (
            candidate_announced_at
            and current_announced_at
            and candidate_announced_at != current_announced_at
        ):
            return candidate_announced_at > current_announced_at
        return int(candidate.id or 0) > int(current.id or 0)
