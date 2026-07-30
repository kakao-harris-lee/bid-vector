"""스캔 산출 특성화 + 메모리 위생 회귀 가드 (설계 2026-07-30 §5 PR-A).

PR-A 는 "산출 불변" 리팩터링이다: preview/monitor 스캔의 후보 목록·점수·정렬을
고정 fixture 위에서 특성화(characterization)로 못박아 두고, 이후의 메모리 위생
변경(evaluations 슬림화 · read-only 스캔 · 피드백 윈도우 슬림화)이 산출을 단 한
값도 바꾸지 않음을 증명한다. 정렬키 캐스케이드(priority → probability →
matched → budget → id)를 전부 밟도록 아래 _ANALYSIS_TABLE 을 설계했다:
D(priority 0.95) → B,C(0.90/0.85/0.60/9천만, id 오름차순 타이브레이크) → A(0.90/0.80).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.single_user import ensure_operator_account, ensure_operator_strategy
from app.models.models import Project
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.project_similarity import ProjectSimilarityService


def _configure_software_operator(client):
    """싱글턴 운영자 프로필 + software 감시 전략 구성 (test_preview_cache 패턴)."""
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


# 선언적 특성화 표 (§4.5): title -> 분석 점수/예산. 정렬 기대 순서는 D, B, C, A.
_ANALYSIS_TABLE = {
    "서울 AI 데이터 통합 A": {"matched": 0.70, "probability": 0.80, "priority": 0.90,
                              "recommended": 111_000_000.0, "budget": 100_000_000.0},
    "서울 AI 데이터 통합 B": {"matched": 0.60, "probability": 0.85, "priority": 0.90,
                              "recommended": 112_000_000.0, "budget": 90_000_000.0},
    "서울 AI 데이터 통합 C": {"matched": 0.60, "probability": 0.85, "priority": 0.90,
                              "recommended": 113_000_000.0, "budget": 90_000_000.0},
    "서울 AI 데이터 통합 D": {"matched": 0.75, "probability": 0.70, "priority": 0.95,
                              "recommended": 114_000_000.0, "budget": 80_000_000.0},
}


def _seed_characterization_projects(test_db) -> dict[str, Project]:
    """_ANALYSIS_TABLE 의 4개 공고를 시드한다 (A,B,C,D 순 → id 오름차순)."""
    projects: dict[str, Project] = {}
    for offset, title in enumerate(_ANALYSIS_TABLE):
        project = Project(
            title=title,
            description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
            requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
            budget_estimate=_ANALYSIS_TABLE[title]["budget"],
            category="software",
            status="open",
            deadline=datetime.now(UTC) + timedelta(hours=10 + offset),
        )
        test_db.add(project)
        test_db.flush()
        projects[title[-1]] = project  # "A".."D"
    test_db.commit()
    for project in projects.values():
        test_db.refresh(project)
    return projects


def _canned_analyze(self, db, project, **kwargs):
    """title 기반 결정적 분석 — 임계값 통과 + 정렬키 캐스케이드 고정."""
    spec = _ANALYSIS_TABLE[project.title]
    return {
        "matched_score": spec["matched"],
        "probability_score": spec["probability"],
        "recommended_amount": spec["recommended"],
        "deadline_hours_remaining": 8,
        "current_active_bids": 0,
        "max_active_bids": 3,
        "current_workload_score": 0.0,
        "workload_source": "auto",
        "analysis_summary": f"{project.title} 요약",
        "strengths": [],
        "risk_flags": [],
        "decision": {
            "pursue_bid": True,
            "action": "review",
            "priority_score": spec["priority"],
            "recommended_amount": spec["recommended"],
            "probability_score": spec["probability"],
            "reasoning": "특성화 고정",
        },
    }


_EXPECTED_ORDER = ["D", "B", "C", "A"]
_CANDIDATE_KEYS = {
    "project_id", "title", "category", "budget_estimate", "deadline",
    "matched_score", "probability_score", "priority_score", "action",
    "recommended_amount", "analysis_summary", "strategy_reasons",
}


def test_preview_scan_output_is_pinned(client, test_db, monkeypatch):
    """preview 후보 목록·점수·정렬을 고정 — PR-A 전 구간의 산출 불변 기준선."""
    _configure_software_operator(client)
    projects = _seed_characterization_projects(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)

    payload = StrategyMonitoringService().preview_candidates(
        test_db, limit=10, high_priority_only=False
    )

    assert payload["evaluated_project_count"] == 4
    assert payload["returned_candidate_count"] == 4
    candidates = payload["candidates"]
    assert [c["project_id"] for c in candidates] == [projects[k].id for k in _EXPECTED_ORDER]
    assert all(set(c.keys()) == _CANDIDATE_KEYS for c in candidates)
    assert [
        (c["matched_score"], c["probability_score"], c["priority_score"]) for c in candidates
    ] == [(0.75, 0.70, 0.95), (0.60, 0.85, 0.90), (0.60, 0.85, 0.90), (0.70, 0.80, 0.90)]
    assert [c["recommended_amount"] for c in candidates] == [
        114_000_000.0, 112_000_000.0, 113_000_000.0, 111_000_000.0
    ]
    assert [c["budget_estimate"] for c in candidates] == [
        80_000_000.0, 90_000_000.0, 90_000_000.0, 100_000_000.0
    ]
    assert [c["title"] for c in candidates] == [
        f"서울 AI 데이터 통합 {k}" for k in _EXPECTED_ORDER
    ]
    assert [c["deadline"] for c in candidates] == [projects[k].deadline for k in _EXPECTED_ORDER]
    assert all(isinstance(c["strategy_reasons"], list) and c["strategy_reasons"] for c in candidates)


def test_monitor_scan_output_is_pinned(client, test_db, monkeypatch):
    """monitor(top-N 선택 포함) 산출 고정 — limit=3 이 evaluations[:limit] 슬라이스를 검증."""
    _configure_software_operator(client)
    projects = _seed_characterization_projects(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)

    response = StrategyMonitoringService().execute_monitoring(
        test_db,
        request=OperatorStrategyMonitorRequest(limit=3, high_priority_only=False),
    )

    assert response["evaluated_project_count"] == 4
    assert response["selected_candidate_count"] == 3
    assert response["persisted_candidate_count"] == 3
    results = response["results"]
    assert [item["project_id"] for item in results] == [projects[k].id for k in ("D", "B", "C")]
    assert [item["title"] for item in results] == [
        "서울 AI 데이터 통합 D", "서울 AI 데이터 통합 B", "서울 AI 데이터 통합 C"
    ]
    assert [item["matched_score"] for item in results] == [0.75, 0.60, 0.60]
    assert [item["probability_score"] for item in results] == [0.70, 0.85, 0.85]
    assert [item["recommended_amount"] for item in results] == [
        114_000_000.0, 112_000_000.0, 113_000_000.0
    ]
    assert all(item["is_new_candidate"] for item in results)
    assert response["new_candidate_count"] == 3
    # action/priority 는 allocation 서비스 재계산 값 — 타입/일관성만 고정
    assert all(item["action"] in {"bid_now", "review", "skip"} for item in results)
    assert response["notification_count"] == sum(
        1 for item in results if item["notification_created"]
    )


def test_evaluations_are_slim_and_hold_no_orm_or_analysis_refs(client, test_db, monkeypatch):
    """수집된 evaluation 은 ORM Project/전체 analysis dict 를 보관하지 않는다 (§5 PR-A-1)."""
    _configure_software_operator(client)
    _seed_characterization_projects(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)

    service = StrategyMonitoringService()
    operator = ensure_operator_account(test_db)
    strategy = ensure_operator_strategy(test_db)
    evaluations, evaluated_count = service._collect_candidate_evaluations(
        test_db,
        strategy=strategy,
        operator=operator,
        high_priority_only=False,
        max_active_bids=3,
        current_workload_score=None,
        same_category_only=True,
        similar_limit=3,
        min_similarity=0.15,
    )

    assert evaluated_count == 4
    assert len(evaluations) == 4
    for evaluation in evaluations:
        assert not hasattr(evaluation, "project")   # ORM 참조 해제
        assert not hasattr(evaluation, "analysis")  # 분석 dict 참조 해제
        assert isinstance(evaluation.project_id, int)
        assert isinstance(evaluation.candidate, dict)
        assert set(evaluation.candidate.keys()) == _CANDIDATE_KEYS
        assert isinstance(evaluation.sort_key, tuple)
    # 정렬은 미리 계산된 sort_key 만으로 결정된다
    assert [e.sort_key for e in evaluations] == sorted(e.sort_key for e in evaluations)


def _make_similarity_project(test_db, *, title: str, category: str = "software") -> Project:
    project = Project(
        title=title,
        description=f"{title} 설명",
        requirements="",
        budget_estimate=50_000_000.0,
        category=category,
    )
    test_db.add(project)
    test_db.flush()
    return project


def test_resolve_embedding_without_persist_matches_refresh_output(test_db):
    """read-only 해석은 refresh 가 반환했을 (vector, model) 과 동일하다 (산출 불변)."""
    service = ProjectSimilarityService()
    project = _make_similarity_project(test_db, title="임베딩 동등성 대상 공고")
    test_db.commit()

    read_only_vector, read_only_model = service.resolve_embedding_without_persist(project)
    refreshed_vector, refreshed_model = service.refresh_project_embedding(test_db, project)

    assert read_only_vector == refreshed_vector
    assert read_only_model == refreshed_model


def test_find_similar_projects_read_only_writes_nothing(test_db):
    """read_only=True: 세션 쓰기 0 + 검색 산출(점수·정렬)은 write 경로와 동일."""
    service = ProjectSimilarityService()
    target = _make_similarity_project(test_db, title="타깃 AI 데이터 공고")
    for index in range(3):
        _make_similarity_project(test_db, title=f"이웃 AI 데이터 공고 {index}")
    test_db.commit()
    payload_before = target.embedding_payload
    semantic_before = target.semantic_text

    read_only_response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.0, same_category_only=True, read_only=True
    )

    # S4 제거: 스캔이 Project 행을 dirty/new 로 만들지 않는다
    assert [obj for obj in test_db.dirty if isinstance(obj, Project)] == []
    assert [obj for obj in test_db.new if isinstance(obj, Project)] == []
    assert target.embedding_payload == payload_before  # 영속화 없음
    assert target.semantic_text == semantic_before

    write_response = service.find_similar_projects(
        test_db, target, limit=5, min_similarity=0.0, same_category_only=True
    )

    # 산출 불변: 점수·정렬 기여 값은 write 경로와 동일
    # (fallback 전용 embedding_model 필드는 비교 제외 — 설계 이탈 노트 4)
    assert [
        (item["project_id"], item["similarity_score"])
        for item in read_only_response["results"]
    ] == [
        (item["project_id"], item["similarity_score"])
        for item in write_response["results"]
    ]
    assert read_only_response["search_mode"] == write_response["search_mode"] == "python_fallback"
    assert read_only_response["target_embedding_model"] == write_response["target_embedding_model"]


def test_preview_scan_leaves_session_clean_and_releases_rows(client, test_db):
    """실분석 preview 스캔 후: Project dirty/new 없음 + 분석 완료 행 expunge."""
    _configure_software_operator(client)
    project = _make_similarity_project(
        test_db, title="서울 AI 데이터 통합 플랫폼 구축"
    )
    project.description = "서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축"
    project.requirements = "SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함"
    project.budget_estimate = 130_000_000.0
    project.status = "open"
    project.deadline = datetime.now(UTC) + timedelta(hours=12)
    test_db.commit()
    test_db.refresh(project)
    project_id = project.id

    payload = StrategyMonitoringService().preview_candidates(
        test_db, limit=10, high_priority_only=False
    )

    assert {c["project_id"] for c in payload["candidates"]} == {project_id}
    # read-only 스캔: 세션에 쓰기 잔류물 없음 (S4)
    assert [obj for obj in test_db.dirty if isinstance(obj, Project)] == []
    assert [obj for obj in test_db.new if isinstance(obj, Project)] == []
    # 세션 위생: 분석 완료 행은 identity map 에서 해제됨 (expunge)
    assert project not in test_db
