"""의사결정 증적 샘플(decision evidence samples) export service.

Roadmap 0단계 L87 "예측/추천 API 응답 샘플 캡처(증적 보관)".

시스템이 이미 영속화한 추천(``BidDecisionRecord``)과 예측(``PricePrediction``)을
최근 순으로 통합 조회해 **감사 증적**으로 노출한다. 정확도 리포트가 정산(settled)
항목만 보여주는 것과 달리, 이 서비스는 정산 여부와 무관하게 시스템이 산출·기록한
추천/예측 자체를 보여준다.

⚠ 정직 표기(§1.5):
- ``probability_score`` 는 가격 적합도(추정)이지 P(낙찰) 이 아니다.
- 이 목록은 감사 증적이며 정산·실낙찰 결과가 아니다.
- 단일 운영자(canonical) 기준. 시간 누수(leakage) 우려 없음 — 과거 서빙 기록을
  읽기만 하며 정산 join 이 없다.

CSV export 는 외부 나라장터 데이터(공고명/수요기관)가 포함되므로 OWASP 권고에 따라
CSV formula injection 을 중화한다(``_csv_safe``).
"""

import csv
import io
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, PricePrediction, Project
from app.schemas.decision_samples import DECISION_SAMPLES_NOTES

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
DEFAULT_LIMIT = 50

# CSV 헤더(평탄화된 핵심 필드 + 예측 컬럼).
_CSV_HEADER = [
    "decision_record_id",
    "project_id",
    "notice_number",
    "title",
    "demand_agency",
    "category",
    "budget_estimate",
    "deadline",
    "recommended_amount",
    "recommended_bid_rate",
    "action",
    "decision_status",
    "probability_score",
    "priority_score",
    "matched_score",
    "competitiveness_score",
    "decided_at",
    "created_at",
    "updated_at",
    "predicted_price",
    "price_range_min",
    "price_range_max",
    "confidence_score",
    "predictor_name",
    "predictor_family",
    "pricing_mode",
]


