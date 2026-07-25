"""Post-개찰 낙찰 적격/경쟁력 검증 코어 (script + beat 공용).

개찰(결과 공개) 이후 운영자가 산정했던 투찰가가 실현된 낙찰하한가 대비
적격이었는지, 실제 낙찰자 대비 얼마나 경쟁력이 있었는지를 **사실값만으로**
계산한다. 확률 등 조작된 값은 만들지 않는 결정 지원용 검증이며(§2 정직 명세),
service_key 등 시크릿은 절대 로그/출력에 남기지 않는다.

이 모듈은 두 곳에서 재사용된다:

- ``scripts/verify_award_eligibility.py`` — 운영자 수동 CLI 리포트(얇은 shim).
- ``app.services.award_notifications.AwardResultNotificationService`` —
  낙찰결과 자동 텔레그램 beat 태스크.

``verify_one`` 은 KONEPS 예비가격 상세를 라이브로 조회하므로(외부 호출) DB 와
KONEPS 접근이 가능한 호스트에서만 실호출된다. 테스트는 ``fetch_detail`` 을
주입하거나 ``KonepsCollectorService._fetch_scsbid_reserve_detail`` 를
monkeypatch 해 라이브 호출 없이 검증한다.

또한 이 모듈은 실투찰의 **낙찰/패찰 판정 순수 함수**를 제공한다(§4.7 — I/O 없음):
``normalize_company_name`` 은 상호에서 법인 접두·접미(주식회사·(주)·㈜·유한회사·
(유) 등)와 공백을 제거해 표기 차이를 흡수하고, ``determine_award_outcome`` 은
운영자 상호와 낙찰자 상호의 정규화 비교로만 ``"won"``/``"lost"`` 를 판정한다(어느
한쪽 상호가 없으면 ``None`` — 불확실하면 미기록). 금액은 판정에 쓰지 않는다(동일
개찰에 동일 투찰가 복수 업체가 실존하는 도메인, §2 정직 명세). 이 판정은
``award_notifications`` 파이프라인이 ``BidDecisionRecord.award_outcome`` 에
영속화한다.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import kst_now
from app.domain.rate_normalization import to_bid_rate_fraction
from app.models.models import HistoricalData, Project, TenderResult

# Verdict codes (also asserted by tests).
VERDICT_NOT_SETTLED = "미적재/개찰 전"
VERDICT_ELIGIBLE_WINNABLE = "적격+낙찰가능"
VERDICT_ELIGIBLE_OUTBID = "적격+밀림"
VERDICT_UNDERCUT = "낙하"
VERDICT_UNDETERMINED = "미확정(낙찰하한율 필요)"

_NOTICE_SUFFIX_RE = re.compile(r"-\d+$")

# A KONEPS ``FetchDetail`` takes ``(notice, category)`` and returns the reserve
# detail summary dict (``reserve_prices``/``selected_numbers``/``planned_price``
# /``base_amount``/``reserve_detail_total_count``).
FetchDetail = Callable[[str, str | None], dict[str, Any]]


class TelegramSender(Protocol):
    """Minimal sender surface used by the alarm callers (injectable in tests)."""

    def send_message(self, message: str) -> dict[str, Any]:
        ...


def strip_notice_suffix(notice: str) -> str:
    """Drop a trailing 차수 suffix (e.g. ``-000``) so LIKE matching works.

    Project.notice_number is often stored without the order suffix that KONEPS
    appends, so we match on the base number prefix.
    """
    return _NOTICE_SUFFIX_RE.sub("", str(notice or "").strip())


# Legal-entity tokens stripped before comparing company names. KONEPS records
# the same company inconsistently (예: "주식회사 해담" vs "(주)해담"), so name
# equality is judged on the bare 상호 with these forms and whitespace removed.
_COMPANY_LEGAL_TOKENS = (
    "주식회사",
    "유한책임회사",
    "유한회사",
    "합자회사",
    "합명회사",
    "(주)",
    "（주）",
    "㈜",
    "(유)",
    "（유）",
    "㈔",
)

# Award outcome codes persisted on BidDecisionRecord.award_outcome.
AWARD_OUTCOME_WON = "won"
AWARD_OUTCOME_LOST = "lost"


def normalize_company_name(value: Any) -> str:
    """Normalize a 상호 for equality: drop legal forms + whitespace, casefold.

    Pure and side-effect free. Returns ``""`` for empty/None so callers can treat
    an unknown name as "no basis to judge" (§2 정직 — 불확실하면 미기록).
    """
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return ""
    for token in _COMPANY_LEGAL_TOKENS:
        text = text.replace(token, "")
    return text.casefold()


def determine_award_outcome(
    winning_company: Any,
    winning_amount: Any,
    operator_company_name: Any,
    submitted_bid_amount: Any,
) -> str | None:
    """Judge a real bid as won/lost by operator vs winner 상호. None if unknown.

    Pure decision core (§4.7) — no I/O. Rules (§2 정직 명세):

    - No operator 상호 (프로필명 부재) → ``None`` (미기록; 불확실하면 남기지 않음).
    - No public 낙찰자 상호 → ``None`` (낙찰자 미확정). ``winning_amount`` alone
      never decides an outcome: the same 개찰 can carry identical 투찰가 across
      distinct companies, so amount-only judgement is forbidden.
    - Normalized 상호 매칭 성공 → ``"won"``.
    - 낙찰자 확정됐고 상호 불일치 → ``"lost"``.

    ``winning_amount`` / ``submitted_bid_amount`` are accepted for caller
    symmetry and audit only; they are deliberately excluded from the verdict.
    """
    operator_name = normalize_company_name(operator_company_name)
    if not operator_name:
        return None
    winner_name = normalize_company_name(winning_company)
    if not winner_name:
        return None
    return AWARD_OUTCOME_WON if winner_name == operator_name else AWARD_OUTCOME_LOST


def _default_fetch_detail(notice: str, category: str | None) -> dict[str, Any]:
    """Live KONEPS reserve-detail fetch. Never logs the service key."""
    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()
    return service._fetch_scsbid_reserve_detail(
        {"bidNtceNo": notice},
        category=category,
        service_key=str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip(),
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate_to_fraction(value: Any) -> float | None:
    """Normalize a rate that may be stored as a ratio or a percentage."""
    numeric = _coerce_float(value)
    if numeric is None or numeric <= 0:
        return None
    return to_bid_rate_fraction(numeric)


def _find_project(db: Session, base_notice: str) -> Project | None:
    if not base_notice:
        return None
    return (
        db.query(Project)
        .filter(Project.notice_number.like(f"{base_notice}%"))
        .order_by(Project.id.asc())
        .first()
    )


def _historical_base_amount(db: Session, project: Project | None) -> float | None:
    if project is None:
        return None
    record = (
        db.query(HistoricalData)
        .filter(HistoricalData.project_id == project.id)
        .order_by(HistoricalData.id.desc())
        .first()
    )
    if record is None:
        return None
    amount = _coerce_float(record.base_amount)
    return amount if amount and amount > 0 else None


def _tender_result(db: Session, project: Project | None) -> TenderResult | None:
    if project is None:
        return None
    return (
        db.query(TenderResult)
        .filter(TenderResult.project_id == project.id)
        .order_by(TenderResult.id.desc())
        .first()
    )


def _eligibility(
    *, bid: float, floor_rate: float | None, planned_price: float | None
) -> dict[str, Any]:
    """Compute 적격 판정 vs the realized 낙찰하한가 when we can."""
    if floor_rate is None or not planned_price or planned_price <= 0:
        return {"floor_price": None, "eligible": None, "margin_won": None, "margin_pp": None}
    floor_price = floor_rate * planned_price
    return {
        "floor_price": floor_price,
        "eligible": bid >= floor_price,
        "margin_won": bid - floor_price,
        "margin_pp": (bid - floor_price) / planned_price * 100.0,
    }


def _competitiveness(
    *, bid: float, winning_amount: float | None, denominator: float | None
) -> dict[str, Any]:
    if winning_amount is None or winning_amount <= 0:
        return {"won": None, "pp": None}
    pp = None
    if denominator and denominator > 0:
        pp = (bid - winning_amount) / denominator * 100.0
    return {"won": bid - winning_amount, "pp": pp}


def _verdict(
    *, eligible: bool | None, bid: float, winning_amount: float | None
) -> str:
    if eligible is None:
        return VERDICT_UNDETERMINED
    if not eligible:
        return VERDICT_UNDERCUT
    if winning_amount is not None and winning_amount > 0 and bid >= winning_amount:
        return VERDICT_ELIGIBLE_OUTBID
    return VERDICT_ELIGIBLE_WINNABLE


def verify_one(
    db: Session,
    notice: str,
    bid: float,
    floor_rate: float | None,
    *,
    fetch_detail: FetchDetail = _default_fetch_detail,
) -> dict[str, Any]:
    """Verify a single (notice, bid, floor_rate) after 개찰. Returns a result dict.

    Structured so callers only format it and tests can inject ``fetch_detail``
    (or monkeypatch ``KonepsCollectorService._fetch_scsbid_reserve_detail``).
    """
    base_notice = strip_notice_suffix(notice)
    project = _find_project(db, base_notice)
    category = project.category if project is not None else None

    # 낙찰하한율 근거: 운영자 입력(submitted)이 우선. 없으면 공고 수집 시 저장한
    # Project.award_floor_rate(notice)로 폴백한다. 어떤 근거로 판정했는지 §2 정직
    # 명세에 따라 결과 dict에 남긴다.
    floor_rate_source: str | None = "submitted" if floor_rate is not None else None
    if floor_rate is None and project is not None:
        notice_floor = _rate_to_fraction(project.award_floor_rate)
        if notice_floor is not None:
            floor_rate = notice_floor
            floor_rate_source = "notice"

    detail: dict[str, Any] = {}
    reserve_error: str | None = None
    try:
        detail = fetch_detail(base_notice, category) or {}
    except Exception as exc:  # noqa: BLE001 - one bad notice must not abort the run
        reserve_error = f"{type(exc).__name__}: {exc}"

    reserve_prices = list(detail.get("reserve_prices") or [])
    selected_numbers = list(detail.get("selected_numbers") or [])
    planned_price = _coerce_float(detail.get("planned_price"))
    reserve_base = _coerce_float(detail.get("base_amount"))
    total_count = int(detail.get("reserve_detail_total_count") or 0)

    result = _tender_result(db, project)
    winning_amount = _coerce_float(result.winning_amount) if result else None
    base_amount = reserve_base or _historical_base_amount(db, project)

    settled = (
        total_count > 0
        or bool(reserve_prices)
        or (winning_amount is not None and winning_amount > 0)
    )

    base: dict[str, Any] = {
        "notice": notice,
        "base_notice": base_notice,
        "project_id": project.id if project is not None else None,
        "category": category,
        "bid": bid,
        "floor_rate": floor_rate,
        "floor_rate_source": floor_rate_source,
        "reserve_error": reserve_error,
        "settled": settled,
    }
    if not settled:
        base["verdict"] = VERDICT_NOT_SETTLED
        return base

    eligibility = _eligibility(bid=bid, floor_rate=floor_rate, planned_price=planned_price)
    denominator = planned_price if (planned_price and planned_price > 0) else base_amount
    competitiveness = _competitiveness(
        bid=bid, winning_amount=winning_amount, denominator=denominator
    )
    base.update(
        {
            "planned_price": planned_price,
            "reserve_price_count": len(reserve_prices),
            "selected_numbers": selected_numbers,
            "base_amount": base_amount,
            "winning_company": result.winning_company if result else None,
            "winning_amount": winning_amount,
            "winning_rate": _rate_to_fraction(result.winning_rate) if result else None,
            "floor_price": eligibility["floor_price"],
            "eligible": eligibility["eligible"],
            "eligibility_margin_won": eligibility["margin_won"],
            "eligibility_margin_pp": eligibility["margin_pp"],
            "competitiveness_won": competitiveness["won"],
            "competitiveness_pp": competitiveness["pp"],
            "verdict": _verdict(
                eligible=eligibility["eligible"], bid=bid, winning_amount=winning_amount
            ),
        }
    )
    return base


# ---------------------------------------------------------------------------
# Number formatting (shared by the console report + the Telegram alarm)
# ---------------------------------------------------------------------------


def _won(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}원"


def _signed_won(value: float | None) -> str:
    return "-" if value is None else f"{value:+,.0f}원"


def _pct(fraction: float | None) -> str:
    return "-" if fraction is None else f"{fraction * 100:.3f}%"


def _signed_pp(value: float | None) -> str:
    return "-" if value is None else f"{value:+.3f}%p"


def _ratio_pct(numerator: float, denominator: float | None) -> str:
    if not denominator or denominator <= 0:
        return "-"
    return f"{numerator / denominator * 100:.3f}%"


# ---------------------------------------------------------------------------
# Telegram alarm message (개찰 완료 공고만; 사실값만, 시크릿/조작확률 없음)
# ---------------------------------------------------------------------------


def _settled_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only 개찰 완료 notices (verdict != VERDICT_NOT_SETTLED)."""
    return [r for r in results if r.get("verdict") != VERDICT_NOT_SETTLED]


