"""저장 JSON payload 복원 단일 경로 + 소비처 degrade 정책 특성 테스트.

방어적 DTO 규율 Phase 5. 여기서 고정하는 것:

1. **복원 정책** — 값이 없는 컬럼은 조용한 부재, 손상/모양 불일치는 경고 후 부재.
   payload 원문은 절대 로그에 남지 않는다(§8).
2. **소비처 degrade 정책 불변** — ``_load_json_object`` 는 빈 객체, ``_load_smoke_phases``
   는 빈 목록, monitoring ``_load_json`` 은 빈 객체로 낮춘다(종전 산출과 동일).
"""

from __future__ import annotations

import logging

from app.services.analytics_reporting import AnalyticsReportingService
from app.services.decision_experiments import DecisionExperimentService
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.stored_json_payload import (
    load_stored_json_array,
    load_stored_json_object,
    load_stored_json_value,
)

_CORRUPT = '{"partial": '


# --- 복원 단일 경로 ---------------------------------------------------------------
def test_object_loader_restores_json_object():
    assert load_stored_json_object('{"a":1,"b":"한글"}') == {"a": 1, "b": "한글"}


def test_object_loader_treats_absent_column_as_quiet_absence(caplog):
    """미기록 컬럼은 정상 상태다 — 경고 없이 부재."""
    with caplog.at_level(logging.WARNING):
        assert load_stored_json_object(None) is None
        assert load_stored_json_object("") is None

    assert caplog.text == ""


def test_object_loader_warns_and_degrades_on_corrupt_payload(caplog):
    with caplog.at_level(logging.WARNING):
        restored = load_stored_json_object(_CORRUPT, context="run.summary_json")

    assert restored is None
    assert "저장 JSON payload 해석 실패" in caplog.text
    assert "run.summary_json" in caplog.text
    # 원문은 남기지 않는다.
    assert "partial" not in caplog.text


def test_object_loader_rejects_non_object_json(caplog):
    """배열이 저장된 객체 컬럼은 오독이 아니라 부재로 처리한다."""
    with caplog.at_level(logging.WARNING):
        assert load_stored_json_object("[1,2]", context="run.summary_json") is None

    assert "shape=object" in caplog.text


def test_array_loader_restores_and_rejects():
    assert load_stored_json_array('[{"name":"a"}]') == [{"name": "a"}]
    assert load_stored_json_array('{"a":1}') is None
    assert load_stored_json_array(None) is None


def test_value_loader_accepts_both_shapes():
    """모양을 고정하지 않는 경로는 객체와 배열을 모두 되읽는다."""
    assert load_stored_json_value('{"a":1}') == {"a": 1}
    assert load_stored_json_value('["alpha"]') == ["alpha"]
    assert load_stored_json_value(_CORRUPT) is None


# --- 소비처 degrade 정책 (산출 불변) ----------------------------------------------
def test_reporting_object_loader_degrades_to_empty_object():
    service = AnalyticsReportingService()

    assert service._load_json_object('{"sample_status":"sufficient"}') == {
        "sample_status": "sufficient"
    }
    # 이미 파싱된 dict 는 그대로 통과(ORM JSON 컬럼 대비 종전 동작).
    assert service._load_json_object({"a": 1}) == {"a": 1}
    assert service._load_json_object(None) == {}
    assert service._load_json_object("") == {}
    assert service._load_json_object(_CORRUPT) == {}
    assert service._load_json_object("[1,2]") == {}


def test_reporting_smoke_phase_loader_degrades_to_empty_list():
    service = AnalyticsReportingService()

    assert service._load_smoke_phases('[{"name":"koneps_collect"},7]') == [
        {"name": "koneps_collect"}
    ]
    assert service._load_smoke_phases([{"name": "a"}, "skip"]) == [{"name": "a"}]
    assert service._load_smoke_phases(None) == []
    assert service._load_smoke_phases(_CORRUPT) == []
    assert service._load_smoke_phases('{"name":"a"}') == []


def test_monitoring_payload_loader_degrades_to_empty_object():
    service = StrategyMonitoringService()

    assert service._load_json('{"results":[]}') == {"results": []}
    assert service._load_json(None) == {}
    assert service._load_json(_CORRUPT) == {}
    assert service._load_json("[1,2]") == {}


def test_experiment_loader_keeps_a_stored_empty_object_distinct_from_absence():
    """저장된 빈 객체(``"{}"``)는 fallback 이 아니다 — falsy 함정 회귀 가드.

    새 run 의 ``latest_evaluation`` 초기값이 ``"{}"`` 이므로 ``or fallback`` 으로 쓰면
    "평가 없음"이 baseline 스냅샷 fallback 으로 조용히 바뀐다. 해석 실패(부재)만
    fallback 이어야 한다.
    """
    service = DecisionExperimentService()
    sentinel = {"fallback": True}

    # happy: 정상적으로 저장된 빈 객체는 그대로 빈 객체다.
    assert service._load_json("{}", fallback=sentinel) == {}
    assert service._load_json('{"outcome":"success"}', fallback=sentinel) == {
        "outcome": "success"
    }

    # sad: 미기록/손상/객체 아님 -> fallback.
    assert service._load_json(None, fallback=sentinel) is sentinel
    assert service._load_json("", fallback=sentinel) is sentinel
    assert service._load_json(_CORRUPT, fallback=sentinel) is sentinel
    assert service._load_json("[1,2]", fallback=sentinel) is sentinel
