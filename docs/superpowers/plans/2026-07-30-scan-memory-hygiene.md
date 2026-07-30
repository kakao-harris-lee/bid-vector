# PR-A "메모리 위생" (fix/scan-memory-hygiene) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 설계(`docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md`) §5 PR-A의 메모리 위생 4건(evaluations 슬림화 · read-only 스캔 · 피드백 윈도우 슬림화 · 스레드/아레나 env 튜닝)을 **스캔 산출(후보 목록·점수·정렬) 완전 불변**으로 구현한다.

**Architecture:** 스캔 루프(`_collect_candidate_evaluations`)가 후보를 분석 직후 직렬화해 경량 dict+정렬키만 보관하고 ORM/analysis 참조를 즉시 해제하며, 유사공고 검색은 `read_only` 플래그로 스캔 중 임베딩 영속화를 건너뛴다. 피드백 윈도우는 `joinedload` 전 컬럼 로드를 `load_only(title, category)`로 제한하고, glibc 아레나/BLAS 스레드는 docker-compose env로 선언적으로 상한한다. 모든 변경 전에 특성화 테스트로 산출 기준선을 고정한다.

**Tech Stack:** Python 3.x + FastAPI + SQLAlchemy 2.0.23 (SQLite test / PostgreSQL+pgvector prod), pytest, PyYAML(compose 가드 테스트), docker compose.

## Global Constraints

- **TDD 필수** — 모든 변경은 실패(또는 특성화 기준선) 테스트를 먼저 작성·실행한 뒤 최소 구현.
- **특성화 테스트로 산출 불변 증명** — 동일 fixture 입력에서 후보 목록·점수·정렬 불변 (spec §5 검증 조항, §8 PR-A).
- **pytest는 절대경로** `/home/deploy/project/bid-vector/.venv/bin/pytest` 사용 (워크트리에는 `.venv`가 없음). 실행 cwd는 워크트리 루트.
- **브랜치 `fix/scan-memory-hygiene`**, **worktree 기반 작업** (`/home/deploy/project/bid-vector-scan-memory`, `origin/main` 기준).
- **CLAUDE.md §4.5 선언적 구성 준수** — 매직값은 코드 흐름이 아니라 선언적 데이터(env/상수/테이블)로.
- **스캔 산출(후보 목록·점수·정렬) 불변** — preview payload와 monitor results의 값·순서가 한 항목도 바뀌면 안 된다.
- compose 변경 반영은 `restart`가 아니라 `docker compose up -d <service>` 재생성 (CLAUDE.md §0, spec §9) — PR 본문 체크리스트에 명기.

## 설계 이탈 노트 (코드 실사 후 확정한 결정)

1. **§5-1 monitor 경로 결합 확인 결과 — 두 소비자를 일관 리팩터링한다.** `_collect_candidate_evaluations`의 소비자는 preview(`_build_preview_payload`, `orchestration.py:96-108`)와 monitor(`execute_monitoring`, `orchestration.py:162-183`) 둘이다. monitor의 `_process_monitor_evaluation`(`orchestration.py:270-322`)은 `evaluation.project`(ORM)를 재분석·의사결정 저장·알림에 쓰지만 **`evaluation.analysis`는 전혀 쓰지 않는다**(항상 `_analyze_project`로 재분석). 따라서 `StrategyCandidateEvaluation`을 ORM 없는 슬림 구조(`project_id + candidate + sort_key + strategy_reasons`)로 바꾸고, monitor는 **선택된 top-N(≤resolved_limit, 기본 10건)만 `db.get(Project, project_id)`로 재조회**한다. 스캔 예산(≤250) 전체의 ORM 고정이 사라지고, 재조회 비용은 PK 조회 ≤수십 건으로 무시 가능. 산출 불변(동일 세션·동일 행).
2. **§5-2 "청크 경계" 위생 → "분석 완료 행 단위" expunge.** 청크 경계 배치 expunge는 처리 행 목록을 다시 들고 있어야 해서 오히려 참조를 늘린다. 분석을 마친 행을 그 자리에서 `db.expunge(project)`하는 편이 더 촘촘하고 단순하며 산출 동일(값은 이미 candidate dict로 복사됨). read-only 스캔(같은 Task)이 행을 clean으로 보장하므로 expunge가 pending write를 버릴 일이 없다.
3. **read_only 적용 범위: monitor top-N 재분석 포함.** 스캔 루프와 monitor 재분석이 같은 헬퍼(`candidates.py _analyze_project`)를 쓰므로 둘 다 `read_only=True`가 된다. "임베딩 갱신은 수집 파이프라인 소관"이라는 스펙 원칙(§5-2, §9 리스크 표)과 일치. 비-스캔 호출자(`/projects/{id}/similar` = `app/api/projects.py:242`, `/operations/analyze` = `app/api/operations.py:189`)는 기본값 `read_only=False`로 현행 유지.
4. **python fallback(테스트 전용) 경로의 `embedding_model` 필드.** `read_only`에서는 미영속 후보의 결과 항목 `embedding_model`이 `None`일 수 있다(비-read_only는 refresh 후 `"fallback-hash-v1"`). 스캔 산출 소비자는 `similarity_score`/`budget_estimate`/`result_count`만 읽고(`market.py:44-47`, `flags.py:44`) `embedding_model`은 아무도 읽지 않으며, 프로덕션 pgvector 경로는 저장값 그대로라 차이가 없다. 산출 불변 비교는 `(project_id, similarity_score)` 기준으로 검증한다.

