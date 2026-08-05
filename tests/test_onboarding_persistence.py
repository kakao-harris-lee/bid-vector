"""온보딩 결정 감사(persistence) 슬라이스 테스트.

``POST /api/v1/operator/onboarding-suggestions/apply`` 는 이제 사용자의 모든 결정을
``onboarding_suggestions`` 감사 로그에 append-only 로 남기고(설계 §3 "수정하거나 거부한
후보도 저장해 다음 온보딩 규칙 개선에 사용"), accepted/modified 만 프로필/전략에
반영한다. 고정하는 것:

- **마이그레이션 왕복**: baseline 위에서 ``alembic upgrade head`` 가 테이블을 만들고
  ``downgrade`` 가 제거한다(down_revision=c9e4a7b1f2d0).
- **기록**: apply 시 각 결정이 status/source/confidence/reason 과 함께 기록된다.
- **기록/반영 분리**: rejected/pending 은 기록만, accepted/modified 만 반영.
- **하위호환**: status 미전달(현재 프론트) → accepted 로 기록 + 반영.
- **per-operator/synthetic 격리**: A 의 감사가 B/canonical 에 섞이지 않는다.
- **append-only**: 재적용 시 행이 증가한다(멱등 강제 없음).
- **원자성**: accepted 값 검증 실패는 감사에도 아무 것도 남기지 않는다.
"""

from __future__ import annotations

import json

import app.core.config as app_config
import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from app.core.security import get_password_hash
from app.models import models  # noqa: F401  -- registers all tables on Base.metadata
from app.models.models import (
    CompanyProfile,
    OnboardingSuggestion,
    OperatorStrategy,
    User,
)
from app.services.onboarding.apply import (
    ApplyDecision,
    DecisionStatus,
    OnboardingApplyError,
    apply_onboarding_decisions,
)
from tests.support.alembic_config import make_alembic_config
from tests.support.schema_baseline import create_premigration_baseline

_APPLY_URL = "/api/v1/operator/onboarding-suggestions/apply"
_TABLE = "onboarding_suggestions"
_REVISION = "d8b3f0a1c9e2"
_DOWN_REVISION = "c9e4a7b1f2d0"
_EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "field",
    "value",
    "status",
    "source",
    "confidence",
    "reason",
    "created_at",
}


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


def _rows_for(test_db, operator: User) -> list[OnboardingSuggestion]:
    return (
        test_db.query(OnboardingSuggestion)
        .filter(OnboardingSuggestion.user_id == operator.id)
        .order_by(OnboardingSuggestion.id.asc())
        .all()
    )


# --- 마이그레이션 왕복 --------------------------------------------------------


def _build_baseline_engine(url: str):
    """모델 스키마(마이그레이션 소유 테이블/컬럼 제외)를 sqlite 에 만든 엔진을 낸다.

    baseline 재구성은 ``tests/support/schema_baseline.py`` 한 벌을 쓴다(스키마 드리프트
    가드·Postgres 티어와 동일). 세 가드가 같은 전제 위에서 "마이그레이션이 실제로
    만드는 것"을 보게 하려는 것이다.
    """
    engine = create_engine(url)
    create_premigration_baseline(engine)
    return engine


def test_migration_round_trip(tmp_path):
    """upgrade head 가 감사 테이블을 만들고 downgrade 가 원복한다(단일 head 왕복)."""
    url = f"sqlite:///{tmp_path / 'onboarding_mig.db'}"
    original_url = app_config.settings.DATABASE_URL
    engine = _build_baseline_engine(url)
    try:
        # baseline 에는 아직 감사 테이블이 없다(마이그레이션 소유).
        assert _TABLE not in inspect(engine).get_table_names()

        app_config.settings.DATABASE_URL = url
        # 파일 없는 Config — ini 를 읽으면 env.py 가 전역 로깅을 재설정한다.
        cfg = make_alembic_config()
        command.upgrade(cfg, "head")

        inspector = inspect(engine)
        assert _TABLE in inspector.get_table_names()
        assert {c["name"] for c in inspector.get_columns(_TABLE)} == _EXPECTED_COLUMNS
        # user_id 인덱스(per-operator 스코프 조회)가 생성된다.
        index_columns = {
            tuple(ix["column_names"]) for ix in inspector.get_indexes(_TABLE)
        }
        assert ("user_id",) in index_columns

        command.downgrade(cfg, _DOWN_REVISION)
        assert _TABLE not in inspect(create_engine(url)).get_table_names()
    finally:
        app_config.settings.DATABASE_URL = original_url
        engine.dispose()


