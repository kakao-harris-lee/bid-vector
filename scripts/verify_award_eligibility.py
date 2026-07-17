#!/usr/bin/env python3
"""Post-개찰 낙찰 적격/경쟁력 검증 리포트 (thin CLI shim).

하나 이상의 공고에 대해, 개찰(결과 공개) 이후 운영자가 산정했던 투찰가가
실현된 낙찰하한가 대비 적격이었는지, 그리고 실제 낙찰자 대비 얼마나
경쟁력이 있었는지를 사실값만으로 출력한다.

검증 코어(``verify_one``)와 텔레그램 메시지 빌더(``build_telegram_message``)는
``app.services.award_verification`` 로 추출되어 낙찰결과 자동 텔레그램 beat
태스크와 공유된다(collector delegator 패턴). 이 스크립트는 CLI 파싱 + 콘솔
리포트 포맷 + ``--telegram`` 게이트만 담당한다.

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
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal
from app.core.time import kst_now
from app.services.award_verification import (  # re-exported for tests + callers
    VERDICT_ELIGIBLE_OUTBID,
    VERDICT_ELIGIBLE_WINNABLE,
    VERDICT_NOT_SETTLED,
    VERDICT_UNDERCUT,
    VERDICT_UNDETERMINED,
    TelegramSender,
    _pct,
    _ratio_pct,
    _settled_results,
    _signed_pp,
    _signed_won,
    _won,
    build_telegram_message,
    strip_notice_suffix,
    verify_one,
)

__all__ = [
    "VERDICT_ELIGIBLE_OUTBID",
    "VERDICT_ELIGIBLE_WINNABLE",
    "VERDICT_NOT_SETTLED",
    "VERDICT_UNDERCUT",
    "VERDICT_UNDETERMINED",
    "build_telegram_message",
    "format_result",
    "notify_telegram",
    "parse_specs",
    "strip_notice_suffix",
    "verify_one",
]


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
    parser.add_argument(
        "--telegram",
        action="store_true",
        help=(
            "개찰 완료(SETTLED) 공고가 하나라도 있으면 운영자 텔레그램으로 요약을 "
            "전송한다. 전부 개찰 전이면 전송하지 않는다 (기본 꺼짐)."
        ),
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args into a validated namespace (``specs`` + ``telegram`` flag)."""
    parser = build_parser()
    namespace = parser.parse_args(argv)
    specs = getattr(namespace, "specs", None) or []
    if not specs:
        parser.error("at least one --notice/--bid pair is required")
    for spec in specs:
        if spec["bid"] is None:
            parser.error(f"--notice {spec['notice']} is missing a --bid")
    namespace.specs = specs
    return namespace


def parse_specs(argv: list[str] | None = None) -> list[dict[str, Any]]:
    return parse_args(argv).specs


# ---------------------------------------------------------------------------
# Console formatting
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Telegram alarm (opt-in; only fires when at least one notice is 개찰 완료)
# ---------------------------------------------------------------------------


def _default_sender() -> TelegramSender:
    """Instantiate the real Telegram service (no-ops in test/unconfigured)."""
    from app.services.notifications.telegram import TelegramNotificationService

    return TelegramNotificationService()


def notify_telegram(
    results: list[dict[str, Any]],
    *,
    sender: TelegramSender | None = None,
) -> dict[str, Any]:
    """Send a Telegram alarm iff at least one notice is 개찰 완료 (SETTLED).

    Gate: if every notice is still 개찰 전 (VERDICT_NOT_SETTLED) we skip entirely
    to avoid daily spam. Never raises — a delivery failure is captured and
    returned as ``status='error'`` so the caller/cron run keeps going.
    """
    settled = _settled_results(results)
    if not settled:
        return {"status": "skipped", "sent": False, "settled_count": 0}

    message = build_telegram_message(settled)
    if sender is None:
        sender = _default_sender()
    try:
        result = sender.send_message(message)
    except Exception as exc:  # noqa: BLE001 - alarm failure must not abort the run
        return {
            "status": "error",
            "sent": False,
            "settled_count": len(settled),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "status": "sent",
        "sent": bool((result or {}).get("sent")),
        "settled_count": len(settled),
        "result": result or {},
    }


def _print_telegram_outcome(outcome: dict[str, Any]) -> None:
    """Echo the send result to stdout so the cron log shows whether it fired."""
    status = outcome.get("status")
    if status == "skipped":
        print("[telegram] 전송 스킵 (개찰 완료 공고 없음)")
    elif status == "error":
        print(f"[telegram] 전송 오류: {outcome.get('error')}")
    else:
        result = outcome.get("result") or {}
        print(
            f"[telegram] 전송 시도 (개찰 {outcome.get('settled_count')}건) "
            f"| sent={outcome.get('sent')} status={result.get('status')}"
        )


def main(argv: list[str] | None = None) -> int:
    namespace = parse_args(argv)
    specs = namespace.specs
    print("=" * 72)
    print(f"낙찰 적격/경쟁력 검증 리포트 (KST {kst_now():%Y-%m-%d %H:%M:%S})")
    print(f"대상 공고 {len(specs)}건")
    print("=" * 72)

    results: list[dict[str, Any]] = []
    db = SessionLocal()
    try:
        for spec in specs:
            result = verify_one(
                db, spec["notice"], spec["bid"], spec["floor_rate"]
            )
            results.append(result)
            for line in format_result(result):
                print(line)
            print("-" * 72)
    finally:
        db.close()

    if namespace.telegram:
        _print_telegram_outcome(notify_telegram(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
