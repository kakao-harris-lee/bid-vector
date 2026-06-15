"""Tests for the singleton operator workflow."""

import json
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.models.models import OperatorStrategyRun
from app.models.models import Analytics, Bid, BidDecisionRecord, Notification, Project, User
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.notifications.telegram import TelegramNotificationService
from app.services.strategy_scheduler import OperatorStrategyScheduler
from app.tasks.celery_app import build_operator_strategy_monitor_beat_schedule


def test_get_operator_profile_bootstraps_single_operator(client):
    """The operator profile endpoint should bootstrap the singleton account and profile."""
    response = client.get("/api/v1/operator/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "operator"
    assert payload["business_type"] == "service"
    assert payload["license_codes"] == []
    assert payload["profile_configured"] is False


def test_update_operator_profile_persists_company_fit_settings(client):
    """The operator profile endpoint should persist fit settings used elsewhere in the app."""
    response = client.put(
        "/api/v1/operator/profile",
        json={
            "full_name": "Harris Operator",
            "company": "Bid Vector Labs",
            "business_type": "software",
            "license_codes": ["SW001", "NET001"],
            "region_codes": ["서울특별시", "경기도"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.94,
            "total_awards": 11,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["full_name"] == "Harris Operator"
    assert payload["company"] == "Bid Vector Labs"
    assert payload["business_type"] == "software"
    assert payload["license_codes"] == ["SW001", "NET001"]
    assert payload["region_codes"] == ["서울특별시", "경기도"]
    assert payload["profile_configured"] is True


def test_get_operator_strategy_bootstraps_defaults(client):
    """The operator strategy endpoint should bootstrap sensible default watch rules."""
    response = client.get("/api/v1/operator/strategy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_categories"] == []
    assert payload["focus_regions"] == []
    assert payload["required_keywords"] == []
    assert payload["min_budget_estimate"] == 0.0
    assert payload["minimum_match_score"] == 0.6
    assert payload["minimum_probability_score"] == 0.55
    assert payload["bid_now_threshold"] == 0.7
    assert payload["review_threshold"] == 0.45
    assert payload["notify_only_high_priority"] is True
    assert payload["strategy_configured"] is False


def test_strategy_candidate_preview_skips_unconfigured_default_strategy(client, test_db, monkeypatch):
    """Default watch rules should not scan production projects during a preview read."""
    test_db.add(
        Project(
            title="서울 AI 데이터 통합 플랫폼 구축",
            description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
            requirements="SW001 보유 업체",
            budget_estimate=130000000.0,
            category="software",
            status="open",
            deadline=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    test_db.commit()

    def fail_analyze(self, db, project, **kwargs):
        raise AssertionError("unconfigured preview should not analyze projects")

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fail_analyze)

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": True, "limit": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluated_project_count"] == 0
    assert payload["returned_candidate_count"] == 0
    assert payload["candidates"] == []


def test_update_operator_strategy_persists_watch_rules(client):
    """The operator strategy endpoint should persist monitoring-focused watch rules."""
    response = client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software", "security"],
            "focus_regions": ["서울특별시", "전국"],
            "exclude_regions": ["제주특별자치도"],
            "required_keywords": ["AI", "데이터"],
            "exclude_keywords": ["유지보수"],
            "min_budget_estimate": 90000000.0,
            "max_budget_estimate": 180000000.0,
            "minimum_match_score": 0.67,
            "minimum_probability_score": 0.62,
            "bid_now_threshold": 0.74,
            "review_threshold": 0.5,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 7,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_categories"] == ["software", "security"]
    assert payload["focus_regions"] == ["서울특별시", "전국"]
    assert payload["exclude_regions"] == ["제주특별자치도"]
    assert payload["required_keywords"] == ["AI", "데이터"]
    assert payload["exclude_keywords"] == ["유지보수"]
    assert payload["min_budget_estimate"] == 90000000.0
    assert payload["max_budget_estimate"] == 180000000.0
    assert payload["minimum_match_score"] == 0.67
    assert payload["minimum_probability_score"] == 0.62
    assert payload["bid_now_threshold"] == 0.74
    assert payload["review_threshold"] == 0.5
    assert payload["notify_only_high_priority"] is False
    assert payload["max_recommended_candidates"] == 7
    assert payload["strategy_configured"] is True


def test_operator_strategy_candidates_filter_and_rank_projects(client, test_db):
    """Strategy candidates endpoint should return only projects that pass watch rules and analysis thresholds."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "exclude_keywords": ["유지보수"],
            "min_budget_estimate": 100000000.0,
            "max_budget_estimate": 160000000.0,
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 5,
        },
    )

    matching_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    excluded_keyword_project = Project(
        title="서울 AI 유지보수 지원 사업",
        description="서울특별시 AI 시스템 유지보수 사업",
        requirements="SW001 보유 업체",
        budget_estimate=135000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    low_budget_project = Project(
        title="서울 AI 데이터 시각화 소규모 사업",
        description="서울특별시 대상 데이터 시각화",
        requirements="SW001 보유 업체",
        budget_estimate=45000000.0,
        category="software",
    )
    wrong_category_project = Project(
        title="서울 AI 장비 도입 사업",
        description="서울특별시 AI 장비 납품",
        requirements="AI 장비 구매",
        budget_estimate=140000000.0,
        category="hardware",
    )
    test_db.add_all([matching_project, excluded_keyword_project, low_budget_project, wrong_category_project])
    test_db.commit()
    test_db.refresh(matching_project)

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluated_project_count"] >= 1
    assert payload["returned_candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["project_id"] == matching_project.id
    assert candidate["action"] in {"bid_now", "review"}
    assert candidate["probability_score"] >= 0.55
    assert any("관심 카테고리" in reason for reason in candidate["strategy_reasons"])
    assert any("관심 키워드" in reason for reason in candidate["strategy_reasons"])


def test_operator_strategy_candidates_include_re_notice_but_skip_cancelled_and_failed(client, test_db):
    """Strategy candidate preview should treat re-notices as active while excluding cancelled/failed notices."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 10,
        },
    )

    open_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        status="open",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    re_notice_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축 재공고",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축 재공고",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=128000000.0,
        category="software",
        status="re_notice",
        deadline=datetime.now(UTC) + timedelta(hours=18),
    )
    failed_project = Project(
        title="서울 AI 데이터 유찰 사업",
        description="서울특별시 대상 AI 데이터 분석 사업",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=126000000.0,
        category="software",
        status="failed",
        deadline=datetime.now(UTC) + timedelta(hours=18),
    )
    cancelled_project = Project(
        title="서울 AI 데이터 취소 사업",
        description="서울특별시 대상 AI 데이터 분석 사업",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=124000000.0,
        category="software",
        status="cancelled",
        deadline=datetime.now(UTC) + timedelta(hours=18),
    )
    test_db.add_all([open_project, re_notice_project, failed_project, cancelled_project])
    test_db.commit()

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    returned_ids = {item["project_id"] for item in payload["candidates"]}
    assert open_project.id in returned_ids
    assert re_notice_project.id in returned_ids
    assert failed_project.id not in returned_ids
    assert cancelled_project.id not in returned_ids


def test_operator_strategy_monitor_persists_decisions_and_notifications(client, test_db, monkeypatch):
    """Strategy monitoring should persist bid decisions and create notifications for selected candidates."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 10,
        },
    )

    high_priority_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=10),
    )
    review_project = Project(
        title="서울 AI 데이터 리포팅 체계 고도화",
        description="서울특별시 대상 AI 데이터 리포팅 자동화",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=110000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=30),
    )
    filtered_project = Project(
        title="서울 일반 유지보수 사업",
        description="서울특별시 일반 유지보수",
        requirements="유지보수 중심",
        budget_estimate=125000000.0,
        category="software",
    )
    test_db.add_all([high_priority_project, review_project, filtered_project])
    test_db.commit()
    test_db.refresh(high_priority_project)
    test_db.refresh(review_project)

    def fake_analyze(self, db, project, **kwargs):
        del db, kwargs
        if project.id == high_priority_project.id:
            return {
                "matched_score": 0.84,
                "probability_score": 0.89,
                "recommended_amount": 126000000.0,
                "deadline_hours_remaining": 10,
                "current_active_bids": 0,
                "max_active_bids": 3,
                "current_workload_score": 0.0,
                "analysis_summary": "고우선 즉시 투찰 후보입니다.",
                "decision": {
                    "pursue_bid": True,
                    "action": "bid_now",
                    "priority_score": 0.91,
                    "recommended_amount": 126000000.0,
                    "probability_score": 0.89,
                    "reasoning": "즉시 투찰 가치가 높습니다.",
                },
            }
        if project.id == review_project.id:
            return {
                "matched_score": 0.78,
                "probability_score": 0.66,
                "recommended_amount": 104000000.0,
                "deadline_hours_remaining": 30,
                "current_active_bids": 1,
                "max_active_bids": 3,
                "current_workload_score": 0.0,
                "analysis_summary": "추가 검토 가치가 있는 후보입니다.",
                "decision": {
                    "pursue_bid": True,
                    "action": "review",
                    "priority_score": 0.63,
                    "recommended_amount": 104000000.0,
                    "probability_score": 0.66,
                    "reasoning": "검토 후 추진이 적절합니다.",
                },
            }
        return {
            "matched_score": 0.1,
            "probability_score": 0.1,
            "recommended_amount": 0.0,
            "deadline_hours_remaining": None,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
            "analysis_summary": "대상 아님",
            "decision": {
                "pursue_bid": False,
                "action": "skip",
                "priority_score": 0.1,
                "recommended_amount": 0.0,
                "probability_score": 0.1,
                "reasoning": "대상 아님",
            },
        }

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fake_analyze)

    response = client.post(
        "/api/v1/operator/strategy/monitor",
        json={
            "high_priority_only": False,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["monitor_run_id"] >= 1
    assert payload["trigger_source"] == StrategyMonitoringService.SYNC_TRIGGER_SOURCE
    assert payload["previous_run_id"] is None
    assert payload["selected_candidate_count"] == 2
    assert payload["persisted_candidate_count"] == 2
    assert payload["notification_count"] == 2
    assert payload["new_candidate_count"] == 2
    assert payload["continuing_candidate_count"] == 0
    assert payload["dropped_candidate_count"] == 0
    assert {item["project_id"] for item in payload["results"]} == {high_priority_project.id, review_project.id}
    assert all(item["is_new_candidate"] is True for item in payload["results"])
    assert all(item["notification_created"] is True for item in payload["results"])
    assert test_db.query(BidDecisionRecord).count() == 2
    assert test_db.query(Notification).count() == 2
    run = test_db.query(OperatorStrategyRun).one()
    assert run.status == "completed"
    assert run.trigger_source == StrategyMonitoringService.SYNC_TRIGGER_SOURCE
    assert run.persisted_candidate_count == 2


def _seed_many_open_software_projects(test_db, *, count):
    """Seed ``count`` filter-passing open software notices with staggered deadlines."""
    projects = []
    for index in range(count):
        projects.append(
            Project(
                title=f"서울 AI 데이터 통합 플랫폼 구축 {index}",
                description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
                requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
                budget_estimate=130000000.0,
                category="software",
                status="open",
                # Staggered ascending deadlines so the bounded (deadline asc) scan
                # deterministically takes the earliest-deadline notices.
                deadline=datetime.now(UTC) + timedelta(hours=1, minutes=index),
            )
        )
    test_db.add_all(projects)
    test_db.commit()
    return projects


def _below_threshold_analysis(self, db, project, **kwargs):
    """Fake analysis returning below-threshold scores (no decisions persist)."""
    del db, project, kwargs
    return {
        "matched_score": 0.1,
        "probability_score": 0.1,
        "recommended_amount": 0.0,
        "deadline_hours_remaining": None,
        "current_active_bids": 0,
        "max_active_bids": 3,
        "current_workload_score": 0.0,
        "analysis_summary": "스캔 바운드 검증",
        "decision": {
            "pursue_bid": False,
            "action": "skip",
            "priority_score": 0.1,
            "recommended_amount": 0.0,
            "probability_score": 0.1,
            "reasoning": "below threshold",
        },
    }


def test_operator_strategy_monitor_scheduled_path_bounds_analysis_scan(client, test_db, monkeypatch):
    """The scheduled monitor must analyze only the bounded most-imminent slice, not every open notice."""
    from app.schemas.schemas import OperatorStrategyMonitorRequest

    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 10,
        },
    )

    # Pin the scan bound deterministically below the seeded project count.
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT", 40)
    monkeypatch.setattr(StrategyMonitoringService, "SCHEDULE_SCAN_MULTIPLIER", 1)
    monkeypatch.setattr(StrategyMonitoringService, "SCHEDULE_SCAN_FLOOR", 1)
    expected_scan_limit = 40

    project_count = 120
    _seed_many_open_software_projects(test_db, count=project_count)

    analyze_calls = {"count": 0}

    def counting_analyze(self, db, project, **kwargs):
        analyze_calls["count"] += 1
        return _below_threshold_analysis(self, db, project, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", counting_analyze)

    service = StrategyMonitoringService()
    response = service.execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
        trigger_source=StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE,
    )

    # The scheduled path loads only the bounded deadline-imminent slice, so the
    # expensive per-candidate analysis runs at most ``scan_limit`` times — never
    # once per open notice.
    assert analyze_calls["count"] == expected_scan_limit
    assert analyze_calls["count"] < project_count
    assert response["evaluated_project_count"] == expected_scan_limit
    assert response["trigger_source"] == StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE


def test_operator_strategy_monitor_manual_path_keeps_full_scan(client, test_db, monkeypatch):
    """Manual (operator-initiated) runs keep full-scan behavior — the bound is schedule-only."""
    from app.schemas.schemas import OperatorStrategyMonitorRequest

    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 10,
        },
    )

    # Even with a tiny configured scan bound, the manual path must ignore it.
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT", 5)

    project_count = 25
    _seed_many_open_software_projects(test_db, count=project_count)

    analyze_calls = {"count": 0}

    def counting_analyze(self, db, project, **kwargs):
        analyze_calls["count"] += 1
        return _below_threshold_analysis(self, db, project, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", counting_analyze)

    service = StrategyMonitoringService()
    response = service.execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
        trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
    )

    assert analyze_calls["count"] == project_count
    assert response["evaluated_project_count"] == project_count
    assert response["trigger_source"] == StrategyMonitoringService.SYNC_TRIGGER_SOURCE


def test_operator_strategy_monitor_high_priority_only_reuses_existing_records(client, test_db, monkeypatch):
    """High-priority monitoring runs should avoid review-only candidates and reuse active records on repeat runs."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "notify_only_high_priority": True,
        },
    )

    high_priority_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
    )
    review_project = Project(
        title="서울 AI 데이터 리포팅 체계 고도화",
        description="서울특별시 대상 AI 데이터 리포팅 자동화",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=110000000.0,
        category="software",
    )
    test_db.add_all([high_priority_project, review_project])
    test_db.commit()
    test_db.refresh(high_priority_project)
    test_db.refresh(review_project)

    def fake_analyze(self, db, project, **kwargs):
        del db, kwargs
        if project.id == high_priority_project.id:
            return {
                "matched_score": 0.84,
                "probability_score": 0.89,
                "recommended_amount": 126000000.0,
                "deadline_hours_remaining": 8,
                "current_active_bids": 0,
                "max_active_bids": 3,
                "current_workload_score": 0.0,
                "analysis_summary": "고우선 즉시 투찰 후보입니다.",
                "decision": {
                    "pursue_bid": True,
                    "action": "bid_now",
                    "priority_score": 0.91,
                    "recommended_amount": 126000000.0,
                    "probability_score": 0.89,
                    "reasoning": "즉시 투찰 가치가 높습니다.",
                },
            }
        return {
            "matched_score": 0.78,
            "probability_score": 0.66,
            "recommended_amount": 104000000.0,
            "deadline_hours_remaining": 30,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
            "analysis_summary": "추가 검토 가치가 있는 후보입니다.",
            "decision": {
                "pursue_bid": True,
                "action": "review",
                "priority_score": 0.63,
                "recommended_amount": 104000000.0,
                "probability_score": 0.66,
                "reasoning": "검토 후 추진이 적절합니다.",
            },
        }

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fake_analyze)

    first = client.post(
        "/api/v1/operator/strategy/monitor",
        json={
            "high_priority_only": True,
            "limit": 10,
        },
    )
    second = client.post(
        "/api/v1/operator/strategy/monitor",
        json={
            "high_priority_only": True,
            "limit": 10,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["notification_count"] == 1
    assert first.json()["new_candidate_count"] == 1
    assert first.json()["persisted_candidate_count"] == 1
    assert second.json()["notification_count"] == 0
    assert second.json()["new_candidate_count"] == 0
    assert second.json()["continuing_candidate_count"] == 1
    assert second.json()["persisted_candidate_count"] == 1
    assert second.json()["results"][0]["is_new_candidate"] is False
    assert second.json()["results"][0]["notification_created"] is False
    assert second.json()["results"][0]["notification_id"] is None
    assert test_db.query(BidDecisionRecord).count() == 1
    assert test_db.query(Notification).count() == 1


def test_operator_strategy_monitor_async_returns_pollable_task_and_result(client, test_db, monkeypatch):
    """Async strategy monitoring should return a task id and expose the final persisted result when run eagerly."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "notify_only_high_priority": False,
        },
    )

    high_priority_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=8),
    )
    test_db.add(high_priority_project)
    test_db.commit()
    test_db.refresh(high_priority_project)

    def fake_analyze(self, db, project, **kwargs):
        del db, kwargs
        return {
            "matched_score": 0.84,
            "probability_score": 0.89,
            "recommended_amount": 126000000.0,
            "deadline_hours_remaining": 8,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
            "analysis_summary": "고우선 즉시 투찰 후보입니다.",
            "decision": {
                "pursue_bid": True,
                "action": "bid_now",
                "priority_score": 0.91,
                "recommended_amount": 126000000.0,
                "probability_score": 0.89,
                "reasoning": "즉시 투찰 가치가 높습니다.",
            },
        }

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fake_analyze)

    kickoff = client.post(
        "/api/v1/operator/strategy/monitor/async",
        json={
            "high_priority_only": False,
            "limit": 5,
        },
    )

    assert kickoff.status_code == 200
    kickoff_payload = kickoff.json()
    assert kickoff_payload["task_id"]
    assert kickoff_payload["monitor_run_id"] >= 1
    assert kickoff_payload["task_name"] == "jobs.monitor_operator_strategy"
    assert kickoff_payload["status"] == "completed"
    assert kickoff_payload["poll_url"].endswith(kickoff_payload["task_id"])

    status_response = client.get(f"/api/v1/operator/strategy/monitor/tasks/{kickoff_payload['task_id']}")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["task_id"] == kickoff_payload["task_id"]
    assert payload["monitor_run_id"] == kickoff_payload["monitor_run_id"]
    assert payload["task_name"] == "jobs.monitor_operator_strategy"
    assert payload["status"] == "completed"
    assert payload["raw_status"] == "SUCCESS"
    assert payload["ready"] is True
    assert payload["successful"] is True
    assert payload["error"] is None
    assert payload["result"]["monitor_run_id"] == kickoff_payload["monitor_run_id"]
    assert payload["result"]["trigger_source"] == StrategyMonitoringService.ASYNC_TRIGGER_SOURCE
    assert payload["result"]["persisted_candidate_count"] == 1
    assert payload["result"]["results"][0]["project_id"] == high_priority_project.id
    assert test_db.query(BidDecisionRecord).count() == 1
    assert test_db.query(Notification).count() == 1


def test_operator_strategy_monitor_runs_endpoint_returns_recent_history(client, test_db, monkeypatch):
    """Recent monitoring history endpoint should expose completed strategy run summaries."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "notify_only_high_priority": False,
        },
    )

    project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=8),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    def fake_analyze(self, db, project, **kwargs):
        del db, kwargs
        return {
            "matched_score": 0.84,
            "probability_score": 0.89,
            "recommended_amount": 126000000.0,
            "deadline_hours_remaining": 8,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
            "analysis_summary": "고우선 즉시 투찰 후보입니다.",
            "decision": {
                "pursue_bid": True,
                "action": "bid_now",
                "priority_score": 0.91,
                "recommended_amount": 126000000.0,
                "probability_score": 0.89,
                "reasoning": "즉시 투찰 가치가 높습니다.",
            },
        }

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fake_analyze)

    run_response = client.post(
        "/api/v1/operator/strategy/monitor",
        json={"high_priority_only": False, "limit": 5},
    )
    assert run_response.status_code == 200

    history_response = client.get("/api/v1/operator/strategy/monitor/runs", params={"limit": 10})
    assert history_response.status_code == 200
    payload = history_response.json()
    assert payload["result_count"] == 1
    assert payload["runs"][0]["id"] == run_response.json()["monitor_run_id"]
    assert payload["runs"][0]["status"] == "completed"
    assert payload["runs"][0]["trigger_source"] == StrategyMonitoringService.SYNC_TRIGGER_SOURCE
    assert payload["runs"][0]["persisted_candidate_count"] == 1