# --- 기록: status/source/confidence/reason ----------------------------------


def test_apply_records_decision_with_metadata(test_db):
    """accepted 결정이 provenance(source/confidence/reason)와 함께 감사에 기록된다."""
    operator = _make_operator(test_db, username="operator")
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[
            ApplyDecision(
                field="focus_regions",
                value=["부산"],
                status=DecisionStatus.ACCEPTED,
                source="internal_notices",
                confidence=0.8,
                reason="3건 공고에서 도출",
            )
        ],
    )
    assert result.recorded == 1

    rows = _rows_for(test_db, operator)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == operator.id
    assert row.field == "focus_regions"
    assert json.loads(row.value) == ["부산"]  # 원형 복원(list)
    assert row.status == "accepted"
    assert row.source == "internal_notices"
    assert row.confidence == 0.8
    assert row.reason == "3건 공고에서 도출"
    assert row.created_at is not None


def test_records_raw_value_while_reflecting_normalized(test_db):
    """감사에는 사용자 입력 원형을, 프로필에는 정규화 값을 남긴다(기록/반영 분리)."""
    operator = _make_operator(test_db, username="operator")
    apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[ApplyDecision(field="business_type", value="공사")],
    )
    row = _rows_for(test_db, operator)[0]
    assert json.loads(row.value) == "공사"  # 감사 = 사용자 결정 원형
    profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .one()
    )
    assert profile.business_type == "construction"  # 반영 = 정규화 값


def test_metadata_optional_defaults_null(test_db):
    """source/confidence/reason 미전달 시 감사에 null 로 남는다(있으면 기록, 없으면 null)."""
    operator = _make_operator(test_db, username="operator")
    apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[ApplyDecision(field="focus_regions", value=["부산"])],
    )
    row = _rows_for(test_db, operator)[0]
    assert row.source is None
    assert row.confidence is None
    assert row.reason is None
    assert row.status == "accepted"  # 기본 status


# --- 기록/반영 분리: rejected/pending 은 반영 안 함 --------------------------


def test_rejected_recorded_but_not_reflected(test_db):
    """rejected 는 감사에 남되 프로필/전략에는 반영되지 않는다(정직 §2)."""
    operator = _make_operator(test_db, username="operator")
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[
            ApplyDecision(
                field="business_type", value="construction", status=DecisionStatus.REJECTED
            )
        ],
    )
    assert result.recorded == 1
    assert result.applied == []

    row = _rows_for(test_db, operator)[0]
    assert row.status == "rejected"
    # rejected 는 반영 대상이 아니므로 프로필/전략 행이 생기지 않는다.
    assert (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .first()
        is None
    )
    assert (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == operator.id)
        .first()
        is None
    )


def test_pending_recorded_but_not_reflected(test_db):
    """pending 도 감사에만 남고 반영되지 않는다."""
    operator = _make_operator(test_db, username="operator")
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[
            ApplyDecision(
                field="focus_regions", value=["부산"], status=DecisionStatus.PENDING
            )
        ],
    )
    assert result.recorded == 1
    assert result.applied == []
    assert _rows_for(test_db, operator)[0].status == "pending"
    assert (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == operator.id)
        .first()
        is None
    )


