"""Tests for 국세청 사업자번호 검증(상태조회/진위확인) — masked·hash·graceful no-key.

핵심 불변식(설계 §비기능, CLAUDE.md §8) 검증:

- 순수 helper: ``hash_business_number`` 결정적 + pepper 별 상이, ``mask_business_number``
  마스킹(엣지: 짧은/비정상/빈 값), ``is_valid_business_number`` 10자리.
- Unavailable 어댑터: unknown 반환 + **HTTP 호출 0회**(requests.post patch 후 not called).
- NTS 어댑터: 상태조회 코드→상태(01 계속→verified / 03 폐업→inactive), 진위확인
  valid→상태(01→verified / 02→mismatch) — 가짜 응답 주입(실 API 미접촉). HTTP/파싱 실패는
  raise 없이 unknown.
- 팩토리: 키 없으면 Unavailable, 있으면 NTS.
- 엔드포인트: 키 없으면 200 + status unknown, 프로필 갱신, 응답/저장 어디에도 원문 번호·
  서비스키 없음(hash != raw, payload masked), per-operator 스코프(자기 프로필만 write).
- 마이그레이션: down_revision 스탬프 후 이 리비전을 populated company_profiles 에 적용/롤백.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

import app.services.verification.nts_adapter as nts_mod
from app.core.security import get_password_hash
from app.core.single_user import ensure_operator_account
from app.models.models import CompanyProfile, User
from app.services.verification.hashing import (
    hash_business_number,
    is_valid_business_number,
    mask_business_number,
    normalize_business_number,
)
from app.services.verification.nts_adapter import (
    NtsBusinessVerificationAdapter,
    UnavailableBusinessVerificationAdapter,
    build_business_verification_port,
)

RAW_NUMBER = "1234567890"
MASKED_NUMBER = "123-45-*****"
ENDPOINT = "/api/v1/operator/business-verification"


# --- fakes --------------------------------------------------------------------


class _FakeResponse:
    """가짜 HTTP 응답 — 실 API 미접촉. payload 가 Exception 이면 .json() 이 raise 한다."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _recording_http_post(response: _FakeResponse):
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json_body: dict, timeout: int, params: dict) -> _FakeResponse:
        calls.append({"url": url, "json_body": json_body, "timeout": timeout, "params": params})
        return response

    return fake_post, calls


def _nts_adapter(http_post) -> NtsBusinessVerificationAdapter:
    return NtsBusinessVerificationAdapter(
        service_key="SECRET-KEY",
        base_url="https://api.odcloud.kr/api/nts-businessman/v1",
        timeout_seconds=10,
        http_post=http_post,
    )


# --- pure helpers -------------------------------------------------------------


def test_normalize_strips_non_digits():
    assert normalize_business_number("123-45-67890") == RAW_NUMBER
    assert normalize_business_number("  123 45 67890 ") == RAW_NUMBER
    assert normalize_business_number(None) == ""
    assert normalize_business_number("") == ""


def test_hash_is_deterministic_and_pepper_sensitive():
    h1 = hash_business_number(RAW_NUMBER, "pepper-a")
    h2 = hash_business_number("123-45-67890", "pepper-a")  # normalized → same digits
    h3 = hash_business_number(RAW_NUMBER, "pepper-b")
    # 결정적: 같은 번호/같은 pepper → 같은 해시(하이픈 유무 무관).
    assert h1 == h2
    # pepper 가 다르면 해시가 달라진다.
    assert h1 != h3
    # 64자 hex, 원문을 복원할 수 없다(원문 부분문자열 미포함).
    assert len(h1) == 64
    assert RAW_NUMBER not in h1
    # 빈 pepper 도 유효한(결정적) 키.
    assert hash_business_number(RAW_NUMBER, "") == hash_business_number(RAW_NUMBER, "")


