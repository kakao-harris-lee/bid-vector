"""cohort 정체성 필드(협회 가입/기술부문) — 모델·스키마·프로필 API 캡처 테스트.

``CompanyProfile.association_memberships`` / ``tech_fields`` 는 첫 실수요 고객
(해양엔지니어링협회)의 핵심 cohort 속성이다. 이 슬라이스는 **캡처만** 한다 —
eligibility 게이팅/추천 반영은 후속(코드-only). 커버:

- 모델 기본값(미기재 → 빈 문자열, server_default 왕복)
- 저장 표현(콤마 구분 Text 다중값) ↔ 응답 표현(list[str]) 왕복
- OperatorProfileUpdate 스키마가 두 다중값 필드를 수용
- PUT/GET ``/operator/profile`` end-to-end 왕복 + 부분 업데이트 불변성
- 미기재 프로필은 두 필드를 빈 리스트로 노출(unknown/empty 중립)
"""

from __future__ import annotations

from app.core.single_user import join_multi_value_text, split_multi_value_text
from app.models.models import CompanyProfile, User
from app.schemas.schemas import OperatorProfileUpdate


# ---------------------------------------------------------------------------
# Model — defaults & storage round-trip
# ---------------------------------------------------------------------------


def test_company_profile_cohort_fields_default_to_empty(test_db):
    """미지정 시 두 cohort 필드는 빈 문자열(미기재)로 기본값이 잡힌다."""
    user = User(
        username="operator-cohort-default",
        email="cohort-default@example.com",
        hashed_password="x",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(user_id=user.id, business_type="technical-service")
    test_db.add(profile)
    test_db.commit()
    test_db.refresh(profile)

    assert profile.association_memberships == ""
    assert profile.tech_fields == ""
    # 저장 표현(Text) → 응답 표현(list[str]) 은 빈 리스트(중립).
    assert split_multi_value_text(profile.association_memberships) == []
    assert split_multi_value_text(profile.tech_fields) == []


def test_company_profile_cohort_fields_multi_value_round_trip(test_db):
    """다중값을 콤마 구분 Text 로 저장하고 list[str] 로 복원한다(헬퍼 재사용)."""
    user = User(
        username="operator-cohort-roundtrip",
        email="cohort-roundtrip@example.com",
        hashed_password="x",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="technical-service",
        association_memberships=join_multi_value_text(["엔지니어링협회"]),
        tech_fields=join_multi_value_text(["해양엔지니어링", "수로조사"]),
    )
    test_db.add(profile)
    test_db.commit()
    test_db.refresh(profile)

    assert profile.tech_fields == "해양엔지니어링, 수로조사"  # 저장 표현
    assert split_multi_value_text(profile.tech_fields) == ["해양엔지니어링", "수로조사"]
    assert split_multi_value_text(profile.association_memberships) == ["엔지니어링협회"]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_operator_profile_update_accepts_cohort_fields():
    """OperatorProfileUpdate 가 두 다중값 필드를 수용한다."""
    payload = OperatorProfileUpdate(
        association_memberships=["엔지니어링협회"],
        tech_fields=["해양엔지니어링"],
    )

    assert payload.association_memberships == ["엔지니어링협회"]
    assert payload.tech_fields == ["해양엔지니어링"]


def test_operator_profile_update_cohort_fields_default_none():
    """미지정 필드는 None(부분 업데이트에서 불변 신호)으로 남는다."""
    payload = OperatorProfileUpdate(business_type="technical-service")

    assert payload.association_memberships is None
    assert payload.tech_fields is None


# ---------------------------------------------------------------------------
# PUT / GET /operator/profile round-trip
# ---------------------------------------------------------------------------


def test_put_operator_profile_persists_cohort_fields(client):
    """PUT 후 GET 이 두 cohort 필드를 list[str] 로 end-to-end 노출한다."""
    put_response = client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "technical-service",
            "association_memberships": ["엔지니어링협회"],
            "tech_fields": ["해양엔지니어링", "항만및해안"],
        },
    )
    assert put_response.status_code == 200, put_response.text
    put_payload = put_response.json()
    assert put_payload["association_memberships"] == ["엔지니어링협회"]
    assert put_payload["tech_fields"] == ["해양엔지니어링", "항만및해안"]

    get_payload = client.get("/api/v1/operator/profile").json()
    assert get_payload["association_memberships"] == ["엔지니어링협회"]
    assert get_payload["tech_fields"] == ["해양엔지니어링", "항만및해안"]


def test_put_operator_profile_cohort_partial_update_preserves_others(client):
    """cohort 필드를 넘기지 않은 PUT 은 기존 cohort 값을 지우지 않는다(부분 업데이트)."""
    seed = client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "technical-service",
            "association_memberships": ["엔지니어링협회"],
            "tech_fields": ["해양엔지니어링"],
        },
    )
    assert seed.status_code == 200, seed.text

    # license_codes 만 갱신 — cohort 필드는 넘기지 않는다.
    follow = client.put(
        "/api/v1/operator/profile",
        json={"license_codes": ["엔지니어링사업(해양)"]},
    )
    assert follow.status_code == 200, follow.text

    profile = client.get("/api/v1/operator/profile").json()
    assert profile["license_codes"] == ["엔지니어링사업(해양)"]
    # cohort 필드는 이전 값 그대로(불변).
    assert profile["association_memberships"] == ["엔지니어링협회"]
    assert profile["tech_fields"] == ["해양엔지니어링"]


def test_operator_profile_cohort_fields_default_empty_when_unset(client):
    """새 프로필은 두 cohort 필드를 빈 리스트로 노출한다(unknown/empty 중립)."""
    payload = client.get("/api/v1/operator/profile").json()
    assert payload["association_memberships"] == []
    assert payload["tech_fields"] == []