def test_mixed_statuses_record_all_reflect_accepted_and_modified(test_db):
    """혼합 요청: 모두 기록하되 accepted/modified 만 반영한다."""
    operator = _make_operator(test_db, username="operator")
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[
            ApplyDecision(
                field="business_type", value="construction", status=DecisionStatus.ACCEPTED
            ),
            ApplyDecision(
                field="license_codes",
                value=["토목공사업"],
                status=DecisionStatus.MODIFIED,
            ),
            ApplyDecision(
                field="focus_regions", value=["부산"], status=DecisionStatus.REJECTED
            ),
            ApplyDecision(
                field="focus_categories",
                value=["construction"],
                status=DecisionStatus.PENDING,
            ),
        ],
    )
    # 4건 전부 기록.
    assert result.recorded == 4
    assert {row.status for row in _rows_for(test_db, operator)} == {
        "accepted",
        "modified",
        "rejected",
        "pending",
    }
    # 반영은 accepted/modified 만.
    applied_fields = {item.field for item in result.applied}
    assert applied_fields == {"business_type", "license_codes"}

    profile = (
        test_db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == operator.id)
        .one()
    )
    assert profile.business_type == "construction"  # accepted 반영
    assert profile.license_codes == "토목공사업"  # modified 반영
    strategy = (
        test_db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == operator.id)
        .first()
    )
    # rejected(focus_regions)/pending(focus_categories) 는 전략에 반영되지 않는다.
    if strategy is not None:
        assert strategy.focus_regions in ("", None)
        assert strategy.focus_categories in ("", None)


def test_rejected_invalid_value_is_recorded_not_raised(test_db):
    """rejected 는 coerce 하지 않으므로 화이트리스트 밖 값도 거부 없이 기록만 한다."""
    operator = _make_operator(test_db, username="operator")
    # accepted 였다면 422 였을 값이지만 rejected 라 검증을 타지 않는다.
    result = apply_onboarding_decisions(
        test_db,
        operator=operator,
        decisions=[
            ApplyDecision(
                field="business_type", value="not-a-type", status=DecisionStatus.REJECTED
            )
        ],
    )
    assert result.recorded == 1
    row = _rows_for(test_db, operator)[0]
    assert json.loads(row.value) == "not-a-type"
    assert row.status == "rejected"


# --- 원자성: accepted 검증 실패는 감사에도 남기지 않는다 ---------------------


def test_invalid_accepted_value_records_nothing(test_db):
    """반영 대상(accepted) 값 검증 실패는 전체 요청 거부 → 감사도 원자적으로 비어 있다."""
    operator = _make_operator(test_db, username="operator")
    with pytest.raises(OnboardingApplyError):
        apply_onboarding_decisions(
            test_db,
            operator=operator,
            decisions=[
                ApplyDecision(field="focus_regions", value=["부산"]),  # 유효
                ApplyDecision(field="business_type", value="not-a-type"),  # accepted 무효
            ],
        )
    # 검증이 반영/기록보다 먼저라 아무 행도 남지 않는다.
    assert _rows_for(test_db, operator) == []


# --- append-only: 재적용 시 행 증가 -----------------------------------------


def test_append_only_reapply_grows_rows(test_db):
    """같은 결정을 재적용하면 upsert 가 아니라 새 행이 append 된다(각 apply=이벤트)."""
    operator = _make_operator(test_db, username="operator")
    decision = [ApplyDecision(field="focus_regions", value=["부산"])]

    apply_onboarding_decisions(test_db, operator=operator, decisions=decision)
    assert len(_rows_for(test_db, operator)) == 1

    apply_onboarding_decisions(test_db, operator=operator, decisions=decision)
    assert len(_rows_for(test_db, operator)) == 2  # 멱등 강제 없음


# --- per-operator / synthetic 격리 ------------------------------------------


def test_audit_scoped_to_actor_operator_only(test_db):
    """operator A 의 감사가 operator B 의 user_id 로 새지 않는다."""
    operator_a = _make_operator(test_db, username="operator-a")
    operator_b = _make_operator(test_db, username="operator-b")

    apply_onboarding_decisions(
        test_db,
        operator=operator_a,
        decisions=[ApplyDecision(field="focus_regions", value=["부산"])],
    )

    assert len(_rows_for(test_db, operator_a)) == 1
    assert _rows_for(test_db, operator_b) == []