def test_mask_business_number_edges():
    assert mask_business_number(RAW_NUMBER) == MASKED_NUMBER
    assert mask_business_number("123-45-67890") == MASKED_NUMBER
    # 짧은/비정상 — 뒤 5자리는 절대 노출하지 않는다.
    assert mask_business_number("123") == "123-*****"
    assert mask_business_number("12") == "12-*****"
    assert mask_business_number("12345") == "123-45-*****"
    assert mask_business_number("") == "*****"
    assert mask_business_number(None) == "*****"


def test_is_valid_business_number():
    assert is_valid_business_number(RAW_NUMBER) is True
    assert is_valid_business_number("123-45-67890") is True
    assert is_valid_business_number("12345") is False
    assert is_valid_business_number("123456789012") is False
    assert is_valid_business_number(None) is False


# --- Unavailable adapter (no key → no HTTP) -----------------------------------


def test_unavailable_adapter_returns_unknown_and_makes_no_http_call(monkeypatch):
    posted: list[Any] = []
    monkeypatch.setattr(nts_mod.requests, "post", lambda *a, **k: posted.append((a, k)))

    adapter = UnavailableBusinessVerificationAdapter()
    status_result = adapter.check_status(RAW_NUMBER)
    auth_result = adapter.check_authenticity(
        RAW_NUMBER, start_date="20200101", representative_name="홍길동"
    )

    for result in (status_result, auth_result):
        assert result.status == "unknown"
        assert result.checked is False
        assert result.masked_payload == {"reason": "verification_unavailable"}
    # 외부 HTTP 를 한 번도 호출하지 않는다.
    assert posted == []


# --- NTS adapter: status mapping ----------------------------------------------


def test_nts_status_active_maps_verified():
    payload = {
        "data": [
            {"b_no": RAW_NUMBER, "b_stt": "계속사업자", "b_stt_cd": "01", "tax_type": "부가가치세 일반과세자"}
        ]
    }
    fake_post, calls = _recording_http_post(_FakeResponse(payload))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)

    assert result.status == "verified"
    assert result.checked is True
    # masked payload 는 masked b_no 만 담고 원문 b_no 를 담지 않는다.
    assert result.masked_payload["b_no_masked"] == MASKED_NUMBER
    assert "b_no" not in result.masked_payload
    assert result.masked_payload["b_stt_cd"] == "01"
    # 엔드포인트/요청 형태 확인 + 서비스키는 params 로만 전달(body 미포함).
    assert calls[0]["url"].endswith("/status")
    assert calls[0]["json_body"] == {"b_no": [RAW_NUMBER]}
    assert calls[0]["params"] == {"serviceKey": "SECRET-KEY"}


def test_nts_status_closed_maps_inactive():
    payload = {"data": [{"b_no": RAW_NUMBER, "b_stt": "폐업자", "b_stt_cd": "03"}]}
    fake_post, _ = _recording_http_post(_FakeResponse(payload))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)
    assert result.status == "inactive"
    assert result.checked is True


def test_nts_status_text_fallback_when_code_missing():
    payload = {"data": [{"b_no": RAW_NUMBER, "b_stt": "휴업자", "b_stt_cd": ""}]}
    fake_post, _ = _recording_http_post(_FakeResponse(payload))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)
    assert result.status == "inactive"


# --- NTS adapter: authenticity mapping ----------------------------------------


def test_nts_validate_match_maps_verified_and_drops_free_text():
    # valid_msg 는 비정형 free-text — odcloud 가 원문 사업자번호를 에코할 여지가 있어
    # masked payload 에 담지 않는다(구조화 코드만 보존). 여기서 raw 번호를 심어 확인한다.
    payload = {
        "data": [
            {"b_no": RAW_NUMBER, "valid": "01", "valid_msg": f"확인 {RAW_NUMBER}"}
        ]
    }
    fake_post, calls = _recording_http_post(_FakeResponse(payload))
    result = _nts_adapter(fake_post).check_authenticity(
        RAW_NUMBER, start_date="20200101", representative_name="홍길동"
    )
    assert result.status == "verified"
    assert result.checked is True
    assert calls[0]["url"].endswith("/validate")
    business = calls[0]["json_body"]["businesses"][0]
    assert business == {"b_no": RAW_NUMBER, "start_dt": "20200101", "p_nm": "홍길동"}
    assert "b_no" not in result.masked_payload
    assert result.masked_payload["b_no_masked"] == MASKED_NUMBER
    # valid_msg 는 드롭되어 원문 번호가 payload 어디에도 남지 않는다.
    assert "valid_msg" not in result.masked_payload
    assert RAW_NUMBER not in json.dumps(result.masked_payload, ensure_ascii=False)