def test_operator_strategy_monitor_run_detail_exposes_diff_and_only_new_alerts(client, test_db, monkeypatch):
    """Run detail endpoint should expose new/continuing/dropped candidates and suppress repeat alerts."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "notify_only_high_priority": False,
        },
    )

    alpha_project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=8),
    )
    beta_project = Project(
        title="서울 AI 데이터 리포팅 체계 고도화",
        description="서울특별시 대상 AI 데이터 리포팅 자동화",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=118000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    gamma_project = Project(
        title="서울 AI 분석 포털 구축",
        description="서울특별시 대상 AI 기반 분석 포털 구축",
        requirements="SW001 보유 업체, 데이터 분석 포함",
        budget_estimate=122000000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=16),
    )
    test_db.add_all([alpha_project, beta_project, gamma_project])
    test_db.commit()
    test_db.refresh(alpha_project)
    test_db.refresh(beta_project)
    test_db.refresh(gamma_project)

    active_run = {"index": 0}

    def fake_analyze(self, db, project, **kwargs):
        del db, kwargs
        if active_run["index"] == 0:
            mapping = {
                alpha_project.id: {
                    "matched_score": 0.88,
                    "probability_score": 0.9,
                    "recommended_amount": 126000000.0,
                    "priority_score": 0.92,
                    "summary": "alpha 신규 후보",
                },
                beta_project.id: {
                    "matched_score": 0.82,
                    "probability_score": 0.84,
                    "recommended_amount": 112000000.0,
                    "priority_score": 0.83,
                    "summary": "beta 신규 후보",
                },
            }
        else:
            mapping = {
                beta_project.id: {
                    "matched_score": 0.83,
                    "probability_score": 0.86,
                    "recommended_amount": 113000000.0,
                    "priority_score": 0.85,
                    "summary": "beta 계속 후보",
                },
                gamma_project.id: {
                    "matched_score": 0.81,
                    "probability_score": 0.82,
                    "recommended_amount": 119000000.0,
                    "priority_score": 0.81,
                    "summary": "gamma 신규 후보",
                },
            }

        if project.id not in mapping:
            return {
                "matched_score": 0.05,
                "probability_score": 0.05,
                "recommended_amount": 0.0,
                "deadline_hours_remaining": None,
                "current_active_bids": 0,
                "max_active_bids": 3,
                "current_workload_score": 0.0,
                "analysis_summary": "대상 아님",
                "decision": {
                    "pursue_bid": False,
                    "action": "skip",
                    "priority_score": 0.05,
                    "recommended_amount": 0.0,
                    "probability_score": 0.05,
                    "reasoning": "대상 아님",
                },
            }

        current = mapping[project.id]
        return {
            "matched_score": current["matched_score"],
            "probability_score": current["probability_score"],
            "recommended_amount": current["recommended_amount"],
            "deadline_hours_remaining": 8,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
            "analysis_summary": current["summary"],
            "decision": {
                "pursue_bid": True,
                "action": "bid_now",
                "priority_score": current["priority_score"],
                "recommended_amount": current["recommended_amount"],
                "probability_score": current["probability_score"],
                "reasoning": current["summary"],
            },
        }

    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", fake_analyze)

    first_run = client.post(
        "/api/v1/operator/strategy/monitor",
        json={"high_priority_only": False, "limit": 5},
    )
    assert first_run.status_code == 200
    assert first_run.json()["notification_count"] == 2

    alpha_project.status = "closed"
    test_db.commit()
    active_run["index"] = 1

    second_run = client.post(
        "/api/v1/operator/strategy/monitor",
        json={"high_priority_only": False, "limit": 5},
    )
    assert second_run.status_code == 200
    second_payload = second_run.json()
    assert second_payload["previous_run_id"] == first_run.json()["monitor_run_id"]
    assert second_payload["new_candidate_count"] == 1
    assert second_payload["continuing_candidate_count"] == 1
    assert second_payload["dropped_candidate_count"] == 1
    assert second_payload["notification_count"] == 1
    assert test_db.query(Notification).count() == 3

    detail_response = client.get(f"/api/v1/operator/strategy/monitor/runs/{second_payload['monitor_run_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == second_payload["monitor_run_id"]
    assert detail["previous_run_id"] == first_run.json()["monitor_run_id"]
    assert detail["new_candidate_count"] == 1
    assert detail["continuing_candidate_count"] == 1
    assert detail["dropped_candidate_count"] == 1
    assert detail["new_candidates"][0]["project_id"] == gamma_project.id
    assert detail["new_candidates"][0]["notification_created"] is True
    assert detail["continuing_candidates"][0]["project_id"] == beta_project.id
    assert detail["continuing_candidates"][0]["notification_created"] is False
    assert detail["dropped_candidates"][0]["project_id"] == alpha_project.id


def test_operator_strategy_scheduler_uses_configured_request_overrides(monkeypatch):
    """Periodic strategy scheduler should build its request from configured schedule settings."""
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT", 7)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY", False)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS", 4)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE", 0.15)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY", False)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT", 5)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY", 0.22)

    request = OperatorStrategyScheduler().build_request()

    assert request.limit == 7
    assert request.high_priority_only is False
    assert request.max_active_bids == 4
    assert request.current_workload_score == 0.15
    assert request.same_category_only is False
    assert request.similar_limit == 5
    assert request.min_similarity == 0.22


def test_operator_strategy_beat_schedule_is_configurable(monkeypatch):
    """Beat schedule helper should expose the periodic operator strategy task when enabled."""
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES", 12)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT", 6)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY", False)

    schedule = build_operator_strategy_monitor_beat_schedule()

    assert "operator_strategy_monitor_periodic" in schedule
    entry = schedule["operator_strategy_monitor_periodic"]
    assert entry["task"] == "jobs.monitor_operator_strategy"
    assert entry["schedule"] == 720.0
    assert entry["kwargs"]["trigger_source"] == StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE
    assert entry["kwargs"]["request_payload"]["limit"] == 6
    assert entry["kwargs"]["request_payload"]["high_priority_only"] is False


def test_submit_bid_uses_single_operator_account(client, test_db):
    """Bid submission should no longer require an explicit user_id query parameter."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Operator Bid Project",
            "description": "Single-user bid submission flow",
            "requirements": "Fast turnaround",
            "budget_estimate": 50000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 47000.0,
            "proposed_timeline": 14,
            "description": "We can deliver this quickly.",
        },
    )

    assert response.status_code == 200
    payload = response.json()

    users = test_db.query(User).all()
    bids = test_db.query(Bid).all()
    decisions = test_db.query(BidDecisionRecord).all()
    notifications = test_db.query(Notification).all()

    assert len(users) == 1
    assert len(bids) == 1
    assert len(decisions) == 1
    assert len(notifications) == 1
    assert payload["operator_id"] == users[0].id
    assert payload["user_id"] == users[0].id
    assert payload["decision_status"] == "submitted"
    assert payload["decision_record_id"] == decisions[0].id
    assert bids[0].user_id == users[0].id
    assert decisions[0].decision_status == "submitted"
    assert decisions[0].recommended_amount == 47000.0
    assert notifications[0].type == "bid_update"
    assert "투찰 완료 알림" in notifications[0].message


