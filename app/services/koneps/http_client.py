"""HTTP fetch helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). The original methods carried zero instance state: the
service class has no ``__init__`` and these three methods referenced neither
instance attributes nor sibling ``self`` methods -- every input arrived as a
parameter. They depend only on ``requests`` (a plain ``requests.get`` per call,
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

from typing import Any

import requests

from app.core.config import settings
from app.services.koneps import html_parsing, openapi


def request_openapi_with_key_variants(
    url: str,
    *,
    params: dict[str, Any],
    service_key: str,
    operation: str,
) -> tuple[requests.Response, str]:
    """Call OpenAPI with raw and URL-encoded key forms used by data.go.kr."""
    timeout = max(1, int(settings.KONEPS_OPENAPI_TIMEOUT_SECONDS))
    variants = openapi.openapi_service_key_variants(service_key)
    last_response: requests.Response | None = None

    for variant_name, variant_value, value_is_preencoded in variants:
        if value_is_preencoded:
            query_string = openapi.openapi_query_string(
                params={**params, "ServiceKey": variant_value},
                preencoded_keys={"ServiceKey"},
            )
            response = requests.get(f"{url}?{query_string}", timeout=timeout)
        else:
            response = requests.get(
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


def fetch_detail_html_payload(source_url: str) -> dict[str, str | None]:
    """Fetch + parse a single KONEPS detail page, returning the business-type fields.

    Performs a simple HTTP GET on ``source_url``, parses the HTML with the
    same ``html_parsing.parse_detail_html`` helper used during live
    collection, and returns only the two business-type keys.

    Best-effort: any exception raised by the HTTP call is propagated so
    callers (e.g. backfill scripts) can record per-row failures.
    """
    timeout = max(1, int(getattr(settings, "KONEPS_OPENAPI_TIMEOUT_SECONDS", 30)))
    response = requests.get(source_url, timeout=timeout)
    response.raise_for_status()
    detail = html_parsing.parse_detail_html(response.text)
    return {
        "business_type_code": detail.get("business_type_code"),
        "business_type_label": detail.get("business_type_label"),
    }
