"""Single-pass decision-input characterization for strategy monitoring."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import fields

import pytest

from app.core.config import settings
from app.core.single_user import ensure_operator_account, ensure_operator_strategy
from app.models.models import (
    BidDecisionRecord,
    Notification,
    OperatorStrategyRun,
    OperatorStrategyRunItem,
    Project,
)
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.opportunity_monitoring.base import (
    CandidateDecisionInputs,
    StrategyCandidateEvaluation,
)
from tests import test_scan_memory_hygiene as scan_fixture


_DECISION_INPUT_FIELDS = {
    "project_id",
    "recommended_amount",
    "probability_score",
    "matched_score",
    "deadline_hours_remaining",
    "max_active_bids",
    "provided_workload_score",
    "budget_estimate",
    "competitiveness_score",
    "expected_margin_score",
    "execution_complexity_score",
    "strengths",
    "risk_flags",
    "analysis_summary",
}


def _rich_analysis(project: Project) -> dict:
    analysis = scan_fixture._canned_analyze(None, None, project)
    analysis.update(
        strengths=["기술 적합"],
        risk_flags=["일정 확인"],
        market_insights={"competitiveness_score": 0.67},
    )
    analysis["decision"].update(
        expected_margin_score=0.72,
        execution_complexity_score=0.28,
    )
    return analysis


def test_execute_monitoring_reuses_first_analysis_for_selected_candidates(
    client, test_db, monkeypatch
):
    """Each evaluated project is analyzed once; top-N only rehydrate for notification."""
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)
    service = StrategyMonitoringService()
    monkeypatch.setattr(settings, "APP_RELEASE_SHA", "review-sha")
    monkeypatch.setattr(settings, "APP_RELEASE_TAG", "review-release")
    analysis_calls: Counter[int] = Counter()
    read_only_calls: list[tuple[int, bool]] = []
    project_gets: list[int] = []
    expunged_projects: Counter[int] = Counter()

    def recording_analysis(db, project, request, *, operator, read_only=False):
        del db, request, operator
        project_id = int(project.id)
        analysis_calls[project_id] += 1
        read_only_calls.append((project_id, read_only))
        return _rich_analysis(project)

    original_get = test_db.get
    original_expunge = test_db.expunge

    def recording_get(entity, ident, *args, **kwargs):
        if entity is Project:
            project_gets.append(int(ident))
        return original_get(entity, ident, *args, **kwargs)

    def recording_expunge(instance):
        if isinstance(instance, Project):
            expunged_projects[int(instance.id)] += 1
        return original_expunge(instance)

    monkeypatch.setattr(service.analysis_service, "analyze_project", recording_analysis)
    monkeypatch.setattr(test_db, "get", recording_get)
    monkeypatch.setattr(test_db, "expunge", recording_expunge)

    response = service.execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=3, high_priority_only=False),
    )

    selected_ids = [projects[key].id for key in ("D", "B", "C")]
    assert analysis_calls == Counter({project.id: 1 for project in projects.values()})
    assert read_only_calls == [(project.id, True) for project in projects.values()]
    assert project_gets == selected_ids
    assert expunged_projects == Counter(
        {
            projects["D"].id: 2,
            projects["B"].id: 2,
            projects["C"].id: 2,
            projects["A"].id: 1,
        }
    )
    assert [item["project_id"] for item in response["results"]] == selected_ids

    records = {
        record.project_id: record for record in test_db.query(BidDecisionRecord).all()
    }
    assert set(records) == set(selected_ids)
    run_id = int(response["monitor_run_id"])
    assert response["release_sha"] == "review-sha"
    assert response["release_tag"] == "review-release"
    run = test_db.get(OperatorStrategyRun, run_id)
    assert run.release_sha == "review-sha"
    assert run.release_tag == "review-release"
    assert {record.monitor_run_id for record in records.values()} == {run_id}
    run_items = (
        test_db.query(OperatorStrategyRunItem)
        .filter(OperatorStrategyRunItem.run_id == run_id)
        .all()
    )
    assert {item.project_id for item in run_items} == set(selected_ids)
    assert {item.status for item in run_items} == {"completed"}
    notifications = test_db.query(Notification).filter_by(monitor_run_id=run_id).all()
    assert {row.project_id for row in notifications} == set(selected_ids)
    live_workload_scores: list[float] = []
    for active_count, result in enumerate(response["results"]):
        record = records[result["project_id"]]
        spec = scan_fixture._ANALYSIS_TABLE[result["title"]]
        assert record.recommended_amount == spec["recommended"]
        assert record.probability_score == spec["probability"]
        assert record.matched_score == spec["matched"]
        assert record.deadline_hours_remaining == 8
        assert record.current_active_bids == active_count
        assert record.max_active_bids == 3
        assert record.workload_source == "auto"
        live_workload_scores.append(float(record.current_workload_score))
        assert record.competitiveness_score == 0.67
        assert record.expected_margin_score == 0.72
        assert record.execution_complexity_score == 0.28
        assert json.loads(record.score_breakdown)["strengths"] == ["기술 적합"]
        assert json.loads(record.score_breakdown)["risk_flags"] == ["일정 확인"]
        assert result["decision_record_id"] == record.id
        assert result["action"] == record.action
        assert result["decision_status"] == record.decision_status
        assert result["priority_score"] == record.priority_score
        assert result["probability_score"] == record.probability_score
        assert result["matched_score"] == record.matched_score
        assert result["recommended_amount"] == record.recommended_amount
        assert result["analysis_summary"] == f"{result['title']} 요약"
    assert live_workload_scores[0] == 0.0
    assert live_workload_scores == sorted(live_workload_scores)


def test_max_active_bid_guardrail_refreshes_capacity_without_reanalysis(
    client, test_db, monkeypatch
):
    """Only the first selected candidate can occupy a one-bid capacity."""
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)
    service = StrategyMonitoringService()
    analysis_calls: Counter[int] = Counter()

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        analysis_calls[int(project.id)] += 1
        return _rich_analysis(project)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)

    response = service.execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(
            limit=3,
            high_priority_only=False,
            max_active_bids=1,
        ),
    )

    selected_ids = [projects[key].id for key in ("D", "B", "C")]
    assert analysis_calls == Counter({project.id: 1 for project in projects.values()})
    assert [item["project_id"] for item in response["results"]] == selected_ids

    records = {
        record.project_id: record for record in test_db.query(BidDecisionRecord).all()
    }
    selected_records = [records[project_id] for project_id in selected_ids]
    assert [record.current_active_bids for record in selected_records] == [0, 1, 1]
    assert [record.max_active_bids for record in selected_records] == [1, 1, 1]
    assert selected_records[0].decision_status in {"planned", "reviewing"}
    assert [record.action for record in selected_records[1:]] == ["skip", "skip"]
    assert [record.decision_status for record in selected_records[1:]] == [
        "skipped",
        "skipped",
    ]
    assert selected_records[1].current_workload_score > 0.0
    assert (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.decision_status.in_(("planned", "reviewing")))
        .count()
        == 1
    )
    for result, record in zip(response["results"], selected_records, strict=True):
        assert result["action"] == record.action
        assert result["decision_status"] == record.decision_status
        assert result["priority_score"] == record.priority_score


def test_explicit_workload_stays_provided_while_active_count_refreshes(
    client, test_db, monkeypatch
):
    """A caller workload override is stable while capacity still advances live."""
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        return _rich_analysis(project)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    response = StrategyMonitoringService().execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(
            limit=2,
            high_priority_only=False,
            max_active_bids=3,
            current_workload_score=0.37,
        ),
    )

    selected_ids = [projects[key].id for key in ("D", "B")]
    assert [item["project_id"] for item in response["results"]] == selected_ids
    records = {
        record.project_id: record for record in test_db.query(BidDecisionRecord).all()
    }
    assert [records[project_id].current_active_bids for project_id in selected_ids] == [
        0,
        1,
    ]
    assert [
        records[project_id].current_workload_score for project_id in selected_ids
    ] == [
        0.37,
        0.37,
    ]
    assert [records[project_id].workload_source for project_id in selected_ids] == [
        "provided",
        "provided",
    ]


def test_candidate_evaluation_retains_only_bounded_typed_decision_inputs(
    client, test_db, monkeypatch
):
    """Ranked evaluations retain no ORM project or full analysis result tree."""
    scan_fixture._configure_software_operator(client)
    scan_fixture._seed_characterization_projects(test_db)

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        return _rich_analysis(project)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    service = StrategyMonitoringService()
    evaluations, evaluated_count = service._collect_candidate_evaluations(
        test_db,
        strategy=ensure_operator_strategy(test_db),
        operator=ensure_operator_account(test_db),
        high_priority_only=False,
        max_active_bids=3,
        current_workload_score=None,
        same_category_only=True,
        similar_limit=3,
        min_similarity=0.15,
    )

    assert evaluated_count == len(evaluations) == 4
    assert {field.name for field in fields(StrategyCandidateEvaluation)} == {
        "candidate",
        "sort_key",
        "strategy_reasons",
        "decision_inputs",
    }
    assert {
        field.name for field in fields(CandidateDecisionInputs)
    } == _DECISION_INPUT_FIELDS
    for evaluation in evaluations:
        assert not hasattr(evaluation, "project")
        assert not hasattr(evaluation, "analysis")
        assert not hasattr(evaluation, "__dict__")
        assert isinstance(evaluation.decision_inputs, CandidateDecisionInputs)
        assert evaluation.project_id == evaluation.decision_inputs.project_id
        assert not any(
            isinstance(value, (dict, Project))
            for value in (
                getattr(evaluation.decision_inputs, field.name)
                for field in fields(CandidateDecisionInputs)
            )
        )


def test_monitor_processing_failure_rolls_back_and_finalizes_run(
    client, test_db, monkeypatch
):
    """A selected-candidate persistence failure rolls back and closes the run."""
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)
    original_title = projects["D"].title
    service = StrategyMonitoringService()

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        return _rich_analysis(project)

    def failing_save(db, request, *, operator):
        del operator
        db.query(Project).filter(Project.id == request.project_id).update(
            {Project.title: "rollback 대상 변경"}, synchronize_session=False
        )
        db.flush()
        raise RuntimeError("decision persistence failed")

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    monkeypatch.setattr(service.decision_service, "save_decision", failing_save)

    with pytest.raises(RuntimeError, match="decision persistence failed"):
        service.execute_monitoring(
            test_db,
            request=OperatorStrategyMonitorRequest(limit=1, high_priority_only=False),
        )

    assert test_db.get(Project, projects["D"].id).title == original_title
    assert test_db.query(BidDecisionRecord).count() == 0
    run = test_db.query(OperatorStrategyRun).one()
    assert run.status == "failed"
    assert run.error_message == "decision persistence failed"
    failed_item = test_db.query(OperatorStrategyRunItem).one()
    assert failed_item.run_id == run.id
    assert failed_item.project_id == projects["D"].id
    assert failed_item.status == "failed"
    assert failed_item.stage == "decision"


def test_monitor_defers_candidate_when_stored_projection_is_missing(
    client, test_db, monkeypatch
):
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)
    service = StrategyMonitoringService()

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        payload = _rich_analysis(project)
        payload["similar_projects"] = {
            "search_mode": "stored_missing",
            "projection_status": "missing",
            "results": [],
            "result_count": 0,
        }
        return payload

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    response = service.execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=1, high_priority_only=False),
    )

    assert response["persisted_candidate_count"] == 0
    assert response["projection_not_ready_count"] == len(projects)
    assert test_db.query(BidDecisionRecord).count() == 0
    items = test_db.query(OperatorStrategyRunItem).all()
    assert len(items) == len(projects)
    assert {item.status for item in items} == {"deferred"}
    assert {item.stage for item in items} == {"similarity_projection"}
    assert not any(project in test_db for project in projects.values())


def test_monitor_does_not_mutate_submitted_real_bid(client, test_db, monkeypatch):
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)
    operator = ensure_operator_account(test_db)
    submitted = BidDecisionRecord(
        project_id=projects["D"].id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="submitted",
        initial_action="bid_now",
        initial_decision_status="submitted",
        recommended_amount=901_000.0,
        submitted_bid_amount=899_000.0,
    )
    test_db.add(submitted)
    test_db.commit()
    submitted_id = int(submitted.id)

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        return _rich_analysis(project)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    response = StrategyMonitoringService().execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=1, high_priority_only=False),
    )

    test_db.refresh(submitted)
    assert submitted.id == submitted_id
    assert submitted.decision_status == "submitted"
    assert submitted.action == "bid_now"
    assert submitted.recommended_amount == 901_000.0
    assert submitted.submitted_bid_amount == 899_000.0
    assert submitted.monitor_run_id is None

    records = (
        test_db.query(BidDecisionRecord)
        .filter(BidDecisionRecord.project_id == projects["D"].id)
        .order_by(BidDecisionRecord.id)
        .all()
    )
    assert len(records) == 2
    monitor_record = records[1]
    assert monitor_record.id != submitted_id
    assert monitor_record.monitor_run_id == response["monitor_run_id"]
    assert response["results"][0]["decision_record_id"] == monitor_record.id


def test_monitor_keeps_decision_lineage_immutable_across_runs(
    client, test_db, monkeypatch
):
    scan_fixture._configure_software_operator(client)
    projects = scan_fixture._seed_characterization_projects(test_db)

    def analyze(self, db, project, **kwargs):
        del self, db, kwargs
        return _rich_analysis(project)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", analyze)
    service = StrategyMonitoringService()
    request = OperatorStrategyMonitorRequest(limit=1, high_priority_only=False)
    first = service.execute_monitoring(test_db, request=request)
    first_record = test_db.get(
        BidDecisionRecord, first["results"][0]["decision_record_id"]
    )
    first_record_id = int(first_record.id)
    first_run_id = int(first["monitor_run_id"])

    second = service.execute_monitoring(test_db, request=request)
    test_db.refresh(first_record)
    second_record = test_db.get(
        BidDecisionRecord, second["results"][0]["decision_record_id"]
    )

    assert first["results"][0]["project_id"] == projects["D"].id
    assert second["results"][0]["project_id"] == projects["D"].id
    assert first_record.id == first_record_id
    assert first_record.monitor_run_id == first_run_id
    assert second_record.id != first_record_id
    assert second_record.monitor_run_id == second["monitor_run_id"]