---

### Task 1: 워크트리 생성 + 문서 커밋

**Files:**
- Create: `/home/deploy/project/bid-vector-scan-memory` (git worktree)
- Create(커밋): `docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md` (main 체크아웃에 untracked 상태로 존재 — 워크트리로 복사해 커밋)
- Create(커밋): `docs/superpowers/plans/2026-07-30-scan-memory-hygiene.md` (본 문서)

**Interfaces:**
- Consumes: `origin/main` (dfa04e5 이후 최신)
- Produces: 이후 모든 Task의 작업 디렉터리 `/home/deploy/project/bid-vector-scan-memory`, 브랜치 `fix/scan-memory-hygiene`

- [ ] **Step 1: 워크트리 생성**

```bash
cd /home/deploy/project/bid-vector
git fetch origin
git worktree add ../bid-vector-scan-memory -b fix/scan-memory-hygiene origin/main
```

- [ ] **Step 2: 스펙/계획 문서를 워크트리로 복사**

```bash
cp /home/deploy/project/bid-vector/docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md \
   /home/deploy/project/bid-vector-scan-memory/docs/superpowers/specs/
cp /home/deploy/project/bid-vector/docs/superpowers/plans/2026-07-30-scan-memory-hygiene.md \
   /home/deploy/project/bid-vector-scan-memory/docs/superpowers/plans/
```

(두 디렉터리 모두 tracked 파일이 이미 있어 워크트리에 존재함 — 없으면 `mkdir -p` 후 복사)

- [ ] **Step 3: 상태 확인**

Run: `git -C /home/deploy/project/bid-vector-scan-memory status --short`
Expected: 두 문서만 `??`로 표시, 브랜치 `fix/scan-memory-hygiene`

- [ ] **Step 4: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md \
        docs/superpowers/plans/2026-07-30-scan-memory-hygiene.md
git commit -m "docs(plan): 인라인 ML 메모리 설계 spec + PR-A 메모리 위생 구현 계획"
```

---

### Task 2: 스캔 산출 특성화 테스트 (리팩터링 전 GREEN 기준선)

**Files:**
- Create: `tests/test_scan_memory_hygiene.py`
- 참조(읽기 전용): `app/services/opportunity_monitoring/orchestration.py:86-116` (preview payload 형태), `app/services/opportunity_monitoring/candidates.py:159-167` (정렬키 캐스케이드), `tests/test_preview_cache.py:407-447` (fixture 원형)

**Interfaces:**
- Consumes: `StrategyMonitoringService.preview_candidates(db, *, limit, high_priority_only, operator=None) -> dict`, `StrategyMonitoringService.execute_monitoring(db, *, request: OperatorStrategyMonitorRequest, ...) -> dict`
- Produces: Task 3~5, 7이 회귀 검증에 재사용하는 특성화 테스트 2건 + 공용 fixture 헬퍼(`_configure_software_operator`, `_seed_characterization_projects`, `_canned_analyze`, `_ANALYSIS_TABLE`)

**주의:** 이 Task의 테스트는 **리팩터링 전 코드에서 통과(GREEN)해야 하는 기준선**이다. FAIL이면 fixture가 잘못된 것이므로 fixture를 고친다(코드는 만지지 않는다). 이후 Task 3~6에서 이 테스트가 한 번이라도 깨지면 산출 불변 위반이다.

- [ ] **Step 1: 특성화 테스트 작성**

`tests/test_scan_memory_hygiene.py` 신규 작성 (정렬키 캐스케이드 — priority → probability → matched → budget → id — 를 전부 밟도록 표를 설계):

```python
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
```

- [ ] **Step 2: 기준선 GREEN 확인 (리팩터링 전 코드에서 통과해야 함)**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py -q`
Expected: `2 passed` (FAIL이면 fixture/기대값을 현재 산출에 맞게 수정 — 코드는 절대 만지지 않는다)

