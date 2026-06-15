"""투찰서 초안(bid-form draft) export service (PR8 / item 4-B).

Maps a persisted ``BidDecisionRecord`` onto the 나라장터(KONEPS) 투찰서 입력 항목 so
the operator can read the draft and **type the values into KONEPS by hand**.

⚠ 자동 제출은 명시적으로 범위 밖입니다. This service NEVER calls the KONEPS API and
performs NO automated submission. It only re-shapes the already-aggregated PR7
summary into a field-label/value mapping plus optional CSV / plain-text renders.

No new domain logic: aggregation is delegated to ``BidSummaryService`` (PR7) to
avoid duplication. This module only adds the KONEPS field mapping + the honest
적격여부(추정) label + CSV/text serialization.
"""

import csv
import io
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.schemas.bid_form_draft import (
    BID_FORM_DRAFT_NOTICE,
    ELIGIBILITY_NOTE,
)
from app.services.bid_summary import BidSummaryService

logger = logging.getLogger(__name__)

# 적격여부(추정) 라벨 상수 — 정직 표기. P(낙찰)/실제 적격 아님.
_ELIGIBILITY_LIKELY = "적격 추정"
_ELIGIBILITY_NEAR_FLOOR = "하한 근접"
_ELIGIBILITY_BELOW_FLOOR = "하한 미만(주의)"
_ELIGIBILITY_UNKNOWN = "판단 불가"

# 추천가가 하한가의 이 비율 이내면 "하한 근접"으로 표기 (참고 하한 기준).
_NEAR_FLOOR_MARGIN = 0.02


