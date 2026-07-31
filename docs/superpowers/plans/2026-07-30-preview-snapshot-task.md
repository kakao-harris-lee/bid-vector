# PR-B "스냅샷+task 전환" (feature/preview-snapshot-task) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 설계(`docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md`) §6 PR-B — preview 를 **스냅샷(DB 영속) + 온디맨드 재계산 task** 로 전환하고, sync monitor 를 async 쌍으로 위임(202)하며, `preview_cache` 모듈과 API 웜업을 제거하고, §6.4 워커 메모리 이중 가드(`worker_max_memory_per_child` + compose `mem_limit`)를 선언한다. **API 요청 경로에서 인라인 ML 스캔이 완전히 사라진다.**

**Architecture:** 새 테이블 `operator_preview_snapshots`(UNIQUE(operator_id, high_priority_only), limit 은 키 차원이 아님)가 마지막 스캔 결과(top-100 직렬화 후보 + 스캔 메타)를 보유한다. `GET /operator/strategy/candidates` 는 순수 읽기(요청 limit 슬라이스 + `computed_at`/`snapshot_status`/`stale` 메타)이고, 부재/stale 시 **행 status 기반 DB 단일비행 가드** 하에 ops 큐 task `jobs.recompute_preview_snapshot` 를 자동 디스패치한다. task body 는 자체 `SessionLocal` + `mark_running/completed/failed`(synthetic experiment run 패턴) + `celery_task_id` 스탬프(crawl_jobs 패턴)이며 stale-task-reconciler 가 고아 running 행을 회수한다. 계산 자체는 기존 `preview_candidates → _build_preview_payload`(PR-A 특성화로 고정된 산출)를 `limit=100`(→ 스캔 예산 = PREVIEW_SCAN_CEILING 250 고정)으로 그대로 호출한다. 기존 `preview_cache.invalidate` 4개 call site(5경로)는 "기존 스냅샷 키만 재계산 디스패치"로 대체된다.

**Tech Stack:** Python 3.x + FastAPI + SQLAlchemy 2.0.23 (SQLite test / PostgreSQL prod) + Alembic + Celery(ops 큐) + pytest + PyYAML, openapi-typescript(타입 재생성).

## Global Constraints

- **TDD 필수** — 모든 변경은 실패하는 테스트 먼저.
- **GET /strategy/candidates 하위호환 superset 필수 (HARD)** — 기존 응답 필드(`operator_id`/`evaluated_project_count`/`returned_candidate_count`/`high_priority_only`/`candidates[]`/`current_operator_*`)는 이름·형태 그대로 유지하고 메타 필드만 **추가**한다. PR-C 전까지 현행 프론트가 그대로 동작해야 한다.
- **특성화 테스트 GREEN 유지** — `tests/test_scan_memory_hygiene.py` 의 preview/monitor 산출 고정 테스트는 한 번도 깨지면 안 된다(`preview_candidates` 는 삭제하지 않고 캐시 래핑만 제거 — 이탈 노트 5).
- **pytest 는 절대경로** `/home/deploy/project/bid-vector/.venv/bin/pytest` (워크트리에는 `.venv` 없음). 실행 cwd 는 워크트리 루트 `/home/deploy/project/bid-vector-preview-snapshot`.
- **브랜치 `feature/preview-snapshot-task`**, worktree `/home/deploy/project/bid-vector-preview-snapshot` (`origin/main`, PR-A #318 포함 커밋 `3721484` 이후 기준).
- **마이그레이션은 additive 1건** — 롤백 = 테이블 drop. `tests/test_schema_drift.py` 의 `MIGRATION_OWNED_TABLES` 등록 필수.
- **§4.5 선언적 구성** — stale 기준·후보 상한·자식 메모리 상한은 Settings/모듈 상수로. **§4.7 주입** — 스냅샷 서비스는 db 인자 주입 + `now=utc_now` 주입, enqueue 는 `app.tasks.jobs` 모듈 속성 late-바인딩(테스트 monkeypatch 표면).
- **sync-types**: 워크트리에서 `` /home/deploy/project/bid-vector/.venv/bin/python scripts/sync_openapi_types.py --frontend-dir /home/deploy/project/bid-vector/frontend `` (openapi-typescript 바이너리는 메인 체크아웃 `frontend/node_modules` 재사용 — 존재 확인됨. 스키마는 cwd=워크트리라 워크트리 `app` 에서 생성되고 출력도 워크트리 `frontend/src/shared/types/openapi.d.ts`).
- compose 변경 반영은 `restart` 불가 — `docker compose --profile tasks up -d <service>` 재생성 (CLAUDE.md §0, spec §9). PR 본문에 명기.

## 설계 이탈 노트 (코드 실사 후 확정한 결정)

1. **payload_json 의 datetime.** 후보 dict 의 `deadline` 은 datetime(`_serialize_candidate`)인데 `JSON` 컬럼은 datetime 을 직렬화하지 못한다. 저장 전 `deadline` 을 ISO 문자열로 변환(`_json_safe_payload`)하고, 서빙 시 `OperatorStrategyCandidateItem`(pydantic) 이 datetime 으로 복원 — 응답 형태 불변.
2. **메타 필드명 `status` → `snapshot_status`.** 스펙 §6.2 의 응답 메타 "status" 는 monitor 응답들의 `status`(task 상태) 와 의미 충돌 소지가 있어 `snapshot_status` 로 명명. `computed_at`/`stale` 은 스펙 그대로.
3. **202 적용 범위.** 스펙 §6.2 "요청 즉시 202" 는 새 `/candidates/refresh` 와 전환되는 `/monitor` 에만 `status_code=202` 로 명시한다. 기존 `/monitor/async` 는 200 계약 그대로(계약 불변, 프론트 사용 중).
4. **invalidate 는 5경로/4 call site.** 텔레그램 set/clear/버튼이 `_persist_strategy_edit`(telegram_strategy.py:216-225) 단일 seam 을 공유하므로 실제 코드 교체 지점은 `app/api/operator_strategy.py:224` · `telegram_strategy.py:225` · `decision_experiments/lifecycle.py:295,357` 의 4곳.
5. **`preview_candidates` 는 삭제하지 않는다.** 소비자가 셋: PR-A 특성화 테스트(GREEN 유지 제약), g2 recheck 워커(`evidence_jobs.py:65`), 그리고 새 스냅샷 task 자신. 캐시 래핑(`preview_cache.get_or_compute`)만 제거해 직접 계산으로 바꾼다 — 캐시 미스 경로와 동일 산출이므로 특성화 GREEN.
6. **웜업은 모듈까지 삭제.** 스펙은 "호출 제거"만 명시하나 호출 제거 후 `model_warmup.py` 는 프로덕션 호출자 0 의 죽은 코드가 된다(§4.5). 모듈 + 웜업 전용 테스트를 삭제하되, `tests/test_model_warmup.py` §6 의 **로더 단일 로드 동시성 가드**는 classifier 계약이므로 `tests/test_classifier_model_loader.py` 로 이전 보존. `EMBEDDING_MODEL_WARMUP_ON_STARTUP` 설정도 제거. 워커 측 웜업은 **추가하지 않는다** — 첫 task 가 콜드 로드(~25s)를 지불하며, 백그라운드 task 라 허용(스펙 합의).
7. **`OPERATOR_STRATEGY_PREVIEW_CACHE_TTL_SECONDS` 제거.** preview_cache 삭제로 사멸하는 설정(config.py:123, .env.example:87). staleness 는 새 `OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS=1800` 이 승계.
8. **eager 테스트 모드의 성질.** 테스트(`memory://` 브로커)에서 자동 디스패치는 인라인 실행되므로 **첫 GET 이 이미 계산된 스냅샷을 반환**한다 — 기존 GET 후보 테스트들(test_operator.py:92,206,288 등)이 무수정 GREEN 이 되는 의도된 성질. "부재 → 빈 후보+running" 은 enqueue 를 stub 으로 monkeypatch 해 검증한다.
9. **단일비행 회수 임계 재사용.** stale-running 회수는 reconciler 와 같은 공식(hard limit + grace, 60s floor)을 쓰도록 `stale_task_reconciler._stale_threshold_seconds` 를 모듈 함수 `stale_threshold_seconds()` 로 승격해 공유한다(중복 선언 금지 §4.5).

---

### Task 1: 워크트리 생성 + 계획 문서 커밋

**Files:**
- Create: `/home/deploy/project/bid-vector-preview-snapshot` (git worktree)
- Create(커밋): `docs/superpowers/plans/2026-07-30-preview-snapshot-task.md` (본 문서)

**Interfaces:**
- Consumes: `origin/main` (`3721484` = PR-A #318 머지 이후)
- Produces: 이후 모든 Task 의 작업 디렉터리, 브랜치 `feature/preview-snapshot-task`

- [ ] **Step 1: 워크트리 생성**

```bash
cd /home/deploy/project/bid-vector
git fetch origin
git worktree add ../bid-vector-preview-snapshot -b feature/preview-snapshot-task origin/main
```

- [ ] **Step 2: 계획 문서를 워크트리로 복사**

```bash
cp /home/deploy/project/bid-vector/docs/superpowers/plans/2026-07-30-preview-snapshot-task.md \
   /home/deploy/project/bid-vector-preview-snapshot/docs/superpowers/plans/
```

(spec `2026-07-30-inline-ml-memory-design.md` 는 PR-A 에서 이미 tracked — 복사 불필요. 없으면 함께 복사·커밋)

- [ ] **Step 3: 상태 확인**

Run: `git -C /home/deploy/project/bid-vector-preview-snapshot status --short`
Expected: 계획 문서만 `??`, 브랜치 `feature/preview-snapshot-task`

- [ ] **Step 4: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add docs/superpowers/plans/2026-07-30-preview-snapshot-task.md
git commit -m "docs(plan): PR-B 스냅샷+task 전환 구현 계획"
```

---

### Task 2: OperatorPreviewSnapshot 모델 + Alembic 마이그레이션 + drift 등록

**Files:**
- Modify: `app/models/models.py` (파일 끝, `OnboardingSuggestion` 뒤 — 현재 771행)
- Create: `alembic/versions/b7e3a9c4d5f1_add_operator_preview_snapshots.py` (down_revision = 현재 head `a1f4c8e7b2d9`, `alembic heads` 로 재확인)
- Modify: `tests/test_schema_drift.py:59-66` (`MIGRATION_OWNED_TABLES` 에 추가)

**Interfaces:**
- Produces: `OperatorPreviewSnapshot(operator_id, high_priority_only, status, task_id, payload_json, computed_at, last_error, updated_at)` — Task 3+ 전부가 의존

- [ ] **Step 1: 모델 추가 + drift 테이블 등록 (실패 유도)**

`app/models/models.py` 끝에 추가 (import 행 2 는 이미 `Boolean/JSON/String/Text/UniqueConstraint` 보유 — 변경 불필요):

```python
class OperatorPreviewSnapshot(Base):
    """운영자 전략 preview 의 마지막 계산 결과 스냅샷 (키: operator x high_priority_only).

    설계 2026-07-30 §6.1: GET /operator/strategy/candidates 는 요청 경로 인라인
    ML 스캔 대신 이 행을 순수 읽기한다. **limit 은 키 차원이 아니다** — 상한
    예산(PREVIEW_SCAN_CEILING)으로 1회 계산해 top-100 을 ``payload_json`` 에
    저장하고 요청 limit(≤100)은 서빙 시 슬라이스한다. ``status`` 3값
    (idle/running/failed)이 DB 단일비행 가드의 원천이고, ``task_id`` 는
    crawl_jobs.celery_task_id 패턴(소유 task 추적 + 재전달 멱등)이다.
    ``computed_at`` 은 마지막 **성공** 계산 시각(stale 판정 기준),
    ``last_error`` 는 마지막 실패 사유(성공 시 null 로 초기화).
    """
    __tablename__ = "operator_preview_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "operator_id", "high_priority_only",
            name="uq_operator_preview_snapshots_key",
        ),
    )

    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    high_priority_only = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), default="idle", index=True)  # idle / running / failed
    task_id = Column(String(155), nullable=True, index=True)
    payload_json = Column(JSON(none_as_null=True), nullable=True)
    computed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User")