- [ ] **Step 3: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add tests/test_scan_memory_hygiene.py
git commit -m "test(scan): 스캔 산출 특성화 고정 — preview/monitor 후보 목록·점수·정렬 기준선"
```

---

### Task 3: evaluations 슬림화 (spec §5 PR-A-1)

**Files:**
- Modify: `app/services/opportunity_monitoring/base.py:20,34-40` (dataclass 교체 + 불용 `Project` import 제거)
- Modify: `app/services/opportunity_monitoring/candidates.py:151-167` (append/sort), `:299-315` (`_serialize_candidate` 재서명 + `_build_candidate_evaluation` 신설)
- Modify: `app/services/opportunity_monitoring/orchestration.py:108` (preview), `:270-291` (`_process_monitor_evaluation` 재조회), `:316-322,388-412` (`_serialize_monitor_result`)
- Modify: `tests/test_license_gate_wiring.py:240,264`, `tests/test_operator.py:712,735,775` (`ev.project.id` → `ev.project_id`)
- Test: `tests/test_scan_memory_hygiene.py` (신규 테스트 추가)

**Interfaces:**
- Consumes: Task 2의 fixture 헬퍼(`_configure_software_operator`, `_seed_characterization_projects`, `_canned_analyze`)
- Produces (이후 Task가 의존):
  - `StrategyCandidateEvaluation(project_id: int, candidate: dict, sort_key: tuple, strategy_reasons: list[str])`
  - `_CandidateCollectionMixin._build_candidate_evaluation(*, project: Project, analysis: dict, strategy_reasons: list[str]) -> StrategyCandidateEvaluation`
  - `_CandidateCollectionMixin._serialize_candidate(project: Project, analysis: dict, strategy_reasons: list[str]) -> dict`
  - `_OrchestrationMixin._serialize_monitor_result(*, evaluation: StrategyCandidateEvaluation, project: Project, decision_record, notification, refreshed_analysis: dict, is_new_candidate: bool) -> dict`

- [ ] **Step 1: 실패하는 슬림 구조 테스트 작성**

`tests/test_scan_memory_hygiene.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py::test_evaluations_are_slim_and_hold_no_orm_or_analysis_refs -q`
Expected: FAIL — `assert not hasattr(evaluation, "project")` AssertionError (현행 dataclass는 `project`/`analysis` 필드 보유)

- [ ] **Step 3: 최소 구현**

(3a) `app/services/opportunity_monitoring/base.py` — 기존 34-40행 dataclass를 교체하고, 20행 `from app.models.models import Project` import를 제거(이 파일에서 더 이상 미사용):

```python
@dataclass
class StrategyCandidateEvaluation:
    """A strategy-filtered candidate, serialized at analysis time (slim).

    스캔 메모리 위생(설계 2026-07-30 §5 PR-A-1): 분석 직후 ``candidate``
    (preview 직렬화 dict)와 ``sort_key`` 만 보관하고 ORM ``Project`` / 전체
    analysis dict 참조는 즉시 해제한다. monitor 경로는 선택된 top-N 만
    ``project_id`` 로 재조회한다 (orchestration._process_monitor_evaluation).
    """

    project_id: int
    candidate: dict
    sort_key: tuple
    strategy_reasons: list[str]
```

(3b) `app/services/opportunity_monitoring/candidates.py` — 루프 append(기존 151-157행)를:

```python
            evaluations.append(
                self._build_candidate_evaluation(
                    project=project,
                    analysis=analysis,
                    strategy_reasons=filter_result.reasons,
                )
            )
```

정렬(기존 159-167행)을:

```python
        # 정렬·top-N 선택 로직 불변: 이전 sort(key=...) 람다와 바이트 동일한
        # 키 튜플을 분석 시점에 미리 계산해 둔 것뿐이다
        # (_build_candidate_evaluation).
        evaluations.sort(key=lambda evaluation: evaluation.sort_key)
```

`_serialize_candidate`(기존 299-315행)를 아래 두 메서드로 교체:

```python
    def _build_candidate_evaluation(
        self,
        *,
        project: Project,
        analysis: dict,
        strategy_reasons: list[str],
    ) -> StrategyCandidateEvaluation:
        """통과한 후보를 즉시 직렬화한다 (설계 §5 PR-A-1).

        반환되는 evaluation 은 순수 값(candidate dict + sort_key)만 들고 있어
        스캔 루프가 분석 예산(≤250)만큼의 ORM Project 행/전체 analysis dict 를
        정렬 시점까지 보유하지 않는다. sort_key 는 기존 루프 종료 후
        sort(key=...) 람다와 동일한 튜플이다.
        """
        return StrategyCandidateEvaluation(
            project_id=int(project.id),
            candidate=self._serialize_candidate(project, analysis, strategy_reasons),
            sort_key=(
                -float(analysis.get("decision", {}).get("priority_score", 0.0) or 0.0),
                -float(analysis.get("probability_score", 0.0) or 0.0),
                -float(analysis.get("matched_score", 0.0) or 0.0),
                -float(project.budget_estimate or 0.0),
                int(project.id),
            ),
            strategy_reasons=strategy_reasons,
        )

    def _serialize_candidate(
        self, project: Project, analysis: dict, strategy_reasons: list[str]
    ) -> dict:
        """Convert an analyzed strategy candidate into the preview API shape."""
        decision = analysis["decision"]
        return {
            "project_id": project.id,
            "title": project.title,
            "category": project.category,
            "budget_estimate": float(project.budget_estimate or 0.0),
            "deadline": project.deadline,
            "matched_score": float(analysis["matched_score"]),
            "probability_score": float(analysis["probability_score"]),
            "priority_score": float(decision["priority_score"]),
            "action": str(decision["action"]),
            "recommended_amount": float(analysis["recommended_amount"]),
            "analysis_summary": str(analysis["analysis_summary"]),
            "strategy_reasons": strategy_reasons,
        }
