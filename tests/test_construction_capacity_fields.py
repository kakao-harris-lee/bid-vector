"""Tests for 시공능력평가액 / 도급한도 fields on the company profile.

Covers the new manual operator inputs:

* ``construction_capacity_amount`` (시공능력평가액) — when populated and the
  project category is ``construction``, it outranks ``annual_revenue`` /
  ``capacity_score`` as the canonical 도급가능규모 indicator inside
  ``NoticeClassifierService._assess_budget``.
* ``awarded_contract_limit`` (도급한도) — persisted only, no matcher hook yet.

These cover:
- model defaults (server_default=0 round-trips through SQLAlchemy)
- schema validation (``ge=0`` rejects negatives)
- PUT/GET ``/operator/profile`` round-trip
- classifier construction branch (STRONG / GOOD / BORDERLINE / FAIL)
- classifier non-construction category leaves legacy behaviour untouched
- classifier construction with ``construction_capacity_amount=0`` falls back
  to ``annual_revenue``
"""

from __future__ import annotations

import pytest

from app.models.models import CompanyProfile, Project, User
from app.schemas.schemas import OperatorProfileUpdate
from app.services.classifier import NoticeClassifierService


# ---------------------------------------------------------------------------
# Model — defaults & round-trip
# ---------------------------------------------------------------------------


def test_company_profile_construction_fields_default_to_zero(test_db):
    """New construction fields default to 0.0 when not specified explicitly."""
    user = User(
        username="operator-default-fields",
        email="default-fields@example.com",
        hashed_password="x",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(user_id=user.id, business_type="construction")
    test_db.add(profile)
    test_db.commit()
    test_db.refresh(profile)

    assert profile.construction_capacity_amount == 0.0
    assert profile.awarded_contract_limit == 0.0


def test_company_profile_construction_fields_round_trip(test_db):
    """The new construction fields persist their assigned values across a refresh."""
    user = User(
        username="operator-roundtrip",
        email="roundtrip@example.com",
        hashed_password="x",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    profile = CompanyProfile(
        user_id=user.id,
        business_type="construction",
        construction_capacity_amount=5_000_000_000.0,
        awarded_contract_limit=12_000_000_000.0,
    )
    test_db.add(profile)
    test_db.commit()
    test_db.refresh(profile)

    assert profile.construction_capacity_amount == pytest.approx(5_000_000_000.0)
    assert profile.awarded_contract_limit == pytest.approx(12_000_000_000.0)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_operator_profile_update_accepts_new_construction_fields():
    """OperatorProfileUpdate accepts and preserves the two new optional fields."""
    payload = OperatorProfileUpdate(
        construction_capacity_amount=3_500_000_000.0,
        awarded_contract_limit=9_000_000_000.0,
    )

    assert payload.construction_capacity_amount == pytest.approx(3_500_000_000.0)
    assert payload.awarded_contract_limit == pytest.approx(9_000_000_000.0)


@pytest.mark.parametrize(
    "field",
    ["construction_capacity_amount", "awarded_contract_limit"],
)
def test_operator_profile_update_rejects_negative_construction_fields(field):
    """Schema enforces ``ge=0`` for both new fields."""
    with pytest.raises(ValueError):
        OperatorProfileUpdate(**{field: -1.0})


# ---------------------------------------------------------------------------
# PUT / GET /operator/profile round-trip
# ---------------------------------------------------------------------------


def test_put_operator_profile_persists_construction_capacity_fields(client):
    """PUT then GET should surface the two new fields end-to-end."""
    put_response = client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "construction",
            "construction_capacity_amount": 7_500_000_000.0,
            "awarded_contract_limit": 15_000_000_000.0,
        },
    )
    assert put_response.status_code == 200
    put_payload = put_response.json()
    assert put_payload["construction_capacity_amount"] == pytest.approx(
        7_500_000_000.0
    )
    assert put_payload["awarded_contract_limit"] == pytest.approx(15_000_000_000.0)
    # Filling either of the new fields counts as "configured".
    assert put_payload["profile_configured"] is True

    get_response = client.get("/api/v1/operator/profile")
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["construction_capacity_amount"] == pytest.approx(
        7_500_000_000.0
    )
    assert get_payload["awarded_contract_limit"] == pytest.approx(15_000_000_000.0)


def test_operator_profile_defaults_zero_when_unset(client):
    """A brand-new operator profile reports the two new fields as 0.0."""
    response = client.get("/api/v1/operator/profile")
    assert response.status_code == 200
    payload = response.json()
    assert payload["construction_capacity_amount"] == 0.0
    assert payload["awarded_contract_limit"] == 0.0


# ---------------------------------------------------------------------------
# Classifier — construction-only 시공능력평가액 priority branch
# ---------------------------------------------------------------------------