class DecisionSampleService:
    """Build a consolidated, exportable view of recent served recommendations+predictions."""

    def build_samples(
        self,
        db: Session,
        *,
        days: int = DEFAULT_DAYS,
        limit: int = DEFAULT_LIMIT,
        operator=None,
    ) -> dict:
        """Aggregate recent ``BidDecisionRecord`` evidence (most-recent-first).

        Each decision is joined with its project meta and the latest
        ``PricePrediction`` for that project (nullable). Predictions are
        batch-loaded for the page's project ids to avoid N+1.

        Returns a JSON-serializable dict with honest ``notes`` and a
        ``truncated`` flag (set when ``sample_count >= limit``).
        """
        if operator is None:
            operator = ensure_operator_account(db)

        days = max(int(days), 0)
        limit = max(int(limit), 0)

        cutoff = utc_now() - _timedelta_days(days)

        records = (
            db.query(BidDecisionRecord)
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.created_at >= cutoff,
            )
            .order_by(
                BidDecisionRecord.created_at.desc(),
                BidDecisionRecord.id.desc(),
            )
            .limit(limit)
            .all()
        )

        project_ids = [r.project_id for r in records if r.project_id is not None]
        projects = self._load_projects(db, project_ids)
        latest_predictions = self._load_latest_predictions(
            db, project_ids, operator_id=operator.id
        )

        samples = [
            self._build_sample(
                record,
                project=projects.get(record.project_id),
                prediction=latest_predictions.get(record.project_id),
            )
            for record in records
        ]

        sample_count = len(samples)
        return {
            "operator_id": int(operator.id),
            "period_days": days,
            "limit": limit,
            "sample_count": sample_count,
            "generated_at": utc_now(),
            "truncated": limit > 0 and sample_count >= limit,
            "notes": DECISION_SAMPLES_NOTES,
            "samples": samples,
        }

    # --- loaders (batch, avoid N+1) -------------------------------------------

    def _load_projects(self, db: Session, project_ids: list[int]) -> dict[int, Project]:
        if not project_ids:
            return {}
        rows = (
            db.query(Project)
            .filter(Project.id.in_(set(project_ids)))
            .all()
        )
        return {project.id: project for project in rows}

    def _load_latest_predictions(
        self, db: Session, project_ids: list[int], *, operator_id: int
    ) -> dict[int, PricePrediction]:
        """Load the latest ``PricePrediction`` per project id in one query.

        Scoped to ``operator_id`` (``PricePrediction.user_id``) so a foreign
        operator's prediction for the same project never surfaces in another
        operator's audit sample (single-operator invariant documented in
        CLAUDE.md).

        Orders by ``created_at`` (then id) ascending and lets later rows
        overwrite earlier ones in the dict, so each project id maps to its most
        recent stored prediction.
        """
        if not project_ids:
            return {}
        rows = (
            db.query(PricePrediction)
            .filter(
                PricePrediction.user_id == operator_id,
                PricePrediction.project_id.in_(set(project_ids)),
            )
            .order_by(
                PricePrediction.created_at.asc(),
                PricePrediction.id.asc(),
            )
            .all()
        )
        latest: dict[int, PricePrediction] = {}
        for prediction in rows:
            latest[prediction.project_id] = prediction
        return latest

    # --- shaping --------------------------------------------------------------

    def _build_sample(
        self,
        record: BidDecisionRecord,
        *,
        project: Optional[Project],
        prediction: Optional[PricePrediction],
    ) -> dict:
        budget_estimate = (
            float(project.budget_estimate)
            if project is not None and project.budget_estimate is not None
            else None
        )
        recommended_amount = float(record.recommended_amount or 0.0)
        recommended_bid_rate = (
            recommended_amount / budget_estimate
            if budget_estimate and budget_estimate > 0
            else None
        )

        return {
            "decision_record_id": int(record.id),
            "project_id": int(record.project_id) if record.project_id is not None else None,
            "notice_number": project.notice_number if project is not None else None,
            "title": str(project.title) if project is not None and project.title else "",
            "demand_agency": project.demand_agency if project is not None else None,
            "category": project.category if project is not None else None,
            "budget_estimate": budget_estimate,
            "deadline": project.deadline if project is not None else None,
            "recommended_amount": recommended_amount,
            "recommended_bid_rate": recommended_bid_rate,
            "action": record.action,
            "decision_status": record.decision_status,
            "probability_score": _as_float(record.probability_score),
            "priority_score": _as_float(record.priority_score),
            "matched_score": _as_float(record.matched_score),
            "competitiveness_score": _as_float(record.competitiveness_score),
            "reasoning": record.reasoning,
            "decided_at": record.first_decided_at,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "prediction": self._build_prediction(prediction),
        }

    def _build_prediction(self, prediction: Optional[PricePrediction]) -> Optional[dict]:
        if prediction is None:
            return None
        return {
            "predicted_price": _as_float(prediction.predicted_price),
            "price_range_min": _as_float(prediction.price_range_min),
            "price_range_max": _as_float(prediction.price_range_max),
            "confidence_score": _as_float(prediction.confidence_score),
            "predictor_name": prediction.predictor_name,
            "predictor_family": prediction.predictor_family,
            "pricing_mode": prediction.pricing_mode,
            "created_at": prediction.created_at,
        }

    # --- CSV serialization ----------------------------------------------------

    def render_csv(self, payload: dict) -> str:
        """Render the samples as a CSV body (header + one row per sample).

        모든 셀은 ``_csv_safe`` 로 중화한다 — 외부 나라장터 데이터(공고명/수요기관)가
        ``=``/``+``/``-``/``@`` 로 시작하면 Excel·Sheets 에서 수식으로 실행되는 CSV
        injection 을 막기 위해 선행 작은따옴표를 붙인다(OWASP 권고).
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([self._csv_safe(col) for col in _CSV_HEADER])

        for sample in payload.get("samples", []):
            prediction = sample.get("prediction") or {}
            row = [
                sample.get("decision_record_id"),
                sample.get("project_id"),
                sample.get("notice_number"),
                sample.get("title"),
                sample.get("demand_agency"),
                sample.get("category"),
                sample.get("budget_estimate"),
                self._format_datetime(sample.get("deadline")),
                sample.get("recommended_amount"),
                sample.get("recommended_bid_rate"),
                sample.get("action"),
                sample.get("decision_status"),
                sample.get("probability_score"),
                sample.get("priority_score"),
                sample.get("matched_score"),
                sample.get("competitiveness_score"),
                self._format_datetime(sample.get("decided_at")),
                self._format_datetime(sample.get("created_at")),
                self._format_datetime(sample.get("updated_at")),
                prediction.get("predicted_price"),
                prediction.get("price_range_min"),
                prediction.get("price_range_max"),
                prediction.get("confidence_score"),
                prediction.get("predictor_name"),
                prediction.get("predictor_family"),
                prediction.get("pricing_mode"),
            ]
            writer.writerow([self._csv_safe(cell) for cell in row])

        return buffer.getvalue()

    @staticmethod
    def _csv_safe(value) -> str:
        """Neutralize CSV formula injection on a single cell.

        If the stringified cell is non-empty and begins with a formula-trigger
        character (``= + - @`` or the control chars ``\\t``/``\\r``), prepend a
        single quote so spreadsheet apps treat it as text, not a formula
        (OWASP-recommended neutralization). Numeric values are unaffected.
        """
        text = "" if value is None else str(value)
        if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + text
        return text

    def _format_datetime(self, value) -> str:
        if value is None:
            return ""
        try:
            return value.isoformat()
        except (AttributeError, ValueError):
            return str(value)


def _as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timedelta_days(days: int):
    return timedelta(days=days)
