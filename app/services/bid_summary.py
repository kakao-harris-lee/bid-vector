"""Bid-decision summary aggregation service (투찰 의사결정 요약).

PR7 / item 4-A. No new domain logic — this purely aggregates already-persisted
data for one ``BidDecisionRecord`` so the operator can consult a single payload
while writing the 나라장터 투찰서 **by hand**. Automated submission is out of scope.

Sources:
- ``BidDecisionRecord`` — recommended_amount / probability_score / reasoning /
  action / decision_status / scores (allocation.py persists these).
- ``Project`` — title / notice_number / budget_estimate / category / agencies /
  deadline.
- Category 낙찰하한율(참고): reuses the predictor guardrail floor resolver
  ``_resolve_floor_bid_rate`` (read-only import, ml-builder owned). Live notices
  do not expose 예정가/실하한가 before opening, so this is a *reference* floor.
- 분야 통계(체크리스트): latest completed synthetic-experiment run's per-category
  breakdown (``est_price_close_rate`` / ``eligible_favorable_rate`` /
  ``settled_count``). Optional — absent → ``field_stat`` is None (graceful).
- Prediction meta: the latest ``PricePrediction`` linked to the same project /
  operator, if any.
"""

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import (
    BidDecisionRecord,
    PricePrediction,
    Project,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
    User,
)
from app.services.stored_json_payload import load_stored_json_value
from app.services.synthetic_experiment import RESULT_BREAKDOWN_COLUMN

# 이 서비스가 직접 읽는 컬럼의 degrade 라벨(교차 패키지 컬럼은 소유 모듈 상수를 쓴다).
BID_DECISION_SCORE_BREAKDOWN_COLUMN = "bid_decision_record.score_breakdown"

logger = logging.getLogger(__name__)