```

(3c) `app/services/opportunity_monitoring/orchestration.py` — 108행:

```python
        candidates = [evaluation.candidate for evaluation in evaluations[:resolved_limit]]
```

`_process_monitor_evaluation` 도입부(기존 281행 `project = evaluation.project`)를:

```python
        # 슬림 evaluation 은 ORM 참조를 갖지 않는다(설계 §5 PR-A-1): 선택된
        # top-N(≤ resolved_limit 행)만 PK 로 재조회한다. 스캔과 같은
        # 세션/트랜잭션이므로 행 내용은 동일하다.
        project = db.get(Project, evaluation.project_id)
        if project is None:  # pragma: no cover - 동일 트랜잭션에서 행 소실 불가
            return None
```

`_serialize_monitor_result` 호출부(기존 316-322행)에 `project=project,` 인자 추가, 메서드 서명(기존 388-412행)에 `project: Project` keyword-only 인자를 추가하고 본문의 `evaluation.project` 접근을 전부 `project`로 치환(기존 필드 구성·값은 전부 동일 유지 — `decision_record`/`refreshed_analysis`/`evaluation.strategy_reasons` 사용부 변경 없음).

(3d) 기존 테스트 표면 갱신 — `tests/test_license_gate_wiring.py` 240·264행과 `tests/test_operator.py` 712·735·775행의 `evaluation.project.id` / `ev.project.id`를 `evaluation.project_id` / `ev.project_id`로 변경.

- [ ] **Step 4: 통과 + 특성화 GREEN 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py tests/test_license_gate_wiring.py tests/test_operator.py tests/test_preview_cache.py tests/test_operator_strategy_monitor_finalize.py -q`
Expected: 전부 PASS (특성화 2건 포함 — 값 하나라도 바뀌면 여기서 잡힘)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add app/services/opportunity_monitoring/base.py \
        app/services/opportunity_monitoring/candidates.py \
        app/services/opportunity_monitoring/orchestration.py \
        tests/test_scan_memory_hygiene.py tests/test_license_gate_wiring.py tests/test_operator.py
git commit -m "fix(scan): evaluations 슬림화 — 분석 직후 직렬화, ORM/analysis 참조 해제 (산출 불변)"
```

---

### Task 4: read-only 스캔 + 세션 위생 (spec §5 PR-A-2)

**Files:**
- Modify: `app/services/project_similarity.py:8` (Callable import), `:74-93` 아래 신규 메서드, `:172-229` (`find_similar_projects`), `:283-306` (`_search_with_python`)
- Modify: `app/services/opportunity_analysis/orchestration.py:32-49` (`analyze_project`), `:157-183` (`_build_analysis_inputs`)
- Modify: `app/services/opportunity_monitoring/candidates.py:132-157` (try/finally + expunge), `:285-297` (`_analyze_project`에 `read_only=True`)
- Modify: `tests/test_license_gate_wiring.py:172-177` (`_FakeDB`에 `expunge` no-op 추가)
- Test: `tests/test_scan_memory_hygiene.py` (신규 테스트 3건)

**Interfaces:**
- Consumes: Task 3의 `_build_candidate_evaluation` (루프 tail을 try/finally로 감쌈)
- Produces:
  - `ProjectSimilarityService.find_similar_projects(db, project, *, limit=5, min_similarity=0.0, same_category_only=True, read_only=False) -> dict`
  - `ProjectSimilarityService.resolve_embedding_without_persist(project: Project) -> tuple[list[float], str]`
  - `ProjectSimilarityService._search_with_python(candidates, *, query_embedding, limit, min_similarity, embedding_resolver: Callable[[Project], list[float]] | None = None) -> list[dict]`
  - `OpportunityAnalysisService.analyze_project(db, project, request, *, operator=None, read_only: bool = False) -> dict`

- [ ] **Step 1: 실패하는 테스트 3건 작성**

`tests/test_scan_memory_hygiene.py`에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py -q`
Expected: 신규 3건 FAIL — 순서대로 `AttributeError: ... 'resolve_embedding_without_persist'`, `TypeError: find_similar_projects() got an unexpected keyword argument 'read_only'`, `assert [obj for obj in test_db.dirty ...] == []` AssertionError. 기존 특성화/슬림 테스트는 PASS 유지.

