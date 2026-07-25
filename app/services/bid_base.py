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

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.time import to_kst
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, Project
from app.services.award_verification import _rate_to_fraction

logger = logging.getLogger(__name__)


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

    basis-aware: once the latest positive-base row is chosen, ``get_reliable_base``
    inspects that row's ``base_amount_basis`` (#199). A ``clean`` row (or an
    unclassified ``NULL`` basis — the common case for open notices) returns its
    ``base_amount`` unchanged, so pricing is byte-identical. Only a row whose basis
    is explicitly non-clean (derived-yega/derived-vat/suspect) AND carries a
    reserve-recovered ``base_amount_estimated`` substitutes that estimate, defending
    the 예측 base against 예정가-basis pollution. This closes the #199 consumer gap;
    its live impact is ~0 today because open notices carry no post-settlement
    pollution (measured — see scripts/measure_reliable_base_impact.py).
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
            reliable = get_reliable_base(
                base_amount=record.base_amount,
                basis=record.base_amount_basis,
                base_amount_estimated=record.base_amount_estimated,
            )
            if reliable.source is ReliableBaseSource.RESERVE_ESTIMATE:
                logger.debug(
                    "resolve_notice_bid_base: project=%s non-clean basis=%s → "
                    "reserve estimate %s (raw base_amount %s rejected)",
                    project_id,
                    record.base_amount_basis,
                    reliable.value,
                    record.base_amount,
                )
            if reliable.value is not None and reliable.value > 0:
                return float(reliable.value)
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


def build_prediction_text(project: Project) -> str:
    """Assemble the single predictor-input text blob from a notice's fields.

    WHY THIS EXISTS — SINGLE SOURCE FOR PREDICT INPUT
    -------------------------------------------------
    ``Project`` stores ``title`` / ``description`` / ``requirements`` in SEPARATE
    columns, and the predictor reads regulatory mechanism keywords out of this text
    (``resolve_procurement_rate_band`` → price band, ``_detect_price_regime_signals``
    → regime label). The regulatory cues that drive those signals — 2단계(규격·가격
    동시), 가격입찰/적격심사, 협상/제안, 수의계약/(수의) — very often live in the
    **title**, not the body.

    The live/dominant predict path (opportunity_analysis → monitor/telegram) used to
    assemble ``f"{description} {requirements}"`` and DROP the title, while the
    validated backtest (paper_bidding_backtest) and smoke paths already fed
    title+description+requirements. That asymmetry meant production ran a DIFFERENT
    input than the pipeline validated by the holdout backtest — the live path missed
    the title's regulatory signals and under-detected price-competitive / negotiated
    regimes. This helper makes all three predict paths assemble the SAME text so the
    live path matches the validated input (CLAUDE.md §4.6 single source).

    ``filter(None, ...)`` drops empty/None fields (a title-less or body-less notice
    is joined from whatever fields are present), matching the backtest/smoke join
    semantics exactly so their outputs stay byte-identical after this refactor.

    This changes ONLY the predictor's INPUT TEXT. It does not touch the price
    guardrail, legal floor, or band constants (RED LINE unchanged).
    """
    return " ".join(filter(None, [project.title, project.description, project.requirements]))


@dataclass(frozen=True)
class NoticePredictionInputs:
    """The notice-derived predictor inputs the LIVE path feeds, bundled as one unit.

    Every predict path — live (opportunity_analysis), API (prediction_workflow),
    backtest, smoke, holdout — must feed the SAME preprocessing, or an accuracy
    measurement diverges from the pipeline that actually places bids. Bundling the
    four notice-derived inputs into one frozen record makes PARTIAL adoption
    structurally hard: a caller cannot silently drop the published floor while
    keeping the base/text, because they all arrive together from one call.

    - ``bid_base``: 기초금액/사업금액 the 적격심사 rate applies to (``resolve_notice_bid_base``).
    - ``text``: title+description+requirements predictor input (``build_prediction_text``).
    - ``legal_floor_bid_rate``: the notice's OWN published 낙찰하한율 (award_floor_rate,
      #201), folded into the guardrail floor with ``max()`` only downstream — it can
      only RAISE the floor (RED LINE). ``None`` when nothing is published/requested.
    - ``estimation_amount`` / ``reference_date``: construction legal 낙찰하한 tier inputs
      (구간=추정가격, 기준일=공고 시점, leakage-safe — the notice's own date).
    """

    bid_base: float
    text: str
    legal_floor_bid_rate: float | None
    estimation_amount: float | None
    reference_date: date | None


def prepare_prediction_inputs(
    db: Session,
    project: Project,
    *,
    request_legal_floor_bid_rate: float | None = None,
) -> NoticePredictionInputs:
    """Assemble the notice-derived predictor inputs in ONE place (single source).

    This is a pure composition of the four notice-scoped helpers already used by the
    live path — no new pricing logic. Every validation path routes through it so the
    published floor (previously dropped by backtest/smoke/holdout), the 기초금액 base
    (smoke passed 추정가격 directly), and the title-carrying text all match live.

    ``request_legal_floor_bid_rate`` lets an API caller override the published 하한
    (respected by ``resolve_notice_legal_floor_bid_rate``); validation paths pass
    ``None`` so the notice's own published 하한 is used.
    """
    estimation_amount, reference_date = resolve_notice_legal_floor_inputs(project)
    return NoticePredictionInputs(
        bid_base=resolve_notice_bid_base(db, project),
        text=build_prediction_text(project),
        legal_floor_bid_rate=resolve_notice_legal_floor_bid_rate(
            project, request_legal_floor_bid_rate=request_legal_floor_bid_rate
        ),
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