class BidSummaryService:
    """Aggregate a single bid-decision record into a decision-support summary."""

    def build_summary(
        self,
        db: Session,
        decision_record_id: int,
        operator: User | None = None,
    ) -> dict:
        """Build the summary payload for one persisted ``BidDecisionRecord``.

        Resolves the canonical singleton operator when ``operator`` is not
        supplied (default single-operator read path). Raises ``ValueError`` for
        an unknown / cross-operator record id so the router can map it to 404.
        """
        target = operator or ensure_operator_account(db)
        record = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.id == decision_record_id,
                BidDecisionRecord.operator_id == target.id,
            )
            .first()
        )
        if record is None:
            raise ValueError("Bid decision record not found")

        project = record.project
        if project is None:
            raise ValueError("Project not found for bid decision record")

        strengths, risk_flags = self._extract_decision_reasons(record.score_breakdown)
        prediction = self._load_linked_prediction(
            db, project_id=record.project_id, operator_id=target.id
        )

        return {
            "decision_record_id": int(record.id),
            "operator_id": int(target.id),
            "generated_at": utc_now(),
            "notice": self._build_notice(project),
            "recommendation": self._build_recommendation(
                record, strengths=strengths, risk_flags=risk_flags
            ),
            "prediction": self._build_prediction(prediction),
            "category_floor": self._build_category_floor(project),
            "field_stat": self._build_field_stat(db, category=project.category),
        }

    # --- notice ---------------------------------------------------------------

    def _build_notice(self, project: Project) -> dict:
        return {
            "project_id": int(project.id),
            "title": str(project.title or ""),
            "notice_number": project.notice_number,
            "category": project.category,
            "business_type_label": project.business_type_label,
            "budget_estimate": float(project.budget_estimate or 0.0),
            "demand_agency": project.demand_agency,
            "issuing_agency": project.issuing_agency,
            "deadline": project.deadline,
            "source_url": project.source_url,
            "status": project.status,
        }

    # --- recommendation -------------------------------------------------------

    def _build_recommendation(
        self,
        record: BidDecisionRecord,
        *,
        strengths: list[str],
        risk_flags: list[str],
    ) -> dict:
        recommended_amount = float(record.recommended_amount or 0.0)
        budget = float(record.project.budget_estimate or 0.0) if record.project else 0.0
        recommended_bid_rate = (
            round(recommended_amount / budget, 6)
            if budget > 0 and recommended_amount > 0
            else None
        )
        return {
            "recommended_amount": recommended_amount,
            "recommended_bid_rate": recommended_bid_rate,
            "probability_score": float(record.probability_score or 0.0),
            "action": str(record.action or "skip"),
            "decision_status": str(record.decision_status or "planned"),
            "priority_score": float(record.priority_score or 0.0),
            "matched_score": float(record.matched_score or 0.0),
            "competitiveness_score": float(record.competitiveness_score or 0.0),
            "reasoning": str(record.reasoning or ""),
            "strengths": strengths,
            "risk_flags": risk_flags,
        }

    # --- prediction -----------------------------------------------------------

    def _load_linked_prediction(
        self, db: Session, *, project_id: int, operator_id: int
    ) -> Optional[PricePrediction]:
        """Return the latest price prediction linked to this project/operator."""
        if project_id is None:
            return None
        return (
            db.query(PricePrediction)
            .filter(
                PricePrediction.project_id == project_id,
                PricePrediction.user_id == operator_id,
            )
            .order_by(PricePrediction.created_at.desc(), PricePrediction.id.desc())
            .first()
        )

    def _build_prediction(
        self, prediction: Optional[PricePrediction]
    ) -> Optional[dict]:
        if prediction is None:
            return None
        return {
            "predicted_price": float(prediction.predicted_price)
            if prediction.predicted_price is not None
            else None,
            "predicted_bid_rate": float(prediction.predicted_bid_rate)
            if prediction.predicted_bid_rate is not None
            else None,
            "price_range_min": float(prediction.price_range_min)
            if prediction.price_range_min is not None
            else None,
            "price_range_max": float(prediction.price_range_max)
            if prediction.price_range_max is not None
            else None,
            "confidence_score": float(prediction.confidence_score)
            if prediction.confidence_score is not None
            else None,
            "pricing_mode": prediction.pricing_mode,
            "predictor_name": prediction.predictor_name,
            "guardrail_applied": bool(prediction.guardrail_applied),
            "floor_bid_rate": float(prediction.floor_bid_rate)
            if prediction.floor_bid_rate is not None
            else None,
            "created_at": prediction.created_at,
        }

    # --- category floor (낙찰하한율 참고) -------------------------------------

    def _build_category_floor(self, project: Project) -> dict:
        """Resolve the reference category 낙찰하한율 via the predictor guardrail.

        Imports are deferred and read-only — ``app/ai`` is ml-builder territory;
        this service only *consults* the guardrail floor resolver so the summary
        stays consistent with what predictions actually enforce.
        """
        category = project.category
        business_group: Optional[str] = None
        floor_bid_rate: Optional[float] = None
        try:
            from app.ai.business_group import resolve_business_group
            from app.ai.price_prediction import _resolve_floor_bid_rate

            business_group = resolve_business_group(project.business_type_code)
            floor_bid_rate = _resolve_floor_bid_rate(
                category, business_group=business_group
            )
        except Exception as exc:  # pragma: no cover - defensive: keep summary graceful
            # Log with traceback so an app/ai signature/contract regression is
            # visible instead of silently degrading to floor_bid_rate=None.
            logger.warning(
                "Category floor resolve 실패 (project %s): %s",
                getattr(project, "id", None),
                exc,
                exc_info=True,
            )

        budget = float(project.budget_estimate or 0.0)
        floor_price = (
            round(budget * floor_bid_rate, 2)
            if floor_bid_rate is not None and budget > 0
            else None
        )
        return {
            "category": category,
            "business_group": business_group,
            "floor_bid_rate": round(float(floor_bid_rate), 6)
            if floor_bid_rate is not None
            else None,
            "floor_price": floor_price,
        }

    # --- field stat (분야 통계 체크리스트) ------------------------------------

    def _build_field_stat(
        self, db: Session, *, category: Optional[str]
    ) -> Optional[dict]:
        """Return the latest backtest field stat row for ``category`` if present.

        Reads the most recent completed synthetic-experiment run's per-operator
        breakdowns and returns the first matching ``by_category`` row. Returns
        None (graceful) when no backtest data is available or the category has no
        matching row.
        """
        if not category:
            return None

        run = (
            db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.status == "completed")
            .order_by(
                SyntheticExperimentRun.finished_at.desc(),
                SyntheticExperimentRun.id.desc(),
            )
            .first()
        )
        if run is None:
            return None

        normalized_category = str(category).strip().lower()
        results = (
            db.query(SyntheticExperimentResult)
            .filter(SyntheticExperimentResult.run_id == run.id)
            .order_by(SyntheticExperimentResult.operator_slug.asc())
            .all()
        )
        for result in results:
            breakdown = self._loads_json(
                result.breakdown_json, context=RESULT_BREAKDOWN_COLUMN
            )
            if not isinstance(breakdown, dict):
                continue
            for row in breakdown.get("by_category", []) or []:
                if not isinstance(row, dict):
                    continue
                row_category = str(row.get("category") or "").strip().lower()
                if row_category != normalized_category:
                    continue
                settled_count = int(row.get("settled_count") or 0)
                if settled_count <= 0:
                    continue
                return {
                    "category": row.get("category"),
                    "settled_count": settled_count,
                    "est_price_close_rate": self._as_optional_float(
                        row.get("est_price_close_rate", row.get("win_rate"))
                    ),
                    "eligible_favorable_rate": self._as_optional_float(
                        row.get("eligible_favorable_rate")
                    ),
                    "source_run_id": int(run.id),
                    "source_operator_slug": result.operator_slug,
                }
        return None

    # --- helpers --------------------------------------------------------------

    def _extract_decision_reasons(
        self, score_breakdown: Any
    ) -> tuple[list[str], list[str]]:
        """Parse persisted strengths/risk_flags out of a score_breakdown blob.

        Mirrors ``app.schemas.schemas._extract_decision_reasons`` — the column
        stores decision signals as JSON text with optional ``strengths`` /
        ``risk_flags`` keys. Older records simply lack the keys.
        """
        parsed = self._loads_json(
            score_breakdown, context=BID_DECISION_SCORE_BREAKDOWN_COLUMN
        )
        if not isinstance(parsed, dict):
            return [], []

        def _as_str_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item) for item in value if isinstance(item, str) and item]

        return _as_str_list(parsed.get("strengths")), _as_str_list(
            parsed.get("risk_flags")
        )

    def _loads_json(self, value: Any, *, context: str = "") -> Any:
        """Restore a stored score/breakdown payload (object or array), else ``None``.

        Decoding + the degrade warning live in the shared restore path; already
        decoded values (ORM JSON columns) pass through as before. ``context`` is the
        **column label** of the caller so a degrade warning names the column that is
        actually unreadable (this service reads two different columns).
        """
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return load_stored_json_value(value, context=context)

    def _as_optional_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
