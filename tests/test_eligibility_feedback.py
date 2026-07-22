"""Tests for the operator eligibility-feedback WRITE path.

운영자 식별 피드백(적합/부적합/보류) → ``NoticeEligibilityLabel(source="operator")``
upsert 를 라우터·서비스 두 층에서 검증한다. verdict→label 매핑·멱등 upsert·rule
라벨과의 (project_id, source) 공존·경계 검증(422/404)을 값 테이블로 고정한다.
"""
from __future__ import annotations

from app.models.models import NoticeEligibilityLabel, Project
from app.services.eligibility_feedback import (
    record_operator_label,
    upsert_eligibility_label,
)
from app.services.eligibility_labeling import (
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    OPERATOR_LABELER_VERSION,
    SOURCE_OPERATOR,
    SOURCE_RULE,
    VERDICT_FIT,
    VERDICT_HOLD,
    VERDICT_UNFIT,
)


def _make_project(test_db, *, title="식별 피드백 대상", eligibility_raw=None) -> int:
    """Persist a minimal project and return its id."""
    project = Project(
        title=title,
        description="eligibility feedback fixture",
        requirements="",
        budget_estimate=100000000.0,
        category="service",
        eligibility_raw=eligibility_raw,
    )
    test_db.add(project)
    test_db.flush()
    project_id = project.id
    test_db.commit()
    return project_id


def _operator_label(test_db, project_id: int) -> NoticeEligibilityLabel | None:
    return (
        test_db.query(NoticeEligibilityLabel)
        .filter(
            NoticeEligibilityLabel.project_id == project_id,
            NoticeEligibilityLabel.source == SOURCE_OPERATOR,
        )
        .one_or_none()
    )


# --- route ------------------------------------------------------------------


def test_submit_feedback_persists_operator_label(client, test_db):
    """POST 성공 → operator 소스 라벨 저장(eligibility_raw 없어도 저장)."""
    # eligibility_raw 부재 공고여도 저장한다(커버리지 결정 (a) 교집합-only 수용).
    project_id = _make_project(test_db)

    response = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": VERDICT_FIT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == project_id
    assert payload["verdict"] == VERDICT_FIT
    assert payload["label"] == LABEL_POSITIVE
    assert payload["source"] == SOURCE_OPERATOR
    assert payload["labeler_version"] == OPERATOR_LABELER_VERSION
    assert VERDICT_FIT in payload["rationale"]
    assert payload["operator_id"] >= 1

    stored = _operator_label(test_db, project_id)
    assert stored is not None
    assert stored.label == LABEL_POSITIVE
    assert stored.source == SOURCE_OPERATOR
    assert stored.labeler_version == OPERATOR_LABELER_VERSION


def test_submit_feedback_rejects_invalid_verdict(client, test_db):
    """허용 값 밖의 verdict 는 422(Pydantic 검증)."""
    project_id = _make_project(test_db)

    response = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": "maybe"},
    )
    assert response.status_code == 422

    empty = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": ""},
    )
    assert empty.status_code == 422

    # 저장은 일어나지 않는다.
    assert _operator_label(test_db, project_id) is None


def test_submit_feedback_missing_project_returns_404(client, test_db):
    """존재하지 않는 project_id 는 404."""
    response = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": 999999, "verdict": VERDICT_UNFIT},
    )
    assert response.status_code == 404


def test_submit_feedback_is_idempotent_upsert(client, test_db):
    """재피드백은 같은 (project_id, operator) 라벨을 갱신(중복 없음)."""
    project_id = _make_project(test_db)

    first = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": VERDICT_FIT},
    )
    assert first.status_code == 200
    assert first.json()["label"] == LABEL_POSITIVE

    # 판정을 뒤집어 재제출.
    second = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": VERDICT_UNFIT},
    )
    assert second.status_code == 200
    assert second.json()["label"] == LABEL_NEGATIVE

    rows = (
        test_db.query(NoticeEligibilityLabel)
        .filter(
            NoticeEligibilityLabel.project_id == project_id,
            NoticeEligibilityLabel.source == SOURCE_OPERATOR,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].label == LABEL_NEGATIVE


def test_operator_label_coexists_with_rule_label(client, test_db):
    """rule 과 operator 라벨은 (project_id, source) 유니크 안에서 공존한다."""
    project_id = _make_project(test_db)

    # rule 라벨을 공유 upsert 헬퍼로 먼저 저장.
    upsert_eligibility_label(
        test_db,
        project_id=project_id,
        label=LABEL_NEGATIVE,
        source=SOURCE_RULE,
        rationale="rule fixture",
        labeler_version="rule-v2",
    )
    test_db.commit()

    response = client.post(
        "/api/v1/operator/eligibility-feedback",
        json={"project_id": project_id, "verdict": VERDICT_FIT},
    )
    assert response.status_code == 200

    labels = {
        row.source: row.label
        for row in test_db.query(NoticeEligibilityLabel)
        .filter(NoticeEligibilityLabel.project_id == project_id)
        .all()
    }
    assert labels == {SOURCE_RULE: LABEL_NEGATIVE, SOURCE_OPERATOR: LABEL_POSITIVE}


# --- service ----------------------------------------------------------------


def test_record_operator_label_maps_each_verdict(test_db):
    """verdict → label 매핑 3케이스 + source/version/rationale 검증(순수 매핑)."""
    cases = {
        VERDICT_FIT: LABEL_POSITIVE,
        VERDICT_UNFIT: LABEL_NEGATIVE,
        VERDICT_HOLD: LABEL_AMBIGUOUS,
    }
    for verdict, expected_label in cases.items():
        project = Project(
            title=f"map-{verdict}",
            description="",
            requirements="",
            budget_estimate=1.0,
            category="service",
        )
        test_db.add(project)
        test_db.flush()

        label = record_operator_label(test_db, project=project, verdict=verdict)

        assert label.label == expected_label
        assert label.source == SOURCE_OPERATOR
        assert label.labeler_version == OPERATOR_LABELER_VERSION
        assert verdict in (label.rationale or "")


def test_record_operator_label_upsert_updates_in_place(test_db):
    """서비스 재호출은 새 행을 만들지 않고 기존 operator 라벨을 갱신한다."""
    project = Project(
        title="service-upsert",
        description="",
        requirements="",
        budget_estimate=1.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    project_id = project.id

    first = record_operator_label(test_db, project=project, verdict=VERDICT_HOLD)
    assert first.label == LABEL_AMBIGUOUS

    second = record_operator_label(test_db, project=project, verdict=VERDICT_FIT)
    assert second.label == LABEL_POSITIVE

    rows = (
        test_db.query(NoticeEligibilityLabel)
        .filter(
            NoticeEligibilityLabel.project_id == project_id,
            NoticeEligibilityLabel.source == SOURCE_OPERATOR,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].label == LABEL_POSITIVE
