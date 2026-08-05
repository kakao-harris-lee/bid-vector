"""FieldContractObservation 집계기 + 라이브 수집 배선의 값-테이블/불변 테스트.

두 축을 고정한다:

- 집계기(순수): raw item 을 흘려보내면 위반/미지 필드를 종류별로 세고, #228 known-benign
  (배정예산 base WARN)은 별도 버킷(benign_suppressed)으로 분리해 errors/warns 를 부풀리지
  않는다. 입력 item 은 변경하지 않는다.
- 배선(수집 불변): collect_openapi_items 가 관찰기를 붙여도 수집된 items 는 토글 ON/OFF 에서
  동일하고, 위반은 metadata['field_contract_observation'] 로만 표면화된다. 토글 OFF 는
  관찰기를 아예 만들지 않아 관찰이 None 이다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.schemas.schemas import CrawlRequest
from app.services.koneps import field_contract
from app.services.koneps.collection import collect_openapi_items
from app.services.koneps.field_contract_observer import FieldContractObservation

_NOTICE_OP = "getBidPblancListInfoServc"


# ---------------------------------------------------------------------------
# 집계기 단위 (순수)
# ---------------------------------------------------------------------------


def test_observe_counts_unknown_field():
    obs = FieldContractObservation()
    obs.observe({"bidNtceNo": "x", "brandNewField": "v"}, operation=_NOTICE_OP)
    assert obs.items_checked == 1
    assert obs.unknown["brandNewField"] == 1
    assert obs.has_findings is True


def test_observe_benign_base_is_suppressed_not_warned():
    # service 공고: 배정예산만(기초금액 키 부재) -> YEGA_ONLY WARN 이지만 known-benign.
    obs = FieldContractObservation()
    obs.observe(
        {"bidNtceNo": "x", "bidNtceNm": "t", "asignBdgtAmt": "100000000"},
        operation=_NOTICE_OP,
    )
    assert obs.benign_suppressed["base_basis_yega_only"] == 1
    assert obs.warns == 0
    assert dict(obs.violations) == {}
    # 미지 필드도 없으니 findings 없음(요약 로그 트리거 안 함).
    assert obs.has_findings is False


def test_observe_real_yega_contamination_is_counted():
    # 예정가(presmptPrce)에서 base 해석 -> 진짜 #220 오염, 억제 대상 아님(WARN 카운트).
    obs = FieldContractObservation()
    obs.observe(
        {"bidNtceNo": "x", "bidNtceNm": "t", "presmptPrce": "100000000"},
        operation=_NOTICE_OP,
    )
    assert obs.violations["base_basis_yega_only"] == 1
    assert obs.warns == 1
    assert dict(obs.benign_suppressed) == {}
    assert obs.has_findings is True


def test_observe_true_base_key_row_is_clean():
    # 해석 순서 교정 후: 배정예산과 기초금액이 함께 와도 기초금액이 선택돼 위반 0.
    obs = FieldContractObservation()
    obs.observe(
        {
            "bidNtceNo": "x",
            "bidNtceNm": "t",
            "asignBdgtAmt": "100000000",
            "bssAmt": "90000000",
        },
        operation=_NOTICE_OP,
    )
    assert dict(obs.violations) == {}
    assert obs.errors == 0
    assert obs.has_findings is False


def test_observe_precedence_error_is_counted_under_order_drift(monkeypatch):
    # PRECEDENCE 는 상수 파생 드리프트 감지기 — 순서가 되돌아가면 관찰기가 ERROR 로 센다.
    monkeypatch.setattr(
        field_contract,
        "BASE_RESOLUTION_ORDER",
        ("asignBdgtAmt", "bdgtAmt", "presmptPrce", "presmptAmt")
        + field_contract.TRUE_BASE_KEYS,
    )
    obs = FieldContractObservation()
    obs.observe(
        {
            "bidNtceNo": "x",
            "bidNtceNm": "t",
            "asignBdgtAmt": "100000000",
            "bssAmt": "90000000",
        },
        operation=_NOTICE_OP,
    )
    assert obs.violations["base_basis_precedence"] == 1
    assert obs.errors == 1


def test_observe_identifier_int_break_is_error():
    obs = FieldContractObservation()
    obs.observe(
        {"bidNtceNo": "x", "bidNtceNm": "t", "bidNtceOrd": 0, "bssAmt": "90000000"},
        operation=_NOTICE_OP,
    )
    assert obs.violations["identifier_not_string"] == 1
    assert obs.errors == 1


def test_observe_does_not_mutate_item():
    item = {"bidNtceNo": "x", "brandNewField": "v", "bidNtceOrd": 0}
    snapshot = dict(item)
    FieldContractObservation().observe(item, operation=_NOTICE_OP)
    assert item == snapshot


def test_summary_and_summary_line_render():
    obs = FieldContractObservation()
    obs.observe({"bidNtceNo": "x", "brandNewField": "v"}, operation=_NOTICE_OP)
    summary = obs.summary()
    assert summary["items_checked"] == 1
    assert summary["unknown_fields"] == {"brandNewField": 1}
    for key in (
        "items_checked",
        "errors",
        "warns",
        "violations",
        "unknown_fields",
        "benign_suppressed",
    ):
        assert key in summary
    line = obs.summary_line()
    assert isinstance(line, str)
    assert "brandNewField" in line


def test_record_internal_error_counts_and_surfaces():
    # 관찰기 예외는 계약 위반(errors/warns)과 별개 축으로 세고 요약에 노출된다.
    obs = FieldContractObservation()
    obs.record_internal_error()
    obs.record_internal_error()
    assert obs.internal_errors == 2
    assert obs.errors == 0 and obs.warns == 0
    assert obs.has_findings is True  # 관찰기 오류만으로도 요약 로그 트리거
    assert obs.summary()["internal_errors"] == 2
    assert "internal_errors=2" in obs.summary_line()


# ---------------------------------------------------------------------------
# 라이브 수집 배선 (수집 불변)
# ---------------------------------------------------------------------------

_SERVICE_KEY = "TESTKEY"


def _setup(monkeypatch) -> None:
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", _SERVICE_KEY)
    monkeypatch.setattr(
        settings,
        "KONEPS_OPENAPI_BID_PUBLIC_INFO_URL",
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService",
    )
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_REQUEST_DELAY_SECONDS", 0)


def _payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "totalCount": len(items),
                "items": {"item": items} if items else None,
            },
        }
    }


def _request() -> CrawlRequest:
    return CrawlRequest(
        source="koneps-openapi",
        category="service",
        target_date="2025-01-01",
        execution_mode="live",
        max_items=500,
    )


def _run_collect(payload: dict[str, Any]) -> dict[str, Any]:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            return_value=(resp, "raw"),
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            return_value=payload,
        ),
    ):
        return collect_openapi_items(_request())


def _violating_item() -> dict[str, Any]:
    # int 차수(제로패딩 소실 ERROR) + 미지 필드 + 배정예산(benign) 이 섞인 행.
    return {
        "bidNtceNo": "20250101000001",
        "bidNtceNm": "테스트 공고",
        "bidNtceOrd": 0,  # int -> IDENTIFIER_NOT_STRING ERROR
        "asignBdgtAmt": "100000000",  # benign base
        "brandNewField": "x",  # 미지 필드
    }


def test_wiring_captures_findings_in_metadata(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", True)

    result = _run_collect(_payload([_violating_item()]))

    observation = result["metadata"]["field_contract_observation"]
    assert observation is not None
    assert observation["items_checked"] == 1
    assert observation["errors"] == 1
    assert observation["violations"]["identifier_not_string"] == 1
    assert observation["unknown_fields"]["brandNewField"] == 1
    # 배정예산 base 는 억제 버킷으로 분리(warns 부풀리지 않음).
    assert observation["benign_suppressed"]["base_basis_yega_only"] == 1
    assert observation["warns"] == 0


def test_wiring_does_not_change_collected_items(monkeypatch):
    """수집 불변 증명: 토글 ON/OFF 에서 수집된 items 가 완전히 동일하다."""
    _setup(monkeypatch)
    payload = _payload([_violating_item()])

    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", True)
    on = _run_collect(payload)
    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", False)
    off = _run_collect(payload)

    assert on["items"] == off["items"]
    assert len(on["items"]) == 1


def test_toggle_off_skips_observation(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", False)

    result = _run_collect(_payload([_violating_item()]))

    # OFF 면 관찰기를 만들지 않으므로 metadata 관찰이 None(무비용 통과).
    assert result["metadata"]["field_contract_observation"] is None


def _clean_item(n: int = 2) -> dict[str, Any]:
    return {
        "bidNtceNo": f"2025010100000{n}",
        "bidNtceNm": "정상 공고",
        "bidNtceOrd": "000",
        "bssAmt": "90000000",
    }


def test_wiring_observer_exception_is_swallowed_and_counted(monkeypatch):
    """관찰기가 매 item 예외를 던져도 수집은 정상(items 전량 보존) + internal_errors 집계.

    향후 validator 추가로 observe 가 던지더라도 라이브 수집이 관찰기 때문에 죽지 않음을
    고정한다. observe 를 예외로 모킹하면 hot-loop 의 try/except 가 삼키고
    record_internal_error 로 세야 한다.
    """
    _setup(monkeypatch)
    monkeypatch.setattr(settings, "KONEPS_FIELD_CONTRACT_LIVE_CHECK", True)
    payload = _payload([_violating_item(), _clean_item()])

    with patch.object(
        FieldContractObservation, "observe", side_effect=RuntimeError("boom")
    ):
        result = _run_collect(payload)

    # 관찰기 예외가 삼켜져 수집은 그대로 — 두 공고 전량 보존(전량 유실 방지).
    assert len(result["items"]) == 2
    observation = result["metadata"]["field_contract_observation"]
    assert observation is not None
    # item 마다 observe 가 던졌으니 internal_errors == item 수. 계약 위반 카운터는 0.
    assert observation["internal_errors"] == 2
    assert observation["errors"] == 0
    assert observation["warns"] == 0
    assert observation["violations"] == {}