- [ ] **Step 3: 최소 구현**

(3a) `app/services/project_similarity.py` — `typing` import에 `Callable` 추가(8행: `from typing import Any, Callable, Iterable`), `refresh_project_embedding` 바로 아래(93행 뒤) 신규 메서드:

```python
    def resolve_embedding_without_persist(self, project: Project) -> tuple[list[float], str]:
        """``refresh_project_embedding`` 이 반환했을 (vector, model) 을 세션 쓰기 없이 돌려준다.

        분기 구조는 refresh 와 동일하다: semantic_text 가 저장본과 같고 payload
        캐시가 있으면 캐시 벡터, 아니면 인메모리 재계산. ORM 행에 아무것도
        대입하지 않고 ``db.add`` 도 없으므로 read-only 스캔이 Project 행을
        dirty/고정할 수 없다 (설계 2026-07-30 §5 PR-A-2 / S4). 스캔 중 임베딩
        freshness 는 수집/backfill 파이프라인 소관이다.
        """
        semantic_text = self.build_semantic_text(project)
        cached_vector = self._load_embedding(project)
        if semantic_text == (project.semantic_text or "").strip() and cached_vector:
            return cached_vector, project.embedding_model or FALLBACK_EMBEDDING_MODEL
        embedding, model_name = self._embed_text(semantic_text)
        return embedding, model_name
```

`find_similar_projects` 서명에 `read_only: bool = False` 추가(docstring에 위 계약 명시), 도입부(기존 190행):

```python
        if read_only:
            target_embedding, target_model = self.resolve_embedding_without_persist(project)
        else:
            target_embedding, target_model = self.refresh_project_embedding(db, project)
```

python fallback 분기(기존 203-218행):

```python
            candidates = candidate_query.all()
            if read_only:
                def _resolve_candidate_embedding(candidate: Project) -> list[float]:
                    return self.resolve_embedding_without_persist(candidate)[0]

                embedding_resolver = _resolve_candidate_embedding
            else:
                self.refresh_project_embeddings(db, candidates)
                embedding_resolver = self._load_embedding

            results = self._search_with_python(
                candidates,
                query_embedding=target_embedding,
                limit=limit,
                min_similarity=min_similarity,
                embedding_resolver=embedding_resolver,
            )
            search_mode = "python_fallback"
```

`_search_with_python` 서명에 `embedding_resolver: Callable[[Project], list[float]] | None = None` 추가, 본문 첫 줄 `resolver = embedding_resolver or self._load_embedding`, 루프에서 `candidate_embedding = resolver(candidate)`.

(3b) `app/services/opportunity_analysis/orchestration.py` — `analyze_project` 서명에 `read_only: bool = False` (keyword-only, `operator` 뒤) 추가하고 docstring에 "``read_only=True``(스캔 경로 전용)는 유사공고 검색의 임베딩 영속화를 건너뛴다. 기본값 False 로 기존 호출자 동작 불변." 명시. `_build_analysis_inputs` 호출에 `read_only=read_only` 전달. `_build_analysis_inputs` 서명에 `read_only: bool = False` 추가, `find_similar_projects` 호출에 `read_only=read_only` 전달.

(3c) `app/services/opportunity_monitoring/candidates.py` — `_analyze_project`의 `analysis_service.analyze_project(...)` 호출에 `read_only=True,` 추가(스캔 + monitor 재분석 공통 — 설계 이탈 노트 3). 루프 tail(기존 132-157행)을 try/finally로 재구성:

```python
            evaluated_project_count += 1
            analysis = self._analyze_project(
                db,
                project,
                operator=operator,
                max_active_bids=max_active_bids,
                current_workload_score=current_workload_score,
                same_category_only=same_category_only,
                similar_limit=similar_limit,
                min_similarity=min_similarity,
            )

            try:
                if float(analysis["matched_score"]) < float(strategy.minimum_match_score or 0.0):
                    continue
                if float(analysis["probability_score"]) < float(strategy.minimum_probability_score or 0.0):
                    continue
                if high_priority_only and not self._is_high_priority_candidate(analysis):
                    continue

                evaluations.append(
                    self._build_candidate_evaluation(
                        project=project,
                        analysis=analysis,
                        strategy_reasons=filter_result.reasons,
                    )
                )
            finally:
                # 세션 위생 (설계 §5 PR-A-2): 분석을 마친 행은 candidate dict 로
                # 값이 전부 복사됐으므로 identity map 에서 즉시 해제한다.
                # read-only 분석이 행을 clean 으로 보장하므로 버려지는 pending
                # write 는 없다. (스펙의 "청크 경계" 위생을 행 단위로 더 촘촘히
                # 수행 — 산출 동일. 미분석 행은 clean+약참조라 이미 수거 가능.)
                db.expunge(project)
```

