"""온보딩 감사 이력 조회(읽기 전용) 서비스·엔드포인트 테스트.

``GET /api/v1/operator/onboarding-suggestions/history`` 는 apply 가 남긴 append-only
감사 로그(``onboarding_suggestions``)를 최신순으로 조회한다. 고정하는 것:

- **최신순**: created_at DESC(동시각은 id DESC tie-break)로 반환.
- **필터**: field 정확 일치 / status(DecisionStatus) equality 필터.
- **per-operator/synthetic 격리**: operator A 는 B/canonical 의 감사 행을 못 본다.
- **페이지네이션**: limit/offset + 필터 후 total.
- **빈 결과**: 행이 없으면 빈 목록 + total 0.
- **엔드포인트**: 인증 스코프별 격리 + 잘못된 status 는 422.
"""

from __future__ import annotations

import json
from datetime import timedelta

from app.core.security import get_password_hash
from app.core.time import utc_now
from app.models.models import OnboardingSuggestion, User
from app.services.onboarding.history import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    list_onboarding_history,
)

_HISTORY_URL = "/api/v1/operator/onboarding-suggestions/history"
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


def _add_row(
    test_db,
    operator: User,
    *,
    field: str,
    value,
    status: str = "accepted",
    source=None,
    confidence=None,
    reason=None,
    created_at=None,
) -> OnboardingSuggestion:
    """감사 행을 직접 삽입한다(읽기 서비스는 apply 결과만 읽으므로 created_at 을 제어).

    ``value`` 는 apply 와 동일하게 JSON 텍스트로 직렬화해 원형 복원을 검증한다.
    """
    row = OnboardingSuggestion(
        user_id=operator.id,
        field=field,
        value=json.dumps(value, ensure_ascii=False),
        status=status,
        source=source,
        confidence=confidence,
        reason=reason,
        created_at=created_at or utc_now(),
    )
    test_db.add(row)
    test_db.commit()
    test_db.refresh(row)
    return row


# --- 서비스: 최신순 + value 원형 복원 ----------------------------------------


def test_history_newest_first_and_value_roundtrip(test_db):
    """created_at DESC 로 최신순 반환하고 value(str/float/list)를 원형 복원한다."""
    operator = _make_operator(test_db, username="operator")
    base = utc_now()
    _add_row(
        test_db, operator, field="business_type", value="construction",
        created_at=base - timedelta(minutes=2),
    )
    _add_row(
        test_db, operator, field="min_budget_estimate", value=1000.0,
        created_at=base - timedelta(minutes=1),
    )
    _add_row(
        test_db, operator, field="focus_regions", value=["부산", "울산"],
        created_at=base,
    )

    page = list_onboarding_history(test_db, operator=operator)
    assert page.total == 3
    assert [r.field for r in page.records] == [
        "focus_regions",  # 최신
        "min_budget_estimate",
        "business_type",  # 가장 오래됨
    ]
    # value 원형 복원(list/float/str).
    assert page.records[0].value == ["부산", "울산"]
    assert page.records[1].value == 1000.0
    assert page.records[2].value == "construction"


def test_history_same_timestamp_tiebreaks_by_id_desc(test_db):
    """동시각 행은 id DESC 로 결정적 newest-first 를 유지한다."""
    operator = _make_operator(test_db, username="operator")
    ts = utc_now()
    first = _add_row(test_db, operator, field="focus_regions", value=["부산"], created_at=ts)
    second = _add_row(test_db, operator, field="focus_regions", value=["울산"], created_at=ts)

    page = list_onboarding_history(test_db, operator=operator)
    assert [r.id for r in page.records] == [second.id, first.id]


# --- 서비스: 필터 --------------------------------------------------------------


def test_history_field_filter(test_db):
    """field 필터는 정확 일치 행만 반환한다."""
    operator = _make_operator(test_db, username="operator")
    _add_row(test_db, operator, field="business_type", value="construction")
    _add_row(test_db, operator, field="focus_regions", value=["부산"])
    _add_row(test_db, operator, field="focus_regions", value=["울산"])

    page = list_onboarding_history(test_db, operator=operator, field="focus_regions")
    assert page.total == 2
    assert {r.field for r in page.records} == {"focus_regions"}


def test_history_status_filter(test_db):
    """status 필터(DecisionStatus)는 해당 상태 행만 반환한다."""
    from app.services.onboarding.apply import DecisionStatus

    operator = _make_operator(test_db, username="operator")
    _add_row(test_db, operator, field="business_type", value="construction", status="accepted")
    _add_row(test_db, operator, field="license_codes", value=["엔지니어링"], status="rejected")
    _add_row(test_db, operator, field="focus_regions", value=["부산"], status="pending")

    page = list_onboarding_history(
        test_db, operator=operator, status=DecisionStatus.REJECTED
    )
    assert page.total == 1
    assert page.records[0].field == "license_codes"
    assert page.records[0].status == "rejected"


# --- 서비스: per-operator / synthetic 격리 -----------------------------------


def test_history_scoped_to_operator_only(test_db):
    """operator A 의 이력 조회에 operator B 의 행이 새지 않는다(total 도 스코프)."""
    operator_a = _make_operator(test_db, username="operator-a")
    operator_b = _make_operator(test_db, username="operator-b")
    _add_row(test_db, operator_a, field="focus_regions", value=["부산"])
    _add_row(test_db, operator_b, field="focus_regions", value=["서울"])
    _add_row(test_db, operator_b, field="business_type", value="construction")

    page_a = list_onboarding_history(test_db, operator=operator_a)
    assert page_a.total == 1
    assert all(True for _ in page_a.records)  # only A's row
    assert page_a.records[0].value == ["부산"]

    page_b = list_onboarding_history(test_db, operator=operator_b)
    assert page_b.total == 2