```

`tests/test_schema_drift.py` `MIGRATION_OWNED_TABLES` 에 `"operator_preview_snapshots",` 추가.

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_schema_drift.py -q`
Expected: FAIL — `migration path did not create expected table 'operator_preview_snapshots'` (마이그레이션 미작성)

- [ ] **Step 3: 마이그레이션 작성**

`alembic/versions/b7e3a9c4d5f1_add_operator_preview_snapshots.py` (d8b3f0a1c9e2 스타일 — inspector 가드 + 명시 인덱스명):

```python
"""add operator_preview_snapshots table

preview 스냅샷 + 온디맨드 갱신(설계 2026-07-30 §6.1)의 유일한 마이그레이션.
키는 UNIQUE(operator_id, high_priority_only) — limit 은 키 차원이 아니며 상한
예산으로 1회 계산한 top-100(payload_json)을 서빙 시 슬라이스한다. status 가
DB 단일비행 가드, task_id 는 crawl_jobs celery_task_id 패턴(String(155)).
additive-only: 롤백 = 테이블 drop. SQLite(CI)/Postgres 양쪽에서 동작한다.

Revision ID: b7e3a9c4d5f1
Revises: a1f4c8e7b2d9
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b7e3a9c4d5f1"
down_revision: Union[str, None] = "a1f4c8e7b2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "operator_preview_snapshots"
INDEXES = (
    ("ix_operator_preview_snapshots_operator_id", ["operator_id"]),
    ("ix_operator_preview_snapshots_status", ["status"]),
    ("ix_operator_preview_snapshots_task_id", ["task_id"]),
)
UNIQUE_NAME = "uq_operator_preview_snapshots_key"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("high_priority_only", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("task_id", sa.String(length=155), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operator_id", "high_priority_only", name=UNIQUE_NAME),
    )
    for index_name, columns in INDEXES:
        op.create_index(index_name, TABLE_NAME, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    for index_name, _ in INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
```

- [ ] **Step 4: 통과 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_schema_drift.py -q`
Expected: 3 passed (테이블·컬럼 드리프트 0)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/models/models.py alembic/versions/b7e3a9c4d5f1_add_operator_preview_snapshots.py tests/test_schema_drift.py
git commit -m "feat(snapshot): operator_preview_snapshots 모델+마이그레이션 — UNIQUE(operator,hpo) 키, limit 은 키 차원 아님 (§6.1)"
```

---

### Task 3: PreviewSnapshotService — DB-first 라이프사이클 + 단일비행 클레임 + JSON-safe payload

**Files:**
- Create: `app/services/preview_snapshot.py`
- Modify: `app/core/config.py:123` 근처 (`OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS` 추가)
- Modify: `app/services/stale_task_reconciler.py:84-90` (`_stale_threshold_seconds` → 모듈 함수 승격)
- Create: `tests/test_preview_snapshot.py`

**Interfaces:**
- Consumes: `StrategyMonitoringService.preview_candidates(db, *, limit, high_priority_only, operator)` (PR-A 특성화 고정 산출), `OperatorPreviewSnapshot`, `stale_threshold_seconds()`
- Produces (Task 4~7 이 의존):
  - `PreviewSnapshotService(monitoring_service=None, now=utc_now)`
  - `.run_recompute(db, *, operator_id, high_priority_only, task_id) -> dict` (task body)
  - `.mark_running / .mark_completed / .mark_failed` (synthetic experiment run 패턴)
  - `.get_row / ._get_or_create_row / ._claim` (DB 단일비행)
  - `.resolve_high_priority_key(db, *, operator, high_priority_only) -> bool`
  - 상수 `SNAPSHOT_STATUS_IDLE/RUNNING/FAILED`, `SNAPSHOT_CANDIDATE_LIMIT = 100`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_snapshot.py` 신규:

```python
"""preview 스냅샷 서비스·task·API 전환 가드 (설계 2026-07-30 §6 PR-B).

구 preview_cache(프로세스-로컬 single-flight+TTL, #315)의 행동 의도를 DB
스냅샷으로 승계한다: 스탬피드 방지 = 행 status DB 단일비행, 단기 TTL =
OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS 기반 stale 판정 + 자동 재계산 디스패치
(스냅샷은 즉시 서빙). fixture 는 test_scan_memory_hygiene 의 특성화 패턴을
재사용한다(운영자 구성 + 결정적 _analyze_project 스텁).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import OperatorPreviewSnapshot, Project
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.preview_snapshot import (
    SNAPSHOT_CANDIDATE_LIMIT,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_IDLE,
    SNAPSHOT_STATUS_RUNNING,
    PreviewSnapshotService,
)


def _configure_software_operator(client):
    """싱글턴 운영자 + software 감시 전략 (test_scan_memory_hygiene 패턴)."""
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


def _seed_matching_project(test_db, *, title: str = "서울 AI 데이터 통합 플랫폼 구축") -> Project:
    project = Project(
        title=title,
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        status="open",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _canned_analyze(self, db, project, **kwargs):
    """결정적 분석 스텁 — 임계값 통과 고정."""
    del db, kwargs
    return {
        "matched_score": 0.7,
        "probability_score": 0.8,
        "recommended_amount": 111_000_000.0,
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
            "priority_score": 0.9,
            "recommended_amount": 111_000_000.0,
            "probability_score": 0.8,
            "reasoning": "스냅샷 고정",
        },
    }


def _snapshot_row(test_db, operator_id: int, high_priority_only: bool = False):
    return (
        test_db.query(OperatorPreviewSnapshot)
        .filter(
            OperatorPreviewSnapshot.operator_id == operator_id,
            OperatorPreviewSnapshot.high_priority_only == high_priority_only,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# 서비스 라이프사이클 (task body 관점)
# ---------------------------------------------------------------------------


def test_run_recompute_persists_top100_payload_and_meta(client, test_db, monkeypatch):
    """run_recompute 는 mark_running→계산→mark_completed 로 스냅샷을 영속화한다."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    operator = ensure_operator_account(test_db)

    result = PreviewSnapshotService().run_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False, task_id="t-1"
    )

    row = _snapshot_row(test_db, int(operator.id))
    assert row is not None
    assert row.status == SNAPSHOT_STATUS_IDLE
    assert row.task_id == "t-1"
    assert row.computed_at is not None
    assert row.last_error is None
    stored = row.payload_json
    assert stored["evaluated_project_count"] == 1
    assert [c["project_id"] for c in stored["candidates"]] == [project.id]
    # JSON-safe: deadline 은 ISO 문자열로 저장된다 (이탈 노트 1)
    assert isinstance(stored["candidates"][0]["deadline"], str)
    assert result["snapshot_id"] == row.id
    assert result["candidate_count"] == 1


def test_run_recompute_failure_marks_failed_with_last_error(client, test_db, monkeypatch):
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    def boom(self, db, **kwargs):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(StrategyMonitoringService, "preview_candidates", boom)

    with pytest.raises(RuntimeError, match="scan exploded"):
        PreviewSnapshotService().run_recompute(
            test_db, operator_id=int(operator.id), high_priority_only=False, task_id="t-2"
        )

    row = _snapshot_row(test_db, int(operator.id))
    assert row.status == SNAPSHOT_STATUS_FAILED
    assert "scan exploded" in (row.last_error or "")


# ---------------------------------------------------------------------------
# DB 단일비행 클레임 (구 preview_cache single-flight 의 승계)
# ---------------------------------------------------------------------------


def test_claim_is_single_flight(test_db):
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )

    assert service._claim(test_db, row) is True
    assert service._claim(test_db, row) is False  # running 이면 스킵