def build_telegram_message(settled: list[dict[str, Any]]) -> str:
    """Render a concise, factual Korean alarm for 개찰 완료 notices.

    Reuses the console report's numbers (예정가격/내 투찰가/낙찰가/사업금액 대비 %).
    No fabricated probabilities and no secrets — honesty §2.
    """
    header = (
        f"[낙찰 적격/경쟁력 검증] 개찰 {len(settled)}건 "
        f"(KST {kst_now():%Y-%m-%d %H:%M})"
    )
    lines = [header]
    for result in settled:
        notice = result.get("notice")
        bid = result.get("bid")
        base_amount = result.get("base_amount")
        # Optional 낙찰/패찰 tag when the caller resolved the outcome (name-match
        # vs 낙찰자). Absent for callers that don't judge outcome (CLI report).
        outcome_tag = {AWARD_OUTCOME_WON: "낙찰 ", AWARD_OUTCOME_LOST: "패찰 "}.get(
            result.get("award_outcome"), ""
        )
        lines.append(
            f"[{notice}] {outcome_tag}{result.get('verdict')} | 예정가 {_won(result.get('planned_price'))}"
            f" | 내 투찰 {_won(bid)} | 낙찰가 {_won(result.get('winning_amount'))}"
            f" | 사업금액대비 {_ratio_pct(bid, base_amount) if bid is not None else '-'}"
        )
    return "\n".join(lines)