def test_submit_bid_promotes_existing_decision_to_submitted(client, test_db):
    """Submitting a bid should promote the active bid-decision record to `submitted` instead of duplicating it."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Decision Promotion Project",
            "description": "Promote active decision on submission",
            "requirements": "Fast approval path",
            "budget_estimate": 88000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 83000.0,
            "probability_score": 0.88,
            "matched_score": 0.82,
            "deadline_hours_remaining": 10,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )

    assert decision_response.status_code == 200
    decision_payload = decision_response.json()
    assert decision_payload["decision_status"] == "planned"

    response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 82500.0,
            "proposed_timeline": 7,
            "description": "Submitting after decision planning.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_record_id"] == decision_payload["id"]
    assert payload["decision_status"] == "submitted"
    assert test_db.query(BidDecisionRecord).count() == 1

    record = test_db.query(BidDecisionRecord).one()
    assert record.id == decision_payload["id"]
    assert record.decision_status == "submitted"
    assert record.initial_action == "bid_now"
    assert record.initial_decision_status == "planned"
    assert record.first_decided_at is not None
    assert record.recommended_amount == 82500.0
    assert "제출 상태" in record.reasoning


def test_submit_bid_preserves_initial_review_path_for_funnel_analytics(client, test_db):
    """Submitting after a review decision should preserve the original review path for funnel analytics."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Review Funnel Preservation Project",
            "description": "Preserve initial review action before submission",
            "requirements": "Track review-to-submit workflow",
            "budget_estimate": 91000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 87000.0,
            "probability_score": 0.58,
            "matched_score": 0.62,
            "deadline_hours_remaining": 48,
            "current_active_bids": 1,
            "max_active_bids": 3,
            "current_workload_score": 0.18,
        },
    )

    assert decision_response.status_code == 200
    decision_payload = decision_response.json()
    assert decision_payload["action"] == "review"
    assert decision_payload["decision_status"] == "reviewing"
    assert decision_payload["initial_action"] == "review"
    assert decision_payload["initial_decision_status"] == "reviewing"

    bid_response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 86500.0,
            "proposed_timeline": 10,
            "description": "Submitting after review queue confirmation.",
        },
    )

    assert bid_response.status_code == 200

    record = test_db.query(BidDecisionRecord).one()
    assert record.id == decision_payload["id"]
    assert record.decision_status == "submitted"
    assert record.action == "bid_now"
    assert record.initial_action == "review"
    assert record.initial_decision_status == "reviewing"
    assert record.first_decided_at is not None


