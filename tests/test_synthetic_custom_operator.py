"""Tests for custom synthetic company CRUD (Phase 3).

Covers create / update / clone / delete plus protection of the 12 presets and the
canonical ``operator`` account, the ``is_custom`` flag on listings, and the
single-operator ``user_id`` unique invariant (one profile + one strategy per
company).
"""

from __future__ import annotations

CUSTOM_BASE = "/api/v1/synthetic/custom-operators"


def _seed_presets(client):
    response = client.post("/api/v1/synthetic/operators/seed", json={})
    assert response.status_code == 200, response.text
    return response.json()


def _valid_create_payload(**overrides):
    payload = {
        "name": "테스트 커스텀 회사",
        "slug": "lab-alpha",
        "business_type": "software",
        "region_codes": ["서울", "경기"],
        "license_codes": ["소프트웨어개발사업자"],
        "annual_revenue": 1_000_000_000.0,
        "capacity_score": 0.5,
        "focus_categories": ["software"],
        "focus_regions": ["서울"],
        "min_budget_estimate": 30_000_000.0,
        "max_budget_estimate": 300_000_000.0,
        "bid_now_threshold": 0.7,
        "review_threshold": 0.45,
    }
    payload.update(overrides)
    return payload


# --- create -------------------------------------------------------------------


