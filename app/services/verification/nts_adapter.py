"""국세청 사업자등록정보 검증 어댑터 + 팩토리(§4.7-1/2).

두 구현을 포트 뒤에 둔다:

* :class:`UnavailableBusinessVerificationAdapter` — 서비스키 미구성. **HTTP 호출을 절대
  하지 않고** status='unknown' 을 돌려준다(graceful no-key, 설계 §비기능).
* :class:`NtsBusinessVerificationAdapter` — 공공데이터포털(odcloud) 국세청 API 호출.
  상태조회(``POST {base}/status``)와 진위확인(``POST {base}/validate``)을 수행하고,
  응답 코드를 :mod:`status` 룩업표로 내부 상태에 매핑한다. HTTP/파싱 실패는 raise 하지
  않고 status='unknown' 을 돌려준다(베스트에포트 — 요청 흐름에 예외를 던지지 않는다).

팩토리 :func:`build_business_verification_port` 는 키가 있으면 NTS, 없으면 Unavailable 을
반환한다. 원문 사업자번호와 서비스키는 masked payload/로그 어디에도 노출하지 않는다(§8).

odcloud 응답 스키마는 실제 키가 있을 때 정확히 검증해 보정한다 — 여기서는 문서화된
계약(``data[].b_stt/b_stt_cd/tax_type``, ``data[].valid``)에 맞춰 방어적으로 파싱한다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import requests

from app.core.config import settings
from app.services.verification.hashing import mask_business_number, normalize_business_number
from app.services.verification.ports import BusinessVerificationPort, VerificationResult
from app.services.verification.status import (
    BusinessVerificationStatus,
    map_status_code,
    map_validate_code,
)

logger = logging.getLogger(__name__)

# HTTP POST seam(§4.7-3). 기본 구현은 requests.post 를 쓰고, 테스트는 가짜를 주입한다.
HttpPost = Callable[..., Any]

STATUS_ENDPOINT = "status"
VALIDATE_ENDPOINT = "validate"

# 응답에서 보존하는(masked payload 로 올리는) 상태 키 — b_no 는 여기 담지 않고 별도로
# 마스킹해 넣는다(원문 미노출). 그 외 서비스키/원문은 절대 담지 않는다.
_STATUS_KEEP_KEYS = ("b_stt", "b_stt_cd", "tax_type", "tax_type_cd", "end_dt", "utcc_yn")
_VALIDATE_KEEP_KEYS = ("valid", "valid_msg", "b_stt", "b_stt_cd", "tax_type")


def _default_http_post(
    url: str,
    *,
    json_body: dict[str, Any],
    timeout: int,
    params: dict[str, Any],
) -> requests.Response:
    """기본 HTTP POST(요청 흐름 seam). data.go.kr odcloud 는 JSON body POST 를 받고
    ``serviceKey`` 는 query string(``params``)으로 전달한다."""
    return requests.post(url, json=json_body, params=params, timeout=timeout)


class UnavailableBusinessVerificationAdapter(BusinessVerificationPort):
    """서비스키 미구성 시 쓰는 어댑터 — 외부 호출 없이 항상 unknown 을 돌려준다."""

    _UNAVAILABLE = VerificationResult(
        status=BusinessVerificationStatus.UNKNOWN.value,
        masked_payload={"reason": "verification_unavailable"},
        checked=False,
    )

    def check_status(self, business_number: str) -> VerificationResult:
        return self._UNAVAILABLE

    def check_authenticity(
        self,
        business_number: str,
        *,
        start_date: str,
        representative_name: str,
    ) -> VerificationResult:
        return self._UNAVAILABLE


class NtsBusinessVerificationAdapter(BusinessVerificationPort):
    """공공데이터포털 국세청 API 어댑터(키 구성 시에만 생성)."""

    def __init__(
        self,
        *,
        service_key: str,
        base_url: str,
        timeout_seconds: int,
        http_post: Optional[HttpPost] = None,
    ) -> None:
        self._service_key = service_key
        self._base_url = base_url.rstrip("/")
        self._timeout = max(1, int(timeout_seconds))
        self._http_post = http_post or _default_http_post

    # --- public port ----------------------------------------------------------

    def check_status(self, business_number: str) -> VerificationResult:
        digits = normalize_business_number(business_number)
        payload = self._post(STATUS_ENDPOINT, {"b_no": [digits]})
        if payload is None:
            return self._unknown()
        record = self._first_record(payload)
        if record is None:
            return self._unknown()
        status = map_status_code(record.get("b_stt_cd"), record.get("b_stt"))
        return VerificationResult(
            status=status.value,
            masked_payload=self._mask_record(record, _STATUS_KEEP_KEYS, digits),
            checked=True,
        )

    def check_authenticity(
        self,
        business_number: str,
        *,
        start_date: str,
        representative_name: str,
    ) -> VerificationResult:
        digits = normalize_business_number(business_number)
        body = {
            "businesses": [
                {
                    "b_no": digits,
                    "start_dt": start_date,
                    "p_nm": representative_name,
                }
            ]
        }
        payload = self._post(VALIDATE_ENDPOINT, body)
        if payload is None:
            return self._unknown()
        record = self._first_record(payload)
        if record is None:
            return self._unknown()
        status = map_validate_code(record.get("valid"))
        return VerificationResult(
            status=status.value,
            masked_payload=self._mask_record(record, _VALIDATE_KEEP_KEYS, digits),
            checked=True,
        )

    # --- helpers --------------------------------------------------------------

    def _post(self, endpoint: str, json_body: dict[str, Any]) -> Optional[dict[str, Any]]:
        """엔드포인트 POST 후 JSON dict 를 돌려준다. 실패는 raise 없이 None(베스트에포트).

        서비스키는 query string(``serviceKey``)으로만 전달하고 로그/payload 에 남기지
        않는다. 어떤 예외(네트워크/타임아웃/파싱)든 삼켜 None 을 반환해 호출부가 unknown
        으로 처리하게 한다 — 요청 흐름에 예외를 전파하지 않는다.
        """
        url = f"{self._base_url}/{endpoint}"
        try:
            response = self._http_post(
                url,
                json_body=json_body,
                timeout=self._timeout,
                # 서비스키는 params(query string)로만 붙여 body/로그에 남기지 않는다.
                params={"serviceKey": self._service_key},
            )
        except Exception:  # noqa: BLE001 - 네트워크/타임아웃 등 베스트에포트
            logger.warning("국세청 사업자 검증 호출 실패 (%s) — unknown 처리", endpoint)
            return None

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code and status_code >= 400:
                logger.warning(
                    "국세청 사업자 검증 응답 오류 (%s, status=%s) — unknown 처리",
                    endpoint,
                    status_code,
                )
                return None
            data = response.json()
        except Exception:  # noqa: BLE001 - JSON 파싱 실패 등 베스트에포트
            logger.warning("국세청 사업자 검증 응답 파싱 실패 (%s) — unknown 처리", endpoint)
            return None

        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def _first_record(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        record = data[0]
        return record if isinstance(record, dict) else None

    @staticmethod
    def _mask_record(
        record: dict[str, Any],
        keep_keys: tuple[str, ...],
        digits: str,
    ) -> dict[str, Any]:
        """응답에서 허용 상태 키만 골라 masked payload 를 만든다(원문 b_no 미포함)."""
        masked: dict[str, Any] = {"b_no_masked": mask_business_number(digits)}
        for key in keep_keys:
            if key in record and record[key] is not None:
                masked[key] = record[key]
        return masked

    @staticmethod
    def _unknown() -> VerificationResult:
        return VerificationResult(
            status=BusinessVerificationStatus.UNKNOWN.value,
            masked_payload={"reason": "verification_unavailable_error"},
            checked=False,
        )


def build_business_verification_port(
    *,
    http_post: Optional[HttpPost] = None,
) -> BusinessVerificationPort:
    """설정 기반 팩토리 — 서비스키가 있으면 NTS, 없으면 Unavailable(§4.7-2).

    키(``NTS_BUSINESS_VERIFICATION_SERVICE_KEY``)가 비어 있으면 외부 호출을 하지 않는
    :class:`UnavailableBusinessVerificationAdapter` 를 돌려준다(graceful no-key).
    """
    service_key = (settings.NTS_BUSINESS_VERIFICATION_SERVICE_KEY or "").strip()
    if not service_key:
        return UnavailableBusinessVerificationAdapter()
    return NtsBusinessVerificationAdapter(
        service_key=service_key,
        base_url=settings.NTS_BUSINESS_VERIFICATION_BASE_URL,
        timeout_seconds=settings.NTS_BUSINESS_VERIFICATION_TIMEOUT_SECONDS,
        http_post=http_post,
    )
