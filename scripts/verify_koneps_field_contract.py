#!/usr/bin/env python3
"""신규 KONEPS 수집 필드를 소비 전에 **실 응답 N건으로 assert** 하는 온-디맨드 게이트.

반복 버그(#209 자격상세 목록 부재, #210 차수 int 파괴, #220 success_rate=예정가)는 전부
신규 수집 필드를 소비 전에 실배치 검증하지 않아 라이브에서 터졌다. 이 스크립트는 실
KONEPS 응답 소량을 읽어(외부 호출, 읽기 전용) 각 item 을 ``field_contract`` 순수 검증기에
통과시키고 **계약 위반 + 미지(unknown) 필드**를 리포트한다. 신규 필드를 소비 코드에 넣기
**전에** 이걸로 그 필드가 정말 기대한 의미/타입/존재인지 확인하는 게 목적이다.

읽기 전용 — DB write 없음, 값 조작 없음. resultCode 게이트(HTTP 200+에러코드)를 적용하고,
호출 간 throttle 하며, service_key 등 시크릿은 절대 출력하지 않는다. ``--dry-run`` 은 외부
호출 없이 선언된 계약만 출력한다(계약 리뷰용).

사용 예 (api 컨테이너에서 실행):
    # 계약만 출력 (호출 없음)
    docker exec bid_vector_api python scripts/verify_koneps_field_contract.py --dry-run

    # 공고 목록(BidPublicInfoService) 최근 피드 5건 실배치 검증
    docker exec bid_vector_api python scripts/verify_koneps_field_contract.py \
        --scope notice --category service --limit 5

    # 특정 공고 표적조회(inqryDiv=2)
    docker exec bid_vector_api python scripts/verify_koneps_field_contract.py \
        --scope notice --notice 20260612345

    # 개찰/낙찰 목록(ScsbidInfoService) — success_rate/차수 계약 검증
    docker exec bid_vector_api python scripts/verify_koneps_field_contract.py \
        --scope scsbid --category construction --limit 5
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings
from app.core.time import kst_now
from app.services.koneps import field_contract, http_client, openapi

# 스코프별 상수(매직값은 함수 밖에 선언). notice=BidPublicInfoService 공고 목록/표적조회,
# scsbid=ScsbidInfoService 개찰/낙찰 목록.
_RESPONSE_TYPE = "json"
_PAGE_NO = 1
_INQUIRY_DIV_FEED = "1"  # inqryDiv=1 => 등록일시 피드
_INQUIRY_DIV_NOTICE_NUMBER = "2"  # inqryDiv=2 => bidNtceNo 표적조회
DEFAULT_LIMIT = 5
DEFAULT_NUM_OF_ROWS = 10
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_CATEGORY = "service"

# A callable that returns a decoded OpenAPI payload dict for one page.
FetchFn = Callable[[], dict[str, Any]]


@dataclass
class ItemReport:
    """한 item 의 검증 결과(위반 + 미지 필드)."""

    index: int
    notice_number: str
    violations: list[field_contract.ContractViolation] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


@dataclass
class ContractReport:
    """한 검증 run 의 집계 결과(순수 — IO 없음)."""

    operation: str
    family: str
    item_count: int
    items: list[ItemReport] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(
            1
            for item in self.items
            for v in item.violations
            if v.severity is field_contract.Severity.ERROR
        )

    @property
    def warn_count(self) -> int:
        return sum(
            1
            for item in self.items
            for v in item.violations
            if v.severity is field_contract.Severity.WARN
        )

    @property
    def unknown_fields(self) -> list[str]:
        seen: set[str] = set()
        for item in self.items:
            seen.update(item.unknown)
        return sorted(seen)

    @property
    def passed(self) -> bool:
        return self.error_count == 0


# --- Pure report builder (no IO) ---------------------------------------------


def build_report(
    raw_items: list[dict[str, Any]],
    *,
    operation: str,
) -> ContractReport:
    """실 응답 rows 를 계약 검증 리포트로 투영한다(순수 — 외부 호출 없음)."""
    family = field_contract.operation_family(operation)
    report = ContractReport(
        operation=operation,
        family=family.value,
        item_count=len(raw_items),
    )
    for index, raw_item in enumerate(raw_items):
        notice_number = str(raw_item.get("bidNtceNo") or "").strip() or "?"
        report.items.append(
            ItemReport(
                index=index,
                notice_number=notice_number,
                violations=field_contract.validate_item(
                    raw_item, operation=operation
                ),
                unknown=field_contract.unknown_fields(raw_item),
            )
        )
    return report


# --- IO seam: default real-API fetch (read-only) -----------------------------


def _resolve_service_key() -> str:
    key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
    if not key:
        raise ValueError(
            "KONEPS_OPENAPI_SERVICE_KEY is required for live field-contract checks."
        )
    return key


def _resolve_operation(scope: str, category: str, override: str | None) -> str:
    if override:
        return override
    if scope == "scsbid":
        return openapi.scsbid_operation_for_category(category)
    return openapi.openapi_operation_for_category(category)


def _scope_url(scope: str) -> str:
    if scope == "scsbid":
        return str(settings.KONEPS_OPENAPI_SCSBID_INFO_URL).rstrip("/")
    return str(settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL).rstrip("/")


def _feed_params(num_of_rows: int) -> dict[str, Any]:
    """등록일시 피드(inqryDiv=1) 파라미터 — 오늘(KST) 하루 윈도."""
    date_token = openapi.openapi_date_token(kst_now().date().isoformat())
    return {
        "type": _RESPONSE_TYPE,
        "numOfRows": num_of_rows,
        "pageNo": _PAGE_NO,
        "inqryDiv": _INQUIRY_DIV_FEED,
        "inqryBgnDt": f"{date_token}0000",
        "inqryEndDt": f"{date_token}2359",
    }


def _targeted_params(notice_number: str, num_of_rows: int) -> dict[str, Any]:
    """표적조회(inqryDiv=2) 파라미터 — 단일 공고번호."""
    return {
        "type": _RESPONSE_TYPE,
        "numOfRows": num_of_rows,
        "pageNo": _PAGE_NO,
        "inqryDiv": _INQUIRY_DIV_NOTICE_NUMBER,
        "bidNtceNo": notice_number,
    }


def make_fetch(
    *,
    scope: str,
    operation: str,
    notice_number: str | None,
    num_of_rows: int,
) -> FetchFn:
    """읽기 전용 실 API fetch 를 만든다. 시크릿은 http_client 로만 흐르고 미출력."""
    service_key = _resolve_service_key()
    endpoint = f"{_scope_url(scope)}/{operation}"
    if notice_number:
        params = _targeted_params(notice_number, num_of_rows)
    else:
        params = _feed_params(num_of_rows)

    def fetch() -> dict[str, Any]:
        response, key_variant = http_client.request_openapi_with_key_variants(
            endpoint,
            params=params,
            service_key=service_key,
            operation=operation,
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS HTTP {response.status_code} for {operation}: "
                f"{response.text[:200]} Tried service key variants: {key_variant}."
            )
        payload = http_client.load_openapi_json(response)
        # data.go.kr 은 쿼터/키 스로틀을 HTTP 200 + 에러 resultCode 로 신호한다(2a 게이트).
        http_client.check_result_code(payload, source="field-contract verify")
        return payload

    return fetch


def fetch_items(
    fetch: FetchFn,
    *,
    limit: int,
    delay: float,
    sleep_fn: Callable[[float], None] = sleep,
) -> list[dict[str, Any]]:
    """한 페이지를 읽어 최대 ``limit`` 개 item 을 반환한다(단일 호출, throttle 후행)."""
    payload = fetch()
    raw_items = openapi.openapi_item_list(openapi.openapi_body(payload))
    if delay > 0:
        sleep_fn(delay)
    return raw_items[: max(0, limit)]


# --- Console rendering --------------------------------------------------------


def render_report(report: ContractReport) -> list[str]:
    """리포트를 stdout 줄로 렌더한다(시크릿 없음)."""
    lines = [
        "=" * 72,
        f"KONEPS 필드 계약 실배치 검증 (KST {kst_now():%Y-%m-%d %H:%M:%S})",
        f"operation={report.operation} family={report.family} "
        f"items={report.item_count}",
        "=" * 72,
    ]
    for item in report.items:
        if not item.violations and not item.unknown:
            continue
        lines.append(f"[{item.index}] notice={item.notice_number}")
        for violation in item.violations:
            lines.append(
                f"    {violation.severity.value.upper()} "
                f"{violation.field}/{violation.kind.value}: {violation.detail}"
            )
        if item.unknown:
            lines.append(f"    UNKNOWN 필드({len(item.unknown)}): {item.unknown}")
    if report.unknown_fields:
        lines.append(
            f"미지 필드 합집합({len(report.unknown_fields)}): {report.unknown_fields}"
        )
    verdict = "PASS" if report.passed else "FAIL"
    lines.append(
        f"[verify] {verdict} errors={report.error_count} warns={report.warn_count} "
        f"unknown={len(report.unknown_fields)}"
    )
    return lines


# --- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("notice", "scsbid"),
        default="notice",
        help="notice=공고 목록/표적조회(BidPublicInfoService), scsbid=개찰/낙찰(ScsbidInfoService).",
    )
    parser.add_argument(
        "--category",
        default=DEFAULT_CATEGORY,
        help="내부 카테고리(오퍼레이션 선택). 기본 service.",
    )
    parser.add_argument(
        "--operation",
        default=None,
        help="오퍼레이션 이름 직접 지정(카테고리 매핑 우회).",
    )
    parser.add_argument(
        "--notice",
        default=None,
        help="공고번호 표적조회(inqryDiv=2). 지정 시 피드 대신 이 공고만 조회(notice 스코프).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="검증할 item 최대 개수(기본 5, 소량 유지).",
    )
    parser.add_argument(
        "--num-of-rows",
        type=int,
        default=DEFAULT_NUM_OF_ROWS,
        help="numOfRows 페이지 크기.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="호출 뒤 throttle 초.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="외부 호출 없이 선언된 계약만 출력.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        for line in field_contract.describe_contracts():
            print(line)
        print("[verify] DRY-RUN (호출 없음) — 계약 선언만 출력.")
        return 0

    operation = _resolve_operation(args.scope, args.category, args.operation)
    fetch = make_fetch(
        scope=args.scope,
        operation=operation,
        notice_number=args.notice,
        num_of_rows=max(1, args.num_of_rows),
    )
    raw_items = fetch_items(
        fetch,
        limit=max(1, args.limit),
        delay=max(0.0, args.delay),
    )
    report = build_report(raw_items, operation=operation)
    for line in render_report(report):
        print(line)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