(3d) `tests/test_license_gate_wiring.py` `_FakeDB`(172-177행)에 추가:

```python
    def expunge(self, obj):
        """스캔 루프 세션 위생은 인메모리 fake 에선 no-op."""
```

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py tests/test_project_similarity_search_path.py tests/test_license_gate_wiring.py tests/test_operator.py tests/test_preview_cache.py tests/test_operations.py tests/test_g2_candidate_recheck.py -q`
Expected: 전부 PASS (`test_python_fallback_still_loads_and_refreshes_candidates`가 비-read_only 기본 동작 불변을 증명)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add app/services/project_similarity.py \
        app/services/opportunity_analysis/orchestration.py \
        app/services/opportunity_monitoring/candidates.py \
        tests/test_scan_memory_hygiene.py tests/test_license_gate_wiring.py
git commit -m "fix(scan): read-only 스캔 — 임베딩 영속화 스킵(read_only 플래그) + 분석 완료 행 expunge (산출 불변)"
```

---

### Task 5: 피드백 윈도우 슬림화 (spec §5 PR-A-3)

**Files:**
- Modify: `app/services/prediction_feedback.py:17` (models import에 `Project` 추가), `:227-246` (`_load_recent_tender_results`)
- Test: `tests/test_prediction_feedback_calibration_perf.py` (신규 테스트 1건 추가 — 기존 `_seed_calibration_window`/`count_select_statements` fixture 재사용)

**Interfaces:**
- Consumes: `PredictionFeedbackService._get_recent_window(db, *, operator_id: int, days: int) -> _RecentWindow` (기존, 서명 불변)
- Produces: `_load_recent_tender_results(db, *, date_from: datetime) -> list[TenderResult]` — 서명·반환 타입 불변, joined Project는 `title`/`category`만 즉시 로드(나머지 deferred)

- [ ] **Step 1: 실패하는 컬럼 한정 테스트 작성**

`tests/test_prediction_feedback_calibration_perf.py` 끝에 추가 (파일 상단 import는 이미 `event`, `PredictionFeedbackService`, `ensure_operator_account` 보유):

```python
def test_recent_window_project_load_is_column_limited(test_db):
    """윈도우 로더의 tender_results SELECT 가 Project 중량 컬럼을 싣지 않는다.

    설계 2026-07-30 §5 PR-A-3 (S2): joinedload(TenderResult.project) 전 컬럼
    로드는 embedding_payload(~8-9KB text)·semantic_text 를 365일 윈도우 전체만큼
    실어 나른다. 다운스트림이 읽는 Project 컬럼은 title/category 뿐이므로
    필요 컬럼 한정으로 제한하되, 반환값·계산 로직은 불변이어야 한다.
    """
    operator = ensure_operator_account(test_db)
    _seed_calibration_window(test_db, operator_id=operator.id, count=4)

    service = PredictionFeedbackService()
    engine = test_db.get_bind()
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "tender_results" in statement and statement.lstrip().upper().startswith("SELECT"):
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        window = service._get_recent_window(test_db, operator_id=operator.id, days=365)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert window.tender_results  # 윈도우 자체는 정상 로드
    joined_sql = "\n".join(captured)
    assert "embedding_payload" not in joined_sql
    assert "semantic_text" not in joined_sql
    # 다운스트림이 실제로 읽는 컬럼은 즉시 로드 상태 그대로다 (추가 SELECT 없이)
    assert all(result.project.title for result in window.tender_results)
    assert all(result.project.category for result in window.tender_results)
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_prediction_feedback_calibration_perf.py::test_recent_window_project_load_is_column_limited -q`
Expected: FAIL — `assert "embedding_payload" not in joined_sql` AssertionError (현행 joinedload는 Project 전 컬럼을 SELECT)

- [ ] **Step 3: 최소 구현**

`app/services/prediction_feedback.py` — 17행 import를:

```python
from app.models.models import BidDecisionRecord, HistoricalData, PricePrediction, Project, TenderResult, User
```

`_load_recent_tender_results`(227-246행)의 options를:

