"""Resolve the base amount a KONEPS 적격심사 bid rate applies to.

WHY THIS EXISTS
---------------
KONEPS 적격심사 투찰가는 추정가격(ex-VAT)이 아니라 **기초금액/사업금액(배정예산,
과세 공고 기준 VAT 포함)** 을 기준으로 산정된다. 수집 단계에서

- ``Project.budget_estimate`` 에는 추정가격(ex-VAT, ``estimated_amount``)이,
- ``HistoricalData.base_amount`` 에는 기초금액/사업금액(과세 공고면 VAT 포함,
  KONEPS ``bssamt``/``asignBdgtAmt``)이

저장된다. 과거 낙찰률(``HistoricalData.bid_rate`` / ``TenderResult.winning_rate``)은
``winning_amount / base_amount`` 로 정규화되어 있으므로(app/services/koneps/scsbid.py),
predictor 에 넘기는 ``budget`` 도 base_amount 여야 이중과세 없이 올바른 VAT 포함
투찰 금액이 나온다. 추정가격을 넘기면 과세 공고에서 투찰가가 ~10% 낮게 산정되어
낙찰하한 미만으로 낙(disqualification)될 위험이 있다.

면세 공고는 ``base_amount == budget_estimate`` 이므로 이 해석은 무해(no-op)하다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import HistoricalData, Project


def resolve_notice_bid_base(db: Session, project: Project) -> float:
    """Return the notice's 기초금액/사업금액 (bid base), falling back to 추정가격.

    Bids are placed against 사업금액/기초금액 (배정예산; 과세 공고면 VAT 포함), not
    추정가격 (ex-VAT). We take the most recent ``HistoricalData`` row for the
    project that actually carries a *positive* ``base_amount`` — a later-collected
    settlement snapshot may leave ``base_amount`` unset (0/NULL) even though an
    earlier collection captured it, so a plain "latest row" lookup could regress
    to the 추정가격 fallback despite a usable base being on record. When no row
    carries a positive base we fall back to ``project.budget_estimate`` (면세
    공고에서는 두 값이 동일하므로 안전하며, 회귀도 없다).
    """
    project_id = getattr(project, "id", None)
    if project_id is not None:
        record = (
            db.query(HistoricalData)
            .filter(
                HistoricalData.project_id == project_id,
                HistoricalData.base_amount > 0,
            )
            .order_by(HistoricalData.id.desc())
            .first()
        )
        if record is not None:
            try:
                base_value = float(record.base_amount or 0.0)
            except (TypeError, ValueError):
                base_value = 0.0
            if base_value > 0:
                return base_value
    try:
        return float(getattr(project, "budget_estimate", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
