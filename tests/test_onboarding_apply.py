"""온보딩 확정값 부분 반영(apply) 서비스·엔드포인트 테스트.

``POST /api/v1/operator/onboarding-suggestions/apply`` 는 사용자가 검토·확정한
필드 값만 CompanyProfile/OperatorStrategy 에 부분 반영한다(설계 §3). 고정하는 것:

- **부분 업데이트**: accepted 필드만 갱신되고 나머지 프로필/전략 필드는 불변.
- **검증**: 알 수 없는 필드/타입/화이트리스트 위반은 422.
- **per-operator/synthetic 격리**: apply 는 요청 operator 자신의 행에만 쓴다 —
  operator A 의 apply 가 B/canonical 을 오염시키지 않는다.
- **멱등**: 같은 apply 재호출이 동일 결과.
- **round-trip**: 반영 후 GET 프로필/전략 조회로 값 확인.
"""

from __future__ import annotations

import pytest

from app.core.security import get_password_hash
from app.core.single_user import (
    ensure_operator_profile_for,
    ensure_operator_strategy_for,
)
from app.models.models import CompanyProfile, OperatorStrategy, User
from app.schemas.onboarding import OnboardingApplyField
from app.services.onboarding.apply import (
    APPLYABLE_FIELDS,
    ApplyDecision,
    OnboardingApplyError,
    apply_onboarding_decisions,
)

_APPLY_URL = "/api/v1/operator/onboarding-suggestions/apply"


def _make_operator(test_db, *, username: str, password: str = "password123") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=f"{username} Co",
        hashed_password=get_password_hash(password),
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _login(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- 선언 스펙 drift 가드 ----------------------------------------------------


def test_enum_matches_applyable_fields_spec():
    """요청 enum 집합과 서비스 스펙 키가 정확히 일치한다(단일 출처, 드리프트 방지)."""
    assert {field.value for field in OnboardingApplyField} == set(APPLYABLE_FIELDS)


# --- 순수 서비스: 검증/정규화 ------------------------------------------------
# 잘못된 값은 반영 루프 이전(검증 단계)에서 raise 되므로, 실제 세션을 넘겨도 DB 에는
# 아무 것도 써지지 않는다(검증 실패 케이스). 실제 public 함수를 그대로 태운다.


def test_service_unknown_field_raises(test_db):
    """스펙에 없는 필드는 OnboardingApplyError(→422)."""
    operator = _make_operator(test_db, username="operator")
    with pytest.raises(OnboardingApplyError) as exc:
        apply_onboarding_decisions(
            test_db,
            operator=operator,
            decisions=[ApplyDecision(field="nonexistent", value="x")],
        )
    assert exc.value.field == "nonexistent"
    assert test_db.query(CompanyProfile).count() == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("min_budget_estimate", ["nope"]),  # 숫자 필드에 리스트
        ("business_type", 123.0),  # 문자열 필드에 숫자
        ("license_codes", "not-a-list"),  # 리스트 필드에 문자열
        ("min_budget_estimate", True),  # bool 은 숫자로 취급하지 않음
    ],
)
def test_service_wrong_type_raises(test_db, field, value):
    """필드 종류에 맞지 않는 타입 → OnboardingApplyError."""
    operator = _make_operator(test_db, username="operator")
    with pytest.raises(OnboardingApplyError) as exc:
        apply_onboarding_decisions(
            test_db,
            operator=operator,
            decisions=[ApplyDecision(field=field, value=value)],
        )
    assert exc.value.field == field


def test_service_invalid_business_type_rejected(test_db):
    """허용 집합 밖 업무구분은 거부한다(화이트리스트)."""
    operator = _make_operator(test_db, username="operator")
    with pytest.raises(OnboardingApplyError) as exc:
        apply_onboarding_decisions(
            test_db,
            operator=operator,
            decisions=[ApplyDecision(field="business_type", value="not-a-type")],
        )
    assert exc.value.field == "business_type"


def test_service_negative_budget_rejected(test_db):
    """음수 예산은 거부한다."""
    operator = _make_operator(test_db, username="operator")
    with pytest.raises(OnboardingApplyError) as exc:
        apply_onboarding_decisions(
            test_db,
            operator=operator,
            decisions=[ApplyDecision(field="min_budget_estimate", value=-1.0)],
        )
    assert exc.value.field == "min_budget_estimate"


def test_service_business_type_alias_normalized(test_db):
    """확정 업무구분 별칭은 canonical 값으로 정규화해 저장한다(재사용 normalizer)."""
    operator = _make_operator(test_db, username="operator")
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[ApplyDecision(field="business_type", value="공사")],
    )
    assert result.applied[0].value == "construction"
    profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .one()
    )
    assert profile.business_type == "construction"


