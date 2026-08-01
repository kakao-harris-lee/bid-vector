"""KPI · 라벨 소비처의 ``analytics.event_data`` 해석 특성 테스트 (Phase 4.3).

``project_view`` / ``recommendation_feedback`` 행을 읽는 두 소비처
(``_build_review_time_kpi`` · ``_dedupe_latest_feedback_verdicts``)는 종전 raw dict 를
``.get()`` + ``_coerce_int`` 로 읽었다. 이 모듈은 그 **관용 해석의 산출을 값 테이블로
고정**한다: 뒤이어 소비처를 타입화된 복원 모델(``Persisted*``)로 승격할 때 산출이
바뀌지 않아야 하기 때문이다(승격은 계약을 드러내는 작업이고, 판정 변경이 아니다).

특히 중요한 경계 케이스(리뷰가 지적한 산출 차이 지점):

* ``12.7`` 처럼 정수화되지 않는 숫자는 **거부(None)** 이지 절삭(12)이 아니다.
* ``True`` 는 int 의 서브클래스지만 식별자로 쓰지 않는다(None).
* 한 키가 망가져도 **다른 키는 살아 있어야 한다** — 예: ``project_id`` 가 쓰레기값이어도
  ``decision_record_id``/``verdict`` 이 유효하면 그 피드백은 계속 집계된다. 복원 모델을
  엄격하게 걸면 이 행 전체가 사라져 ``feedback_count`` 가 조용히 줄어든다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.decision_analytics import DecisionAnalyticsService

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _event(payload, *, timestamp=NOW, raw: str | None = None):
    """복원 대상 행 1건(소비처가 실제로 읽는 두 속성만 갖춘 최소 대역)."""
    return SimpleNamespace(
        event_data=raw if raw is not None else json.dumps(payload),
        timestamp=timestamp,
    )


# --- 식별자 관용 해석 값 테이블 ------------------------------------------------------
# (payload 값, 기대 식별자). None = 그 행을 식별자 없음으로 취급한다.
IDENTIFIER_CASES = [
    pytest.param(5, 5, id="int"),
    pytest.param(0, 0, id="zero"),
    pytest.param(-3, -3, id="negative_int"),
    pytest.param(12.0, 12, id="integral_float_is_accepted"),
    pytest.param(12.7, None, id="fractional_float_is_rejected_not_truncated"),
    pytest.param(True, None, id="true_is_not_an_identifier"),
    pytest.param(False, None, id="false_is_not_an_identifier"),
    pytest.param("42", 42, id="digit_string"),
    pytest.param(" -7 ", -7, id="padded_negative_string"),
    pytest.param("1_000", None, id="underscored_string_is_rejected"),
    pytest.param("4.2", None, id="decimal_string_is_rejected"),
    pytest.param("", None, id="empty_string"),
    pytest.param("abc", None, id="non_numeric_string"),
    pytest.param(None, None, id="explicit_null"),
    pytest.param([1], None, id="list"),
    pytest.param({"a": 1}, None, id="mapping"),
]


class TestFeedbackVerdictDedupe:
    """``recommendation_feedback`` 행 → 최신 verdict 맵."""

    @pytest.mark.parametrize(("raw_value", "expected"), IDENTIFIER_CASES)
    def test_decision_record_id_coercion(self, raw_value, expected) -> None:
        events = [_event({"decision_record_id": raw_value, "verdict": "useful"})]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        assert list(deduped) == ([expected] if expected is not None else [])

    @pytest.mark.parametrize(("raw_value", "expected"), IDENTIFIER_CASES)
    def test_project_id_coercion_never_drops_the_verdict(
        self, raw_value, expected
    ) -> None:
        """``project_id`` 가 어떤 모양이든 verdict 집계는 유지된다(부분 degrade)."""
        events = [
            _event(
                {
                    "decision_record_id": 11,
                    "project_id": raw_value,
                    "verdict": "not_useful",
                }
            )
        ]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        assert deduped[11]["verdict"] == "not_useful"
        assert deduped[11]["project_id"] == expected

    @pytest.mark.parametrize(
        ("verdict", "expected"),
        [
            pytest.param("useful", "useful", id="useful"),
            pytest.param("not_useful", "not_useful", id="not_useful"),
            pytest.param("  USEFUL  ", "useful", id="whitespace_and_case_normalized"),
            pytest.param("maybe", None, id="unknown_verdict_dropped"),
            pytest.param("", None, id="empty_verdict_dropped"),
            pytest.param(None, None, id="missing_verdict_dropped"),
            pytest.param(0, None, id="falsy_non_string_verdict_dropped"),
            pytest.param(["useful"], None, id="non_string_verdict_dropped"),
        ],
    )
    def test_verdict_normalization(self, verdict, expected) -> None:
        events = [_event({"decision_record_id": 7, "verdict": verdict})]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        if expected is None:
            assert deduped == {}
        else:
            assert deduped[7]["verdict"] == expected

    def test_last_write_wins_per_decision(self) -> None:
        """호출부가 오래된 것부터 싣기 때문에 뒤 행이 앞 행을 덮는다."""
        events = [
            _event({"decision_record_id": 3, "verdict": "useful"}, timestamp=NOW),
            _event(
                {"decision_record_id": 3, "verdict": "not_useful"},
                timestamp=NOW + timedelta(hours=1),
            ),
        ]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        assert deduped[3]["verdict"] == "not_useful"
        assert deduped[3]["feedback_at"] == NOW + timedelta(hours=1)

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("", id="empty_text"),
            pytest.param("   ", id="blank_text"),
            pytest.param("not json at all", id="corrupted_text"),
            pytest.param("[1, 2, 3]", id="valid_json_but_not_a_mapping"),
            pytest.param("42", id="scalar_json"),
        ],
    )
    def test_unusable_rows_are_skipped(self, raw) -> None:
        events = [_event(None, raw=raw)]

        assert DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events) == {}

    def test_legacy_python_repr_rows_still_count(self) -> None:
        """``str(dict)`` 로 저장된 옛 행은 ast 폴백으로 계속 읽힌다."""
        legacy = str({"decision_record_id": 9, "verdict": "useful"})
        events = [_event(None, raw=legacy)]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        assert deduped[9]["verdict"] == "useful"

    def test_missing_timestamp_is_preserved_as_absent(self) -> None:
        events = [
            _event({"decision_record_id": 4, "verdict": "useful"}, timestamp=None)
        ]

        deduped = DecisionAnalyticsService()._dedupe_latest_feedback_verdicts(events)

        assert deduped[4]["feedback_at"] is None


class TestEarliestProjectView:
    """``project_view`` 행 → 프로젝트별 최초 열람 시각(리뷰 시간 KPI 입력)."""

    @pytest.mark.parametrize(("raw_value", "expected"), IDENTIFIER_CASES)
    def test_project_id_coercion(self, raw_value, expected) -> None:
        events = [_event({"project_id": raw_value})]

        earliest = DecisionAnalyticsService()._earliest_view_by_project(events)

        assert list(earliest) == ([expected] if expected is not None else [])

    def test_earliest_view_wins_per_project(self) -> None:
        events = [
            _event({"project_id": 8}, timestamp=NOW),
            _event({"project_id": 8}, timestamp=NOW - timedelta(hours=2)),
            _event({"project_id": 8}, timestamp=NOW + timedelta(hours=2)),
        ]

        earliest = DecisionAnalyticsService()._earliest_view_by_project(events)

        assert earliest[8] == NOW - timedelta(hours=2)

    def test_naive_timestamps_are_normalized_to_utc(self) -> None:
        events = [_event({"project_id": 2}, timestamp=NOW.replace(tzinfo=None))]

        earliest = DecisionAnalyticsService()._earliest_view_by_project(events)

        assert earliest[2] == NOW

    def test_rows_without_a_timestamp_are_skipped(self) -> None:
        events = [_event({"project_id": 5}, timestamp=None)]

        assert DecisionAnalyticsService()._earliest_view_by_project(events) == {}

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("", id="empty_text"),
            pytest.param("not json at all", id="corrupted_text"),
            pytest.param("[1, 2, 3]", id="valid_json_but_not_a_mapping"),
        ],
    )
    def test_unusable_rows_are_skipped(self, raw) -> None:
        events = [_event(None, raw=raw)]

        assert DecisionAnalyticsService()._earliest_view_by_project(events) == {}
