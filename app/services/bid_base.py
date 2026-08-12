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
from typing import Final

from sqlalchemy.orm import Session

from app.core.time import to_kst
from app.domain.money import BaseAmount
from app.domain.published_floor_rate import plausible_published_floor_rate
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, Project
from app.services.award_verification import _rate_to_fraction

logger = logging.getLogger(__name__)

# 기초금액을 실제로 확보하지 못해 추정가격으로 떨어졌음을 나타내는 provenance 라벨.
# 나머지 출처 값은 ``ReliableBaseSource`` 어휘를 그대로 쓴다(새 어휘를 만들지 않는다).
BID_BASE_SOURCE_BUDGET_FALLBACK: Final[str] = "budget-estimate-fallback"

# 저장된 공고 금액이 전혀 없어 **요청 본문의 금액**을 투찰 base 로 썼음을 나타내는 라벨.
# 위 폴백과 구분하는 이유: 저 값은 우리가 수집·검증한 ``Project.budget_estimate`` 지만
# 이 값은 클라이언트가 보낸 basis 미표기 float 다. 같은 "추정가격 폴백"으로 뭉뚱그리면
# 검증된 금액과 검증되지 않은 금액이 화면에서 구별되지 않는다.
BID_BASE_SOURCE_CLIENT_ESTIMATE: Final[str] = "client-budget-estimate"


@dataclass(frozen=True)
class NoticeBidBase:
    """공고의 투찰 기준금액 + 그 값이 어디서 왔는지 — 표시 경계용 provenance.

    운영자가 반복해서 밟은 함정은 화면의 "예산"(추정가격, 부가세 별도 표기)과 투찰율이
    실제로 곱해지는 기초금액/사업금액(과세 공고면 부가세 포함)이 **다른 금액**인데 한
    자리에 같은 이름으로 놓인 것이었다. 그래서 이 값 객체는 금액만이 아니라 함께 비교할
    추정가격과 출처를 같이 들고 다닌다 — 응답에서 두 금액이 분리돼 나가게 하기 위함이다.
    """

    amount: BaseAmount
    """투찰율이 곱해지는 기초금액/사업금액(기초금액-basis)."""

    source: str
    """``ReliableBaseSource`` 값 또는 :data:`BID_BASE_SOURCE_BUDGET_FALLBACK`."""

    budget_estimate: float
    """같은 공고의 추정가격(``Project.budget_estimate``) — 비교 대상."""

    @property
    def to_estimate_ratio(self) -> float | None:
        """기초금액 ÷ 추정가격. 추정가격이 0 이하면 ``None``.

        ``1.0`` 이면 두 금액이 같아(면세이거나 기초금액 미확보 폴백) 혼동 여지가 없고,
        ``1.1`` 부근이면 기초금액이 추정가격에 없는 부가세를 포함한다는 뜻이다. 세금
        구분 자체를 주장하지 않고 **관측된 두 금액의 비**만 낸다 — 저장된 추정가격의
        부가세 포함 여부가 이력상 일관되지 않아(수집 키가 공고마다 다름) 이 비율에서
        과세 여부를 단정하면 틀릴 수 있다.
        """
        if self.budget_estimate <= 0:
            return None
        return round(float(self.amount) / self.budget_estimate, 6)