# --- 순수 서비스: per-operator / synthetic 격리 ------------------------------


def test_apply_scopes_to_target_operator_only(test_db):
    """operator A 의 apply 가 operator B 의 프로필/전략을 만들거나 바꾸지 않는다."""
    operator_a = _make_operator(test_db, username="operator-a")
    operator_b = _make_operator(test_db, username="operator-b")

    apply_onboarding_decisions(
        test_db,
        operator=operator_a,
        decisions=[
            ApplyDecision(field="business_type", value="construction"),
            ApplyDecision(field="focus_regions", value=["부산"]),
        ],
    )

    profile_a = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator_a.id)
        .one()
    )
    assert profile_a.business_type == "construction"
    # B 는 어떤 행도 생기지 않는다(격리).
    assert (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator_b.id)
        .first()
        is None
    )
    assert (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == operator_b.id)
        .first()
        is None
    )


def test_apply_synthetic_does_not_pollute_canonical(test_db):
    """synthetic operator 의 apply 가 canonical 프로필/전략을 오염시키지 않는다."""
    canonical = _make_operator(test_db, username="operator")
    synthetic = _make_operator(test_db, username="synthetic-sw-small-seoul")
    # canonical 의 기존 확정 상태를 심는다.
    canonical_profile = ensure_operator_profile_for(test_db, canonical)
    canonical_profile.business_type = "service"
    canonical_profile.license_codes = "엔지니어링"
    test_db.commit()

    apply_onboarding_decisions(
        test_db,
        operator=synthetic,
        decisions=[
            ApplyDecision(field="business_type", value="construction"),
            ApplyDecision(field="license_codes", value=["토목공사업"]),
        ],
    )

    test_db.refresh(canonical_profile)
    assert canonical_profile.business_type == "service"  # 불변
    assert canonical_profile.license_codes == "엔지니어링"  # 불변
    synthetic_profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == synthetic.id)
        .one()
    )
    assert synthetic_profile.business_type == "construction"
    assert synthetic_profile.license_codes == "토목공사업"


# --- 엔드포인트: 부분 반영 / round-trip --------------------------------------


def test_endpoint_applies_accepted_fields_only(client, test_db):
    """accepted 필드만 프로필/전략에 반영되고 응답이 갱신 요약을 낸다."""
    response = client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {"field": "business_type", "value": "construction"},
                {"field": "license_codes", "value": ["토목공사업", "항만및해안"]},
                {"field": "focus_categories", "value": ["construction"]},
                {"field": "min_budget_estimate", "value": 300000000},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    applied = {item["field"]: item for item in payload["applied"]}
    assert applied["business_type"]["target"] == "profile"
    assert applied["business_type"]["value"] == "construction"
    assert applied["license_codes"]["value"] == ["토목공사업", "항만및해안"]
    assert applied["focus_categories"]["target"] == "strategy"
    assert applied["min_budget_estimate"]["value"] == 300000000.0
    assert payload["ignored"] == []
    assert payload["current_operator_username"] == "operator"

    # round-trip: GET 프로필/전략에 반영된다.
    profile = client.get("/api/v1/operator/profile").json()
    assert profile["business_type"] == "construction"
    assert profile["license_codes"] == ["토목공사업", "항만및해안"]
    # 넘기지 않은 프로필 필드는 기본값 유지(부분 업데이트).
    assert profile["region_codes"] == []
    assert profile["annual_revenue"] == 0.0

    strategy = client.get("/api/v1/operator/strategy").json()
    assert strategy["focus_categories"] == ["construction"]
    assert strategy["min_budget_estimate"] == 300000000.0
    # 넘기지 않은 전략 필드는 불변.
    assert strategy["focus_regions"] == []
    assert strategy["max_budget_estimate"] == 0.0


def test_endpoint_partial_update_preserves_prior_fields(client, test_db):
    """apply 는 넘어온 필드만 갱신하고 기존 다른 필드를 지우지 않는다."""
    # 기존 프로필을 PUT 으로 완전 구성.
    seed = client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "service",
            "license_codes": ["엔지니어링"],
            "region_codes": ["부산광역시"],
            "annual_revenue": 1500000000.0,
            "total_awards": 7,
        },
    )
    assert seed.status_code == 200, seed.text

    # apply 로 license_codes 만 확정 갱신.
    response = client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "license_codes", "value": ["항만및해안"]}]},
    )
    assert response.status_code == 200, response.text

    profile = client.get("/api/v1/operator/profile").json()
    assert profile["license_codes"] == ["항만및해안"]  # 갱신됨
    # 나머지는 그대로.
    assert profile["business_type"] == "service"
    assert profile["region_codes"] == ["부산광역시"]
    assert profile["annual_revenue"] == 1500000000.0
    assert profile["total_awards"] == 7