```python
        results = (
            db.query(TenderResult)
            .options(
                # 피드백/캘리브레이션이 읽는 Project 컬럼은 title/category 뿐이다
                # (build_feedback 직렬화 + 카테고리 인메모리 필터). 전 컬럼
                # joinedload 는 embedding_payload(~8-9KB)·semantic_text 같은
                # 중량 컬럼을 365일 윈도우 전체만큼 상주시키므로 필요 컬럼
                # 한정으로 제한한다 (설계 2026-07-30 §5 PR-A-3 / S2). 반환값과
                # 계산 로직은 불변 — 미로드 컬럼은 접근 시 lazy-load 로 동작 동일.
                joinedload(TenderResult.project).load_only(
                    Project.title, Project.category
                )
            )
            .filter(
                TenderResult.project_id.isnot(None),
                or_(
                    TenderResult.announced_at >= date_from,
                    and_(TenderResult.announced_at.is_(None), TenderResult.created_at >= date_from),
                ),
            )
            .all()
        )
        return sorted(results, key=lambda result: result.announced_at or result.created_at, reverse=True)
```

docstring의 eager-load 설명에 "column-limited (title/category)" 한 줄 반영.

- [ ] **Step 4: 통과 + 산출 불변 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_prediction_feedback_calibration_perf.py tests/test_prediction_feedback_chunking.py tests/test_accuracy_integration.py tests/test_operations_kpi.py -q`
Expected: 전부 PASS — 특히 `test_build_calibration_context_result_is_invariant`(숫자 불변)와 `test_build_calibration_context_avoids_project_n_plus_one`(`select <= 4` 유지, load_only는 쿼리 수를 늘리지 않음)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add app/services/prediction_feedback.py tests/test_prediction_feedback_calibration_perf.py
git commit -m "fix(feedback): 최근 윈도우 Project 로드를 title/category 한정으로 — 값·계산 불변 (S2)"
```

---

### Task 6: 스레드/아레나 선언적 env 튜닝 (spec §5 PR-A-4)

**Files:**
- Modify: `docker-compose.yml:93` (api env 블록 끝), `:152` (worker), `:187` (ml-worker), `:221` (training-worker) — 각 `PYTHONPATH: /app` 다음 줄에 추가. `beat`는 ML 미실행이라 제외(스펙이 4개 서비스만 명시).
- Test: `tests/test_compose_memory_env.py` (신규)

**Interfaces:**
- Consumes: 없음 (독립)
- Produces: compose 선언 가드 테스트 `TUNED_SERVICES = ("api", "worker", "ml-worker", "training-worker")`

- [ ] **Step 1: 실패하는 compose 가드 테스트 작성**

`tests/test_compose_memory_env.py` 신규:

```python
"""docker-compose.yml 스레드/아레나 선언 가드 (설계 2026-07-30 §5 PR-A-4 / S1).

glibc malloc 은 스레드마다 아레나를 늘리고 OS 로 잘 반환하지 않는다 — anyio
40-스레드 풀 x torch 추론이 api RSS 단조 증가(1→8.4GiB)의 유력 주범(S1)이었다.
MALLOC_ARENA_MAX=2 / OMP_NUM_THREADS=1 은 코드가 아니라 compose env 로
선언한다(§4.5 선언적 구성). 이 테스트는 선언 누락/오타 회귀를 막는다.
"""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"
# 스펙 §5 PR-A-4 가 명시한 4개 서비스 (beat 는 ML 미실행이라 제외).
TUNED_SERVICES = ("api", "worker", "ml-worker", "training-worker")


def test_malloc_arena_and_omp_threads_declared_for_all_ml_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    for service_name in TUNED_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert str(environment.get("MALLOC_ARENA_MAX")) == "2", service_name
        assert str(environment.get("OMP_NUM_THREADS")) == "1", service_name
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_compose_memory_env.py -q`
Expected: FAIL — `assert str(environment.get("MALLOC_ARENA_MAX")) == "2"` (현재 `None`), 첫 서비스 `api`에서

- [ ] **Step 3: 최소 구현**

`docker-compose.yml` — api(93행 `PYTHONPATH: /app` 뒤) / worker(152행 뒤) / ml-worker(187행 뒤) / training-worker(221행 뒤) 각각에 동일 블록 추가:

```yaml
      # 스레드/아레나 선언적 튜닝 (설계 2026-07-30 §5 PR-A-4, S1): 스레드풀 x
      # torch/BLAS 가 glibc malloc 아레나를 스레드마다 비대화시키는 것을 상한.
      # 값 변경 반영은 restart 가 아니라 `docker compose up -d <service>` 재생성(§0).
      MALLOC_ARENA_MAX: "2"
      OMP_NUM_THREADS: "1"
```

- [ ] **Step 4: 통과 확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_compose_memory_env.py -q`
Expected: `1 passed`

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-scan-memory
git add docker-compose.yml tests/test_compose_memory_env.py
git commit -m "fix(compose): MALLOC_ARENA_MAX=2·OMP_NUM_THREADS=1 선언 — api/worker/ml-worker/training-worker (S1)"
```

---

### Task 7: 전체 회귀 + 특성화 재확인 + PR 본문 준비