def _construction_project(budget: float = 1_000_000_000.0) -> Project:
    return Project(
        title="아파트 신축 공사",
        description="아파트 신축 공사",
        requirements="시공능력평가액 확인 요망",
        budget_estimate=budget,
        category="construction",
    )


def _construction_profile(
    construction_capacity_amount: float,
    *,
    annual_revenue: float = 0.0,
    capacity_score: float = 0.0,
) -> CompanyProfile:
    return CompanyProfile(
        business_type="construction",
        license_codes="건축공사업",
        region_codes="전국",
        annual_revenue=annual_revenue,
        capacity_score=capacity_score,
        construction_capacity_amount=construction_capacity_amount,
        awarded_contract_limit=0.0,
        total_awards=0,
    )


def test_assess_budget_strong_when_capacity_ratio_at_least_three():
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(construction_capacity_amount=3_500_000_000.0)

    result = service._assess_budget(project, profile)

    assert result.passed is True
    assert result.penalty == 0.0
    assert result.score == NoticeClassifierService.BUDGET_STRONG_SCORE
    assert any("시공능력평가액" in reason for reason in result.reasons)


def test_assess_budget_good_when_capacity_ratio_at_least_one():
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(construction_capacity_amount=1_200_000_000.0)

    result = service._assess_budget(project, profile)

    assert result.passed is True
    assert result.penalty == 0.0
    assert result.score == NoticeClassifierService.BUDGET_GOOD_SCORE
    assert any("시공능력평가액" in reason for reason in result.reasons)


def test_assess_budget_borderline_when_capacity_ratio_at_least_zero_six():
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(construction_capacity_amount=700_000_000.0)

    result = service._assess_budget(project, profile)

    assert result.passed is True
    assert result.penalty == 0.0
    assert result.score == NoticeClassifierService.BUDGET_BORDERLINE_SCORE
    assert any("시공능력평가액" in reason for reason in result.reasons)


def test_assess_budget_fails_when_capacity_ratio_below_zero_six():
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(construction_capacity_amount=200_000_000.0)

    result = service._assess_budget(project, profile)

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == NoticeClassifierService.BUDGET_MISMATCH_PENALTY
    assert any("시공능력평가액" in reason for reason in result.reasons)


def test_assess_budget_capacity_outranks_annual_revenue_for_construction():
    """When 시공능력평가액 is filled, the annual_revenue path must not run.

    A construction profile with a huge annual_revenue (so the legacy path would
    return STRONG) but a small 시공능력평가액 must FAIL — proving capacity wins.
    """
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(
        construction_capacity_amount=200_000_000.0,
        annual_revenue=10_000_000_000.0,
    )

    result = service._assess_budget(project, profile)

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == NoticeClassifierService.BUDGET_MISMATCH_PENALTY
    assert any("시공능력평가액" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Classifier — fallback paths remain untouched
# ---------------------------------------------------------------------------


def test_assess_budget_falls_back_to_annual_revenue_when_capacity_unset():
    """construction_capacity_amount=0 → legacy annual_revenue path stays in effect."""
    service = NoticeClassifierService()
    project = _construction_project(budget=1_000_000_000.0)
    profile = _construction_profile(
        construction_capacity_amount=0.0,
        annual_revenue=4_000_000_000.0,
    )

    result = service._assess_budget(project, profile)

    assert result.passed is True
    assert result.penalty == 0.0
    assert result.score == NoticeClassifierService.BUDGET_STRONG_SCORE
    # Legacy path uses the 연매출 wording, not 시공능력평가액.
    assert any("연매출" in reason for reason in result.reasons)
    assert not any("시공능력평가액" in reason for reason in result.reasons)


def test_assess_budget_non_construction_ignores_construction_capacity():
    """Even when 시공능력평가액 is populated, software/service notices stay on the legacy path."""
    service = NoticeClassifierService()
    project = Project(
        title="AI SW 통합 개발",
        description="AI SW 통합 개발",
        requirements="",
        budget_estimate=1_000_000_000.0,
        category="software",
    )
    profile = CompanyProfile(
        business_type="software",
        license_codes="SW001",
        region_codes="전국",
        annual_revenue=4_000_000_000.0,
        capacity_score=0.85,
        construction_capacity_amount=10_000_000_000.0,  # populated but irrelevant
        awarded_contract_limit=0.0,
        total_awards=3,
    )

    result = service._assess_budget(project, profile)

    assert result.passed is True
    assert result.penalty == 0.0
    # Non-construction notice must NOT mention 시공능력평가액.
    assert not any("시공능력평가액" in reason for reason in result.reasons)
    assert any("연매출" in reason for reason in result.reasons)
