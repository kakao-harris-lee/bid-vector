"""HTTP fetch helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). The original methods carried zero instance state: these
three methods referenced neither instance attributes nor sibling ``self``
methods -- every input arrived as a parameter. They depend only on the injected
HTTP GET seam (:data:`HttpGet`, defaulting to a plain ``requests.get`` per call
with no shared ``Session`` / connection pooling), the module-level ``settings``
(OpenAPI timeout config), and the already-extracted pure helpers in ``openapi``
(service-key variants / query-string encoding) and ``html_parsing`` (detail
parsing). For that reason they live here as module-level functions instead of a
stateful class.

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite (the key-variant retry order, the 401 handling,
the timeout, the JSON decoding, and ``raise_for_status`` are unchanged). To
avoid an import cycle, this module must never import ``collector``: the
collector imports ``http_client`` (and the sibling ``openapi`` / ``html_parsing``
modules), not the other way around. The collector keeps a thin delegator method
(``fetch_detail_html_payload``) for external callers (``business_type_enrichment``
and ``scripts/backfill_business_type.py``) that invoke it as an instance method.
"""

from typing import Any, Protocol

import requests

from app.core.config import settings
from app.services.koneps import html_parsing, openapi

# --- HTTP 획득 seam (§4.7-1/3) ----------------------------------------------------
# KONEPS 로 나가는 GET 은 이 모듈에서만 일어난다. 그 획득 방식(transport)만 포트로 떼어
# 주입 가능하게 하고, **rate/throttle/timeout/키 변형 재시도 의미는 계속 이 모듈이 소유**
# 한다(주입은 "어떻게 가져오는가"만 바꾸며 "언제·몇 번·얼마나 기다려" 는 못 바꾼다).
#
# 미주입(기본) 경로는 :func:`_default_http_get` = 이 저장소에서 유일한 ``requests.get``
# 호출 지점이다. 덕분에 테스트는 라이브러리 함수를 문자열 경로로 패치하는 대신 (a) 콜러블
# 주입 또는 (b) 이 좁은 단일 표면 하나만 대체하면 된다.

# OpenAPI 쿼리 파라미터 — 값은 스칼라(str/int)만 실린다(좁은 계약 명시).
HttpGetParams = dict[str, str | int]


class HttpGet(Protocol):
    """HTTP GET 획득 포트. ``params=None`` 이면 URL 에 쿼리를 덧붙이지 않는다.

    ``timeout`` 은 키워드 필수 — 이 모듈의 어떤 호출 지점도 타임아웃을 빼먹을 수 없다
    (mypy 강제). 구현이 그 값을 실제로 지키는지는 포트가 보장하지 않는다.
    """

    def __call__(
        self,
        url: str,
        *,
        params: HttpGetParams | None,
        timeout: int,
    ) -> requests.Response: ...


def _default_http_get(
    url: str,
    *,
    params: HttpGetParams | None,
    timeout: int,
) -> requests.Response:
    """기본 획득 구현 — 이 모듈(및 저장소)의 유일한 ``requests.get`` 호출 지점."""
    return requests.get(url, params=params, timeout=timeout)


def request_openapi_with_key_variants(
    url: str,
    *,
    params: dict[str, Any],
    service_key: str,
    operation: str,
    http_get: HttpGet | None = None,
) -> tuple[requests.Response, str]:
    """Call OpenAPI with raw and URL-encoded key forms used by data.go.kr."""
    fetch: HttpGet = http_get or _default_http_get
    timeout = max(1, int(settings.KONEPS_OPENAPI_TIMEOUT_SECONDS))
    variants = openapi.openapi_service_key_variants(service_key)
    last_response: requests.Response | None = None

    for variant_name, variant_value, value_is_preencoded in variants:
        if value_is_preencoded:
            query_string = openapi.openapi_query_string(
                params={**params, "ServiceKey": variant_value},
                preencoded_keys={"ServiceKey"},
            )
            response = fetch(f"{url}?{query_string}", params=None, timeout=timeout)
        else:
            response = fetch(
                url,
                params={**params, "ServiceKey": variant_value},
                timeout=timeout,
            )
        if response.status_code != 401:
            return response, variant_name
        last_response = response

    if last_response is None:
        raise ValueError(f"KONEPS OpenAPI request was not attempted for {operation}.")
    return last_response, ",".join(name for name, _, _ in variants)