def test_nts_validate_mismatch_maps_mismatch():
    payload = {"data": [{"b_no": RAW_NUMBER, "valid": "02"}]}
    fake_post, _ = _recording_http_post(_FakeResponse(payload))
    result = _nts_adapter(fake_post).check_authenticity(
        RAW_NUMBER, start_date="20200101", representative_name="김철수"
    )
    assert result.status == "mismatch"


# --- NTS adapter: best-effort failure → unknown -------------------------------


def test_nts_http_exception_maps_unknown():
    def boom(url, *, json_body, timeout, params):
        raise RuntimeError("network down")

    result = _nts_adapter(boom).check_status(RAW_NUMBER)
    assert result.status == "unknown"
    assert result.checked is False


def test_nts_parse_failure_maps_unknown():
    fake_post, _ = _recording_http_post(_FakeResponse(ValueError("not json")))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)
    assert result.status == "unknown"
    assert result.checked is False


def test_nts_http_error_status_maps_unknown():
    fake_post, _ = _recording_http_post(_FakeResponse({"data": []}, status_code=500))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)
    assert result.status == "unknown"
    assert result.checked is False


def test_nts_empty_data_maps_unknown():
    fake_post, _ = _recording_http_post(_FakeResponse({"data": []}))
    result = _nts_adapter(fake_post).check_status(RAW_NUMBER)
    assert result.status == "unknown"
    assert result.checked is False


# --- factory ------------------------------------------------------------------


def test_factory_returns_unavailable_when_key_empty(monkeypatch):
    monkeypatch.setattr(nts_mod.settings, "NTS_BUSINESS_VERIFICATION_SERVICE_KEY", "")
    assert isinstance(
        build_business_verification_port(), UnavailableBusinessVerificationAdapter
    )


def test_factory_returns_nts_when_key_set(monkeypatch):
    monkeypatch.setattr(
        nts_mod.settings, "NTS_BUSINESS_VERIFICATION_SERVICE_KEY", "SOME-KEY"
    )
    assert isinstance(
        build_business_verification_port(), NtsBusinessVerificationAdapter
    )


# --- endpoint: graceful no-key ------------------------------------------------


