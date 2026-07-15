#!/usr/bin/env python3
"""Post-개찰 낙찰 적격/경쟁력 검증 리포트.

하나 이상의 공고에 대해, 개찰(결과 공개) 이후 운영자가 산정했던 투찰가가
실현된 낙찰하한가 대비 적격이었는지, 그리고 실제 낙찰자 대비 얼마나
경쟁력이 있었는지를 사실값만으로 출력한다.

이 스크립트는 KONEPS 예비가격 상세를 라이브로 조회하므로(외부 호출), DB와
KONEPS 접근이 가능한 운영 호스트에서만 실행한다. 결과는 stdout으로만 나가고
(cron이 로그로 리다이렉트), 확률 등 조작된 값은 만들지 않는 결정 지원용
검증 리포트다. service_key 등 시크릿은 절대 출력하지 않는다.

사용 예:
    python scripts/verify_award_eligibility.py \
        --notice 20260612345 --bid 88000000 --floor-rate 0.87745 \
        --notice 20260698765 --bid 120000000
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import kst_now
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


def strip_notice_suffix(notice: str) -> str:
    """Drop a trailing 차수 suffix (e.g. ``-000``) so LIKE matching works.

    Project.notice_number is often stored without the order suffix that KONEPS
    appends, so we match on the base number prefix.
    """
    return _NOTICE_SUFFIX_RE.sub("", str(notice or "").strip())


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
    return numeric / 100.0 if numeric > 1.5 else numeric


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

    Structured so ``main()`` only formats it and tests can inject ``fetch_detail``
    (or monkeypatch ``KonepsCollectorService._fetch_scsbid_reserve_detail``).
    """
    base_notice = strip_notice_suffix(notice)
    project = _find_project(db, base_notice)
    category = project.category if project is not None else None

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
# CLI parsing (repeatable --notice/--bid/--floor-rate triples)
# ---------------------------------------------------------------------------


def _amount(value: str) -> float:
    normalized = str(value).strip().replace(",", "").replace("_", "")
    try:
        parsed = float(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("amount must be positive")
    return parsed


def _rate(value: str) -> float:
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if not 0 < parsed <= 1.5:
        raise argparse.ArgumentTypeError("floor-rate must be a ratio in (0, 1.5]")
    return parsed


class _NoticeAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        specs = getattr(namespace, "specs", None) or []
        specs.append({"notice": values, "bid": None, "floor_rate": None})
        setattr(namespace, "specs", specs)


class _BidAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        specs = getattr(namespace, "specs", None)
        if not specs:
            parser.error("--bid must follow a --notice")
        if specs[-1]["bid"] is not None:
            parser.error("duplicate --bid for the same --notice")
        specs[-1]["bid"] = values


class _FloorAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        specs = getattr(namespace, "specs", None)
        if not specs:
            parser.error("--floor-rate must follow a --notice")
        specs[-1]["floor_rate"] = values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "개찰 이후 투찰가의 낙찰 적격/경쟁력을 검증한다. "
            "--notice/--bid/[--floor-rate] 3종을 공고 수만큼 반복한다."
        )
    )
    parser.add_argument(
        "--notice",
        action=_NoticeAction,
        metavar="NOTICE",
        help="공고번호 (차수 접미사 -000 은 매칭 시 제거)",
    )
    parser.add_argument(
        "--bid",
        type=_amount,
        action=_BidAction,
        metavar="AMOUNT",
        help="직전 --notice 의 산정 투찰가 (원)",
    )
    parser.add_argument(
        "--floor-rate",
        type=_rate,
        action=_FloorAction,
        metavar="RATE",
        help="직전 --notice 의 공고 낙찰하한율 (예: 0.87745, 선택)",
    )
    return parser


def parse_specs(argv: list[str] | None = None) -> list[dict[str, Any]]:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    specs = getattr(namespace, "specs", None) or []
    if not specs:
        parser.error("at least one --notice/--bid pair is required")
    for spec in specs:
        if spec["bid"] is None:
            parser.error(f"--notice {spec['notice']} is missing a --bid")
    return specs


# ---------------------------------------------------------------------------
# Formatting
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


def format_result(result: dict[str, Any]) -> list[str]:
    """Render one result dict into stdout lines (no secrets)."""
    notice = result["notice"]
    lines: list[str] = []
    if result.get("reserve_error"):
        lines.append(
            f"[{notice}] 예비가격 상세 조회 실패 (TenderResult로 대체 판정): "
            f"{result['reserve_error']}"
        )
    if not result["settled"]:
        lines.append(f"[{notice}] 아직 개찰 전/미적재 (개찰결과 0건)")
        lines.append(f"[{notice}] VERDICT: {result['verdict']}")
        return lines

    lines.extend(_format_settled_body(result))
    lines.append(f"[{notice}] VERDICT: {result['verdict']}")
    return lines


def _format_settled_body(result: dict[str, Any]) -> list[str]:
    notice = result["notice"]
    bid = result["bid"]
    planned = result.get("planned_price")
    base_amount = result.get("base_amount")
    lines = [
        f"[{notice}] 개찰 결과",
        f"  예정가격: {_won(planned)} | #예비가격: {result.get('reserve_price_count', 0)}"
        f" | 추첨번호: {result.get('selected_numbers') or '-'}"
        f" | 사업금액: {_won(base_amount)}",
        f"  낙찰: {result.get('winning_company') or '-'} / "
        f"{_won(result.get('winning_amount'))} (낙찰률 {_pct(result.get('winning_rate'))})",
        f"  내 투찰: {_won(bid)} | 사업금액대비 {_ratio_pct(bid, base_amount)}"
        f" | 예정가대비 {_ratio_pct(bid, planned)}",
    ]
    lines.extend(_format_eligibility(result))
    lines.append(
        f"  경쟁력(내 투찰 - 낙찰가): {_signed_won(result.get('competitiveness_won'))}"
        f" ({_signed_pp(result.get('competitiveness_pp'))})"
    )
    return lines


def _format_eligibility(result: dict[str, Any]) -> list[str]:
    eligible = result.get("eligible")
    bid = result["bid"]
    winning_amount = result.get("winning_amount")
    if eligible is None:
        lines = ["  적격 판정: 낙찰하한율 없이는 낙하(실격) 여부를 확정할 수 없음"]
        if winning_amount is not None and winning_amount > 0:
            if bid < winning_amount:
                lines.append(
                    "    낙찰가보다 낮게 투찰(낙찰하한 이상이면 낙찰 가능)"
                )
            else:
                lines.append("    낙찰가 이상 — 더 낮은 적격자에게 밀림")
        return lines
    floor_price = result.get("floor_price")
    verdict_kr = "적격" if eligible else "낙하(실격)"
    return [
        f"  적격 판정: {verdict_kr} | 낙찰하한가 {_won(floor_price)}"
        f" | 마진 {_signed_won(result.get('eligibility_margin_won'))}"
        f" ({_signed_pp(result.get('eligibility_margin_pp'))})"
    ]


def main(argv: list[str] | None = None) -> int:
    specs = parse_specs(argv)
    print("=" * 72)
    print(f"낙찰 적격/경쟁력 검증 리포트 (KST {kst_now():%Y-%m-%d %H:%M:%S})")
    print(f"대상 공고 {len(specs)}건")
    print("=" * 72)

    db = SessionLocal()
    try:
        for spec in specs:
            result = verify_one(
                db, spec["notice"], spec["bid"], spec["floor_rate"]
            )
            for line in format_result(result):
                print(line)
            print("-" * 72)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
