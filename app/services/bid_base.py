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

from datetime import date

from sqlalchemy.orm import Session

from app.core.time import to_kst
from app.models.models import HistoricalData, Project
from app.services.award_verification import _rate_to_fraction


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


def resolve_notice_legal_floor_inputs(
    project: Project,
) -> tuple[float | None, date | None]:
    """Return (추정가격, 공고 기준일) for the construction legal 낙찰하한 tier.

    The tier bracket is keyed on the notice **추정가격** (``Project.budget_estimate``,
    ex-VAT), NOT the 기초금액 ``resolve_notice_bid_base`` returns for pricing (#162) —
    the legal 적격심사 표 구간(10억/50억/100억) is defined on 추정가격. VAT 비일관 이력
    때문에 경계 부근 구간 오분류 가능성이 있다(legal_floor_spec caveat 참조).

    The reference date is the notice's KST calendar day (``created_at`` ≈ 공고 수집일,
    the closest 공고일 proxy we store). Using the notice's OWN date — not "today" —
    makes the tier leakage-safe: a historical notice resolves the rate that was in
    effect at its announcement, so a backtest/analysis over past 공고 never has the
    2026-01-30 신율 applied retroactively.

    Either element is ``None`` when unavailable → the caller passes it through and the
    tier is simply not applied (the existing flat floor is preserved).
    """
    try:
        estimation = float(getattr(project, "budget_estimate", 0.0) or 0.0)
    except (TypeError, ValueError):
        estimation = 0.0
    estimation_amount = estimation if estimation > 0 else None

    created_at = getattr(project, "created_at", None)
    reference_date = to_kst(created_at).date() if created_at is not None else None
    return estimation_amount, reference_date


def resolve_notice_legal_floor_bid_rate(
    project: Project,
    *,
    request_legal_floor_bid_rate: float | None = None,
) -> float | None:
    """Return the effective legal 낙찰하한율 to feed the prediction guardrail floor.

    Precedence (declarative): an explicit client-supplied ``legal_floor_bid_rate``
    on the request wins (operator override is respected); otherwise we fall back to
    the notice's OWN published 낙찰하한율 ``Project.award_floor_rate`` (#201). The
    published value may be stored as a fraction (0.88) or a percent (88), so it is
    normalized via ``_rate_to_fraction`` (reused, not re-derived).

    RED LINE: guardrail_core folds this value into the floor with ``max()`` only
    (``_max_optional_rate(configured_floor, legal_floor)``), so a published 하한 can
    only RAISE the recommendation floor — it can never lower the category/legal floor.
    A published rate *below* the configured floor is therefore ignored by that max().
    When both the request value and the published rate are absent this returns
    ``None`` and the existing configured floor is preserved unchanged (the case for
    open notices without a published 하한). It is leakage-safe: ``award_floor_rate``
    is published on the notice itself, not future information.
    """
    if request_legal_floor_bid_rate is not None:
        return request_legal_floor_bid_rate
    return _rate_to_fraction(getattr(project, "award_floor_rate", None))