def test_history_synthetic_isolated_from_canonical(test_db):
    """synthetic operator 이력이 canonical 조회에 섞이지 않는다."""
    canonical = _make_operator(test_db, username="operator")
    synthetic = _make_operator(test_db, username="synthetic-sw-small-seoul")
    _add_row(test_db, synthetic, field="business_type", value="construction")

    assert list_onboarding_history(test_db, operator=canonical).total == 0
    assert list_onboarding_history(test_db, operator=synthetic).total == 1


# --- 서비스: 페이지네이션 + 빈 결과 ------------------------------------------


def test_history_pagination_limit_offset(test_db):
    """limit/offset 로 페이지를 잘라도 total 은 전체 수를 유지한다(최신순 연속)."""
    operator = _make_operator(test_db, username="operator")
    base = utc_now()
    for i in range(5):
        _add_row(
            test_db, operator, field="focus_regions", value=[f"region-{i}"],
            created_at=base + timedelta(seconds=i),
        )

    first = list_onboarding_history(test_db, operator=operator, limit=2, offset=0)
    assert first.total == 5
    assert [r.value for r in first.records] == [["region-4"], ["region-3"]]

    second = list_onboarding_history(test_db, operator=operator, limit=2, offset=2)
    assert second.total == 5
    assert [r.value for r in second.records] == [["region-2"], ["region-1"]]

    third = list_onboarding_history(test_db, operator=operator, limit=2, offset=4)
    assert [r.value for r in third.records] == [["region-0"]]


def test_history_limit_clamped_to_max(test_db):
    """cap 을 넘는 limit 은 MAX_HISTORY_LIMIT 로 정규화된다(직접 호출 방어)."""
    operator = _make_operator(test_db, username="operator")
    _add_row(test_db, operator, field="focus_regions", value=["부산"])
    # 과대 limit 이어도 예외 없이 정상 반환(행 수보다 크므로 전량).
    page = list_onboarding_history(
        test_db, operator=operator, limit=MAX_HISTORY_LIMIT + 500
    )
    assert page.total == 1
    assert len(page.records) == 1


def test_history_empty_result(test_db):
    """행이 없으면 빈 목록 + total 0."""
    operator = _make_operator(test_db, username="operator")
    page = list_onboarding_history(test_db, operator=operator)
    assert page.total == 0
    assert page.records == []


# --- 엔드포인트: 라우팅 + 응답 형태 ------------------------------------------


def test_endpoint_history_returns_recorded_decisions(client, test_db):
    """apply 로 기록된 결정을 GET history 가 최신순으로 반환한다(무인증=canonical)."""
    client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "business_type", "value": "construction"}]},
    )
    client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {"field": "license_codes", "value": ["엔지니어링"], "status": "rejected"}
            ]
        },
    )

    response = client.get(_HISTORY_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == DEFAULT_HISTORY_LIMIT
    assert payload["offset"] == 0
    # 나중에 apply 된 rejected 결정이 최신(첫 항목).
    assert payload["items"][0]["field"] == "license_codes"
    assert payload["items"][0]["status"] == "rejected"
    assert payload["items"][0]["value"] == ["엔지니어링"]
    assert payload["items"][1]["field"] == "business_type"
    assert payload["current_operator_id"] > 0


def test_endpoint_history_field_filter(client, test_db):
    """엔드포인트 field 필터가 해당 필드 행만 반환한다."""
    client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {"field": "business_type", "value": "construction"},
                {"field": "focus_regions", "value": ["부산"]},
            ]
        },
    )
    response = client.get(_HISTORY_URL, params={"field": "focus_regions"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["field"] == "focus_regions"


def test_endpoint_history_invalid_status_returns_422(client, test_db):
    """허용 집합 밖 status 는 Pydantic enum 이 422 로 거른다."""
    response = client.get(_HISTORY_URL, params={"status": "maybe"})
    assert response.status_code == 422


def test_endpoint_history_pagination_params(client, test_db):
    """limit/offset 쿼리로 페이지를 잘라도 total 은 전체를 보고한다."""
    for i in range(3):
        client.post(
            _APPLY_URL,
            json={"decisions": [{"field": "focus_regions", "value": [f"r{i}"]}]},
        )
    response = client.get(_HISTORY_URL, params={"limit": 2, "offset": 0})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["items"]) == 2
    assert payload["limit"] == 2


def test_endpoint_history_over_cap_limit_returns_422(client, test_db):
    """cap 초과 limit 은 라우터 Query 검증에서 422 로 거부된다(선언 상한)."""
    response = client.get(_HISTORY_URL, params={"limit": MAX_HISTORY_LIMIT + 1})
    assert response.status_code == 422


def test_endpoint_history_per_operator_isolation(client, test_db):
    """인증 operator 별로 자기 감사 이력만 조회된다(A 는 B 의 행을 못 본다)."""
    _make_operator(test_db, username="operator-a")
    _make_operator(test_db, username="operator-b")
    headers_a = _login(client, "operator-a")
    headers_b = _login(client, "operator-b")

    client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "business_type", "value": "construction"}]},
        headers=headers_a,
    )
    client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "focus_regions", "value": ["부산"]}]},
        headers=headers_b,
    )

    payload_a = client.get(_HISTORY_URL, headers=headers_a).json()
    assert payload_a["total"] == 1
    assert payload_a["items"][0]["field"] == "business_type"

    payload_b = client.get(_HISTORY_URL, headers=headers_b).json()
    assert payload_b["total"] == 1
    assert payload_b["items"][0]["field"] == "focus_regions"