def resolve_notice_bid_base(db: Session, project: Project) -> BaseAmount:
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

    Returns a :data:`~app.domain.money.BaseAmount` (기초금액-basis): the bid base the
    적격심사 rate multiplies. The 추정가격 fallback is coerced to this basis because it
    only fires on 면세 공고 where ``base_amount == budget_estimate`` (no-op) — the
    NewType makes callers unable to feed this where a 예정가/추정가격 value is expected
    (#162). Runtime-identical: ``BaseAmount`` erases to ``float`` (NewType).

    The resolution itself lives in :func:`describe_notice_bid_base`, which returns the
    same amount plus the provenance display boundaries need; this stays the pricing-path
    entry point so predictor callers keep receiving a bare ``BaseAmount``.
    """
    return describe_notice_bid_base(db, project).amount


def _project_budget_estimate(project: Project) -> float:
    """``Project.budget_estimate`` (추정가격) as a float, 0.0 when unusable."""
    try:
        return float(getattr(project, "budget_estimate", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def describe_notice_bid_base(db: Session, project: Project) -> NoticeBidBase:
    """Resolve the bid base **and** where it came from (basis display boundary).

    Identical resolution to :func:`resolve_notice_bid_base` — this is the shared
    implementation — but it also reports the provenance
    (:class:`~app.domain.reliable_base.ReliableBaseSource` value, or
    :data:`BID_BASE_SOURCE_BUDGET_FALLBACK` when no positive 기초금액 is on record) and
    the 추정가격 it was compared against. Operator-facing responses need those two extras
    to show the bid base and the 추정가격 as the distinct amounts they are, instead of
    collapsing both into one "예산" label.
    """
    budget_estimate = _project_budget_estimate(project)
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
            # Model attrs cross the typed boundary via getattr (→ Any): the
            # old-style ``Column`` declarations type as ``Column[float]`` on the
            # instance, so this is the same DB-read idiom already used for
            # ``project`` fields below. Runtime-identical (getattr == attr access).
            base_amount = getattr(record, "base_amount", None)
            basis = getattr(record, "base_amount_basis", None)
            base_amount_estimated = getattr(record, "base_amount_estimated", None)
            reliable = get_reliable_base(
                base_amount=base_amount,
                basis=basis,
                base_amount_estimated=base_amount_estimated,
            )
            if reliable.source is ReliableBaseSource.RESERVE_ESTIMATE:
                logger.debug(
                    "resolve_notice_bid_base: project=%s non-clean basis=%s → "
                    "reserve estimate %s (raw base_amount %s rejected)",
                    project_id,
                    basis,
                    reliable.value,
                    base_amount,
                )
            if reliable.value is not None and reliable.value > 0:
                return NoticeBidBase(
                    amount=BaseAmount(float(reliable.value)),
                    source=reliable.source.value,
                    budget_estimate=budget_estimate,
                )
    return NoticeBidBase(
        amount=BaseAmount(budget_estimate),
        source=BID_BASE_SOURCE_BUDGET_FALLBACK,
        budget_estimate=budget_estimate,
    )


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


def resolve_notice_published_floor_bid_rate(project: Project) -> float | None:
    """공고가 게시한 낙찰하한율의 정규화된 **원값** — 개연 게이트를 걸지 않는다.

    분석·리포트 경로 전용이다(``notice_floor_shortfall`` / 홀드아웃 리포트). 그쪽은
    자체 게이트(``holdout_quality.resolve_legal_floor_rate``)를 갖고 있고, 개연 범위 밖
    값을 **버리는 대신 ``published_floor_implausible`` 로 세어야** 한다 — 여기서 미리
    ``None`` 으로 접으면 그 계수기가 조용히 0 이 되어 원문 품질을 관측할 수 없게 된다.
    수집 write 게이트(#357) 이후 신규 행에는 밴드 밖 값이 적재되지 않으므로 그 계수기는
    **legacy 행 전용** 신호다 — 신규 수집의 원문 품질 신호는 DTO 거부 경고(notice 포함)와
    백필 ``floor_implausible`` 카운터로 이동했다(둘 다 아직 미집계, 후속).

    가격 경로는 게이트가 걸린 :func:`resolve_notice_legal_floor_bid_rate` 를 쓴다. 두
    이름을 나눈 이유가 이것이다: "하한으로 쓸 값"과 "게시된 값"은 다른 질문이다.
    """
    return _rate_to_fraction(getattr(project, "award_floor_rate", None))


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
    normalized via ``_rate_to_fraction`` (reused, not re-derived) and then has to
    clear the plausibility band (:func:`plausible_published_floor_rate`).

    WHY THE PUBLISHED VALUE IS GATED (#356 follow-up)
    -------------------------------------------------
    ``Project.award_floor_rate`` is a faithful transcription of KONEPS
    ``sucsfbidLwltRate``, and that source carries values that cannot be a 낙찰하한:
    47 notices hold 1.00000 ("bid at or above the full 예정가"). The ``max()`` fold
    below used to make such a value harmless, but #356 gave a *reported* published
    floor the authority to push the recommendation past the budget cap, so
    ``1.0 × 기초금액`` could become the enforced floor and the whole 기초금액 the
    recommended bid. An implausible published rate is therefore treated exactly like
    "no published 하한" (``None``) — the value is not repaired, only refused.

    The **override is deliberately not gated**: it is an operator instruction for
    this notice, not scraped data, and silently dropping it would erase an explicit
    decision. Honest caveat: the request schemas do NOT reject an implausible
    override today — ``legal_floor_bid_rate`` is declared with ``ge=0.0`` only, no
    upper bound (``app/schemas/prediction.py`` / ``app/schemas/opportunity.py``), so
    a 1.0 override WOULD reach the guardrail floor. That residual path is accepted
    here as operator authority (the field is not exposed on the frontend, so the
    surface is API-direct calls only); tightening the schema bound is a follow-up
    because it changes the OpenAPI contract.

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
    return plausible_published_floor_rate(
        resolve_notice_published_floor_bid_rate(project)
    )


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
    # getattr (→ Any) crosses the old-style ``Column`` boundary — same idiom the
    # rest of this module uses for model reads; runtime-identical to attr access.
    fields = [
        getattr(project, "title", None),
        getattr(project, "description", None),
        getattr(project, "requirements", None),
    ]
    return " ".join(filter(None, fields))


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
    - ``bid_base_source``: where that amount came from (``ReliableBaseSource`` value or
      :data:`BID_BASE_SOURCE_BUDGET_FALLBACK`) — carried alongside the amount so a
      caller that surfaces the base can also say what it is, instead of presenting a
      collected 기초금액 and a 추정가격 substitute as the same thing.
    - ``text``: title+description+requirements predictor input (``build_prediction_text``).
    - ``legal_floor_bid_rate``: the notice's OWN published 낙찰하한율 (award_floor_rate,
      #201) OR the caller's explicit override, folded into the guardrail floor with
      ``max()`` only downstream — it can only RAISE the floor (RED LINE). ``None``
      when nothing is published/requested.
    - ``published_floor_bid_rate``: the same published 하한 WITHOUT the request
      override — a predictor FEATURE input (the award-rate model's training corpus
      stores exactly this quantity). Kept separate from ``legal_floor_bid_rate``
      because an operator override is an instruction about THIS bid, not a fact
      about the notice; feeding it to the model would move the feature axis off the
      axis the corpus was labelled on. It reaches no floor arithmetic.
    - ``estimation_amount`` / ``reference_date``: construction legal 낙찰하한 tier inputs
      (구간=추정가격, 기준일=공고 시점, leakage-safe — the notice's own date).
    """

    bid_base: BaseAmount
    bid_base_source: str
    text: str
    legal_floor_bid_rate: float | None
    published_floor_bid_rate: float | None
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
    # Resolve amount and provenance in ONE lookup (``resolve_notice_bid_base`` is the
    # same call minus the source) so a caller can disclose which amount it priced on.
    notice_bid_base = describe_notice_bid_base(db, project)
    return NoticePredictionInputs(
        bid_base=notice_bid_base.amount,
        bid_base_source=notice_bid_base.source,
        text=build_prediction_text(project),
        legal_floor_bid_rate=resolve_notice_legal_floor_bid_rate(
            project, request_legal_floor_bid_rate=request_legal_floor_bid_rate
        ),
        # Override-free published value — the model feature axis (see the dataclass).
        published_floor_bid_rate=plausible_published_floor_rate(
            resolve_notice_published_floor_bid_rate(project)
        ),
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