def test_claim_reclaims_stale_running_row(test_db):
    """reconciler 임계를 넘긴 running(SIGKILL 고아)은 회수 후 재클레임된다."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    assert service._claim(test_db, row) is True
    # 고아 시뮬레이션: updated_at 을 임계 밖으로 밀어낸다
    test_db.query(OperatorPreviewSnapshot).filter(
        OperatorPreviewSnapshot.id == row.id
    ).update({"updated_at": utc_now() - timedelta(hours=2)}, synchronize_session=False)
    test_db.commit()

    assert service._claim(test_db, row) is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py -q`
Expected: 수집 오류 `ModuleNotFoundError: No module named 'app.services.preview_snapshot'`

- [ ] **Step 3: 최소 구현**

(3a) `app/core/config.py` — 123행(구 preview TTL 설정 근처)에 추가:

```python
    # preview 스냅샷 stale 기준(초). computed_at 이 이보다 오래되면 GET 이 기존
    # 스냅샷을 즉시 서빙하면서 재계산 task 를 단일비행 가드 하에 자동 디스패치
    # 한다(설계 2026-07-30 §6.2). 명시 갱신은 POST /candidates/refresh.
    OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS: int = 1800
```

(3b) `app/services/stale_task_reconciler.py` — `_stale_threshold_seconds`(84-90행)를 모듈 함수로 승격하고 메서드는 위임:

```python
def stale_threshold_seconds() -> int:
    """비종단 task 행을 고아로 판정하는 나이(초): hard limit + grace, 60s floor.

    reconciler 와 preview 스냅샷의 단일비행 회수(stale-running reclaim)가 같은
    임계를 공유한다(설계 §6.2 — "running 이 stale-task-reconciler 임계 초과면
    회수"). 단일 출처(§4.5).
    """
    hard_limit = max(0, int(settings.CELERY_TASK_TIME_LIMIT_SECONDS))
    grace = max(0, int(settings.STALE_TASK_RECONCILER_GRACE_SECONDS))
    return max(60, hard_limit + grace)
```

클래스 내부는 `def _stale_threshold_seconds(self) -> int: return stale_threshold_seconds()` 로 축약(기존 호출부 `reconcile` 불변).

(3c) `app/services/preview_snapshot.py` 신규 (~230행):

```python
"""전략 preview 스냅샷: DB 영속 마지막 스캔 + 온디맨드 재계산 (설계 2026-07-30 §6).

GET /operator/strategy/candidates 는 더 이상 요청 경로에서 인라인 ML 을 실행하지
않는다. 마지막 계산 결과(top-100 직렬화 후보 + 스캔 메타)를
``operator_preview_snapshots`` 행에 두고 서빙은 순수 읽기(요청 limit 슬라이스)만
한다. 재계산은 ops 큐 task 로만 실행되며 행 ``status`` 가 DB 단일비행 가드다:
running 이면 스킵, running 이 reconciler 임계(stale_threshold_seconds)를 넘긴
고아면 회수 후 재클레임.

구 preview_cache(#315)의 행동 의도 승계: 스탬피드 방지 = DB 단일비행(프로세스
경계를 넘어 동작 — 구현보다 강한 보장), 단기 TTL = stale 판정 + 자동 디스패치.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_strategy_for
from app.core.time import ensure_utc, utc_now
from app.models.models import OperatorPreviewSnapshot, User
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.stale_task_reconciler import stale_threshold_seconds

logger = logging.getLogger(__name__)

SNAPSHOT_STATUS_IDLE = "idle"
SNAPSHOT_STATUS_RUNNING = "running"
SNAPSHOT_STATUS_FAILED = "failed"
#: 스냅샷이 저장하는 직렬화 후보 상한. GET limit 쿼리 상한(le=100)과 같은 값이라
#: 어떤 요청 limit 도 슬라이스만으로 충족된다(설계 §6.1 — limit 은 키 차원이
#: 아님). 100 을 preview_candidates 에 넘기면 스캔 예산은
#: _preview_scan_limit(100)=min(1200, PREVIEW_SCAN_CEILING)=250 으로 고정된다.
SNAPSHOT_CANDIDATE_LIMIT = 100


class PreviewSnapshotService:
    """스냅샷 행의 조회·클레임·라이프사이클·서빙 슬라이스."""

    def __init__(
        self,
        *,
        monitoring_service: StrategyMonitoringService | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._monitoring = monitoring_service or StrategyMonitoringService()
        self._now = now

    # --- 행 조회/생성 -------------------------------------------------------

    def get_row(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        return (
            db.query(OperatorPreviewSnapshot)
            .filter(
                OperatorPreviewSnapshot.operator_id == int(operator_id),
                OperatorPreviewSnapshot.high_priority_only == bool(high_priority_only),
            )
            .first()
        )

    def _get_or_create_row(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        row = self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)
        if row is not None:
            return row
        row = OperatorPreviewSnapshot(
            operator_id=int(operator_id),
            high_priority_only=bool(high_priority_only),
            status=SNAPSHOT_STATUS_IDLE,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # 동시 생성 경합은 UNIQUE(operator_id, high_priority_only)가 판정한다.
            db.rollback()
            row = self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)
        return row

    # --- DB 단일비행 --------------------------------------------------------

    def _claim(self, db: Session, row: OperatorPreviewSnapshot) -> bool:
        """행을 running 으로 원자적으로 클레임한다 (설계 §6.2 단일비행 가드).

        UPDATE ... WHERE (status != running OR updated_at <= 회수컷오프) 의
        rowcount 판정이라 여러 프로세스가 동시에 디스패치해도 한쪽만 이긴다.
        회수컷오프는 stale-task-reconciler 와 같은 임계 — 그보다 오래된 running
        은 SIGKILL/재시작 고아라 실제 실행 중일 수 없다.
        """
        reclaim_cutoff = self._now() - timedelta(seconds=stale_threshold_seconds())
        claimed = (
            db.query(OperatorPreviewSnapshot)
            .filter(
                OperatorPreviewSnapshot.id == int(row.id),
                or_(
                    OperatorPreviewSnapshot.status != SNAPSHOT_STATUS_RUNNING,
                    OperatorPreviewSnapshot.updated_at <= reclaim_cutoff,
                ),
            )
            .update(
                {"status": SNAPSHOT_STATUS_RUNNING, "updated_at": self._now()},
                synchronize_session=False,
            )
        )
        db.commit()
        return bool(claimed)

    # --- task 라이프사이클 (synthetic experiment run 패턴, DB-first) ---------

    def run_recompute(
        self,
        db: Session,
        *,
        operator_id: int,
        high_priority_only: bool,
        task_id: str | None,
    ) -> dict:
        """task body: mark_running → 스캔(preview_candidates) → mark_completed/failed.

        celery_task_id 멱등(crawl_jobs 패턴): 키당 행이 UNIQUE 라 고아 행 자체가
        생길 수 없고, acks_late 재전달(동일 task id)은 task_id 가 일치하는 자기
        행을 그대로 재사용해 재계산한다.
        """
        row = self._get_or_create_row(
            db, operator_id=operator_id, high_priority_only=high_priority_only
        )
        if row is None:  # pragma: no cover - 생성 경합 직후 소실 불가
            raise ValueError(f"snapshot row unavailable for operator {operator_id}")
        row_id = int(row.id)
        self.mark_running(db, row_id=row_id, task_id=task_id)
        try:
            operator = db.query(User).filter(User.id == int(operator_id)).first()
            if operator is None:
                raise ValueError(f"Operator {int(operator_id)} not found")
            payload = self._monitoring.preview_candidates(
                db,
                limit=SNAPSHOT_CANDIDATE_LIMIT,
                high_priority_only=bool(high_priority_only),
                operator=operator,
            )
            self.mark_completed(db, row_id=row_id, payload=self._json_safe_payload(payload))
        except Exception as exc:
            db.rollback()
            self.mark_failed(db, row_id=row_id, error=str(exc))
            raise
        return {
            "operator_id": int(operator_id),
            "high_priority_only": bool(high_priority_only),
            "snapshot_id": row_id,
            "candidate_count": int(payload.get("returned_candidate_count") or 0),
            "evaluated_project_count": int(payload.get("evaluated_project_count") or 0),
        }

    def mark_running(self, db: Session, *, row_id: int, task_id: str | None) -> None:
        row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
        if row is None:
            return
        row.status = SNAPSHOT_STATUS_RUNNING
        if task_id:
            row.task_id = str(task_id)
        row.updated_at = self._now()
        db.commit()

    def mark_completed(self, db: Session, *, row_id: int, payload: dict) -> None:
        row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
        if row is None:
            return
        row.status = SNAPSHOT_STATUS_IDLE
        row.payload_json = payload
        row.computed_at = self._now()
        row.last_error = None
        db.commit()

    def mark_failed(self, db: Session, *, row_id: int, error: str) -> None:
        """실패 마킹 — run_recompute 의 except 암에서 호출되므로 절대 raise 금지."""
        try:
            row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
            if row is None:
                return
            row.status = SNAPSHOT_STATUS_FAILED
            row.last_error = str(error)
            db.commit()
        except Exception:  # noqa: BLE001 - 원인 예외를 가리지 않는다
            db.rollback()

    # --- 직렬화/키 해석 ------------------------------------------------------

    def resolve_high_priority_key(
        self, db: Session, *, operator: User, high_priority_only: bool | None
    ) -> bool:
        """요청 파라미터(None=전략 기본값)를 스냅샷 키 불리언으로 해석한다."""
        strategy = ensure_operator_strategy_for(db, operator)
        _, resolved = self._monitoring._resolve_runtime_options(
            strategy, limit=None, high_priority_only=high_priority_only
        )
        return bool(resolved)

    def _json_safe_payload(self, payload: dict) -> dict:
        """payload_json(JSON 컬럼)에 넣을 수 있게 후보 deadline 을 ISO 문자열화.

        후보 dict 에서 datetime 은 deadline 뿐이다(_serialize_candidate). 서빙 시
        OperatorStrategyCandidateItem(pydantic)이 datetime 으로 복원하므로 응답
        형태는 불변이다(구현 계획 이탈 노트 1).
        """
        candidates = [
            {
                **candidate,
                "deadline": candidate["deadline"].isoformat()
                if isinstance(candidate.get("deadline"), datetime)
                else candidate.get("deadline"),
            }
            for candidate in payload.get("candidates") or []
        ]
        return {**payload, "candidates": candidates}

    def _computed_age_exceeds(self, row: OperatorPreviewSnapshot) -> bool:
        """마지막 성공 계산이 stale 기준을 넘겼는가 (응답 stale 플래그의 원천)."""
        if row.computed_at is None:
            return False
        age = self._now() - ensure_utc(row.computed_at)
        return age > timedelta(seconds=int(settings.OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS))

    def _needs_recompute(self, row: OperatorPreviewSnapshot) -> bool:
        """성공 계산이 없거나(최초/실패) stale 이면 재계산 대상이다."""
        return row.computed_at is None or self._computed_age_exceeds(row)
```

- [ ] **Step 4: 통과 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_stale_task_reconciler.py -q`
Expected: 전부 PASS (reconciler 기존 8건 포함 — 함수 승격은 위임이라 불변)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/services/preview_snapshot.py app/core/config.py app/services/stale_task_reconciler.py tests/test_preview_snapshot.py
git commit -m "feat(snapshot): PreviewSnapshotService — DB-first 라이프사이클+단일비행 클레임+JSON-safe payload (§6.2-6.3)"
```

---

### Task 4: Celery task `jobs.recompute_preview_snapshot` + ops 큐 라우팅 + 디스패치 + reconciler 등록

**Files:**
- Modify: `app/tasks/celery_app.py:108-109` (task 이름 상수), `:112-133` (`build_task_routes`)
- Modify: `app/tasks/jobs.py` (task shell + enqueue helper — `reconcile_stale_task_runs` 근처, 파일 끝 enqueue 헬퍼 블록)
- Modify: `app/services/preview_snapshot.py` (`dispatch_recompute` / `dispatch_for_strategy_write` 추가)
- Modify: `app/services/stale_task_reconciler.py` (`_reconcile_preview_snapshots` + 결과 키)
- Test: `tests/test_preview_snapshot.py` 추가, `tests/test_stale_task_reconciler.py` 추가

**Interfaces:**
- Produces:
  - `PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME = "jobs.recompute_preview_snapshot"` (ops 큐 라우팅)
  - `jobs.recompute_preview_snapshot(operator_id, high_priority_only)` / `jobs.enqueue_preview_snapshot_recompute(*, operator_id, high_priority_only)`
  - `PreviewSnapshotService.dispatch_recompute(db, *, operator_id, high_priority_only) -> OperatorPreviewSnapshot | None` (None = running 스킵)
  - `PreviewSnapshotService.dispatch_for_strategy_write(db, *, operator_id) -> int`
  - reconcile 결과 키 `preview_snapshots_finalized`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_snapshot.py` 에 추가:

```python
class _EnqueueRecorder:
    """jobs.enqueue_preview_snapshot_recompute 대역 — 실행 없이 디스패치만 기록."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    def __call__(self, *, operator_id: int, high_priority_only: bool):
        self.calls.append((int(operator_id), bool(high_priority_only)))
        return SimpleNamespace(id=f"stub-task-{len(self.calls)}")


@pytest.fixture
def enqueue_stub(monkeypatch) -> _EnqueueRecorder:
    from app.tasks import jobs

    recorder = _EnqueueRecorder()
    monkeypatch.setattr(jobs, "enqueue_preview_snapshot_recompute", recorder)
    return recorder


def test_recompute_task_is_routed_to_ops_queue():
    """워커 컨테이너(이미 monitor·g2 recheck 로 동일 스캔 실행 중)로 라우팅 (§6.3)."""
    from app.core.config import settings
    from app.tasks.celery_app import PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME, build_task_routes

    assert PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME == "jobs.recompute_preview_snapshot"
    assert build_task_routes()[PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME]["queue"] == settings.CELERY_OPS_QUEUE


def test_dispatch_recompute_is_single_flight(test_db, enqueue_stub):
    """running 중 재디스패치는 스킵 — 구 preview_cache 스탬피드 방지의 승계."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()

    first = service.dispatch_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    second = service.dispatch_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )

    assert first is not None
    assert first.status == SNAPSHOT_STATUS_RUNNING
    assert first.task_id == "stub-task-1"
    assert second is None
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_dispatch_for_strategy_write_targets_existing_keys_only(test_db, enqueue_stub):
    """기존 스냅샷 행이 있는 키만 재계산 — 미사용 키 변형 스캔 방지 (§6.3)."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    for key in (False, True):
        service._get_or_create_row(
            test_db, operator_id=int(operator.id), high_priority_only=key
        )

    dispatched = service.dispatch_for_strategy_write(test_db, operator_id=int(operator.id))

    assert dispatched == 2
    assert sorted(enqueue_stub.calls) == [(int(operator.id), False), (int(operator.id), True)]


def test_dispatch_for_strategy_write_defaults_to_false_key_when_no_rows(test_db, enqueue_stub):
    operator = ensure_operator_account(test_db)

    dispatched = PreviewSnapshotService().dispatch_for_strategy_write(
        test_db, operator_id=int(operator.id)
    )

    assert dispatched == 1
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_recompute_task_reuses_unique_row_idempotently(client, test_db, monkeypatch):
    """같은 키 재실행(재전달 상당)은 두 번째 행을 만들지 않는다 (celery_task_id 멱등)."""
    from app.tasks import jobs

    _configure_software_operator(client)
    _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    operator = ensure_operator_account(test_db)

    jobs.recompute_preview_snapshot.apply_async(
        kwargs={"operator_id": int(operator.id), "high_priority_only": False}
    )
    jobs.recompute_preview_snapshot.apply_async(
        kwargs={"operator_id": int(operator.id), "high_priority_only": False}
    )

    rows = (
        test_db.query(OperatorPreviewSnapshot)
        .filter(OperatorPreviewSnapshot.operator_id == int(operator.id))
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == SNAPSHOT_STATUS_IDLE
    assert rows[0].computed_at is not None
```

`tests/test_stale_task_reconciler.py` 에 추가 (기존 `_operator`/`_stale_age_seconds` 재사용):

```python
def test_reconciler_finalizes_stale_running_preview_snapshot(test_db):
    """running 고아 스냅샷 행은 failed 로 마감된다 (설계 §6.3 테이블 등록)."""
    from app.models.models import OperatorPreviewSnapshot

    operator = _operator(test_db)
    row = OperatorPreviewSnapshot(
        operator_id=operator.id,
        high_priority_only=False,
        status="running",
        updated_at=utc_now() - timedelta(seconds=_stale_age_seconds()),
    )
    test_db.add(row)
    test_db.commit()
    row_id = row.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["preview_snapshots_finalized"] == 1
    assert result["total_finalized"] == 1
    refreshed = test_db.query(OperatorPreviewSnapshot).filter_by(id=row_id).first()
    assert refreshed.status == "failed"
    assert RECONCILED_MARKER in (refreshed.last_error or "")
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_stale_task_reconciler.py -q`
Expected: 신규 6건 FAIL — 순서대로 `ImportError: ... 'PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME'`, `AttributeError: ... 'enqueue_preview_snapshot_recompute'`(fixture), `AttributeError: ... 'dispatch_recompute'`, `KeyError: 'preview_snapshots_finalized'`. 기존 테스트는 PASS 유지.

- [ ] **Step 3: 최소 구현**

(3a) `app/tasks/celery_app.py` — 109행 `NOTIFY_AWARD_RESULTS_TASK_NAME` 아래에:

```python
PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME = "jobs.recompute_preview_snapshot"
```

`build_task_routes()` 의 `RECONCILE_STALE_TASK_RUNS_TASK_NAME` 행 아래에:

```python
        PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
```

(3b) `app/tasks/jobs.py` — celery_app import 블록에 `PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME` 추가, 상단에 `from app.services.preview_snapshot import PreviewSnapshotService` 추가(preview_snapshot 은 tasks 를 모듈 레벨에서 import 하지 않으므로 순환 없음). `reconcile_stale_task_runs` 아래에:

```python
@celery_app.task(bind=True, name=PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME)
def recompute_preview_snapshot(self, operator_id: int, high_priority_only: bool = False) -> dict:
    """운영자 preview 스냅샷 1키를 재계산·영속화한다 (설계 2026-07-30 §6.3).

    body 는 자체 SessionLocal + DB-first 라이프사이클(mark_running/completed/
    failed — synthetic experiment run 패턴). celery_task_id 멱등(crawl_jobs
    패턴): UNIQUE(operator_id, high_priority_only) 행에 task id 를 스탬프하고
    acks_late 재전달은 같은 행을 재사용한다(고아 행 불가). 고아 running 은
    stale-task-reconciler 가 회수한다.
    """
    task_id = getattr(getattr(self, "request", None), "id", None)
    db = SessionLocal()
    try:
        return PreviewSnapshotService().run_recompute(
            db,
            operator_id=int(operator_id),
            high_priority_only=bool(high_priority_only),
            task_id=str(task_id) if task_id else None,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

파일 끝 enqueue 헬퍼 블록(`enqueue_koneps_notice_collection` 아래)에:

```python
def enqueue_preview_snapshot_recompute(*, operator_id: int, high_priority_only: bool):
    """Queue a preview-snapshot recompute task and return the async task handle."""
    return recompute_preview_snapshot.apply_async(
        kwargs={
            "operator_id": int(operator_id),
            "high_priority_only": bool(high_priority_only),
        },
        queue=settings.CELERY_OPS_QUEUE,
    )
```

(3c) `app/services/preview_snapshot.py` — `_claim` 아래에 디스패치 2종 추가:

```python
    # --- 디스패치 (API·전략 쓰기 트리거) --------------------------------------

    def dispatch_recompute(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        """단일비행 가드 하에 재계산 task 를 디스패치한다.

        반환: 클레임에 성공해 디스패치한 행(재조회본). 이미 running 이라
        스킵했으면 None. enqueue 실패는 요청을 죽이지 않고 행을 failed 로
        마감한다(다음 GET 이 재디스패치).
        """
        row = self._get_or_create_row(
            db, operator_id=operator_id, high_priority_only=high_priority_only
        )
        if row is None:  # pragma: no cover - 생성 경합 직후 소실 불가
            return None
        if not self._claim(db, row):
            return None
        try:
            # 순환 import 회피 + 테스트 monkeypatch 표면(§4.7): 호출 시점에
            # app.tasks.jobs 모듈 속성으로 해석한다.
            from app.tasks import jobs

            async_result = jobs.enqueue_preview_snapshot_recompute(
                operator_id=int(operator_id), high_priority_only=bool(high_priority_only)
            )
        except Exception as exc:  # noqa: BLE001 - enqueue 실패가 GET 을 죽여선 안 된다
            logger.exception(
                "preview snapshot enqueue failed operator_id=%s high_priority_only=%s",
                operator_id, high_priority_only,
            )
            self.mark_failed(db, row_id=int(row.id), error=f"enqueue failed: {exc}")
            return None
        # eager(테스트) 모드에선 위 enqueue 가 task 를 인라인 완주시키므로 상태를
        # 덮지 않도록 task_id 만 표적 UPDATE 한다.
        db.query(OperatorPreviewSnapshot).filter(
            OperatorPreviewSnapshot.id == int(row.id)
        ).update({"task_id": str(async_result.id)}, synchronize_session=False)
        db.commit()
        return self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)

    def dispatch_for_strategy_write(self, db: Session, *, operator_id: int) -> int:
        """전략 쓰기 후 재계산 트리거 (설계 §6.3, 구 preview_cache.invalidate 대체).

        기존 스냅샷 행이 있는 키만 디스패치한다 — 사용된 적 없는 키 변형의
        불필요한 스캔 방지. 행이 하나도 없으면 기본 키(high_priority_only=False)
        만. 반환: 디스패치 수(running 스킵 제외).
        """
        rows = (
            db.query(OperatorPreviewSnapshot)
            .filter(OperatorPreviewSnapshot.operator_id == int(operator_id))
            .all()
        )
        keys = sorted({bool(row.high_priority_only) for row in rows}) or [False]
        dispatched = 0
        for key in keys:
            if (
                self.dispatch_recompute(
                    db, operator_id=int(operator_id), high_priority_only=key
                )
                is not None
            ):
                dispatched += 1
        return dispatched
```

(3d) `app/services/stale_task_reconciler.py` — `reconcile` 에 스냅샷 스윕 추가(`crawl_finalized` 다음):

```python
        snapshot_finalized = self._reconcile_preview_snapshots(
            db, cutoff=cutoff, batch_limit=batch_limit, reason=reason
        )
```

커밋 조건과 반환 dict 를 3종으로 확장(`"preview_snapshots_finalized": snapshot_finalized`, `total` 합산), import 에 `OperatorPreviewSnapshot` 추가, 메서드:

```python
    def _reconcile_preview_snapshots(
        self,
        db: Session,
        *,
        cutoff,
        batch_limit: int,
        reason: str,
    ) -> int:
        """running 고아 preview 스냅샷 행을 failed 로 마감한다. Returns the count.

        키당 UNIQUE 행이라 삭제는 없다(payload 는 보존 — 다음 GET 이 stale 로
        서빙하며 재디스패치). ``updated_at`` 이 유일한 age 신호다:
        클레임/mark_running 이 매번 갱신하므로 cutoff 보다 오래된 running 은
        실제 실행 중일 수 없다.
        """
        rows = (
            db.query(OperatorPreviewSnapshot)
            .filter(OperatorPreviewSnapshot.status.in_(NON_TERMINAL_STATUSES))
            .filter(OperatorPreviewSnapshot.updated_at <= cutoff)
            .order_by(OperatorPreviewSnapshot.id.asc())
            .limit(batch_limit)
            .all()
        )
        finalized = 0
        for row in rows:
            row.status = "failed"
            row.last_error = self._compose_reason(row.last_error, reason)
            finalized += 1
        return finalized
```

`app/tasks/jobs.py` 의 `reconcile_stale_task_runs` 로그 라인에 `preview_snapshots=%s` + `result.get("preview_snapshots_finalized")` 추가.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_stale_task_reconciler.py tests/test_stale_task_reconciler_schedule.py tests/test_task_runtime.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/tasks/celery_app.py app/tasks/jobs.py app/services/preview_snapshot.py \
        app/services/stale_task_reconciler.py tests/test_preview_snapshot.py tests/test_stale_task_reconciler.py
git commit -m "feat(snapshot): jobs.recompute_preview_snapshot(ops 큐)+단일비행 디스패치+reconciler 등록 (§6.3)"
```

---

### Task 5: GET /strategy/candidates 순수 읽기 전환 + 응답 메타(superset) + 자동 디스패치

**Files:**
- Modify: `app/services/opportunity_monitoring/orchestration.py:25-28,34-84` (preview_cache 래핑 제거 — 직접 계산)
- Modify: `app/services/preview_snapshot.py` (`serve` + `_build_response` 추가)
- Modify: `app/api/operator_strategy.py:246-259` (`list_strategy_candidates_impl`)
- Modify: `app/schemas/operator_strategy.py:68-75` (`OperatorStrategyCandidatesResponse` 메타 추가)
- Modify: `tests/test_preview_cache.py` (`test_preview_candidates_recomputes_after_strategy_update` 삭제 — 등가 테스트는 Task 6 에서 스냅샷판으로 부활)
- Test: `tests/test_preview_snapshot.py` 추가

**Interfaces:**
- Produces:
  - `PreviewSnapshotService.serve(db, *, operator, limit, high_priority_only) -> dict` (순수 읽기 + 자동 디스패치)
  - `OperatorStrategyCandidatesResponse` 신규 필드 `computed_at: Optional[datetime]` / `snapshot_status: Literal["idle","running","failed"]` / `stale: bool` — **기존 필드 전부 유지 (하위호환 superset)**
  - `preview_candidates` = 캐시 없는 직접 계산 (특성화·g2·task 공용)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_snapshot.py` 에 추가:

```python
# ---------------------------------------------------------------------------
# GET /strategy/candidates — 순수 읽기 + 자동 디스패치 (§6.2)
# ---------------------------------------------------------------------------

_LEGACY_RESPONSE_FIELDS = {
    "operator_id", "evaluated_project_count", "returned_candidate_count",
    "high_priority_only", "candidates", "current_operator_id", "current_operator_username",
}


def test_get_candidates_bootstraps_empty_running_when_snapshot_missing(
    client, test_db, enqueue_stub
):
    """부재(최초) 시: 빈 후보 + snapshot_status=running + 단일비행 디스패치 (§6.2)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    # 하위호환 superset (HARD): 기존 필드는 전부 그대로 존재한다
    assert _LEGACY_RESPONSE_FIELDS <= set(payload.keys())
    assert payload["candidates"] == []
    assert payload["evaluated_project_count"] == 0
    assert payload["returned_candidate_count"] == 0
    assert payload["snapshot_status"] == "running"
    assert payload["computed_at"] is None
    assert payload["stale"] is False
    assert enqueue_stub.calls == [(int(operator.id), False)]
    # 재조회는 running 스킵 — 디스패치가 늘지 않는다 (스탬피드 방지)
    client.get("/api/v1/operator/strategy/candidates", params={"high_priority_only": False})
    assert len(enqueue_stub.calls) == 1


def test_get_candidates_first_read_computes_inline_in_eager_mode(client, test_db, monkeypatch):
    """eager(테스트) 모드: 첫 GET 이 스냅샷을 인라인 계산해 반환하고, 재조회는
    스캔 없이 스냅샷을 서빙한다 — 구 repeat-read 스탬피드 테스트의 승계."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    calls = {"count": 0}
    original = StrategyMonitoringService._collect_candidate_evaluations

    def counting(self, db, **kwargs):
        calls["count"] += 1
        return original(self, db, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_collect_candidate_evaluations", counting)

    first = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )
    second = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert first.status_code == 200 and second.status_code == 200
    assert calls["count"] == 1  # 스캔은 task 1회뿐
    assert {c["project_id"] for c in first.json()["candidates"]} == {project.id}
    assert first.json()["candidates"] == second.json()["candidates"]
    assert second.json()["snapshot_status"] == "idle"
    assert second.json()["computed_at"] is not None
    assert second.json()["stale"] is False


def test_get_candidates_slices_stored_top100_by_requested_limit(client, test_db, enqueue_stub):
    """저장 top-100 을 요청 limit 으로 슬라이스 — limit 은 키 차원이 아니다 (§6.1)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    stored_candidates = [
        {
            "project_id": index, "title": f"공고 {index}", "category": "software",
            "budget_estimate": 1.0, "deadline": None, "matched_score": 0.7,
            "probability_score": 0.8, "priority_score": 0.9, "action": "review",
            "recommended_amount": 1.0, "analysis_summary": "s", "strategy_reasons": ["r"],
        }
        for index in range(1, 4)
    ]
    service.mark_completed(
        test_db,
        row_id=int(row.id),
        payload={
            "operator_id": int(operator.id),
            "evaluated_project_count": 42,
            "returned_candidate_count": 3,
            "high_priority_only": False,
            "candidates": stored_candidates,
        },
    )

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 2},
    )

    payload = response.json()
    assert [c["project_id"] for c in payload["candidates"]] == [1, 2]
    assert payload["returned_candidate_count"] == 2
    assert payload["evaluated_project_count"] == 42
    assert enqueue_stub.calls == []  # 신선한 스냅샷은 디스패치하지 않는다


def test_get_candidates_serves_stale_snapshot_and_redispatches(client, test_db, enqueue_stub):
    """stale(>OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS) 시: 기존 payload 즉시 서빙
    + stale=true + 재계산 자동 디스패치 (§6.2)."""
    from app.core.config import settings

    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    service.mark_completed(
        test_db,
        row_id=int(row.id),
        payload={
            "operator_id": int(operator.id), "evaluated_project_count": 1,
            "returned_candidate_count": 1, "high_priority_only": False,
            "candidates": [{
                "project_id": 7, "title": "오래된 후보", "category": "software",
                "budget_estimate": 1.0, "deadline": None, "matched_score": 0.7,
                "probability_score": 0.8, "priority_score": 0.9, "action": "review",
                "recommended_amount": 1.0, "analysis_summary": "s", "strategy_reasons": ["r"],
            }],
        },
    )
    stale_at = utc_now() - timedelta(
        seconds=int(settings.OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS) + 60
    )
    test_db.query(OperatorPreviewSnapshot).filter(
        OperatorPreviewSnapshot.id == row.id
    ).update({"computed_at": stale_at}, synchronize_session=False)
    test_db.commit()

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    payload = response.json()
    assert [c["project_id"] for c in payload["candidates"]] == [7]  # stale 즉시 서빙
    assert payload["stale"] is True
    assert payload["snapshot_status"] == "running"  # 재계산 클레임됨
    assert enqueue_stub.calls == [(int(operator.id), False)]
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py -q`
Expected: 신규 4건 FAIL — 첫 건은 `KeyError: 'snapshot_status'`(현행 응답에 메타 없음), eager 건은 `assert calls["count"] == 1` 실패(현행은 GET 마다 인라인 스캔)

- [ ] **Step 3: 최소 구현**

(3a) `app/services/opportunity_monitoring/orchestration.py` — 25-28행 `preview_cache` import 제거, `preview_candidates`(34-84행)의 캐시 래핑 제거:

```python
        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=limit,
            high_priority_only=high_priority_only,
        )
        return self._build_preview_payload(
            db,
            strategy=strategy,
            operator=operator,
            resolved_limit=resolved_limit,
            resolved_high_priority_only=resolved_high_priority_only,
        )
```

docstring 의 캐시 문단을 교체: "이 메서드는 호출 즉시 스캔을 실행한다(캐시 없음). API 요청 경로에서 직접 호출하지 않는다 — 소비자는 스냅샷 재계산 task(`jobs.recompute_preview_snapshot`)와 g2 recheck 워커(`evidence_jobs`), 그리고 특성화 테스트다(설계 2026-07-30 §6.2: preview 서빙은 `PreviewSnapshotService.serve` 의 순수 읽기)."

(3b) `app/services/preview_snapshot.py` — `dispatch_for_strategy_write` 아래에:

```python
    # --- 서빙 (API GET — 순수 읽기) ------------------------------------------

    def serve(
        self,
        db: Session,
        *,
        operator: User,
        limit: int | None,
        high_priority_only: bool | None,
    ) -> dict:
        """스냅샷 순수 읽기 + 부재/stale 시 단일비행 자동 디스패치 (설계 §6.2).

        이 메서드는 어떤 경우에도 ML 스캔을 실행하지 않는다: 부재(최초)는 빈
        후보 + status=running, stale 은 기존 payload 즉시 서빙 — 재계산은 task
        디스패치로만 트리거된다.
        """
        strategy = ensure_operator_strategy_for(db, operator)
        resolved_limit, resolved_high_priority_only = self._monitoring._resolve_runtime_options(
            strategy, limit=limit, high_priority_only=high_priority_only
        )
        row = self.get_row(
            db, operator_id=int(operator.id), high_priority_only=resolved_high_priority_only
        )
        if row is None or self._needs_recompute(row):
            self.dispatch_recompute(
                db,
                operator_id=int(operator.id),
                high_priority_only=resolved_high_priority_only,
            )
            row = self.get_row(
                db, operator_id=int(operator.id), high_priority_only=resolved_high_priority_only
            )
        return self._build_response(
            row,
            operator=operator,
            resolved_limit=resolved_limit,
            resolved_high_priority_only=resolved_high_priority_only,
        )

    def _build_response(
        self,
        row: OperatorPreviewSnapshot | None,
        *,
        operator: User,
        resolved_limit: int,
        resolved_high_priority_only: bool,
    ) -> dict:
        """저장 top-100 을 요청 limit 으로 슬라이스해 레거시 응답 형태 + 메타를 만든다.

        레거시 필드(operator_id/evaluated_project_count/returned_candidate_count/
        high_priority_only/candidates)는 이름·형태 그대로다 — PR-C 전까지 현행
        프론트가 그대로 동작해야 하는 하위호환 superset 계약(HARD).
        """
        stored = dict(row.payload_json or {}) if row is not None else {}
        candidates = list(stored.get("candidates") or [])[: max(1, int(resolved_limit))]
        return {
            "operator_id": int(operator.id),
            "evaluated_project_count": int(stored.get("evaluated_project_count") or 0),
            "returned_candidate_count": len(candidates),
            "high_priority_only": bool(resolved_high_priority_only),
            "candidates": candidates,
            "computed_at": row.computed_at if row is not None else None,
            "snapshot_status": str(row.status or SNAPSHOT_STATUS_IDLE)
            if row is not None
            else SNAPSHOT_STATUS_RUNNING,
            "stale": self._computed_age_exceeds(row) if row is not None else False,
        }
```

(3c) `app/api/operator_strategy.py` — 상단 import 에 `from app.services.preview_snapshot import PreviewSnapshotService` 추가, `list_strategy_candidates_impl`(246-259행) 교체:

```python
def list_strategy_candidates_impl(
    target: User,
    db: Session,
    limit: int | None,
    high_priority_only: bool | None,
) -> dict:
    """스냅샷 순수 읽기 (설계 2026-07-30 §6.2) — 요청 경로 인라인 ML 스캔 없음."""
    payload = PreviewSnapshotService().serve(
        db,
        operator=target,
        limit=limit,
        high_priority_only=high_priority_only,
    )
    payload.update(_operator_context_fields(target))
    return payload
```

(3d) `app/schemas/operator_strategy.py` — `OperatorStrategyCandidatesResponse`(68-75행)에 추가:

```python
    # 스냅샷 메타 (설계 2026-07-30 §6.2). 기존 필드는 전부 유지 — PR-C 전까지
    # 현행 프론트가 그대로 동작해야 하는 하위호환 superset (HARD 제약).
    computed_at: Optional[datetime] = None
    snapshot_status: Literal["idle", "running", "failed"] = "idle"
    stale: bool = False
```

(3e) `tests/test_preview_cache.py` — `test_preview_candidates_recomputes_after_strategy_update` 삭제(전략 저장→신선 읽기 의도는 Task 6 의 end-to-end 스냅샷 테스트로 부활. PUT 이 아직 dead-cache invalidate 만 하므로 이 시점엔 성립 불가). 나머지(유닛/invalidate-intent/repeat-read)는 GREEN 유지.

- [ ] **Step 4: 통과 + 회귀 확인 (특성화 포함)**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_scan_memory_hygiene.py tests/test_preview_cache.py tests/test_operator.py tests/test_operator_context_api.py tests/test_license_gate_wiring.py tests/test_g2_candidate_recheck.py -q`
Expected: 전부 PASS — 특히 `test_preview_scan_output_is_pinned`(특성화, `preview_candidates` 직접 계산으로 동일 산출) 과 기존 GET 후보 테스트(eager 인라인 계산 성질 — 이탈 노트 8)

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/services/opportunity_monitoring/orchestration.py app/services/preview_snapshot.py \
        app/api/operator_strategy.py app/schemas/operator_strategy.py \
        tests/test_preview_snapshot.py tests/test_preview_cache.py
git commit -m "feat(snapshot): GET candidates 순수 읽기 전환 — 스냅샷 슬라이스+메타(superset)+자동 디스패치 (§6.2)"
```

---

### Task 6: 갱신 트리거 대체(4 call site) + preview_cache 완전 삭제

**Files:**
- Modify: `app/api/operator_strategy.py:25,222-224`, `app/services/notifications/telegram_strategy.py:39,216-225`, `app/services/decision_experiments/lifecycle.py:33,293-295,355-357`
- Delete: `app/services/opportunity_monitoring/preview_cache.py`, `tests/test_preview_cache.py`
- Modify: `conftest.py:73-86` (`reset_preview_cache` autouse fixture 삭제 — **삭제하지 않으면 전 스위트 수집 오류**)
- Modify: `app/core/config.py:114-123` (`OPERATOR_STRATEGY_PREVIEW_CACHE_TTL_SECONDS` 및 그 주석 삭제), `.env.example:87` (`OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS=1800` 으로 교체)
- Test: `tests/test_preview_snapshot.py` 추가 (invalidation-intent 의 스냅샷판)

**Interfaces:**
- Consumes: `PreviewSnapshotService.dispatch_for_strategy_write` (Task 4)
- Produces: repo 전체에서 `preview_cache` 참조 0

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_snapshot.py` 에 추가 (구 `test_preview_cache.py` 의 "모든 전략 쓰기는 invalidate" 섹션의 행동 의도 승계):

```python
# ---------------------------------------------------------------------------
# 모든 전략 쓰기는 재계산을 디스패치한다 (구 preview_cache.invalidate 5경로 대체)
# ---------------------------------------------------------------------------


def test_strategy_put_dispatches_recompute_for_existing_keys(client, test_db, enqueue_stub):
    """웹 PUT: 기존 스냅샷 키(양쪽)만 재계산 디스패치 (§6.3)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    for key in (False, True):
        service._get_or_create_row(
            test_db, operator_id=int(operator.id), high_priority_only=key
        )
    enqueue_stub.calls.clear()

    update = client.put("/api/v1/operator/strategy", json={"exclude_keywords": ["데이터"]})

    assert update.status_code == 200
    assert sorted(enqueue_stub.calls) == [(int(operator.id), False), (int(operator.id), True)]


def test_telegram_strategy_edit_dispatches_recompute(test_db, enqueue_stub):
    """텔레그램 set/clear/버튼은 _persist_strategy_edit 단일 seam 을 지난다."""
    from app.core.single_user import ensure_operator_strategy
    from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor

    strategy = ensure_operator_strategy(test_db)
    reply = TelegramStrategyCommandProcessor()._handle_set(test_db, ["categories=software"])

    assert "전략이 업데이트되었습니다" in reply
    assert enqueue_stub.calls == [(int(strategy.user_id), False)]


def test_experiment_threshold_apply_dispatches_recompute(test_db, enqueue_stub):
    from app.models.models import DecisionExperimentRun
    from app.schemas.schemas import DecisionExperimentThresholdApplyRequest
    from app.services.decision_experiments import DecisionExperimentService
    from app.core.single_user import ensure_operator_strategy

    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key="exp-review-threshold-tighten",
        recommendation_key="rec-exp-review-threshold-tighten",
        status="completed",
        outcome="success",
        title="threshold tuning",
    )
    test_db.add(run)
    test_db.commit()

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=False),
        operator=operator,
    )

    assert result["applied"] is True
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_experiment_threshold_dry_run_does_not_dispatch(test_db, enqueue_stub):
    from app.models.models import DecisionExperimentRun
    from app.schemas.schemas import DecisionExperimentThresholdApplyRequest
    from app.services.decision_experiments import DecisionExperimentService
    from app.core.single_user import ensure_operator_strategy

    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key="exp-review-threshold-tighten",
        recommendation_key="rec-exp-review-threshold-tighten",
        status="completed",
        outcome="success",
        title="threshold tuning",
    )
    test_db.add(run)
    test_db.commit()

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=True),
        operator=operator,
    )

    assert result["applied"] is False
    assert enqueue_stub.calls == []