def test_endpoint_no_key_returns_unknown_and_persists_masked(client, test_db, monkeypatch):
    # 키 미구성 보장 + 외부 HTTP 가 절대 호출되지 않음을 강제.
    monkeypatch.setattr(nts_mod.settings, "NTS_BUSINESS_VERIFICATION_SERVICE_KEY", "")
    posted: list[Any] = []
    monkeypatch.setattr(nts_mod.requests, "post", lambda *a, **k: posted.append((a, k)))

    response = client.post(ENDPOINT, json={"business_number": "123-45-67890"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert body["masked_number"] == MASKED_NUMBER
    assert body["verified_at"] is None
    assert "검증 미구성" in body["detail"]
    # 외부 호출 0회.
    assert posted == []
    # 응답 어디에도 원문 번호가 없다(뒤 5자리 포함).
    assert RAW_NUMBER not in response.text
    assert "67890" not in response.text

    # 저장된 행: 해시만, 원문 없음, 상태 unknown, payload masked, verified_at None.
    operator = ensure_operator_account(test_db)
    profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .first()
    )
    assert profile is not None
    assert profile.business_registration_number_hash
    assert profile.business_registration_number_hash != RAW_NUMBER
    assert RAW_NUMBER not in profile.business_registration_number_hash
    assert profile.business_verification_status == "unknown"
    payload = json.loads(profile.business_verification_payload)
    assert payload == {"reason": "verification_unavailable"}
    assert RAW_NUMBER not in (profile.business_verification_payload or "")
    assert profile.business_verified_at is None
    # 해시는 pepper HMAC — bare sha256 과 다르다.
    assert profile.business_registration_number_hash == hash_business_number(
        RAW_NUMBER, nts_mod.settings.BUSINESS_NUMBER_HASH_PEPPER
    )


def test_endpoint_keyed_path_does_not_leak_raw_number(client, test_db, monkeypatch):
    """키 구성된 KEYED 경로를 실제 팩토리/NTS 어댑터/_default_http_post 로 태우되,
    requests.post 만 가짜 odcloud 응답으로 대체한다. 응답 data 에 원문 b_no(10자리)를
    일부러 심어, 엔드포인트 응답과 영속화 어디에도 원문이 새지 않음을 end-to-end 로 증명한다.
    """
    monkeypatch.setattr(
        nts_mod.settings, "NTS_BUSINESS_VERIFICATION_SERVICE_KEY", "FAKE-KEY"
    )
    # 원문 10자리를 여러 필드(정형 b_no + 비정형 valid_msg)에 심는다 — 새면 안 된다.
    leaky_payload = {
        "data": [
            {
                "b_no": RAW_NUMBER,
                "b_stt": "계속사업자",
                "b_stt_cd": "01",
                "tax_type": "부가가치세 일반과세자",
                "valid_msg": f"확인 {RAW_NUMBER}",
            }
        ]
    }

    def fake_requests_post(url, *args, **kwargs):
        return _FakeResponse(leaky_payload)

    monkeypatch.setattr(nts_mod.requests, "post", fake_requests_post)

    response = client.post(ENDPOINT, json={"business_number": "123-45-67890"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "verified"
    assert body["masked_number"] == MASKED_NUMBER
    assert body["verified_at"] is not None
    # (a) 응답 본문에 원문 번호/뒤 5자리 없음.
    assert RAW_NUMBER not in response.text
    assert "67890" not in response.text

    # (b) 영속화된 프로필: 해시 + masked 만, 원문은 어떤 컬럼에도 없음.
    operator = ensure_operator_account(test_db)
    profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .first()
    )
    assert profile is not None
    assert profile.business_verification_status == "verified"
    assert profile.business_verified_at is not None
    assert profile.business_registration_number_hash == hash_business_number(
        RAW_NUMBER, nts_mod.settings.BUSINESS_NUMBER_HASH_PEPPER
    )
    # 해시/payload 어떤 컬럼에도 원문 10자리가 없다(해시는 일방향 HMAC — 원문 미복원).
    for value in (
        profile.business_registration_number_hash,
        profile.business_verification_payload,
    ):
        assert RAW_NUMBER not in (value or "")
    # payload 는 masked 만 담고 원문 뒤 5자리도 없다(hex 해시엔 우연 조각이 낄 수 있어 제외).
    assert "67890" not in (profile.business_verification_payload or "")
    payload = json.loads(profile.business_verification_payload)
    assert payload["b_no_masked"] == MASKED_NUMBER
    assert "b_no" not in payload
    assert "valid_msg" not in payload


def test_endpoint_rejects_invalid_number(client):
    response = client.post(ENDPOINT, json={"business_number": "12345"})
    assert response.status_code == 422


# --- endpoint: per-operator scoping -------------------------------------------


def test_endpoint_is_per_operator_scoped(client, test_db, monkeypatch):
    monkeypatch.setattr(nts_mod.settings, "NTS_BUSINESS_VERIFICATION_SERVICE_KEY", "")

    canonical = ensure_operator_account(test_db)
    other = User(
        username="synthetic-scope-x",
        email="scope-x@example.com",
        hashed_password=get_password_hash("x"),
        is_active=True,
    )
    test_db.add(other)
    test_db.commit()
    test_db.refresh(other)

    # cross-operator write(다른 operator_id) → 403.
    forbidden = client.post(
        f"{ENDPOINT}?operator_id={other.id}", json={"business_number": RAW_NUMBER}
    )
    assert forbidden.status_code == 403

    # self write(operator_id 미지정) → canonical 프로필만 갱신.
    ok = client.post(ENDPOINT, json={"business_number": RAW_NUMBER})
    assert ok.status_code == 200

    canon_profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == canonical.id)
        .first()
    )
    assert canon_profile is not None
    assert canon_profile.business_registration_number_hash

    other_profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == other.id)
        .first()
    )
    # 다른 operator 의 프로필은 생성/변경되지 않는다.
    assert other_profile is None or other_profile.business_registration_number_hash is None


