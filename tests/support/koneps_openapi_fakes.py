"""KONEPS OpenAPI 응답 fake — 수집 테스트 공용 (실호출 금지 경계).

``FakeOpenApiResponse`` + 응답 body 빌더가 scsbid 테스트 3개 파일에 3중복으로
복사돼 있었다(``test_scsbid_forward_coverage`` / ``test_scsbid_reserve_detail_defer``
/ ``test_scsbid_reserve_detail_reuse``). 같은 문제를 두 번째로 풀면 공용 헬퍼로
추출한다는 규칙(§4.5-6)에 따라 여기로 단일화한다.

``requests.get`` 을 이 fake 로 대체하면 ``ENVIRONMENT=test`` 에서 KONEPS 실호출이
발생하지 않는다. HTTP 획득 방식(``http_client``)은 건드리지 않고 응답만 대체한다.
"""

from __future__ import annotations

from typing import Any


class FakeOpenApiResponse:
    """``requests.Response`` 의 최소 대역 — 상태코드/텍스트/JSON 만 제공한다."""

    status_code = 200
    text = "{}"

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def openapi_body(
    items: list[dict[str, Any]],
    *,
    total_count: int,
    num_of_rows: int,
    page_no: int = 1,
    result_code: str = "00",
    result_msg: str = "NORMAL",
) -> dict[str, Any]:
    """공통 OpenAPI 응답 봉투(header + body.items.item)."""
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": result_msg},
            "body": {
                "items": {"item": items},
                "numOfRows": str(num_of_rows),
                "pageNo": str(page_no),
                "totalCount": str(total_count),
            },
        }
    }


def award_body(
    items: list[dict[str, Any]],
    *,
    total_count: int,
    num_of_rows: int,
    page_no: int = 1,
) -> dict[str, Any]:
    """ScsbidInfoService 낙찰 목록 응답(기존 3중복 헬퍼와 동일 형태)."""
    return openapi_body(
        items,
        total_count=total_count,
        num_of_rows=num_of_rows,
        page_no=page_no,
    )


def award_item(
    notice_number: str,
    *,
    title: str = "테스트 낙찰",
    amount: str = "88,000,000",
) -> dict[str, Any]:
    """ScsbidInfoService 낙찰 목록 한 행(기존 3중복 헬퍼와 동일 값)."""
    return {
        "bidNtceNo": notice_number,
        "bidNtceOrd": "000",
        "bidClsfcNo": "0",
        "rbidNo": "0",
        "bidNtceNm": title,
        "prtcptCnum": "10",
        "bidwinnrNm": "낙찰사",
        "bidwinnrBizno": "1234567890",
        "sucsfbidAmt": amount,
        "sucsfbidRate": "88.0",
        "rlOpengDt": "2026-05-13 11:00:00",
        "dminsttNm": "서울특별시",
        "rgstDt": "2026-05-13 12:00:00",
        "fnlSucsfDate": "2026-05-13",
    }


def empty_reserve_body() -> dict[str, Any]:
    """복수예비가격 상세가 아직 없는(미개찰) 응답."""
    return openapi_body([], total_count=0, num_of_rows=100)


def reserve_detail_body() -> dict[str, Any]:
    """복수예비가격 2행을 싣는 상세 응답(1번만 추첨)."""
    return openapi_body(
        [
            {
                "compnoRsrvtnPrceSno": "1",
                "bsisPlnprc": "101000000",
                "plnprc": "100000000",
                "bssamt": "100000000",
                "drwtYn": "Y",
            },
            {
                "compnoRsrvtnPrceSno": "2",
                "bsisPlnprc": "102000000",
                "plnprc": "100000000",
                "bssamt": "100000000",
                "drwtYn": "N",
            },
        ],
        total_count=2,
        num_of_rows=100,
    )