def test_endpoint_empty_request_is_noop(client, test_db):
    """빈 결정 목록은 no-op — 프로필/전략 행을 만들지 않는다(PUT 규약 정합)."""
    response = client.post(_APPLY_URL, json={"decisions": []})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["applied"] == []
    assert payload["ignored"] == []
    assert test_db.query(CompanyProfile).count() == 0
    assert test_db.query(OperatorStrategy).count() == 0


def test_endpoint_idempotent(client, test_db):
    """같은 apply 재호출이 동일 응답 + 동일 저장 상태를 낸다."""
    body = {
        "decisions": [
            {"field": "business_type", "value": "construction"},
            {"field": "focus_regions", "value": ["부산"]},
        ]
    }
    first = client.post(_APPLY_URL, json=body)
    second = client.post(_APPLY_URL, json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()

    profiles = test_db.query(CompanyProfile).all()
    strategies = test_db.query(OperatorStrategy).all()
    assert len(profiles) == 1  # 재호출이 중복 행을 만들지 않는다.
    assert len(strategies) == 1
    assert profiles[0].business_type == "construction"
    assert strategies[0].focus_regions == "부산"


def test_endpoint_duplicate_field_last_wins_and_reports_ignored(client, test_db):
    """같은 필드가 중복되면 마지막 값이 적용되고 앞선 값은 무시로 보고된다."""
    response = client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {"field": "license_codes", "value": ["엔지니어링"]},
                {"field": "license_codes", "value": ["토목공사업"]},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    applied = [item for item in payload["applied"] if item["field"] == "license_codes"]
    assert len(applied) == 1
    assert applied[0]["value"] == ["토목공사업"]  # 마지막 값
    ignored_fields = [item["field"] for item in payload["ignored"]]
    assert ignored_fields == ["license_codes"]

    profile = client.get("/api/v1/operator/profile").json()
    assert profile["license_codes"] == ["토목공사업"]


# --- 엔드포인트: 검증 실패(422) ---------------------------------------------


def test_endpoint_unknown_field_returns_422(client, test_db):
    """스펙에 없는 필드명은 Pydantic enum 이 422 로 거른다."""
    response = client.post(
        _APPLY_URL, json={"decisions": [{"field": "nope", "value": "x"}]}
    )
    assert response.status_code == 422


def test_endpoint_wrong_value_type_returns_422(client, test_db):
    """숫자 필드에 리스트를 주면 422(서비스 검증)."""
    response = client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "min_budget_estimate", "value": ["x"]}]},
    )
    assert response.status_code == 422


def test_endpoint_invalid_business_type_returns_422(client, test_db):
    """허용 집합 밖 업무구분은 422."""
    response = client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "business_type", "value": "not-a-type"}]},
    )
    assert response.status_code == 422
    # 실패 시 프로필이 생성/변경되지 않는다.
    assert test_db.query(CompanyProfile).count() == 0


# --- 엔드포인트: per-operator 격리(HTTP) ------------------------------------


def test_endpoint_cross_operator_write_forbidden(client, test_db):
    """synthetic operator 가 다른 operator_id 로 apply 하면 403, 대상 프로필 불변."""
    canonical = _make_operator(test_db, username="operator")
    _make_operator(test_db, username="synthetic-sw-small-seoul")  # login 대상
    # canonical 기존 상태.
    canonical_profile = ensure_operator_profile_for(test_db, canonical)
    canonical_profile.business_type = "service"
    test_db.commit()

    headers = _login(client, "synthetic-sw-small-seoul")
    response = client.post(
        _APPLY_URL,
        params={"operator_id": canonical.id},
        headers=headers,
        json={"decisions": [{"field": "business_type", "value": "construction"}]},
    )
    assert response.status_code == 403

    test_db.refresh(canonical_profile)
    assert canonical_profile.business_type == "service"  # 오염 없음


def test_endpoint_self_scoped_write_targets_actor(client, test_db):
    """인증된 operator 는 자기 행에만 쓴다(operator_id 생략 시 actor 자신)."""
    synthetic = _make_operator(test_db, username="synthetic-sw-small-seoul")
    headers = _login(client, "synthetic-sw-small-seoul")

    response = client.post(
        _APPLY_URL,
        headers=headers,
        json={"decisions": [{"field": "focus_regions", "value": ["부산"]}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_operator_username"] == "synthetic-sw-small-seoul"

    strategy = ensure_operator_strategy_for(test_db, synthetic)
    assert strategy.focus_regions == "부산"