def test_synthetic_audit_does_not_pollute_canonical(test_db):
    """synthetic operator 의 감사가 canonical 감사에 섞이지 않는다."""
    canonical = _make_operator(test_db, username="operator")
    synthetic = _make_operator(test_db, username="synthetic-sw-small-seoul")

    apply_onboarding_decisions(
        test_db,
        operator=synthetic,
        decisions=[
            ApplyDecision(field="business_type", value="construction"),
            ApplyDecision(
                field="license_codes", value=["토목공사업"], status=DecisionStatus.REJECTED
            ),
        ],
    )

    synthetic_rows = _rows_for(test_db, synthetic)
    assert len(synthetic_rows) == 2
    assert all(row.user_id == synthetic.id for row in synthetic_rows)
    assert _rows_for(test_db, canonical) == []


# --- 하위호환: status 미전달(현재 프론트) = accepted -------------------------


def test_endpoint_backward_compat_field_value_only(client, test_db):
    """현재 프론트({field,value})가 status 없이 보내도 accepted 로 기록 + 반영된다."""
    response = client.post(
        _APPLY_URL,
        json={"decisions": [{"field": "business_type", "value": "construction"}]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recorded"] == 1

    rows = test_db.query(OnboardingSuggestion).all()
    assert len(rows) == 1
    assert rows[0].status == "accepted"  # 미전달 → accepted
    assert json.loads(rows[0].value) == "construction"
    # 반영도 이뤄진다(하위호환 동작 보존).
    profile = client.get("/api/v1/operator/profile").json()
    assert profile["business_type"] == "construction"


def test_endpoint_reports_recorded_and_records_rejected(client, test_db):
    """엔드포인트가 accepted+rejected 를 모두 기록하고 recorded 수를 보고한다."""
    response = client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {
                    "field": "business_type",
                    "value": "construction",
                    "status": "accepted",
                    "source": "internal_notices",
                    "confidence": 0.7,
                    "reason": "5건 공고",
                },
                {
                    "field": "license_codes",
                    "value": ["엔지니어링"],
                    "status": "rejected",
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["recorded"] == 2
    # 반영은 accepted 만.
    assert {item["field"] for item in payload["applied"]} == {"business_type"}

    rows = test_db.query(OnboardingSuggestion).order_by(OnboardingSuggestion.id).all()
    assert len(rows) == 2
    by_field = {row.field: row for row in rows}
    assert by_field["business_type"].status == "accepted"
    assert by_field["business_type"].source == "internal_notices"
    assert by_field["business_type"].confidence == 0.7
    assert by_field["license_codes"].status == "rejected"

    # rejected 필드는 프로필에 반영되지 않는다.
    profile = client.get("/api/v1/operator/profile").json()
    assert profile["business_type"] == "construction"
    assert profile["license_codes"] == []


def test_endpoint_invalid_status_returns_422(client, test_db):
    """허용 집합 밖 status 는 Pydantic enum 이 422 로 거른다(선언 허용값)."""
    response = client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {"field": "business_type", "value": "construction", "status": "maybe"}
            ]
        },
    )
    assert response.status_code == 422


def test_endpoint_empty_request_records_nothing(client, test_db):
    """빈 요청은 no-op — 감사에도 아무 것도 남지 않는다(반영 no-op 정합)."""
    response = client.post(_APPLY_URL, json={"decisions": []})
    assert response.status_code == 200, response.text
    assert response.json()["recorded"] == 0
    assert test_db.query(OnboardingSuggestion).count() == 0


def test_endpoint_oversized_source_returns_422(client, test_db):
    """source 가 감사 컬럼 String(50)을 넘으면 API 경계에서 422 로 막는다.

    길이 제약이 없으면 SQLite(CI)는 통과하지만 Postgres commit 시
    StringDataRightTruncation → 500 이 된다(라우터 try/except 는 OnboardingApplyError
    만 잡아 미포착). max_length=50 으로 SQLite≡Postgres 정합을 맞춰 truncation 500
    대신 422 로 거부한다. 실패 요청은 감사에 남지 않는다.
    """
    response = client.post(
        _APPLY_URL,
        json={
            "decisions": [
                {
                    "field": "business_type",
                    "value": "construction",
                    "source": "x" * 51,  # 컬럼 한도(50) 초과
                }
            ]
        },
    )
    assert response.status_code == 422
    assert test_db.query(OnboardingSuggestion).count() == 0