def test_operator_can_list_and_mark_notifications_from_web(client):
    """The web dashboard should list recent notifications and allow marking them as read."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Notification Feed Project",
            "description": "Create a bid decision notification for the web dashboard",
            "requirements": "Need Telegram-style alerts and web visibility",
            "budget_estimate": 92000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 88000.0,
            "probability_score": 0.84,
            "matched_score": 0.78,
            "deadline_hours_remaining": 12,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.2,
        },
    )
    assert decision_response.status_code == 200

    list_response = client.get("/api/v1/operator/notifications")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["type"] == "recommendation"
    assert payload[0]["is_read"] is False
    assert "입찰 판단 알림" in payload[0]["message"]

    notification_id = payload[0]["id"]
    mark_read_response = client.put(f"/api/v1/operator/notifications/{notification_id}/read")
    assert mark_read_response.status_code == 200
    mark_payload = mark_read_response.json()
    assert mark_payload["id"] == notification_id
    assert mark_payload["is_read"] is True

    unread_response = client.get("/api/v1/operator/notifications", params={"unread_only": True})
    assert unread_response.status_code == 200
    assert unread_response.json() == []


def test_bid_decision_triggers_telegram_delivery_when_configured(client, test_db, monkeypatch):
    """Saving a bid decision should attempt Telegram delivery when credentials are configured."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test-bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PRIORITY_THRESHOLD", 0.78)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PROBABILITY_THRESHOLD", 0.8)

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({
            "message": message,
            "reply_markup": reply_markup,
            "chat_id": chat_id,
        })
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Telegram Delivery Project",
            "description": "Verify automatic Telegram delivery on bid decisions",
            "requirements": "Alert immediately when worth bidding",
            "budget_estimate": 140000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 132000.0,
            "probability_score": 0.9,
            "matched_score": 0.83,
            "deadline_hours_remaining": 6,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )

    assert response.status_code == 200
    assert len(deliveries) == 1
    assert "입찰 판단 알림" in deliveries[0]["message"]
    assert "Telegram Delivery Project" in deliveries[0]["message"]
    reply_markup = deliveries[0]["reply_markup"]
    assert reply_markup is not None
    callback_data = reply_markup["inline_keyboard"][0][0]["callback_data"]
    assert callback_data.startswith("bid-decision:")
    assert callback_data.endswith(":submit")
    delivery_event = test_db.query(Analytics).filter(Analytics.event_type == "telegram.delivery").one()
    delivery_payload = json.loads(delivery_event.event_data)
    assert delivery_payload["status"] == "sent"
    assert delivery_payload["sent"] is True
    assert delivery_payload["source"] == "bid_decision"