def load_openapi_json(response: requests.Response) -> dict[str, Any]:
    """Decode one OpenAPI response, surfacing non-JSON error bodies clearly."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(
            f"KONEPS OpenAPI response was not JSON: {response.text[:500]}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("KONEPS OpenAPI response did not contain a JSON object.")
    return payload


# resultCode envelope validation (consolidated from 4 copy-paste sites: the
# scsbid list / reserve-detail checks in ``collector.py``, the notice-list check
# in ``collection.py``, and the standalone helper in
# ``scripts/backfill_award_floor_rate.py``). Behavior is preserved exactly; each
# site keeps its verbatim message via the ``source`` subject below. It lives here
# next to ``load_openapi_json`` — the other decode-and-validate step — because
# http_client already owns turning a raw response into a validated OpenAPI payload
# and already imports ``openapi`` (no cycle: ``openapi`` never imports
# ``http_client``).
OK_RESULT_CODES = frozenset({"00", "03"})


def check_result_code(payload: dict[str, Any], *, source: str) -> tuple[str, str]:
    """Reject a non-OK OpenAPI ``resultCode``; return the ``(code, message)`` pair.

    KONEPS / data.go.kr signal quota-exceeded and key-throttle as **HTTP 200 with
    an error ``resultCode``** in the envelope header, not a 4xx status. If that
    slipped through, the payload would parse to zero items and be miscounted as
    "no data", so every caller must reject non-OK codes explicitly. ``00``/``03``
    are success ("03" = normal service, empty result set); an empty/absent code is
    treated as OK because some payload shapes omit the header.

    ``source`` is the message subject inserted between ``"KONEPS "`` and
    ``" resultCode="`` (e.g. ``"ScsbidInfoService returned"`` or
    ``"BidPublicInfoService"``). It carries each call site's original wording so
    this consolidation preserves the per-site message verbatim; the service key is
    never included. Only ``resultCode`` is validated here — ``totalCount`` handling
    stays with each caller, which counts it differently. The returned pair is
    stripped and ready for callers that echo it into collection state.
    """
    header = openapi.openapi_header(payload)
    result_code = str(header.get("resultCode") or "").strip()
    result_message = str(header.get("resultMsg") or "").strip()
    if result_code and result_code not in OK_RESULT_CODES:
        raise ValueError(
            f"KONEPS {source} resultCode={result_code}: "
            f"{result_message or 'unknown error'}"
        )
    return result_code, result_message


def fetch_detail_html_payload(
    source_url: str, *, http_get: HttpGet | None = None
) -> dict[str, str | None]:
    """Fetch + parse a single KONEPS detail page, returning the business-type fields.

    Performs a simple HTTP GET on ``source_url`` (via the injected seam, default
    ``requests.get``), parses the HTML with the same
    ``html_parsing.parse_detail_html`` helper used during live collection, and
    returns only the two business-type keys.

    Best-effort: any exception raised by the HTTP call is propagated so
    callers (e.g. backfill scripts) can record per-row failures.
    """
    fetch: HttpGet = http_get or _default_http_get
    timeout = max(1, int(getattr(settings, "KONEPS_OPENAPI_TIMEOUT_SECONDS", 30)))
    response = fetch(source_url, params=None, timeout=timeout)
    response.raise_for_status()
    detail = html_parsing.parse_detail_html(response.text)
    return {
        "business_type_code": detail.get("business_type_code"),
        "business_type_label": detail.get("business_type_label"),
    }