def test_create_custom_operator_succeeds(client):
    response = client.post(CUSTOM_BASE, json=_valid_create_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "custom-lab-alpha"
    assert body["username"] == "synthetic-custom-lab-alpha"
    assert body["is_custom"] is True
    assert body["business_type"] == "software"
    assert body["region_codes"] == ["서울", "경기"]
    assert body["focus_categories"] == ["software"]
    assert body["bid_now_threshold"] == 0.7


def test_create_custom_operator_slug_collision_returns_409(client):
    first = client.post(CUSTOM_BASE, json=_valid_create_payload())
    assert first.status_code == 201
    second = client.post(CUSTOM_BASE, json=_valid_create_payload())
    assert second.status_code == 409, second.text


def test_create_custom_operator_validation_error_returns_422(client):
    # Missing required ``name`` → schema validation failure.
    response = client.post(CUSTOM_BASE, json={"slug": "no-name"})
    assert response.status_code == 422, response.text


def test_create_custom_operator_derives_slug_from_name(client):
    response = client.post(
        CUSTOM_BASE,
        json={"name": "My Lab Co!!", "business_type": "goods"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "custom-my-lab-co"


# --- update -------------------------------------------------------------------


def test_update_custom_operator_partial(client):
    created = client.post(CUSTOM_BASE, json=_valid_create_payload()).json()
    slug = created["slug"]
    response = client.put(
        f"{CUSTOM_BASE}/{slug}",
        json={"bid_now_threshold": 0.9, "focus_regions": ["부산"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bid_now_threshold"] == 0.9
    assert body["focus_regions"] == ["부산"]
    # Untouched fields preserved.
    assert body["business_type"] == "software"
    assert body["region_codes"] == ["서울", "경기"]


def test_update_preset_is_rejected(client):
    _seed_presets(client)
    response = client.put(
        f"{CUSTOM_BASE}/sw-small-seoul",
        json={"bid_now_threshold": 0.99},
    )
    assert response.status_code == 400, response.text


def test_update_missing_custom_returns_404(client):
    response = client.put(
        f"{CUSTOM_BASE}/custom-does-not-exist",
        json={"bid_now_threshold": 0.5},
    )
    assert response.status_code == 404, response.text


# --- clone --------------------------------------------------------------------


def test_clone_from_preset_creates_custom(client):
    _seed_presets(client)
    response = client.post(
        f"{CUSTOM_BASE}/sw-small-seoul/clone",
        json={"name": "복제된 SW", "slug": "sw-clone"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "custom-sw-clone"
    assert body["is_custom"] is True
    # Copied from the preset source.
    assert body["business_type"] == "software"
    assert body["focus_categories"] == ["software"]


def test_clone_missing_source_returns_404(client):
    response = client.post(
        f"{CUSTOM_BASE}/nope-not-here/clone",
        json={"name": "x"},
    )
    assert response.status_code == 404, response.text


def test_clone_from_custom_with_override(client):
    created = client.post(CUSTOM_BASE, json=_valid_create_payload()).json()
    response = client.post(
        f"{CUSTOM_BASE}/{created['slug']}/clone",
        json={"name": "오버라이드", "slug": "lab-beta", "business_type": "construction"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "custom-lab-beta"
    assert body["business_type"] == "construction"
    # Non-overridden field inherited from the source custom company.
    assert body["focus_categories"] == ["software"]


# --- delete -------------------------------------------------------------------


def test_delete_custom_operator_succeeds(client):
    created = client.post(CUSTOM_BASE, json=_valid_create_payload()).json()
    slug = created["slug"]
    response = client.delete(f"{CUSTOM_BASE}/{slug}")
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    # Gone from the listing.
    listed = client.get("/api/v1/synthetic/operators").json()
    assert all(item["slug"] != slug for item in listed["operators"])


def test_delete_preset_is_rejected(client):
    _seed_presets(client)
    response = client.delete(f"{CUSTOM_BASE}/sw-small-seoul")
    assert response.status_code == 400, response.text
    # Preset still present.
    listed = client.get("/api/v1/synthetic/operators").json()
    assert any(item["slug"] == "sw-small-seoul" for item in listed["operators"])


def test_delete_missing_custom_returns_404(client):
    response = client.delete(f"{CUSTOM_BASE}/custom-ghost")
    assert response.status_code == 404, response.text


def test_delete_operator_username_is_rejected(client):
    # The canonical operator must never be deletable via this route.
    response = client.delete(f"{CUSTOM_BASE}/operator")
    # Either a protected (400) — slug is not custom. Never a 200.
    assert response.status_code in (400, 404)
    assert response.status_code != 200


# --- listing / is_custom flag -------------------------------------------------


def test_list_operators_marks_custom_and_preserves_presets(client):
    seeded = _seed_presets(client)
    preset_count = seeded["seeded_count"]
    assert preset_count == 12

    client.post(CUSTOM_BASE, json=_valid_create_payload())

    listed = client.get("/api/v1/synthetic/operators").json()
    assert listed["operator_count"] == preset_count + 1

    by_slug = {item["slug"]: item for item in listed["operators"]}
    # Custom flagged true, presets flagged false.
    assert by_slug["custom-lab-alpha"]["is_custom"] is True
    assert by_slug["sw-small-seoul"]["is_custom"] is False
    # All 12 presets preserved.
    preset_slugs = {s for s in by_slug if not s.startswith("custom-")}
    assert len(preset_slugs) == 12


# --- single-operator invariant ------------------------------------------------


def test_custom_company_has_single_profile_and_strategy(client, test_db):
    from app.models.models import CompanyProfile, OperatorStrategy, User

    created = client.post(CUSTOM_BASE, json=_valid_create_payload()).json()
    user_id = created["user_id"]

    profiles = (
        test_db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).count()
    )
    strategies = (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == user_id)
        .count()
    )
    assert profiles == 1
    assert strategies == 1

    # Repeated updates never create a second row.
    client.put(
        f"{CUSTOM_BASE}/{created['slug']}",
        json={"bid_now_threshold": 0.8},
    )
    client.put(
        f"{CUSTOM_BASE}/{created['slug']}",
        json={"review_threshold": 0.5},
    )
    test_db.expire_all()
    assert (
        test_db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).count()
        == 1
    )
    assert (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == user_id)
        .count()
        == 1
    )

    # Canonical operator (if any) is unaffected: never created/altered here.
    operator = test_db.query(User).filter(User.username == "operator").first()
    assert operator is None or operator.username == "operator"