def test_low_priority_bid_decision_stays_on_web_without_telegram(client, test_db, monkeypatch):
    """Lower-value opportunities should remain web-only without creating Telegram noise."""
    deliveries: list[str] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test-bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PRIORITY_THRESHOLD", 0.78)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PROBABILITY_THRESHOLD", 0.8)

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(message)
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Web Only Decision Project",
            "description": "Should not trigger Telegram because priority is too low",
            "requirements": "Keep this in the dashboard only",
            "budget_estimate": 90000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 85000.0,
            "probability_score": 0.62,
            "matched_score": 0.6,
            "deadline_hours_remaining": 36,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.2,
        },
    )

    assert response.status_code == 200
    assert deliveries == []
    notification = test_db.query(Notification).one()
    assert notification.type == "recommendation"
    assert "입찰 판단 알림" in notification.message


def test_operator_overview_reports_single_user_counts(client):
    """The operator overview endpoint should provide a compact single-user dashboard summary."""
    client.get("/api/v1/operator/profile")
    response = client.get("/api/v1/operator/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] >= 1
    assert payload["project_count"] >= 0
    assert payload["bid_count"] >= 0
    assert payload["profile_configured"] is False


def test_operator_dashboard_returns_card_ready_workflow_payload(client, test_db):
    """The operator dashboard endpoint should connect analysis, decision, monitoring, and feedback surfaces."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.9,
            "total_awards": 5,
        },
    )
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Dashboard Connected Project",
            "description": "Expose this bid decision on the web dashboard",
            "requirements": "SW001 and 서울특별시 execution",
            "budget_estimate": 120000000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]
    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 113000000.0,
            "probability_score": 0.86,
            "matched_score": 0.82,
            "deadline_hours_remaining": 12,
            "current_active_bids": 0,
            "max_active_bids": 3,
            "current_workload_score": 0.0,
        },
    )
    decision_id = decision_response.json()["id"]
    operator = test_db.query(User).filter(User.username == "operator").one()
    monitor_run = OperatorStrategyRun(
        operator_id=operator.id,
        trigger_source="manual_sync",
        status="completed",
        high_priority_only=True,
        limit_applied=5,
        request_payload="{}",
        result_payload="{}",
        persisted_candidate_count=1,
        notification_count=1,
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    test_db.add(monitor_run)
    test_db.commit()
    test_db.refresh(monitor_run)

    response = client.get("/api/v1/operator/dashboard", params={"days": 30, "limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["profile_configured"] is True
    card_keys = {card["key"] for card in payload["cards"]}
    assert {
        "profile_configured",
        "active_bid_decisions",
        "unread_notifications",
        "monitor_failures",
        "recommendation_error_rate",
    }.issubset(card_keys)
    assert payload["action_hrefs"]["opportunity_analysis"] == "/api/v1/operations/opportunity-analysis"
    assert payload["action_hrefs"]["strategy_monitor_runs"] == "/api/v1/operator/strategy/monitor/runs"
    assert payload["feedback_summary"]["href"] == "/api/v1/analytics/prediction-feedback"
    assert payload["recent_decisions"][0]["decision_record_id"] == decision_id
    assert payload["recent_decisions"][0]["detail_href"].endswith(str(decision_id))
    assert payload["recent_decisions"][0]["analysis_href"] == "/api/v1/operations/opportunity-analysis"
    assert payload["recent_monitor_runs"][0]["monitor_run_id"] == monitor_run.id
    assert payload["recent_monitor_runs"][0]["detail_href"] == f"/api/v1/operator/strategy/monitor/runs/{monitor_run.id}"


def test_operator_dashboard_contract_handles_empty_state_and_openapi_schema(client):
    """The dashboard contract should be stable even before any decisions or monitor runs exist."""
    response = client.get("/api/v1/operator/dashboard", params={"days": 7, "limit": 3})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "operator_id",
        "generated_at",
        "period_days",
        "overview",
        "cards",
        "recent_decisions",
        "recent_monitor_runs",
        "feedback_summary",
        "action_hrefs",
        "current_operator_id",
        "current_operator_username",
    }
    assert payload["recent_decisions"] == []
    assert payload["recent_monitor_runs"] == []
    assert payload["feedback_summary"]["result_count"] == 0
    assert set(payload["action_hrefs"]) == {
        "opportunity_analysis",
        "decision_list",
        "strategy_candidates",
        "strategy_monitor",
        "strategy_monitor_runs",
        "prediction_feedback",
        "operations_dashboard",
    }
    for card in payload["cards"]:
        assert {"key", "label", "value", "unit", "status", "detail", "href"}.issubset(card)
        assert card["status"] in {"healthy", "watch", "critical", "info"}

    openapi = client.get("/openapi.json").json()
    dashboard_schema = openapi["paths"]["/api/v1/operator/dashboard"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert dashboard_schema["$ref"].endswith("/OperatorDashboardResponse")
    component = openapi["components"]["schemas"]["OperatorDashboardResponse"]
    assert {
        "cards",
        "recent_decisions",
        "recent_monitor_runs",
        "feedback_summary",
        "action_hrefs",
    }.issubset(component["properties"])