class BidFormDraftService:
    """Build a KONEPS-field-mapped 투찰서 초안 from a persisted decision record.

    Delegates aggregation to ``BidSummaryService`` and never performs any
    automated KONEPS submission.
    """

    def __init__(self, summary_service: Optional[BidSummaryService] = None) -> None:
        self._summary_service = summary_service or BidSummaryService()

    def build_draft(
        self,
        db: Session,
        decision_record_id: int,
        operator=None,
    ) -> dict:
        """Build the 투찰서 초안 payload for one persisted ``BidDecisionRecord``.

        Reuses the PR7 summary aggregation (same single-operator scope, same
        404-on-unknown ``ValueError`` contract) and maps it onto the 나라장터
        입력 항목. Raises ``ValueError`` for an unknown / cross-operator record id
        so the router can map it to 404.
        """
        summary = self._summary_service.build_summary(
            db, decision_record_id=decision_record_id, operator=operator
        )

        notice = summary["notice"]
        recommendation = summary["recommendation"]
        category_floor = summary["category_floor"]

        recommended_amount = float(recommendation.get("recommended_amount") or 0.0)
        recommended_bid_rate = recommendation.get("recommended_bid_rate")
        budget_estimate = float(notice.get("budget_estimate") or 0.0)
        floor_bid_rate = category_floor.get("floor_bid_rate")

        eligibility = self._estimate_eligibility(
            recommended_bid_rate=recommended_bid_rate,
            floor_bid_rate=floor_bid_rate,
        )

        fields = self._build_field_map(
            notice=notice,
            recommended_amount=recommended_amount,
            recommended_bid_rate=recommended_bid_rate,
            budget_estimate=budget_estimate,
            eligibility=eligibility,
            category_floor=category_floor,
        )

        return {
            "decision_record_id": int(summary["decision_record_id"]),
            "operator_id": int(summary["operator_id"]),
            "generated_at": summary["generated_at"],
            "notice_number": notice.get("notice_number"),
            "title": str(notice.get("title") or ""),
            "demand_agency": notice.get("demand_agency"),
            "budget_estimate": budget_estimate,
            "recommended_amount": recommended_amount,
            "recommended_bid_rate": recommended_bid_rate,
            "category": notice.get("category"),
            "business_type_label": notice.get("business_type_label"),
            "deadline": notice.get("deadline"),
            "eligibility_estimate": eligibility,
            "eligibility_note": ELIGIBILITY_NOTE,
            "fields": fields,
            "direct_submission_notice": BID_FORM_DRAFT_NOTICE,
        }

    # --- eligibility (적격여부 추정) ------------------------------------------

    def _estimate_eligibility(
        self,
        *,
        recommended_bid_rate: Optional[float],
        floor_bid_rate: Optional[float],
    ) -> str:
        """Derive an honest 적격여부(추정) label from rec-rate vs. reference floor.

        This is a *reference* estimate only — the real 낙찰하한가 is decided at
        opening against the 예정가, which live notices do not expose beforehand.
        """
        if recommended_bid_rate is None or floor_bid_rate is None:
            return _ELIGIBILITY_UNKNOWN
        if recommended_bid_rate < floor_bid_rate:
            return _ELIGIBILITY_BELOW_FLOOR
        # at-or-above floor: flag the near-floor band so the operator double-checks.
        if recommended_bid_rate <= floor_bid_rate * (1.0 + _NEAR_FLOOR_MARGIN):
            return _ELIGIBILITY_NEAR_FLOOR
        return _ELIGIBILITY_LIKELY

    # --- KONEPS field mapping --------------------------------------------------

    def _build_field_map(
        self,
        *,
        notice: dict,
        recommended_amount: float,
        recommended_bid_rate: Optional[float],
        budget_estimate: float,
        eligibility: str,
        category_floor: dict,
    ) -> list[dict]:
        """Map the aggregation onto 나라장터 투찰서 입력 항목(label + value pairs)."""
        floor_bid_rate = category_floor.get("floor_bid_rate")

        fields: list[dict] = [
            {
                "key": "notice_number",
                "field_label": "공고번호",
                "value": str(notice.get("notice_number") or ""),
                "raw_value": None,
                "note": None,
            },
            {
                "key": "title",
                "field_label": "공고명",
                "value": str(notice.get("title") or ""),
                "raw_value": None,
                "note": None,
            },
            {
                "key": "demand_agency",
                "field_label": "수요기관",
                "value": str(notice.get("demand_agency") or ""),
                "raw_value": None,
                "note": None,
            },
            {
                "key": "category",
                "field_label": "분류(카테고리)",
                "value": str(notice.get("category") or ""),
                "raw_value": None,
                "note": None,
            },
            {
                "key": "business_type_label",
                "field_label": "업종",
                "value": str(notice.get("business_type_label") or ""),
                "raw_value": None,
                "note": None,
            },
            {
                "key": "budget_estimate",
                "field_label": "기초금액(추정가격)",
                "value": self._format_currency(budget_estimate),
                "raw_value": budget_estimate if budget_estimate else None,
                "note": "예정가/실하한가는 개찰 전 미공개입니다.",
            },
            {
                "key": "recommended_amount",
                "field_label": "투찰금액",
                "value": self._format_currency(recommended_amount),
                "raw_value": recommended_amount if recommended_amount else None,
                "note": "추천 투찰가이며 보장된 낙찰가가 아닙니다.",
            },
            {
                "key": "recommended_bid_rate",
                "field_label": "투찰률(%)",
                "value": self._format_percent(recommended_bid_rate),
                "raw_value": recommended_bid_rate,
                "note": "투찰가 / 추정가격 비율(참고).",
            },
            {
                "key": "eligibility_estimate",
                "field_label": "적격여부(추정)",
                "value": eligibility,
                "raw_value": None,
                "note": ELIGIBILITY_NOTE,
            },
            {
                "key": "category_floor_bid_rate",
                "field_label": "카테고리 낙찰하한율(참고)",
                "value": self._format_percent(floor_bid_rate),
                "raw_value": floor_bid_rate,
                "note": ("참고 하한율입니다. 실제 낙찰하한가는 개찰 시 예정가 기준으로 " "결정됩니다."),
            },
            {
                "key": "deadline",
                "field_label": "투찰 마감일시",
                "value": self._format_datetime(notice.get("deadline")),
                "raw_value": None,
                "note": None,
            },
        ]
        return fields

    # --- serialization (CSV / plain text) -------------------------------------

    def render_csv(self, draft: dict) -> str:
        """Render the draft as a CSV body (header + one row per field).

        Columns: ``field_label,value,note``. The direct-submission notice is
        emitted as a leading comment-style row so a printed/copied CSV still
        carries the honest caveat.

        모든 셀은 ``_csv_safe``로 중화한다 — 외부 나라장터 데이터(공고명/수요기관 등)가
        ``=``/``+``/``-``/``@``로 시작하면 Excel·Sheets에서 수식으로 실행되는 CSV
        injection을 막기 위해 선행 작은따옴표를 붙인다(OWASP 권고).
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                self._csv_safe("고지"),
                self._csv_safe(draft.get("direct_submission_notice", "")),
                self._csv_safe(""),
            ]
        )
        writer.writerow(
            [
                self._csv_safe("field_label"),
                self._csv_safe("value"),
                self._csv_safe("note"),
            ]
        )
        for field in draft.get("fields", []):
            writer.writerow(
                [
                    self._csv_safe(field.get("field_label", "")),
                    self._csv_safe(field.get("value", "")),
                    self._csv_safe(field.get("note", "") or ""),
                ]
            )
        return buffer.getvalue()

    @staticmethod
    def _csv_safe(value) -> str:
        """Neutralize CSV formula injection on a single cell.

        If the stringified cell is non-empty and begins with a formula-trigger
        character (``= + - @`` or the control chars ``\\t``/``\\r``), prepend a
        single quote so spreadsheet apps treat it as text, not a formula
        (OWASP-recommended neutralization). Numeric strings like ``"88,200,000"``
        are unaffected.
        """
        text = "" if value is None else str(value)
        if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + text
        return text

    def render_text(self, draft: dict) -> str:
        """Render the draft as a copy/print-friendly plain-text block."""
        lines: list[str] = []
        lines.append("[나라장터 투찰서 초안 — 참고용, 자동 제출 아님]")
        lines.append("")
        for field in draft.get("fields", []):
            label = field.get("field_label", "")
            value = field.get("value", "")
            lines.append(f"{label}: {value}")
            note = field.get("note")
            if note:
                lines.append(f"  - {note}")
        lines.append("")
        lines.append(draft.get("direct_submission_notice", ""))
        return "\n".join(lines)

    # --- formatting helpers ----------------------------------------------------

    def _format_currency(self, amount: Optional[float]) -> str:
        if not amount:
            return ""
        return f"{int(round(amount)):,}원"

    def _format_percent(self, rate: Optional[float]) -> str:
        if rate is None:
            return ""
        return f"{rate * 100:.3f}%"

    def _format_datetime(self, value) -> str:
        if value is None:
            return ""
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except (AttributeError, ValueError):
            return str(value)