# --- migration: additive columns on a populated table -------------------------


def test_migration_applies_and_rolls_back_on_populated_table(tmp_path, monkeypatch):
    """down_revision 스탬프 후 이 리비전만 populated company_profiles 에 적용/롤백.

    전체 체인은 sqlite 에서 빌드되지 않으므로(과거 pgvector 리비전) 이 리비전을 격리
    검증한다: 구 스키마 company_profiles + 기존 행을 만들고, alembic 을 down_revision 에
    stamp 한 뒤 upgrade → 네 컬럼/인덱스 존재 + 기존 행 status 기본값 'unverified',
    downgrade → 컬럼 제거를 확인한다.
    """
    from alembic import command

    from app.core.config import settings as app_settings
    from tests.support.alembic_config import make_alembic_config

    db_path = tmp_path / "verify_mig.db"
    url = f"sqlite:///{db_path}"

    # 구 스키마 + populated row(마이그레이션 이전 상태를 모사).
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE company_profiles ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, business_type VARCHAR(50))"
        )
        conn.exec_driver_sql(
            "INSERT INTO company_profiles (id, user_id, business_type) "
            "VALUES (1, 1, 'service')"
        )

    # env.py 는 settings.DATABASE_URL 로 sqlalchemy.url 을 덮으므로 그것을 스크래치로 지정.
    monkeypatch.setattr(app_settings, "DATABASE_URL", url)
    # ``Config("alembic.ini")`` 대신 파일 없는 Config 를 쓴다 — ini 를 읽으면 env.py 가
    # ``fileConfig`` 로 전역 로깅을 재설정하기 때문이다(공용 빌더/회귀 가드는
    # ``tests/support/alembic_config.py``, ``tests/test_alembic_logging_isolation.py``).
    cfg = make_alembic_config()
    command.stamp(cfg, "d8b3f0a1c9e2")
    command.upgrade(cfg, "a1f4c8e7b2d9")

    inspector = sa.inspect(sa.create_engine(url))
    cols = {c["name"] for c in inspector.get_columns("company_profiles")}
    assert {
        "business_registration_number_hash",
        "business_verification_status",
        "business_verification_payload",
        "business_verified_at",
    } <= cols
    index_names = {ix["name"] for ix in inspector.get_indexes("company_profiles")}
    assert "ix_company_profiles_business_registration_number_hash" in index_names

    # 기존 행은 보존되고 status 는 server_default 'unverified'.
    with sa.create_engine(url).connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT business_verification_status, business_registration_number_hash "
            "FROM company_profiles WHERE id = 1"
        ).first()
    assert row[0] == "unverified"
    assert row[1] is None

    # 롤백 → 컬럼 제거.
    command.downgrade(cfg, "d8b3f0a1c9e2")
    inspector2 = sa.inspect(sa.create_engine(url))
    cols2 = {c["name"] for c in inspector2.get_columns("company_profiles")}
    assert "business_registration_number_hash" not in cols2
    assert "business_verification_status" not in cols2
