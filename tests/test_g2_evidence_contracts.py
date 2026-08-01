"""G-2 sweep 요약 payload 계약 테스트 — 산출 불변 + 2모양 union + 관용 복원.

방어적 DTO 규율 Phase 5. 여기서 고정하는 것:

1. **산출 불변** — ``Analytics.event_data`` 로 저장되는 JSON 이 종전 ``json.dumps``
   산출과 키 집합·키 순서·값이 같다(공백만 다르다). 이 행은 G-2 exit 게이트의
   counted_days 입력이므로 문자열이 조용히 달라지면 안 된다.
2. **per-operator 2모양** — 정상 셀과 에러 셀의 키 집합이 배타적으로 유지된다(에러 셀에
   ``sections: {}`` 같은 없던 키가 생기지 않는다).
3. **생산/복원 비대칭** — 생산은 오타 키를 거부하고, 복원은 과거/미래 행을 관용한다.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.schemas.g2_evidence import (
    G2CandidateRecheckOperatorError,
    G2CandidateRecheckOperatorResult,
    G2CandidateRecheckSummary,
    G2CollectEvidenceSummary,
    G2EvidenceOperatorSnapshot,
    G2EvidenceOperatorSnapshotError,
    G2LedgerOperatorSummary,
    G2LedgerOperatorSummaryError,
    PersistedG2CollectEvidenceSummary,
)
from app.services.analytics_event_payload import dump_analytics_event

_SECTIONS = {
    "smoke": "ready",
    "strategy_monitor": "ready",
    "decision_experiments": "missing",
    "synthetic_experiments": "ready",
    "notifications": "ready",
}

# 종전 evidence_jobs.py 가 ``json.dumps`` 로 저장한 recheck payload (키 순서 포함).
LEGACY_RECHECK_PAYLOAD: dict[str, object] = {
    "operator_count": 2,
    "total_candidates": 3,
    "operators_with_candidates": 1,
    "error_count": 1,
    "per_operator": [
        {
            "operator_id": 19,
            "username": "synthetic-alpha",
            "evaluated_project_count": 12,
            "returned_candidate_count": 3,
        },
        {
            "operator_id": 20,
            "username": "synthetic-bravo",
            "error": "RuntimeError",
        },
    ],
}

# 종전 evidence_jobs.py 가 ``json.dumps`` 로 저장한 collect payload (키 순서 포함).
LEGACY_COLLECT_PAYLOAD: dict[str, object] = {
    "generated_window_days": 30,
    "recent_limit": 5,
    "operator_count": 2,
    "ready_count": 1,
    "error_count": 1,
    "per_operator": [
        {
            "operator_id": 19,
            "username": "synthetic-alpha",
            "evidence_status": "ready",
            "blocking_gaps_count": 0,
            "sections": dict(_SECTIONS),
        },
        {
            "operator_id": 20,
            "username": "synthetic-bravo",
            "error": "RuntimeError",
        },
    ],
}


def _recheck_summary() -> G2CandidateRecheckSummary:
    return G2CandidateRecheckSummary(
        operator_count=2,
        total_candidates=3,
        operators_with_candidates=1,
        error_count=1,
        per_operator=[
            G2CandidateRecheckOperatorResult(
                operator_id=19,
                username="synthetic-alpha",
                evaluated_project_count=12,
                returned_candidate_count=3,
            ),
            G2CandidateRecheckOperatorError(
                operator_id=20,
                username="synthetic-bravo",
                error="RuntimeError",
            ),
        ],
    )


def _collect_summary() -> G2CollectEvidenceSummary:
    return G2CollectEvidenceSummary(
        generated_window_days=30,
        recent_limit=5,
        operator_count=2,
        ready_count=1,
        error_count=1,
        per_operator=[
            G2EvidenceOperatorSnapshot(
                operator_id=19,
                username="synthetic-alpha",
                evidence_status="ready",
                blocking_gaps_count=0,
                sections=dict(_SECTIONS),
            ),
            G2EvidenceOperatorSnapshotError(
                operator_id=20,
                username="synthetic-bravo",
                error="RuntimeError",
            ),
        ],
    )


# --- 산출 불변 --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("payload", "legacy"),
    [
        (_recheck_summary(), LEGACY_RECHECK_PAYLOAD),
        (_collect_summary(), LEGACY_COLLECT_PAYLOAD),
    ],
)
def test_stored_string_is_parse_equivalent_to_legacy_dumps(payload, legacy):
    """compact separator 차이만 허용 — 키 집합·키 순서·값은 같아야 한다."""
    stored = dump_analytics_event(payload)

    assert json.loads(stored) == legacy
    assert list(json.loads(stored)) == list(legacy)
    # 종전 산출과의 유일한 차이는 공백이다.
    assert stored == json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))


def test_per_operator_error_cell_has_no_measurement_keys():
    """에러 셀은 측정치 키를 갖지 않는다 — 두 모양이 배타적으로 유지된다."""
    cells = json.loads(dump_analytics_event(_collect_summary()))["per_operator"]

    assert set(cells[0]) == {
        "operator_id",
        "username",
        "evidence_status",
        "blocking_gaps_count",
        "sections",
    }
    assert set(cells[1]) == {"operator_id", "username", "error"}


def test_ledger_summary_dump_keeps_the_draft_builder_contract():
    """daily draft 로 넘기는 요약도 정상/에러 2모양의 키 집합을 유지한다."""
    ready = G2LedgerOperatorSummary(
        operator_id=19,
        username="synthetic-alpha",
        evidence_status="ready",
        sections=dict(_SECTIONS),
        blocking_gaps=["missing strategy monitor evidence"],
    ).model_dump()
    failed = G2LedgerOperatorSummaryError(
        operator_id=20, username="synthetic-bravo", error="RuntimeError"
    ).model_dump()

    assert ready == {
        "operator_id": 19,
        "username": "synthetic-alpha",
        "evidence_status": "ready",
        "sections": dict(_SECTIONS),
        "blocking_gaps": ["missing strategy monitor evidence"],
    }
    assert failed == {
        "operator_id": 20,
        "username": "synthetic-bravo",
        "error": "RuntimeError",
    }


# --- 생산 계약 (sad) --------------------------------------------------------------
def test_production_models_reject_typo_keys():
    with pytest.raises(ValidationError):
        G2EvidenceOperatorSnapshot(
            operator_id=19,
            username="synthetic-alpha",
            evidence_status="ready",
            blocking_gaps_count=0,
            sections={},
            blocking_gap_count=1,  # 오타 키
        )


def test_production_models_require_the_declared_measurements():
    with pytest.raises(ValidationError):
        G2CandidateRecheckOperatorResult(
            operator_id=19, username="synthetic-alpha"
        )  # 측정치 누락


# --- union 배타성 (두 모양 중 어느 것도 아닌 셀은 만들 수 없다) ---------------------
def test_shared_keys_alone_match_neither_shape():
    """공유 키(operator_id/username)만으로는 정상도 에러도 아니다 — 둘 다 거부한다.

    한 모델로 합쳐 두면 이 payload 가 "측정치 0, 에러 없음"으로 조용히 통과해 관측이
    거짓이 된다. 2모양 union 이 그것을 구조적으로 막는지 고정한다.
    """
    shared_only = {"operator_id": 19, "username": "synthetic-alpha"}

    with pytest.raises(ValidationError):
        G2EvidenceOperatorSnapshot(**shared_only)
    with pytest.raises(ValidationError):
        G2EvidenceOperatorSnapshotError(**shared_only)


def test_measurement_and_error_keys_cannot_coexist():
    """측정치와 ``error`` 를 동시에 실은 셀은 두 모양 모두에서 거부된다.

    성공과 실패가 한 셀에 섞이면 sweep 의 error_count 와 per-operator 가 서로를 반증한다.
    """
    mixed = {
        "operator_id": 19,
        "username": "synthetic-alpha",
        "evidence_status": "ready",
        "blocking_gaps_count": 0,
        "sections": {},
        "error": "RuntimeError",
    }

    # 정상 모양은 ``error`` 를 미지 키로 거부(extra="forbid").
    with pytest.raises(ValidationError):
        G2EvidenceOperatorSnapshot(**mixed)
    # 에러 모양은 측정치 키들을 미지 키로 거부.
    with pytest.raises(ValidationError):
        G2EvidenceOperatorSnapshotError(**mixed)
    # 같은 배타성이 recheck sweep 의 2모양에도 적용된다.
    with pytest.raises(ValidationError):
        G2CandidateRecheckOperatorError(
            operator_id=19,
            username="synthetic-alpha",
            error="RuntimeError",
            returned_candidate_count=3,
        )


# --- 복원 계약 (관용) -------------------------------------------------------------
def test_restore_ignores_unknown_keys_and_keeps_absence_as_none():
    restored = PersistedG2CollectEvidenceSummary.model_validate(
        {
            "operator_count": 2,
            "future_key": "ignored",
            "per_operator": [{"operator_id": 19, "future_cell_key": 1}],
        }
    )

    assert restored.operator_count == 2
    assert restored.ready_count is None
    cell = (restored.per_operator or [])[0]
    assert cell.operator_id == 19
    # 미기록 gap 수는 0 이 아니라 부재로 남는다(counted_day 날조 방지).
    assert cell.blocking_gaps_count is None
    assert cell.evidence_status is None


def test_restore_round_trips_the_production_payload():
    restored = PersistedG2CollectEvidenceSummary.model_validate_json(
        dump_analytics_event(_collect_summary())
    )

    assert restored.ready_count == 1
    cells = restored.per_operator or []
    assert cells[0].blocking_gaps_count == 0
    assert cells[0].error is None
    assert cells[1].error == "RuntimeError"
    assert cells[1].evidence_status is None