def test_strategy_update_refreshes_preview_end_to_end(client, test_db, monkeypatch):
    """(eager) 저장 → 재계산 → 다음 GET 은 새 규칙 반영 — 구
    test_preview_candidates_recomputes_after_strategy_update 의 승계."""
    _configure_software_operator(client)
    _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    calls = {"count": 0}
    original = StrategyMonitoringService._collect_candidate_evaluations

    def counting(self, db, **kwargs):
        calls["count"] += 1
        return original(self, db, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_collect_candidate_evaluations", counting)

    client.get("/api/v1/operator/strategy/candidates", params={"high_priority_only": False})
    update = client.put("/api/v1/operator/strategy", json={"exclude_keywords": ["데이터"]})
    after = client.get(
        "/api/v1/operator/strategy/candidates", params={"high_priority_only": False}
    )

    assert update.status_code == 200
    assert calls["count"] == 2  # 최초 1 + 전략 저장 트리거 1 (GET 재조회는 0)
    assert after.json()["candidates"] == []  # 새 규칙이 시드 공고를 배제
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py -q`
Expected: 신규 5건 FAIL — `assert sorted(enqueue_stub.calls) == [...]` (현행은 preview_cache.invalidate 만 호출, 디스패치 0)

- [ ] **Step 3: 최소 구현 (call site 교체 → 삭제)**

(3a) `app/api/operator_strategy.py` — 25행 import 를 이미 추가된 `PreviewSnapshotService` 로 대체(중복 제거), 222-224행:

```python
    db.commit()
    db.refresh(strategy)
    # 전략 저장은 preview 산출을 바꾼다: 사용 중인 스냅샷 키를 단일비행 가드
    # 하에 재계산 디스패치한다 (설계 §6.3 — 구 preview_cache.invalidate 대체).
    PreviewSnapshotService().dispatch_for_strategy_write(db, operator_id=int(operator.id))
```

(3b) `app/services/notifications/telegram_strategy.py` — 39행 import 를 `from app.services.preview_snapshot import PreviewSnapshotService` 로 교체, `_persist_strategy_edit`(216-225행):

```python
    def _persist_strategy_edit(self, db: Session, strategy) -> None:
        """전략 편집 커밋 + 해당 운영자의 스냅샷 재계산 디스패치.

        텔레그램 set/clear/버튼은 웹 PUT 과 같은 행을 쓰므로 같은 갱신 트리거가
        필요하다(설계 §6.3): 기존 스냅샷 키만 단일비행 가드 하에 재계산한다.
        """
        db.commit()
        db.refresh(strategy)
        PreviewSnapshotService().dispatch_for_strategy_write(
            db, operator_id=int(strategy.user_id)
        )
```

(3c) `app/services/decision_experiments/lifecycle.py` — 33행 import 교체, 295행·357행을 각각:

```python
            # 적용된 임계/튜닝은 preview 후보를 바꾼다: 사용 중인 스냅샷 키를
            # 재계산 디스패치한다 (설계 §6.3 — 구 preview_cache.invalidate 대체).
            PreviewSnapshotService().dispatch_for_strategy_write(
                db, operator_id=int(target_operator.id)
            )
```

(3d) 삭제·정리:

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git rm app/services/opportunity_monitoring/preview_cache.py tests/test_preview_cache.py
```

- `conftest.py:73-86` `reset_preview_cache` autouse fixture 전체 삭제.
- `app/core/config.py` 114-123행의 preview TTL 설정+주석 블록 삭제.
- `.env.example:87` → `OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS=1800` 로 교체.
- 잔여 참조 0 확인: `grep -rn "preview_cache\|PREVIEW_CACHE_TTL" app tests conftest.py .env.example` → 출력 없음이어야 함.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_operator.py tests/test_scan_memory_hygiene.py tests/test_decision_experiments.py tests/test_telegram_strategy_commands.py -q`
(텔레그램/실험 테스트 파일명은 `ls tests | grep -i "telegram\|experiment"` 로 실측해 대체)
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add -A
git commit -m "refactor(snapshot): invalidate 5경로→스냅샷 재계산 디스패치 대체 + preview_cache 모듈·테스트·설정 삭제 (§6.3)"
```

---

### Task 7: POST /candidates/refresh (202) + sync POST /monitor → 202 async 위임

**Files:**
- Modify: `app/schemas/operator_strategy.py` (refresh 응답 스키마), `app/schemas/schemas.py:335-348,340-346` (import + `__all__` 등록)
- Modify: `app/api/operator_strategy.py` (`refresh_strategy_candidates_impl` 추가, `run_strategy_monitor_impl`(262-268행) 삭제)
- Modify: `app/api/operator.py:11`(import `status`), `:29-38`(import 목록), `:145-167`(refresh 라우트 추가 + monitor 전환)
- Modify: `tests/test_operator.py:417,1068,1075,1250,1400,1411` / `tests/test_operator_context_api.py:998,1138,1183,1213` (sync monitor 호출부 202 전환)
- Test: `tests/test_preview_snapshot.py` 추가

**Interfaces:**
- Produces:
  - `POST /api/v1/operator/strategy/candidates/refresh` → 202 `OperatorStrategyCandidatesRefreshResponse{task_id, operator_id, current_operator_*, high_priority_only, snapshot_status, detail, poll_url}` — 폴링은 `GET /candidates` 재조회(스펙 §6.2: 별도 task-status 표면 없음)
  - `POST /api/v1/operator/strategy/monitor` → 202 `OperatorStrategyMonitorTaskResponse` (기존 `run_monitor_async_impl` 재사용, 경로 유지)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_preview_snapshot.py` 에 추가:

```python
# ---------------------------------------------------------------------------
# POST /candidates/refresh (202) + sync monitor 위임 (§6.2)
# ---------------------------------------------------------------------------


def test_candidates_refresh_returns_202_envelope(client, test_db, enqueue_stub):
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    response = client.post(
        "/api/v1/operator/strategy/candidates/refresh",
        params={"high_priority_only": False},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_id"] == "stub-task-1"
    assert payload["operator_id"] == int(operator.id)
    assert payload["high_priority_only"] is False
    assert payload["snapshot_status"] == "running"
    assert payload["poll_url"] == "/api/v1/operator/strategy/candidates"
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_candidates_refresh_single_flight_reuses_running_task(client, test_db, enqueue_stub):
    _configure_software_operator(client)

    first = client.post("/api/v1/operator/strategy/candidates/refresh")
    second = client.post("/api/v1/operator/strategy/candidates/refresh")

    assert first.status_code == 202 and second.status_code == 202
    assert len(enqueue_stub.calls) == 1  # 단일비행: 두 번째는 디스패치 없음
    assert second.json()["task_id"] == first.json()["task_id"]


def test_sync_monitor_route_returns_202_async_envelope(client, test_db, monkeypatch):
    """(eager) POST /monitor 는 202 async envelope 을 반환하고 결과는 poll_url 로 읽는다."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)

    kickoff = client.post(
        "/api/v1/operator/strategy/monitor",
        json={"high_priority_only": False, "limit": 5},
    )

    assert kickoff.status_code == 202
    envelope = kickoff.json()
    assert envelope["task_name"] == "jobs.monitor_operator_strategy"
    assert envelope["poll_url"].endswith(envelope["task_id"])

    status_payload = client.get(envelope["poll_url"]).json()
    assert status_payload["status"] == "completed"
    assert status_payload["result"]["results"][0]["project_id"] == project.id
    assert (
        status_payload["result"]["trigger_source"]
        == StrategyMonitoringService.ASYNC_TRIGGER_SOURCE
    )
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py -q`
Expected: 신규 3건 FAIL — refresh 2건은 `assert 404 == 202`(라우트 없음), monitor 건은 `assert 200 == 202`

- [ ] **Step 3: 최소 구현**

(3a) `app/schemas/operator_strategy.py` — `OperatorStrategyCandidatesResponse` 아래에:

```python
class OperatorStrategyCandidatesRefreshResponse(BaseModel):
    """명시 재계산 202 응답 (설계 §6.2). 폴링은 별도 task-status 없이
    GET /operator/strategy/candidates 재조회(snapshot_status·computed_at 관찰)."""

    task_id: Optional[str] = None
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    high_priority_only: bool
    snapshot_status: Literal["idle", "running", "failed"]
    detail: str
    poll_url: str
```

`app/schemas/schemas.py` 의 operator_strategy import 블록(335행 부근)과 `__all__`(340행 부근)에 `OperatorStrategyCandidatesRefreshResponse` 를 알파벳 순서로 추가.

(3b) `app/api/operator_strategy.py` — `run_strategy_monitor_impl`(262-268행) 삭제하고 그 자리에:

```python
def refresh_strategy_candidates_impl(
    target: User,
    db: Session,
    high_priority_only: bool | None,
) -> dict:
    """명시 재계산 디스패치 (202, 설계 §6.2). 단일비행: 이미 running 이면 그
    task 를 재사용한다 — 새로고침 연타가 스캔을 중복 실행하지 못한다."""
    service = PreviewSnapshotService()
    resolved_high_priority_only = service.resolve_high_priority_key(
        db, operator=target, high_priority_only=high_priority_only
    )
    row = service.dispatch_recompute(
        db, operator_id=int(target.id), high_priority_only=resolved_high_priority_only
    )
    already_running = row is None
    if row is None:
        row = service.get_row(
            db, operator_id=int(target.id), high_priority_only=resolved_high_priority_only
        )
    return {
        "task_id": row.task_id if row is not None else None,
        "operator_id": int(target.id),
        **_operator_context_fields(target),
        "high_priority_only": bool(resolved_high_priority_only),
        "snapshot_status": str(row.status) if row is not None else "failed",
        "detail": (
            "이미 실행 중인 재계산을 재사용합니다."
            if already_running
            else "미리보기 재계산을 큐에 등록했습니다."
        ),
        "poll_url": "/api/v1/operator/strategy/candidates",
    }
```

(3c) `app/api/operator.py` — 11행을 `from fastapi import APIRouter, Depends, Query, status` 로, import 목록에서 `run_strategy_monitor_impl` 제거·`refresh_strategy_candidates_impl` 추가, 스키마 import 에 `OperatorStrategyCandidatesRefreshResponse` 추가. 145-155행(GET candidates) 아래에 refresh 라우트 추가, 158-167행(monitor) 교체:

```python
@router.post(
    "/strategy/candidates/refresh",
    response_model=OperatorStrategyCandidatesRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_operator_strategy_candidates(
    high_priority_only: bool | None = Query(default=None),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Queue a strategy-candidate snapshot recompute; poll via GET /strategy/candidates."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return refresh_strategy_candidates_impl(target, db, high_priority_only)


@router.post(
    "/strategy/monitor",
    response_model=OperatorStrategyMonitorTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_operator_strategy_monitor(
    request: OperatorStrategyMonitorRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Queue the stored-strategy monitor and return the async task envelope (202).

    설계 2026-07-30 §6.2: 요청 경로 인라인 ML 폐쇄 — 구현을 기존 async 쌍
    (/strategy/monitor/async + /strategy/monitor/tasks/{id})에 위임한다. 경로는
    유지되고 응답이 async envelope 으로 바뀐다(프론트 sync 호출부 없음 확인).
    """
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return run_monitor_async_impl(request, operator, db)
```

(3d) 기존 sync monitor 테스트 전환 — `tests/test_operator.py` 417·1068·1075·1250·1400·1411행, `tests/test_operator_context_api.py` 998·1138·1183·1213행의 `client.post(".../strategy/monitor", ...)` 사용부를 다음 패턴으로:

```python
    kickoff = client.post("/api/v1/operator/strategy/monitor", json={...})
    assert kickoff.status_code == 202
    payload = client.get(kickoff.json()["poll_url"]).json()["result"]
    # 이하 기존 단언은 payload(구 sync 응답과 동일 형태)에 그대로 적용
```

(연속 2회 호출 테스트는 각 kickoff 마다 poll. run-history 를 만들기 위해 monitor 를 쓰는 테스트는 `trigger_source` 단언이 있으면 `manual_sync` → `manual_async` 로 갱신 — `grep -n "manual_sync" tests/test_operator.py tests/test_operator_context_api.py` 로 실측.)

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_preview_snapshot.py tests/test_operator.py tests/test_operator_context_api.py tests/test_operator_strategy_monitor_finalize.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/schemas/operator_strategy.py app/schemas/schemas.py app/api/operator_strategy.py \
        app/api/operator.py tests/test_preview_snapshot.py tests/test_operator.py tests/test_operator_context_api.py
git commit -m "feat(api): candidates/refresh 202 신설 + sync monitor→async 쌍 202 위임 (§6.2)"
```

---

### Task 8: API 웜업 제거 (스펙 §6.3 / #316 역전)

**Files:**
- Modify: `app/main.py:21,57-60` (import + 호출 + 주석 제거)
- Delete: `app/services/model_warmup.py`, `tests/test_model_warmup.py`
- Create: `tests/test_classifier_model_loader.py` (로더 단일 로드 동시성 가드 보존 — 이탈 노트 6)
- Modify: `app/core/config.py:609` (`EMBEDDING_MODEL_WARMUP_ON_STARTUP` 삭제)

**Interfaces:**
- Consumes: 없음 (독립)
- Produces: API 프로세스는 시작 시 임베딩 모델을 로드하지 않는다. 단일 공고 ML(`/operations/analyze` 등)은 lazy-load 유지(비목표 §3). 워커는 첫 task 가 콜드 로드를 지불(허용).

- [ ] **Step 1: 로더 가드 이전 테스트 작성 (기존 테스트의 이동이라 즉시 GREEN)**

`tests/test_classifier_model_loader.py` 신규 — `tests/test_model_warmup.py:185-244` 의 `clean_embedding_cache` fixture 와 `test_concurrent_callers_load_the_embedding_model_only_once` 를 그대로 이동(필요 import: `threading`, `time`, `types.SimpleNamespace`, `pytest`, `NoticeClassifierService`). 모듈 docstring:

```python
"""NoticeClassifierService 임베딩 모델 로더의 단일 로드 보장 가드.

구 test_model_warmup.py §6 에서 이전(설계 2026-07-30 §6.3 — API 시작 웜업
제거로 warmup 모듈은 삭제됐지만, 요청 경로 lazy-load 가 동시에 두 번 모델을
만들지 않는다는 계약은 단일 공고 ML 경로에 여전히 유효하다).
"""
```

- [ ] **Step 2: 웜업 제거**

- `app/main.py` — 21행 `from app.services.model_warmup import start_embedding_model_warmup` 삭제, 57-60행(주석 3줄 + 호출) 삭제.
- `git rm app/services/model_warmup.py tests/test_model_warmup.py`
- `app/core/config.py:609` `EMBEDDING_MODEL_WARMUP_ON_STARTUP: bool = True` 삭제.
- 잔여 참조 0 확인: `grep -rn "model_warmup\|EMBEDDING_MODEL_WARMUP" app tests .env.example` → 출력 없음.

- [ ] **Step 3: 통과 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_classifier_model_loader.py tests/test_operations.py -q && /home/deploy/project/bid-vector/.venv/bin/python -c "import app.main"`
Expected: 전부 PASS + import 무오류

- [ ] **Step 4: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add -A
git commit -m "refactor(api): 시작 시 임베딩 웜업 제거(#316 역전) — 스캔 ML 은 워커 소관, 단일 공고 ML 은 lazy-load (§6.3)"
```

---

### Task 9: 워커 메모리 가드 — `worker_max_memory_per_child` + compose `mem_limit` (spec §6.4)

**Files:**
- Modify: `app/core/config.py:69` 근처 (설정 추가), `.env.example` (항목 추가)
- Modify: `app/tasks/celery_app.py:649-652` 부근 (조건부 conf 주입)
- Modify: `docker-compose.yml:163`(worker) `:203`(ml-worker) `:242`(training-worker) — 각 `shm_size` 다음 줄
- Modify: `tests/test_task_runtime.py:31-56`, `tests/test_compose_memory_env.py`

**Interfaces:**
- Produces: `settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB = 3145728` → celery `worker_max_memory_per_child`; compose `mem_limit` worker 10g / ml-worker 6g / training-worker 6g

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_task_runtime.py` 의 `test_build_celery_runtime_config_registers_tasks_and_worker_defaults` 에 monkeypatch 1줄 + 단언 1줄 추가:

```python
    monkeypatch.setattr(settings, "CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", 1048576)
```
```python
    assert config["worker_max_memory_per_child"] == 1048576
```

그리고 새 테스트:

```python
def test_worker_max_memory_per_child_zero_disables_the_limit(monkeypatch):
    """0 이하는 celery 기본(무제한) — conf 키 자체를 넣지 않는다."""
    monkeypatch.setattr(settings, "CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB", 0)

    config = build_celery_runtime_config()

    assert "worker_max_memory_per_child" not in config
```

`tests/test_compose_memory_env.py` 에 추가:

```python
# 스펙 §6.4: #317(api 8g)과 동일한 컨테이너 스코프 격리의 완성. worker 는 상주
# ~4.8GiB 관측 대비 여유 10g, ML 워커 2종은 6g. 값은 배포 후 관측으로 조정.
DECLARED_MEM_LIMITS = {
    "api": "8g",
    "worker": "10g",
    "ml-worker": "6g",
    "training-worker": "6g",
}


def test_mem_limits_declared_for_all_ml_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    for service_name, expected in DECLARED_MEM_LIMITS.items():
        assert str(compose["services"][service_name].get("mem_limit")) == expected, service_name
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_task_runtime.py tests/test_compose_memory_env.py -q`
Expected: FAIL — `AttributeError: ... CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB` 및 `assert str(None) == '10g'` (worker)

- [ ] **Step 3: 최소 구현**

(3a) `app/core/config.py` — 69행 `CELERY_WORKER_MAX_TASKS_PER_CHILD` 아래에:

```python
    # Celery worker 자식 프로세스 RSS 상한(KB, celery 네이티브
    # worker_max_memory_per_child). 초과한 자식은 현재 task 를 마친 뒤
    # 재생성된다 — 스캔 이주로 워커가 지게 된 glibc 아레나 비대·torch 잔류를
    # 주기적으로 리셋한다(설계 2026-07-30 §6.4). 3145728KB = 3GiB.
    # 0 이하 = 미설정(celery 기본: 무제한).
    CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB: int = 3145728
```

`.env.example` 의 celery 블록에 `CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB=3145728` 추가.

(3b) `app/tasks/celery_app.py` — `build_celery_runtime_config` 의 soft/hard limit 조건부(649-652행) 아래에 동일 패턴으로:

```python
    if settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB > 0:
        config["worker_max_memory_per_child"] = int(
            settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB
        )
```

(3c) `docker-compose.yml` — worker 163행(`shm_size: "1gb"` 다음)에:

```yaml
    # 스캔 ML 이주지(설계 2026-07-30 §6.4): monitor·g2 recheck·preview 스냅샷
    # 재계산이 임베딩 포함 스캔을 이 컨테이너에서 실행한다. 상주 ~4.8GiB 관측
    # 대비 여유 10g — worker_max_memory_per_child(자식 3GiB)와 이중 가드.
    # 값 변경 반영은 restart 가 아니라 `docker compose up -d worker` 재생성(§0).
    mem_limit: 10g
```

ml-worker 203행 아래에:

```yaml
    # ML backfill/재평가 큐 워커 메모리 상한 (설계 2026-07-30 §6.4). #317 의
    # 컨테이너 스코프 격리 완성 — 값은 배포 후 관측으로 조정.
    # 반영은 `docker compose up -d ml-worker` 재생성(§0).
    mem_limit: 6g
```

training-worker 242행 아래에 동일 블록(`up -d training-worker`) `mem_limit: 6g`.

- [ ] **Step 4: 통과 확인**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest tests/test_task_runtime.py tests/test_compose_memory_env.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add app/core/config.py app/tasks/celery_app.py docker-compose.yml .env.example \
        tests/test_task_runtime.py tests/test_compose_memory_env.py
git commit -m "feat(worker): worker_max_memory_per_child 3GiB + mem_limit worker 10g/ml 6g/training 6g 이중 가드 (§6.4)"
```

---

### Task 10: openapi.d.ts 재생성 + 전체 회귀 + PR 본문

**Files:**
- Modify(재생성): `frontend/src/shared/types/openapi.d.ts` (백엔드 전용 PR 이지만 CI `check:sync-types` 가 요구 — 다른 프론트 변경 없음)

**Interfaces:**
- Consumes: Task 2~9 전부
- Produces: green 전체 스위트 + 동기화된 타입 + PR 본문 스켈레톤

- [ ] **Step 1: 타입 재생성 + check**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
/home/deploy/project/bid-vector/.venv/bin/python scripts/sync_openapi_types.py \
  --frontend-dir /home/deploy/project/bid-vector/frontend
/home/deploy/project/bid-vector/.venv/bin/python scripts/sync_openapi_types.py \
  --frontend-dir /home/deploy/project/bid-vector/frontend --check
```

Expected: `Wrote OpenAPI types to .../bid-vector-preview-snapshot/frontend/src/shared/types/openapi.d.ts` 후 `OpenAPI types are up to date.` (스키마는 워크트리 `app` 에서, openapi-typescript 바이너리는 메인 체크아웃 node_modules 재사용 — Global Constraints 참조. diff 에 candidates 메타/refresh/monitor 202 반영 확인: `git diff --stat frontend/`)

- [ ] **Step 2: 프론트 빌드 검증 (openapi.d.ts 만 변경 — 타입 호환 확인)**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot/frontend
npm ci && npm run build
```

Expected: 빌드 성공(신규 필드는 optional/추가라 기존 코드 무영향). 실패 시 타입 사용부를 고치지 말고 스키마 하위호환 위반을 의심한다(HARD 제약).

- [ ] **Step 3: 전체 pytest**

Run: `cd /home/deploy/project/bid-vector-preview-snapshot && /home/deploy/project/bid-vector/.venv/bin/pytest -q`
Expected: 전체 green (0 failed). 특성화(`tests/test_scan_memory_hygiene.py`) 실패는 산출 변화 = 구현 오류이므로 기대값 수정 금지, 원인 Task 를 수정.

- [ ] **Step 4: 타입 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-snapshot
git add frontend/src/shared/types/openapi.d.ts
git commit -m "chore(types): openapi.d.ts 재생성 — 스냅샷 메타·refresh 202·monitor 202 반영"
```

- [ ] **Step 5: PR 본문 스켈레톤 (push 후 `gh pr create --title "feat(preview): 스냅샷+task 전환 — API 인라인 ML 스캔 전면 제거 (PR-B)" --body ...`)**

```markdown
## 무엇
설계 `docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md` §6 PR-B:
1. `operator_preview_snapshots` 테이블(마이그레이션 1건, additive) — UNIQUE(operator, high_priority_only), top-100 payload, limit 은 서빙 슬라이스
2. `jobs.recompute_preview_snapshot`(ops 큐) — DB-first 라이프사이클 + celery_task_id 멱등 + stale-task-reconciler 등록
3. `GET /strategy/candidates` 순수 읽기(+`computed_at`/`snapshot_status`/`stale` 메타, **기존 필드 superset 유지**), 부재/stale 시 DB 단일비행 자동 디스패치
4. `POST /strategy/candidates/refresh` 202 신설, sync `POST /strategy/monitor` → async 쌍 202 위임
5. `preview_cache` 모듈 삭제(#315 대체 — 스탬피드 방지는 DB 단일비행이 승계), API 시작 웜업 제거(#316 역전)
6. 워커 이중 가드: `worker_max_memory_per_child` 3GiB + compose `mem_limit` worker 10g / ml-worker 6g / training-worker 6g

## 왜
2026-07-29 api OOM 근본 원인(요청 경로 인라인 ML 스캔) 제거 2단계 — API 프로세스에서 스캔 ML 이 완전히 사라진다.
PR-A(#318)는 산출 불변 위생, 본 PR 이 아키텍처 전환.

## 테스트
- `tests/test_preview_snapshot.py`: 라이프사이클·단일비행·멱등·슬라이스·stale·트리거·202
- 특성화 GREEN 유지(`tests/test_scan_memory_hygiene.py` — preview 산출 불변)
- reconciler/compose/celery-config 가드 확장, 전체 pytest green, `check:sync-types` 통과

## 배포 체크리스트
- [ ] **마이그레이션**: api 컨테이너 command 가 `alembic upgrade head` 를 서빙 전에 수행 — `docker compose up -d api` 로 자동 적용 (additive-only, 롤백 = 테이블 drop)
- [ ] **배포 순서**: `docker compose --profile tasks up -d api worker ml-worker training-worker` (compose env/mem_limit 변경 → restart 불가, 재생성 필수 §0) + `docker compose restart beat` (beat 는 compose 변경 없음 — 코드 리로드만)
- [ ] **PR-B↔PR-C 과도기 UX**: 현행 UI 는 스냅샷 상태를 폴링하지 않는다 — 배포 직후 첫 화면은 빈 후보("갱신 중"), 최초 계산 완료 후 수동 새로고침으로 표시된다(수십 초). PR-C(`feature/preview-snapshot-ui`)가 "N분 전 기준" 배지+폴링 UX 를 붙인다
- [ ] 배포 후 관측: preview 갱신 5회 반복 → api RSS 평탄(목표 ≤1.5GiB, spec §8), worker 자식 재생성 로그(`worker_max_memory_per_child`) 확인, `docker stats` 추이 기록
- [ ] 워커 첫 task 콜드 로드(~25s)는 의도된 트레이드오프 — 워커 웜업은 추가하지 않음

## 로드맵 연결
설계 §4 3-PR 분할의 B. 후속: PR-C `feature/preview-snapshot-ui`(프론트 스냅샷 UX + 폴링).
```

- [ ] **Step 6: 최종 상태 보고**

`git -C /home/deploy/project/bid-vector-preview-snapshot log --oneline origin/main..` (커밋 10개 내외)과 전체 pytest 요약을 사용자에게 보고하고, push/PR 생성 여부 확인을 받는다.

---

## Self-Review 결과 (계획 작성 후 점검)

- **스펙 커버리지:** §6.1→Task 2, §6.2(GET/refresh/폴링/sync 폐쇄)→Task 5·7, §6.3(task/트리거/preview_cache 삭제/웜업 제거)→Task 3·4·6·8, §6.4→Task 9, §8 PR-B 검증(라이프사이클·단일비행·멱등·drift·전체 pytest)→Task 2~4·10.
- **하드 제약 재확인:** GET superset 은 Task 5 스키마(기존 필드 무변경+optional 추가)와 `_LEGACY_RESPONSE_FIELDS` 단언으로 강제. 특성화 GREEN 은 `preview_candidates` 보존(이탈 노트 5)으로 보장 — Task 5 Step 4 에서 명시 재검증.
- **placeholder 스캔:** 전 코드 스텝 실제 코드/커맨드/기대 출력 포함, "TBD" 없음. 마이그레이션 down_revision(`a1f4c8e7b2d9`)·삭제 대상 라인·테스트 전환 라인 번호는 실측값.
- **타입/명칭 일관성:** `PreviewSnapshotService.run_recompute/dispatch_recompute/dispatch_for_strategy_write/serve`, `SNAPSHOT_CANDIDATE_LIMIT`, `PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME`, `enqueue_preview_snapshot_recompute`, `OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS` 가 Task 3↔4↔5↔6↔7 에서 일치함을 확인.
- **삭제 안전:** `preview_cache` 삭제 전 conftest autouse fixture(`conftest.py:74`) 제거를 Task 6 에 명시(누락 시 전 스위트 수집 오류). `model_warmup` 삭제는 로더 동시성 가드 이전과 함께(Task 8).

### Critical Files for Implementation

- /home/deploy/project/bid-vector/app/services/preview_snapshot.py (신규 — 서비스 본체)
- /home/deploy/project/bid-vector/app/services/opportunity_monitoring/orchestration.py
- /home/deploy/project/bid-vector/app/api/operator_strategy.py (+ /home/deploy/project/bid-vector/app/api/operator.py 라우트)
- /home/deploy/project/bid-vector/app/tasks/jobs.py (+ /home/deploy/project/bid-vector/app/tasks/celery_app.py)
- /home/deploy/project/bid-vector/app/models/models.py (+ /home/deploy/project/bid-vector/tests/test_schema_drift.py)