**Files:**
- 수정 없음 (검증 전용). PR 본문 텍스트 산출.

**Interfaces:**
- Consumes: Task 2~6의 모든 테스트
- Produces: green 전체 스위트 + PR 본문 스켈레톤 (push/`gh pr create`는 CLAUDE.md 머지 프로토콜에 따라 사용자 확인 후)

- [ ] **Step 1: 특성화(산출 불변) 최종 재확인**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_scan_memory_hygiene.py tests/test_prediction_feedback_calibration_perf.py tests/test_compose_memory_env.py -v`
Expected: 전부 PASS — 특히 `test_preview_scan_output_is_pinned`/`test_monitor_scan_output_is_pinned`가 Task 2 커밋 시점과 동일 기대값으로 GREEN

- [ ] **Step 2: 전체 pytest**

Run: `cd /home/deploy/project/bid-vector-scan-memory && /home/deploy/project/bid-vector/.venv/bin/pytest -q`
Expected: 전체 green (0 failed). 실패 시 systematic-debugging으로 원인 규명 — 특성화 실패는 산출 변화이므로 구현을 되돌려 원인 Task를 수정한다(기대값 수정 금지).

- [ ] **Step 3: 프론트엔드 무변경 확인**

Run: `git -C /home/deploy/project/bid-vector-scan-memory diff origin/main --stat -- frontend/`
Expected: 출력 없음 (PR-A는 백엔드+compose만 — vitest/build 불필요)

- [ ] **Step 4: PR 본문 스켈레톤 작성 (push 후 `gh pr create --title "fix(scan): 메모리 위생 4건 — 산출 불변 (PR-A)" --body ...`에 사용)**

```markdown
## 무엇
설계 `docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md` §5 PR-A — 메모리 위생 4건 (아키텍처·산출 불변):
1. evaluations 슬림화: 후보 분석 직후 직렬화, ORM/analysis 참조 즉시 해제 (S5)
2. read-only 스캔: `find_similar_projects(read_only=True)` — 스캔 중 임베딩 영속화 스킵 + 분석 완료 행 expunge (S4)
3. 피드백 윈도우 슬림화: joined Project 를 title/category 한정 로드 (S2)
4. compose env: `MALLOC_ARENA_MAX=2`·`OMP_NUM_THREADS=1` — api/worker/ml-worker/training-worker (S1)

## 왜
2026-07-29 api OOM(8.4GiB) 근본 원인인 인라인 ML 스캔의 메모리 증가 제거 1단계.
#317(mem_limit 8g + --reload 제거)은 안전망, 본 PR 은 증가 요인 자체를 줄인다.

## 테스트
- 특성화: `tests/test_scan_memory_hygiene.py` — 동일 fixture 에서 preview/monitor 후보 목록·점수·정렬 불변 고정
- 위생 가드: 슬림 evaluation 구조 / read-only 세션 clean+expunge / 윈도우 SELECT 컬럼 한정 / compose env 선언
- 전체 pytest green (`.venv/bin/pytest -q`)

## 체크리스트
- [ ] 배포 시 compose env 반영은 `docker compose --profile tasks up -d api worker ml-worker training-worker` (restart 불가, CLAUDE.md §0)
- [ ] 배포 후 관측: preview 갱신 5회 반복 → api RSS 평탄 확인 (`docker stats`, spec §8)
- [ ] read-only 스캔의 stale 임베딩 검색은 허용 오차 (수집 파이프라인이 주기 갱신, spec §9)

## 로드맵 연결
설계 §4 3-PR 분할의 A. 후속: PR-B `feature/preview-snapshot-task`(스냅샷+task 전환, 마이그레이션 1건),
PR-C `feature/preview-snapshot-ui`. preview_cache 삭제·API 웜업 제거는 PR-B 소관.
```

- [ ] **Step 5: 최종 상태 보고**

`git -C /home/deploy/project/bid-vector-scan-memory log --oneline origin/main..` 출력(커밋 6개 내외)과 전체 pytest 요약을 사용자에게 보고하고, push/PR 생성 여부 확인을 받는다.

---

## Self-Review 결과 (계획 작성 후 점검)

- **스펙 커버리지:** §5-1→Task 3, §5-2→Task 4, §5-3→Task 5, §5-4→Task 6, §5 검증(특성화+전체 pytest)→Task 2·7. §6/§7(스냅샷·UI)은 PR-B/C 소관으로 미포함(정상).
- **placeholder 스캔:** 모든 코드 스텝에 실제 코드/커맨드/기대 출력 포함. "TBD" 없음.
- **타입 일관성:** `StrategyCandidateEvaluation(project_id, candidate, sort_key, strategy_reasons)`·`read_only`·`resolve_embedding_without_persist`·`embedding_resolver` 명칭이 Task 3↔4↔7에서 일치함을 확인.
