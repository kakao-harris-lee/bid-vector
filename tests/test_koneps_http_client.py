"""Unit tests for the extracted KONEPS HTTP fetch helpers.

These cover ``app.services.koneps.http_client`` in isolation: the key-variant
retry loop (401 -> next variant), JSON decoding error surfacing, and the single
detail-page fetch/parse path. All HTTP is mocked via
``app.services.koneps.http_client.requests.get`` -- no real KONEPS calls are
made under ``ENVIRONMENT=test``.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.koneps import http_client


class _FakeResponse:
    """Minimal ``requests.Response`` stand-in for the helpers under test."""

    def __init__(self, *, status_code=200, payload=None, text="", raise_exc=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._raise_exc = raise_exc

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


def test_request_openapi_retries_to_next_variant_on_401(monkeypatch):
    """A 401 on the first key variant should fall through to the next one."""
    # A key containing characters that change under URL-encoding ("+" / "=")
    # yields a distinct ``url_encoded`` variant in addition to ``configured``.
    raw_key = "abc+def==/xyz"
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", raw_key)
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_ENCODED_SERVICE_KEY", "")

    ok = _FakeResponse(status_code=200, payload={"ok": True})
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        # First attempt (raw "configured" variant) is unauthorized; the second
        # attempt (url_encoded, sent as a pre-encoded query string) succeeds.
        if len(calls) == 1:
            return _FakeResponse(status_code=401)
        return ok

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    response, variant = http_client.request_openapi_with_key_variants(
        "https://example.test/api",
        params={"numOfRows": 10},
        service_key=raw_key,
        operation="testOperation",
    )

    assert response is ok
    assert variant == "url_encoded"
    assert len(calls) == 2
    # The retried variant is pre-encoded, so it is sent as a query-string URL.
    assert calls[1]["url"].startswith("https://example.test/api?")


def test_request_openapi_returns_last_response_when_all_401(monkeypatch):
    """When every variant 401s, the final response is returned, not raised."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "raw-key")
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_ENCODED_SERVICE_KEY", "")

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(status_code=401)

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    response, variant = http_client.request_openapi_with_key_variants(
        "https://example.test/api",
        params={"numOfRows": 10},
        service_key="raw-key",
        operation="testOperation",
    )

    assert response.status_code == 401
    # The joined variant string lists each attempted variant name.
    assert "configured" in variant


def test_load_openapi_json_returns_dict_payload():
    response = _FakeResponse(payload={"response": {"header": {}}})
    assert http_client.load_openapi_json(response) == {"response": {"header": {}}}


def test_load_openapi_json_raises_on_non_json_body():
    response = _FakeResponse(payload=ValueError("not json"), text="<html>err</html>")
    with pytest.raises(ValueError, match="was not JSON"):
        http_client.load_openapi_json(response)


def test_load_openapi_json_raises_on_non_object_json():
    response = _FakeResponse(payload=[1, 2, 3])
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        http_client.load_openapi_json(response)


def test_fetch_detail_html_payload_parses_business_type(monkeypatch):
    captured: dict = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(status_code=200, text="<html>detail</html>")

    def fake_parse(html):
        captured["html"] = html
        return {
            "business_type_code": "1234",
            "business_type_label": "정보통신공사업",
            "other": "ignored",
        }

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)
    monkeypatch.setattr(
        "app.services.koneps.html_parsing.parse_detail_html", fake_parse
    )

    result = http_client.fetch_detail_html_payload("https://example.test/detail/1")

    assert result == {
        "business_type_code": "1234",
        "business_type_label": "정보통신공사업",
    }
    assert captured["url"] == "https://example.test/detail/1"
    assert captured["html"] == "<html>detail</html>"


def test_fetch_detail_html_payload_propagates_http_error(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(status_code=500, raise_exc=RuntimeError("boom"))

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    with pytest.raises(RuntimeError, match="boom"):
        http_client.fetch_detail_html_payload("https://example.test/detail/1")


# --- check_result_code: the consolidated resultCode envelope guard -------------
# This helper replaced four copy-paste checks (collector list / reserve-detail,
# collection notice-list, backfill script). The regression guard lives here on
# the helper itself, not only on the backfill wrapper (which discards the tuple).


def _result_payload(code, message=""):
    return {"response": {"header": {"resultCode": code, "resultMsg": message}}}


@pytest.mark.parametrize("code", ["00", "03", ""])
def test_check_result_code_allows_ok_and_empty_codes(code):
    # "00"/"03" are success; an empty/absent code passes (some payloads omit the
    # header). The returned pair echoes the (stripped) header values.
    assert http_client.check_result_code(
        _result_payload(code, "ok"), source="OpenAPI returned"
    ) == (code, "ok")


@pytest.mark.parametrize("code", ["22", "30", "99"])
def test_check_result_code_raises_on_error_code(code):
    # Quota/throttle come back as HTTP 200 + non-OK resultCode -> must raise, and
    # the message surfaces both the code and the resultMsg for diagnosis.
    with pytest.raises(ValueError) as excinfo:
        http_client.check_result_code(
            _result_payload(code, "LIMITED_NUMBER"), source="OpenAPI returned"
        )
    assert f"resultCode={code}" in str(excinfo.value)
    assert "LIMITED_NUMBER" in str(excinfo.value)


def test_check_result_code_returns_stripped_pair():
    # The (code, message) contract is what feeds collector/collection's
    # ``state.last_result_code`` / ``last_result_message``: whitespace is stripped
    # so a padded header does not corrupt the persisted state.
    assert http_client.check_result_code(
        _result_payload("  03 ", "  service normal  "), source="OpenAPI returned"
    ) == ("03", "service normal")


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "ScsbidInfoService returned",
            "KONEPS ScsbidInfoService returned resultCode=22: quota exceeded",
        ),
        (
            "OpenAPI returned",
            "KONEPS OpenAPI returned resultCode=22: quota exceeded",
        ),
        (
            "BidPublicInfoService",
            "KONEPS BidPublicInfoService resultCode=22: quota exceeded",
        ),
    ],
)
def test_check_result_code_message_preserves_per_site_wording(source, expected):
    # ``source`` carries each original call site's exact subject (note the
    # collector/collection sites say "returned", the backfill script does not),
    # so this consolidation preserves every per-site message verbatim.
    with pytest.raises(ValueError) as excinfo:
        http_client.check_result_code(
            _result_payload("22", "quota exceeded"), source=source
        )
    assert str(excinfo.value) == expected


def test_check_result_code_defaults_blank_message_to_unknown_error():
    with pytest.raises(ValueError, match="resultCode=22: unknown error"):
        http_client.check_result_code(
            _result_payload("22", ""), source="OpenAPI returned"
        )


def test_check_result_code_message_excludes_service_key(monkeypatch):
    # The error is built only from source/code/message -- never a service key.
    # Plant a sentinel key in settings and assert it cannot leak into the raise.
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "SENTINEL-SECRET-KEY")
    with pytest.raises(ValueError) as excinfo:
        http_client.check_result_code(
            _result_payload("22", "quota exceeded"), source="OpenAPI returned"
        )
    assert "SENTINEL-SECRET-KEY" not in str(excinfo.value)
    assert "ServiceKey" not in str(excinfo.value)
