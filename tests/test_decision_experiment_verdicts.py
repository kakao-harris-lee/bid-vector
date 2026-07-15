"""Characterization tests for the decision-experiment verdict machine.

These lock the exact observable behavior of three pieces of
``DecisionExperimentService`` before they are converted into declarative
transition tables:

* ``_build_evaluation`` — the five-branch verdict machine that turns metric
  deltas into an outcome / recommended-action / Korean summary sentence.
* ``_guardrail_broken`` / ``_metric_improved`` — the numeric predicates that
  route ``_build_evaluation`` (pinned with above/below boundary deltas,
  including the float-comparison quirks at the exact threshold).
* ``evaluate_run`` — the verdict + timing → lifecycle-status mapping.
* ``update_run`` — the manual status → outcome/ended_at side-effect mapping.

The Korean summary strings are asserted byte-for-byte so the table refactor is
provably output-equivalent.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc, utc_now
from app.models.models import DecisionExperimentRun
from app.schemas.schemas import DecisionExperimentRunUpdateRequest
from app.services.decision_experiments import DecisionExperimentService

EVAL_AT = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
FUTURE_END = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _service() -> DecisionExperimentService:
    return DecisionExperimentService()


def _make_run(**overrides) -> DecisionExperimentRun:
    """Build a transient run exposing only the fields ``_build_evaluation`` reads."""
    defaults = dict(
        minimum_decision_sample=1,
        target_metric="review_submission_rate",
        guardrail_metric="overall_submission_rate",
        expected_direction="increase",
    )
    defaults.update(overrides)
    return DecisionExperimentRun(**defaults)


# ---------------------------------------------------------------------------
# _build_evaluation — five-branch verdict machine (byte-equal summaries)
# ---------------------------------------------------------------------------


def test_build_evaluation_insufficient_data():
    """Below the minimum sample the verdict short-circuits to insufficient_data."""
    run = _make_run(minimum_decision_sample=5)
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"decision_count": 0},
        current_summary={"decision_count": 2},
        evaluated_at=EVAL_AT,
        scheduled_end=FUTURE_END,
    )
    assert evaluation["outcome"] == "insufficient_data"
    assert evaluation["recommended_action"] == "collect_more_data"
    assert evaluation["summary"] == (
        "현재 표본 2건으로는 실험 판단이 이릅니다. "
        "최소 5건이 쌓일 때까지 더 수집하세요."
    )
    assert evaluation["sample_size"] == 2
    assert evaluation["minimum_sample_reached"] is False


def test_build_evaluation_rollback_on_broken_guardrail():
    """A degraded guardrail beats an otherwise-improving target metric."""
    run = _make_run()
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"overall_submission_rate": 0.5, "review_submission_rate": 0.4},
        current_summary={
            "decision_count": 5,
            "overall_submission_rate": 0.4,  # -0.10 delta: clearly broken
            "review_submission_rate": 0.6,  # improved, but guardrail wins
        },
        evaluated_at=EVAL_AT,
        scheduled_end=FUTURE_END,
    )
    assert evaluation["outcome"] == "rollback"
    assert evaluation["recommended_action"] == "rollback"
    assert evaluation["summary"] == (
        "가드레일 지표 `overall_submission_rate`가 기준 대비 악화되었습니다. "
        "현재 변경안을 롤백하고 원인을 점검하는 편이 안전합니다."
    )


def test_build_evaluation_improved_and_period_elapsed_is_success():
    """Improved target metric after the scheduled end completes as a success."""
    run = _make_run()
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"overall_submission_rate": 0.5, "review_submission_rate": 0.4},
        current_summary={
            "decision_count": 5,
            "overall_submission_rate": 0.5,  # unchanged guardrail
            "review_submission_rate": 0.6,  # +0.20 delta: improved
        },
        evaluated_at=EVAL_AT,
        scheduled_end=EVAL_AT,  # evaluated_at >= scheduled_end
    )
    assert evaluation["outcome"] == "success"
    assert evaluation["recommended_action"] == "complete"
    assert evaluation["summary"] == (
        "목표 지표 `review_submission_rate`가 기준 대비 개선되었습니다. "
        "현재 추세를 유지하며 실험을 종료하세요."
    )
    # Lock the full evaluation payload shape for the representative success path.
    assert evaluation == {
        "evaluated_at": EVAL_AT,
        "sample_size": 5,
        "minimum_sample_reached": True,
        "target_metric": "review_submission_rate",
        "baseline_target_value": 0.4,
        "current_target_value": 0.6,
        "target_delta": 0.2,
        "guardrail_metric": "overall_submission_rate",
        "baseline_guardrail_value": 0.5,
        "current_guardrail_value": 0.5,
        "guardrail_delta": 0.0,
        "outcome": "success",
        "recommended_action": "complete",
        "summary": (
            "목표 지표 `review_submission_rate`가 기준 대비 개선되었습니다. "
            "현재 추세를 유지하며 실험을 종료하세요."
        ),
        "current_summary": {
            "decision_count": 5,
            "overall_submission_rate": 0.5,
            "review_submission_rate": 0.6,
        },
    }


def test_build_evaluation_improved_before_period_end_is_watch():
    """Improved target metric before the scheduled end keeps watching."""
    run = _make_run()
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"overall_submission_rate": 0.5, "review_submission_rate": 0.4},
        current_summary={
            "decision_count": 5,
            "overall_submission_rate": 0.5,
            "review_submission_rate": 0.6,
        },
        evaluated_at=EVAL_AT,
        scheduled_end=FUTURE_END,  # evaluated_at < scheduled_end
    )
    assert evaluation["outcome"] == "watch"
    assert evaluation["recommended_action"] == "continue"
    assert evaluation["summary"] == (
        "목표 지표 `review_submission_rate`가 기준 대비 개선되었습니다. "
        "현재 추세를 유지하며 추가 표본을 수집하세요."
    )


def test_build_evaluation_period_elapsed_without_improvement_is_inconclusive():
    """Scheduled end reached without enough improvement records an inconclusive verdict."""
    run = _make_run()
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"overall_submission_rate": 0.5, "review_submission_rate": 0.5},
        current_summary={
            "decision_count": 5,
            "overall_submission_rate": 0.5,
            "review_submission_rate": 0.52,  # +0.02 delta: not improved enough
        },
        evaluated_at=EVAL_AT,
        scheduled_end=EVAL_AT,
    )
    assert evaluation["outcome"] == "inconclusive"
    assert evaluation["recommended_action"] == "complete"
    assert evaluation["summary"] == (
        "예정된 실험 기간은 종료되었지만 `review_submission_rate` 개선이 충분하지 않았습니다. "
        "결과를 기록하고 다음 가설로 넘어가는 편이 좋습니다."
    )


def test_build_evaluation_default_watch_before_period_end():
    """Before the scheduled end with weak improvement the verdict is a plain watch."""
    run = _make_run()
    evaluation = _service()._build_evaluation(
        run,
        baseline_summary={"overall_submission_rate": 0.5, "review_submission_rate": 0.5},
        current_summary={
            "decision_count": 5,
            "overall_submission_rate": 0.5,
            "review_submission_rate": 0.52,
        },
        evaluated_at=EVAL_AT,
        scheduled_end=FUTURE_END,
    )
    assert evaluation["outcome"] == "watch"
    assert evaluation["recommended_action"] == "continue"
    assert evaluation["summary"] == (
        "아직 목표 지표 `review_submission_rate` 개선 폭이 충분하지 않습니다. "
        "기간 종료 전까지 추이를 더 관찰하세요."
    )


# ---------------------------------------------------------------------------
# _guardrail_broken — numeric routing predicate (boundary matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric,baseline,current,expected",
    [
        # rate metric: broken when current - baseline <= -0.05 (float-exact)
        ("overall_submission_rate", 0.5, 0.44, True),
        ("overall_submission_rate", 0.5, 0.46, False),
        ("overall_submission_rate", 0.5, 0.45, False),  # float: -0.0499… > -0.05
        ("overall_submission_rate", None, 0.4, False),
        ("overall_submission_rate", 0.5, None, False),
        ("overall_submission_rate", 0.5, 0.5, False),
        # count metric: broken when drop ratio >= 0.20
        ("submitted_count", 10.0, 8.0, True),
        ("submitted_count", 10.0, 8.1, False),
        ("submitted_count", None, 5.0, False),
        ("submitted_count", 0.0, 5.0, False),
        ("submitted_count", 10.0, None, False),
        ("submitted_count", 10.0, 10.0, False),
        # active_pending_count: special growth handling
        ("active_pending_count", 0.0, 2.0, True),
        ("active_pending_count", 0.0, 1.99, False),
        ("active_pending_count", None, 2.0, True),
        ("active_pending_count", 10.0, 12.0, True),
        ("active_pending_count", 10.0, 11.9, False),
        ("active_pending_count", 10.0, 10.0, False),
        ("active_pending_count", 0.5, None, False),
    ],
)
def test_guardrail_broken_boundary_matrix(metric, baseline, current, expected):
    assert _service()._guardrail_broken(metric, baseline, current) is expected


# ---------------------------------------------------------------------------
# _metric_improved — numeric routing predicate (boundary matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "direction,metric,baseline,current,expected",
    [
        # increase, rate threshold 0.1 (float-exact around the boundary)
        ("increase", "review_submission_rate", 0.4, 0.6, True),
        ("increase", "review_submission_rate", 0.5, 0.6, False),  # float: 0.0999… < 0.1
        ("increase", "review_submission_rate", 0.5, 0.59, False),
        ("increase", "review_submission_rate", None, 0.2, True),
        ("increase", "review_submission_rate", None, 0.05, False),
        ("increase", "review_submission_rate", 0.5, None, False),
        # decrease
        ("decrease", "review_submission_rate", 0.5, 0.4, False),  # float: -0.0999… > -0.1
        ("decrease", "review_submission_rate", 0.5, 0.39, True),
        ("decrease", "review_submission_rate", None, 0.0, True),
        ("decrease", "review_submission_rate", None, -0.1, True),
        # stabilize
        ("stabilize", "review_submission_rate", 0.5, 0.55, True),
        ("stabilize", "review_submission_rate", 0.5, 0.65, False),
        # count metric, threshold 1.0, baseline None branch
        ("increase", "submitted_count", None, 1.0, True),
        ("increase", "submitted_count", None, 0.9, False),
    ],
)
def test_metric_improved_boundary_matrix(direction, metric, baseline, current, expected):
    assert _service()._metric_improved(direction, metric, baseline, current) is expected


# ---------------------------------------------------------------------------
# evaluate_run — verdict + timing → lifecycle status / ended_at
# ---------------------------------------------------------------------------


def _persist_run(db, operator, **overrides) -> DecisionExperimentRun:
    defaults = dict(
        operator_id=operator.id,
        experiment_key="exp-review-threshold-tighten",
        recommendation_key="review-threshold-tighten",
        status="running",
        outcome=None,
        title="verdict-machine-characterization",
        target_metric="review_submission_rate",
        expected_direction="increase",
        guardrail_metric="overall_submission_rate",
        minimum_decision_sample=1,
        duration_days=14,
        baseline_days=14,
        baseline_summary="{}",
        latest_evaluation="{}",
        started_at=utc_now(),
    )
    defaults.update(overrides)
    run = DecisionExperimentRun(**defaults)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _stub_verdict(service, *, outcome, recommended_action):
    """Force ``evaluate_run`` down a chosen verdict branch without real metrics."""
    service._build_snapshot = lambda db, **kwargs: {}
    service._build_evaluation = lambda run, **kwargs: {
        "outcome": outcome,
        "recommended_action": recommended_action,
        "sample_size": 0,
        "summary": "stub",
    }


def test_evaluate_run_rollback_verdict_rolls_back(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, started_at=utc_now() - timedelta(days=1))
    service = _service()
    _stub_verdict(service, outcome="rollback", recommended_action="rollback")

    before = utc_now()
    service.evaluate_run(test_db, run_id=int(run.id), operator=operator)
    after = utc_now()

    assert run.status == "rolled_back"
    assert run.outcome == "rollback"
    assert before <= ensure_utc(run.ended_at) <= after


def test_evaluate_run_after_scheduled_end_completes(test_db):
    operator = ensure_operator_account(test_db)
    started_at = utc_now() - timedelta(days=30)
    run = _persist_run(test_db, operator, started_at=started_at, duration_days=0)
    service = _service()
    _stub_verdict(service, outcome="inconclusive", recommended_action="complete")

    service.evaluate_run(test_db, run_id=int(run.id), operator=operator)

    # scheduled_end == started_at + 0 days == started_at (already in the past)
    assert run.status == "completed"
    assert ensure_utc(run.ended_at) == ensure_utc(started_at)


def test_evaluate_run_in_flight_is_running(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(
        test_db,
        operator,
        started_at=utc_now() - timedelta(days=2),
        duration_days=30,
        ended_at=None,
    )
    service = _service()
    _stub_verdict(service, outcome="watch", recommended_action="continue")

    service.evaluate_run(test_db, run_id=int(run.id), operator=operator)

    assert run.status == "running"
    assert run.ended_at is None


def test_evaluate_run_before_start_is_planned(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(
        test_db,
        operator,
        started_at=utc_now() + timedelta(days=10),
        duration_days=14,
        ended_at=None,
    )
    service = _service()
    _stub_verdict(service, outcome="watch", recommended_action="continue")

    service.evaluate_run(test_db, run_id=int(run.id), operator=operator)

    assert run.status == "planned"
    assert run.ended_at is None


# ---------------------------------------------------------------------------
# update_run — manual status transition → outcome / ended_at side-effects
# ---------------------------------------------------------------------------


def test_update_run_rolled_back_forces_rollback_outcome(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, status="running", outcome=None, ended_at=None)
    service = _service()

    before = utc_now()
    # request.outcome="success" must be overridden to "rollback".
    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="rolled_back", outcome="success"),
        operator=operator,
    )
    after = utc_now()

    assert run.status == "rolled_back"
    assert run.outcome == "rollback"
    assert before <= ensure_utc(run.ended_at) <= after


def test_update_run_rolled_back_honors_explicit_ended_at(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, status="running", ended_at=None)
    service = _service()
    explicit_end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="rolled_back", ended_at=explicit_end),
        operator=operator,
    )

    assert run.status == "rolled_back"
    assert ensure_utc(run.ended_at) == explicit_end


def test_update_run_completed_with_outcome_sets_ended_at(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, status="running", outcome=None, ended_at=None)
    service = _service()

    before = utc_now()
    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="completed", outcome="success"),
        operator=operator,
    )
    after = utc_now()

    assert run.status == "completed"
    assert run.outcome == "success"
    assert before <= ensure_utc(run.ended_at) <= after


def test_update_run_completed_without_outcome_keeps_prior_outcome(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, status="running", outcome="watch", ended_at=None)
    service = _service()

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="completed"),
        operator=operator,
    )

    assert run.status == "completed"
    assert run.outcome == "watch"  # unchanged
    assert run.ended_at is not None


def test_update_run_running_clears_ended_at(test_db):
    operator = ensure_operator_account(test_db)
    prior_end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    run = _persist_run(test_db, operator, status="completed", outcome="success", ended_at=prior_end)
    service = _service()

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="running", outcome="watch"),
        operator=operator,
    )

    assert run.status == "running"
    assert run.outcome == "watch"
    assert run.ended_at is None


def test_update_run_planned_clears_ended_at(test_db):
    operator = ensure_operator_account(test_db)
    prior_end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    run = _persist_run(test_db, operator, status="completed", ended_at=prior_end)
    service = _service()

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(status="planned"),
        operator=operator,
    )

    assert run.status == "planned"
    assert run.ended_at is None


def test_update_run_outcome_only_leaves_status_and_ended_at(test_db):
    operator = ensure_operator_account(test_db)
    prior_end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    run = _persist_run(test_db, operator, status="running", outcome=None, ended_at=prior_end)
    service = _service()

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(outcome="success"),
        operator=operator,
    )

    assert run.status == "running"  # unchanged
    assert run.outcome == "success"
    assert ensure_utc(run.ended_at) == prior_end  # untouched


def test_update_run_replace_and_append_notes(test_db):
    operator = ensure_operator_account(test_db)
    run = _persist_run(test_db, operator, status="running", notes="original")
    service = _service()

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(replace_notes="fresh"),
        operator=operator,
    )
    assert run.notes == "fresh"

    service.update_run(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentRunUpdateRequest(append_note="second line"),
        operator=operator,
    )
    assert run.notes == "fresh\nsecond line"
