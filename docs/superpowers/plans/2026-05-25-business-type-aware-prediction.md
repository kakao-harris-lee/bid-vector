# 업무구분 기반 예측 보정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KONEPS 업종코드를 Project에 영속화하고, 도메인 그룹(공사·용역)별 calibration을 predictor에 적용해 service 그룹 예측 오차율을 −2.0%p 이상 개선한다.

**Architecture:** 2단계 작업. Phase A는 KONEPS 크롤러가 이미 파싱하지만 버려지고 있는 `business_type` 코드/라벨을 `Project` 테이블에 추가하고 19,824건을 백필. Phase B는 `PricePredictionContext`에 코드/그룹을 주입해 `HistoricalStatisticalPredictor`에서 공사·용역 그룹별 base_rate 분기 + group-keyed guardrail + manifest의 `group_calibration` prior로 보정. 평가 게이트로 `predictor_backtest`를 그룹 차원으로 분할하고 `promote_ml_release.py preflight-rollout`에 회귀 차단 검사를 추가.

**Tech Stack:** FastAPI · SQLAlchemy · Alembic · pytest · BeautifulSoup(KONEPS HTML) · pydantic-settings · 기존 ML release manifest 체계.

**Source spec:** `docs/superpowers/specs/2026-05-25-business-type-aware-prediction-design.md`

**Rollout policy:** spec §5.3의 4단계 PR을 따른다. 각 PR은 `feature/<slug>` 또는 `chore/<slug>` 브랜치 → push → `/code-review` → 머지. 본 plan은 단일 문서이지만 task 묶음을 **PR-1(Phase A) / PR-2(Phase B no-op) / PR-3(Phase B activate) / PR-4(Manifest gate)** 로 표기.

---

## File Structure

**Create**

- `alembic/versions/<rev>_add_business_type_to_project.py` — 컬럼 2개 + 인덱스 추가 마이그레이션
- `app/ai/business_group.py` — 업종코드 prefix → 그룹 매핑 (config 기반)
- `scripts/backfill_business_type.py` — 19,824건 백필 (source_url detail + title-rule fallback)
- `tests/test_business_type_backfill.py` — 백필 스크립트 단위 테스트
- `tests/test_business_group_resolver.py` — 그룹 resolver 단위 테스트
- `tests/test_predictor_business_group.py` — 그룹별 base_rate 분기 테스트

**Modify**

- `app/models/models.py` — `Project`에 `business_type_code`, `business_type_label` 추가
- `app/schemas/schemas.py` — `CrawlNoticeItem`에 `business_type_code`, `business_type_label` 추가
- `app/services/koneps/collector.py` — HTML/OpenAPI에서 코드+라벨 분리 + upsert 영속화
- `app/ai/predictors/base.py` — `PricePredictionContext` 필드 2개 추가
- `app/ai/price_prediction.py` — `predict_price` 시그니처 + guardrail group 우선 resolve
- `app/ai/predictors/historical.py` — `select_competitive_base_rate`에 group 분기
- `app/core/config.py` — group 매핑·guardrail·coverage·kill-switch 설정 추가
- `app/ai/predictor_backtest.py` — `by_group` 차원 리포트
- `app/services/ml_training.py` — manifest에 `group_calibration` 블록 기록
- `scripts/promote_ml_release.py` — preflight-rollout group 검사
- `tests/test_predictions.py` — guardrail/predict_price 회귀 (또는 신규 파일)
- `tests/test_ml_release.py` — manifest gate 신규 검증

---

## PR-1 — Phase A (데이터 레이어)

### Task 1: 브랜치 + Project 모델 컬럼 + Alembic 마이그레이션

**Files:**
- Modify: `app/models/models.py:38-67` (Project 클래스)
- Create: `alembic/versions/<rev>_add_business_type_to_project.py`

- [ ] **Step 1: 브랜치 생성**

```bash
git checkout main && git pull --ff-only origin main
git switch -c feature/phase-a-business-type-columns
```

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_business_type_backfill.py`

```python
"""Tests for the business_type columns on Project + the backfill pipeline."""

import pytest
from app.models.models import Project


def test_project_has_business_type_columns(test_db):
    """Project model must expose business_type_code and business_type_label."""
    project = Project(
        title="컬럼 추가 검증 공고",
        description="신규 컬럼 존재 검증",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()
    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "건축공사"


def test_project_business_type_columns_are_nullable(test_db):
    """Existing rows pre-backfill must coexist with NULL columns."""
    project = Project(
        title="레거시 호환 공고",
        description="-",
        requirements="-",
        budget_estimate=50_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    test_db.refresh(project)
    assert project.business_type_code is None
    assert project.business_type_label is None
```

- [ ] **Step 3: 실패 확인**

```bash
source .venv/bin/activate && pytest -q tests/test_business_type_backfill.py
```

Expected: FAIL — `AttributeError: type object 'Project' has no attribute 'business_type_code'`

- [ ] **Step 4: 모델에 컬럼 추가** — `app/models/models.py:62` 라인 근처(`embedding = Column(...)` 위)

```python
    business_type_code = Column(String(8), nullable=True, index=True)
    business_type_label = Column(String(64), nullable=True)
```

- [ ] **Step 5: Alembic 마이그레이션 생성**

```bash
source .venv/bin/activate && alembic revision -m "add business_type to project"
```

생성된 파일을 수정:

```python
"""add business_type to project

Revision ID: <auto>
Revises: <prev>
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = "<auto>"
down_revision = "<prev>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("business_type_code", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("business_type_label", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_projects_business_type_code",
        "projects",
        ["business_type_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_business_type_code", table_name="projects")
    op.drop_column("projects", "business_type_label")
    op.drop_column("projects", "business_type_code")
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add app/models/models.py alembic/versions/ tests/test_business_type_backfill.py
git commit -m "feat(model): add business_type_code/label columns to Project

KONEPS 4-digit 업종코드 + sub-label을 Project에 영속화할 수 있도록
컬럼 추가. nullable=True로 기존 19,824 row 호환. business_type_code에
b-tree 인덱스 추가 (필터/조인 자주 발생할 예정).

Alembic 마이그레이션 'add business_type to project' 포함."
```

---

### Task 2: config 키 (coverage gate, group prefixes, kill switch 자리)

**Files:**
- Modify: `app/core/config.py:126-150` 근처

- [ ] **Step 1: 실패 테스트** — `tests/test_business_group_resolver.py` 신규

```python
"""Tests for the business_group resolver + config defaults."""

from app.core.config import settings


def test_business_group_code_prefixes_default():
    prefixes = settings.BUSINESS_GROUP_CODE_PREFIXES
    assert prefixes["construction"] == ["04"]
    assert prefixes["service"] == ["06"]
    assert "goods" in prefixes


def test_business_type_coverage_gate_default():
    assert settings.BUSINESS_TYPE_COVERAGE_GATE == 0.95


def test_business_group_calibration_enabled_default():
    assert settings.BUSINESS_GROUP_CALIBRATION_ENABLED is True
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_group_resolver.py
```

Expected: FAIL — `AttributeError: ... has no attribute 'BUSINESS_GROUP_CODE_PREFIXES'`

- [ ] **Step 3: 설정 키 추가** — `app/core/config.py`의 `PREDICTION_CATEGORY_MAXIMUM_BID_RATES` 정의 바로 뒤에 삽입

```python
    BUSINESS_GROUP_CODE_PREFIXES: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "construction": ["04"],
            "service": ["06"],
            "goods": ["01", "02"],
        }
    )
    BUSINESS_TYPE_COVERAGE_GATE: float = 0.95
    BUSINESS_GROUP_CALIBRATION_ENABLED: bool = True
    PREDICTION_GROUP_MINIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "construction": 0.87,
            "service": 0.70,
            "goods": 0.84,
        }
    )
    PREDICTION_GROUP_MAXIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "construction": 0.93,
            "service": 1.00,
            "goods": 1.00,
        }
    )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest -q tests/test_business_group_resolver.py
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_business_group_resolver.py
git commit -m "feat(config): business_group prefixes + guardrail + coverage gate

새 설정 4종:
- BUSINESS_GROUP_CODE_PREFIXES: 업종코드 prefix → 그룹 매핑
- BUSINESS_TYPE_COVERAGE_GATE (기본 0.95): Phase B 활성 임계값
- BUSINESS_GROUP_CALIBRATION_ENABLED (기본 True): 즉시 회귀용 kill switch
- PREDICTION_GROUP_{MIN,MAX}_BID_RATES: group-keyed guardrail

기존 PREDICTION_CATEGORY_*는 한 릴리즈 동안 deprecated 호환 유지."
```

---

### Task 3: CrawlNoticeItem 스키마 확장 + collector 파싱

**Files:**
- Modify: `app/schemas/schemas.py:1596-1606`
- Modify: `app/services/koneps/collector.py:1735-1790` (`_parse_koneps_result_row`)

- [ ] **Step 1: 실패 테스트** — `tests/test_business_type_backfill.py` 에 추가

```python
def test_crawl_notice_item_carries_business_type_code():
    from app.schemas.schemas import CrawlNoticeItem

    item = CrawlNoticeItem(
        notice_number="2026-001",
        title="OO 건축공사",
        base_amount=100_000_000.0,
        business_type="공사",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    assert item.business_type_code == "0411"
    assert item.business_type_label == "건축공사"


def test_extract_business_type_code_from_cell_text():
    """Collector helper은 KONEPS HTML 셀의 'code 라벨' 형식을 분리해야 한다."""
    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()
    code, label = service._split_business_type_cell("0411 건축공사")
    assert code == "0411"
    assert label == "건축공사"

    code, label = service._split_business_type_cell("건축공사")
    assert code is None
    assert label == "건축공사"

    code, label = service._split_business_type_cell("")
    assert code is None
    assert label is None
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: FAIL — `CrawlNoticeItem ... no field 'business_type_code'` + missing helper.

- [ ] **Step 3: `CrawlNoticeItem` 확장** — `app/schemas/schemas.py:1596`

```python
class CrawlNoticeItem(BaseModel):
    notice_number: str
    title: str
    base_amount: float
    estimated_amount: Optional[float] = None
    closing_at: Optional[datetime] = None
    business_type: Optional[str] = None
    business_type_code: Optional[str] = None
    business_type_label: Optional[str] = None
    region: Optional[str] = None
    license_codes: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
```

- [ ] **Step 4: helper 추가** — `app/services/koneps/collector.py` 의 `KonepsCollectorService` 내부 (private 메서드 클러스터)

```python
    @staticmethod
    def _split_business_type_cell(raw: str | None) -> tuple[str | None, str | None]:
        """Split 'NNNN 라벨' KONEPS 업종 셀로부터 코드/라벨을 분리.

        - 'NNNN 라벨' → ('NNNN', '라벨')
        - '라벨' 단독 → (None, '라벨')
        - 빈 문자열/None → (None, None)
        """
        if not raw:
            return None, None
        text = str(raw).strip()
        if not text:
            return None, None
        parts = text.split(maxsplit=1)
        if parts and parts[0].isdigit() and 3 <= len(parts[0]) <= 8:
            code = parts[0]
            label = parts[1].strip() if len(parts) > 1 else None
            return code, (label or None)
        return None, text
```

- [ ] **Step 5: 셀 파싱 적용** — `app/services/koneps/collector.py:1741` (`business_type = cells[1].get_text(...)` 라인 직후)

```python
        business_type = cells[1].get_text(" ", strip=True)
        business_type_code, business_type_label = self._split_business_type_cell(business_type)
```

그리고 `CrawlNoticeItem(...)` 생성 지점(`:1787` 근처)에 두 필드 전달:

```python
            business_type=detail_data.get("business_type") or business_type,
            business_type_code=detail_data.get("business_type_code") or business_type_code,
            business_type_label=detail_data.get("business_type_label") or business_type_label,
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: 4 passed (이전 2개 + 신규 2개)

- [ ] **Step 7: Commit**

```bash
git add app/schemas/schemas.py app/services/koneps/collector.py tests/test_business_type_backfill.py
git commit -m "feat(collector): parse KONEPS 업종 cell into code + label

KONEPS HTML 리스트 셀이 '0411 건축공사' 형식으로 코드+라벨을 함께 담고 있는
점을 분리해 CrawlNoticeItem.business_type_code/label로 전달.

_split_business_type_cell()이 (code, label) 튜플을 반환하며 다음 3가지
입력을 안전 처리: 'NNNN 라벨' / '라벨' / 빈/None."
```

---

### Task 4: Project upsert 영속화

**Files:**
- Modify: `app/services/koneps/collector.py:2580-2610` (project upsert 지점)

- [ ] **Step 1: 실패 테스트** — `tests/test_business_type_backfill.py` 추가

```python
def test_upsert_project_persists_business_type(test_db):
    """KonepsCollectorService.upsert_project가 business_type 필드를 영속화한다."""
    from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()
    request = CrawlRequest(source="test", category="construction")
    item = CrawlNoticeItem(
        notice_number="UPSERT-0411-1",
        title="건축공사 upsert 검증",
        base_amount=100_000_000.0,
        business_type="공사",
        business_type_code="0411",
        business_type_label="건축공사",
    )

    project, _ = service.upsert_project(test_db, item=item, request=request)
    test_db.flush()
    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "건축공사"
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_type_backfill.py::test_upsert_project_persists_business_type
```

Expected: FAIL — assertion (둘 다 None).

- [ ] **Step 3: 영속화 코드 추가** — upsert_project (line ~2580 근처) 의 project 필드 할당 블록

```python
        if item.business_type_code is not None:
            project.business_type_code = item.business_type_code
        if item.business_type_label is not None:
            project.business_type_label = item.business_type_label
```

(기존 필드 할당 패턴과 같은 위치, `project.title = ...` 등 이웃에 추가)

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/koneps/collector.py tests/test_business_type_backfill.py
git commit -m "feat(collector): persist business_type_code/label to Project rows

upsert_project 경로에서 신규 컬럼을 영속화. 기존 항목 재크롤 시 갱신,
신규 항목은 처음부터 포함. 빈 값은 덮어쓰지 않음(보수적)."
```

---

### Task 5: Backfill 스크립트 — source_url detail fetch

**Files:**
- Create: `scripts/backfill_business_type.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_business_type_backfill.py` 추가

```python
def test_backfill_updates_rows_from_detail_html(test_db, monkeypatch):
    """source_url이 있는 row를 상세 페이지에서 코드/라벨 추출해 갱신."""
    from app.models.models import Project
    from scripts.backfill_business_type import (
        BackfillStats,
        backfill_from_detail_html,
    )

    project = Project(
        title="백필 대상 공고 1",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        source_url="http://koneps.example.com/notice/UPSERT-0411-1",
    )
    test_db.add(project)
    test_db.flush()

    def fake_fetcher(url: str) -> dict[str, str]:
        return {"business_type_code": "0411", "business_type_label": "건축공사"}

    stats = BackfillStats()
    backfill_from_detail_html(
        test_db,
        fetcher=fake_fetcher,
        limit=10,
        stats=stats,
    )
    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "건축공사"
    assert stats.updated_from_detail == 1
    assert stats.failed == 0
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_type_backfill.py::test_backfill_updates_rows_from_detail_html
```

Expected: FAIL — `ModuleNotFoundError: scripts.backfill_business_type`.

- [ ] **Step 3: 스크립트 생성**

```python
#!/usr/bin/env python3
"""Backfill Project.business_type_code/label for existing rows.

Usage:
    python scripts/backfill_business_type.py --dry-run
    python scripts/backfill_business_type.py --limit 500
    python scripts/backfill_business_type.py --use-title-rules
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import Project


@dataclass
class BackfillStats:
    candidates: int = 0
    updated_from_detail: int = 0
    updated_from_title_rule: int = 0
    failed: int = 0
    skipped_already_set: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


DetailFetcher = Callable[[str], dict[str, str | None]]


def backfill_from_detail_html(
    db: Session,
    *,
    fetcher: DetailFetcher,
    limit: int,
    stats: BackfillStats,
) -> None:
    """Update rows whose source_url is populated by re-fetching the detail page."""
    query = (
        db.query(Project)
        .filter(Project.business_type_code.is_(None))
        .filter(Project.source_url.isnot(None))
        .order_by(Project.id.asc())
        .limit(limit)
    )
    for project in query.all():
        stats.candidates += 1
        try:
            payload = fetcher(project.source_url)
        except Exception as exc:  # noqa: BLE001  -- best-effort backfill
            stats.failed += 1
            stats.failures.append({"id": str(project.id), "reason": str(exc)})
            continue
        code = (payload.get("business_type_code") or "").strip() or None
        label = (payload.get("business_type_label") or "").strip() or None
        if not code and not label:
            stats.failed += 1
            stats.failures.append({"id": str(project.id), "reason": "empty payload"})
            continue
        if code:
            project.business_type_code = code
        if label:
            project.business_type_label = label
        stats.updated_from_detail += 1
    db.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Project.business_type_*")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--audit", type=Path, default=Path("reports/business-type-backfill/run.json"))
    args = parser.parse_args()

    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()

    def fetcher(url: str) -> dict[str, str | None]:
        # Reuse the live detail HTML parser; isolated so tests can swap it.
        detail = service.fetch_detail_html_payload(url)
        return {
            "business_type_code": detail.get("business_type_code"),
            "business_type_label": detail.get("business_type_label"),
        }

    stats = BackfillStats()
    db = SessionLocal()
    try:
        backfill_from_detail_html(db, fetcher=fetcher, limit=args.limit, stats=stats)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()

    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: collector helper 노출** — `app/services/koneps/collector.py`의 `KonepsCollectorService`에 detail HTML payload 헬퍼가 이미 있으면 그대로 사용, 없으면 다음 placeholder 메서드를 detail 파싱 함수 인근에 추가하고 기존 detail 파서 결과를 dict로 wrapping:

```python
    def fetch_detail_html_payload(self, source_url: str) -> dict[str, str | None]:
        """Fetch + parse a single KONEPS detail page, returning the fields we backfill."""
        html = self._fetch_detail_html(source_url)
        detail = self._parse_detail_html(html, source_url=source_url)
        return {
            "business_type_code": detail.get("business_type_code"),
            "business_type_label": detail.get("business_type_label"),
        }
```

(`_fetch_detail_html`/`_parse_detail_html`은 collector 내 기존 helper. 시그니처가 다르면 호출 어댑터만 맞춘다 — 새로 만들지 않는다.)

- [ ] **Step 5: 테스트 통과 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_business_type.py app/services/koneps/collector.py tests/test_business_type_backfill.py
git commit -m "feat(backfill): source_url 기반 detail HTML로 19,824건 백필 1차 경로

KonepsCollectorService.fetch_detail_html_payload()를 노출해
backfill_business_type 스크립트가 동일 파서를 재사용. BackfillStats로
candidates/updated/failed/skipped 추적. --dry-run + --limit 지원."
```

---

### Task 6: Backfill 스크립트 — title-rule fallback

**Files:**
- Modify: `scripts/backfill_business_type.py`
- Modify: `app/core/config.py` (title-rule config 자리)

- [ ] **Step 1: 실패 테스트** — `tests/test_business_type_backfill.py` 추가

```python
def test_backfill_uses_title_rules_when_detail_missing(test_db):
    """source_url 없는 row는 title-rule fallback으로 코드 추정."""
    from app.models.models import Project
    from scripts.backfill_business_type import backfill_from_title_rules, BackfillStats

    rule_table = [
        {"pattern": r"건축공사", "code": "0411", "label": "건축공사"},
        {"pattern": r"연구개발용역|학술연구", "code": "0621", "label": "학술연구용역"},
    ]

    p1 = Project(title="OO 건축공사", description="-", requirements="-", budget_estimate=1, category="construction")
    p2 = Project(title="2026 학술연구 용역", description="-", requirements="-", budget_estimate=1, category="service")
    p3 = Project(title="물품 구매", description="-", requirements="-", budget_estimate=1, category="goods")
    test_db.add_all([p1, p2, p3])
    test_db.flush()

    stats = BackfillStats()
    backfill_from_title_rules(test_db, rules=rule_table, limit=10, stats=stats)
    test_db.refresh(p1); test_db.refresh(p2); test_db.refresh(p3)
    assert p1.business_type_code == "0411"
    assert p2.business_type_code == "0621"
    assert p3.business_type_code is None
    assert stats.updated_from_title_rule == 2
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_type_backfill.py::test_backfill_uses_title_rules_when_detail_missing
```

Expected: FAIL — `ImportError: cannot import name 'backfill_from_title_rules'`.

- [ ] **Step 3: 함수 추가** — `scripts/backfill_business_type.py` 에 다음 추가

```python
import re


def backfill_from_title_rules(
    db: Session,
    *,
    rules: list[dict[str, str]],
    limit: int,
    stats: BackfillStats,
) -> None:
    """Match remaining NULL rows by title regex; idempotent."""
    compiled = [
        (re.compile(rule["pattern"]), rule["code"], rule.get("label"))
        for rule in rules
    ]
    query = (
        db.query(Project)
        .filter(Project.business_type_code.is_(None))
        .order_by(Project.id.asc())
        .limit(limit)
    )
    for project in query.all():
        text = " ".join(filter(None, [project.title, project.description, project.demand_agency or ""]))
        for pattern, code, label in compiled:
            if pattern.search(text):
                project.business_type_code = code
                if label:
                    project.business_type_label = label
                stats.updated_from_title_rule += 1
                break
    db.flush()
```

- [ ] **Step 4: 설정 키 추가** — `app/core/config.py`의 `BUSINESS_GROUP_CODE_PREFIXES` 옆에 삽입

```python
    BUSINESS_TYPE_TITLE_RULES: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {"pattern": r"건축공사", "code": "0411", "label": "건축공사"},
            {"pattern": r"토목공사", "code": "0412", "label": "토목공사"},
            {"pattern": r"전기공사", "code": "0413", "label": "전기공사"},
            {"pattern": r"학술연구용역|연구개발용역", "code": "0621", "label": "학술연구용역"},
            {"pattern": r"일반용역", "code": "0611", "label": "일반용역"},
        ]
    )
```

- [ ] **Step 5: main()에서 두 경로 차례로 호출** — `scripts/backfill_business_type.py::main`

```python
    args.use_title_rules = getattr(args, "use_title_rules", True)
```

`main()`에 다음 인자 추가:

```python
    parser.add_argument("--use-title-rules", action="store_true", default=True)
    parser.add_argument("--skip-detail", action="store_true")
```

본체 흐름을 다음으로 교체:

```python
    db = SessionLocal()
    try:
        if not args.skip_detail:
            backfill_from_detail_html(db, fetcher=fetcher, limit=args.limit, stats=stats)
        if args.use_title_rules:
            from app.core.config import settings as runtime_settings
            backfill_from_title_rules(
                db,
                rules=runtime_settings.BUSINESS_TYPE_TITLE_RULES,
                limit=args.limit,
                stats=stats,
            )
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest -q tests/test_business_type_backfill.py
```

Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_business_type.py app/core/config.py tests/test_business_type_backfill.py
git commit -m "feat(backfill): title-rule fallback for rows without source_url

regex 룰을 config로 외부화해 도메인 전문가가 운영 중 추가/조정 가능.
detail 백필 실패 후 2차 경로로 자동 실행. p1/p2/p3 패턴 매칭 검증."
```

---

### Task 7: Phase A PR 생성 + /code-review + 머지

**Files:** N/A (PR 단위 액션)

- [ ] **Step 1: 회귀 확인**

```bash
source .venv/bin/activate && pytest -q tests/test_business_type_backfill.py tests/test_business_group_resolver.py
```

Expected: 모든 테스트 PASS.

- [ ] **Step 2: 전체 pytest 회귀**

```bash
pytest -q --tb=short
```

Expected: 기존 200 + 신규 테스트 모두 PASS.

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/phase-a-business-type-columns
gh pr create --base main --head feature/phase-a-business-type-columns \
  --title "Phase A: business_type 컬럼 + 백필 (KONEPS 업종코드 영속화)" \
  --body "spec: docs/superpowers/specs/2026-05-25-business-type-aware-prediction-design.md (§3)

## Summary
- Project.business_type_code/label 추가 + alembic 마이그레이션
- KonepsCollectorService가 'NNNN 라벨' 형식 셀을 분리해 영속화
- BUSINESS_GROUP_* / coverage gate / kill switch 설정 키 추가
- scripts/backfill_business_type.py: source_url 상세 페이지 1차 + title-rule fallback

## Test plan
- [x] pytest -q tests/test_business_type_backfill.py — 신규 테스트 7개 PASS
- [ ] dry-run 실행: python scripts/backfill_business_type.py --dry-run (운영 DB 대상)
- [ ] coverage 측정: SELECT count(*) FILTER (WHERE business_type_code IS NULL) / count(*)

## Acceptance
- Phase B는 coverage >= BUSINESS_TYPE_COVERAGE_GATE (0.95) 충족 후 활성"
```

- [ ] **Step 4: /code-review 실행**

PR 페이지에서 `/code-review` 흐름을 실행하거나 동등한 self-review를 수행한다. 사용자에게 결과 보고 후 사용자가 명시적으로 머지를 지시할 때만 다음 단계 진행.

- [ ] **Step 5: 머지 (사용자 승인 후)**

```bash
gh pr merge $(gh pr view --json number -q .number) --merge --delete-branch
git switch main && git pull --ff-only origin main
```

---

## PR-2 — Phase B (no-op): Context 확장 + business_group resolver

### Task 8: PricePredictionContext 확장 (no-op 단계)

**Files:**
- Modify: `app/ai/predictors/base.py`
- Modify: `app/ai/price_prediction.py`

- [ ] **Step 1: 브랜치**

```bash
git switch -c feature/phase-b-noop-context
```

- [ ] **Step 2: 실패 테스트** — `tests/test_predictor_business_group.py` 신규

```python
"""Tests for business_group propagation through PricePredictionContext."""

from app.ai.price_prediction import predict_price
from app.ai.predictors.base import PricePredictionContext


def test_context_accepts_business_type_fields():
    context = PricePredictionContext(
        budget=100_000_000.0,
        description="OO 건축공사",
        historical_bids=[],
        category="construction",
        business_type_code="0411",
        business_group="construction",
    )
    assert context.business_type_code == "0411"
    assert context.business_group == "construction"


def test_predict_price_accepts_business_type_kwargs():
    """Signature must accept new kwargs without raising."""
    result = predict_price(
        budget=100_000_000.0,
        description="OO 건축공사",
        historical_bids=[],
        category="construction",
        business_type_code="0411",
        business_group="construction",
    )
    assert isinstance(result, dict)
    assert "predicted_price" in result or "price_predicted" in result
```

- [ ] **Step 3: 실패 확인**

```bash
pytest -q tests/test_predictor_business_group.py
```

Expected: FAIL — `unexpected keyword argument 'business_type_code'`.

- [ ] **Step 4: PricePredictionContext 확장** — `app/ai/predictors/base.py:11`

```python
@dataclass
class PricePredictionContext:
    budget: float
    description: str
    historical_bids: list[Any]
    category: str
    business_type_code: str | None = None
    business_group: str | None = None
```

- [ ] **Step 5: predict_price 시그니처 확장** — `app/ai/price_prediction.py:51`

```python
def predict_price(
    budget: float,
    description: str,
    historical_bids: list[Any],
    category: str,
    business_type_code: str | None = None,
    business_group: str | None = None,
) -> dict[str, Any]:
    context = PricePredictionContext(
        budget=float(budget or 0.0),
        description=str(description or ""),
        historical_bids=historical_bids or [],
        category=str(category or "other"),
        business_type_code=business_type_code,
        business_group=business_group,
    )
    # ... 기존 본문 그대로
```

(기존 본문은 손대지 않고 context 빌더에만 두 필드 전달.)

- [ ] **Step 6: 테스트 통과 + 회귀**

```bash
pytest -q tests/test_predictor_business_group.py
pytest -q tests/test_predictions.py
```

Expected: 신규 2 PASS, 기존 prediction 테스트 PASS (시그니처 변경이 호환).

- [ ] **Step 7: Commit**

```bash
git add app/ai/predictors/base.py app/ai/price_prediction.py tests/test_predictor_business_group.py
git commit -m "feat(predictor): business_type fields on PricePredictionContext (no-op)

PricePredictionContext + predict_price()에 business_type_code/group 인자
추가. 둘 다 optional이며 본문은 아직 두 필드를 사용하지 않음 — 다음 PR에서
historical predictor 분기에 활용. 호환성 회귀 없음."
```

---

### Task 9: business_group resolver

**Files:**
- Create: `app/ai/business_group.py`
- Modify: `tests/test_business_group_resolver.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_business_group_resolver.py` 추가

```python
def test_resolve_business_group_by_prefix():
    from app.ai.business_group import resolve_business_group

    assert resolve_business_group("0411") == "construction"
    assert resolve_business_group("0621") == "service"
    assert resolve_business_group("0101") == "goods"
    assert resolve_business_group("9999") is None
    assert resolve_business_group(None) is None
    assert resolve_business_group("") is None


def test_resolve_business_group_uses_config_overrides(monkeypatch):
    from app.ai.business_group import resolve_business_group

    override = {
        "construction": ["07"],
        "service": ["08"],
    }
    monkeypatch.setattr(
        "app.core.config.settings.BUSINESS_GROUP_CODE_PREFIXES",
        override,
        raising=False,
    )
    assert resolve_business_group("0711") == "construction"
    assert resolve_business_group("0411") is None
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_business_group_resolver.py
```

Expected: FAIL — `ModuleNotFoundError: app.ai.business_group`.

- [ ] **Step 3: 모듈 생성** — `app/ai/business_group.py`

```python
"""Resolve KONEPS 업종코드 → 도메인 그룹.

Group resolution is prefix-based and config-driven so domain experts can
adjust mappings via `settings.BUSINESS_GROUP_CODE_PREFIXES` without code
changes. The resolver is intentionally tolerant — unknown or missing codes
return None so callers can fall back to the legacy `category` path.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings


def resolve_business_group(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    cleaned = str(code).strip()
    if not cleaned:
        return None
    prefixes = settings.BUSINESS_GROUP_CODE_PREFIXES or {}
    for group, prefix_list in prefixes.items():
        for prefix in prefix_list or []:
            if cleaned.startswith(prefix):
                return group
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest -q tests/test_business_group_resolver.py
```

Expected: 5 passed (이전 3 + 신규 2).

- [ ] **Step 5: Commit**

```bash
git add app/ai/business_group.py tests/test_business_group_resolver.py
git commit -m "feat(ai): resolve_business_group(prefix → group) helper

config-driven prefix 매핑. None/빈 입력은 None 반환 — 레거시 category
fallback 보존. 도메인 룰은 BUSINESS_GROUP_CODE_PREFIXES로 외부화."
```

---

### Task 10: 호출처 — Project에서 코드 읽어 predict_price에 전달

**Files:**
- Modify: `app/services/opportunity_analysis.py`
- Modify: `app/services/allocation.py` (BidDecisionService)
- Modify: `app/services/paper_bidding_backtest.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_predictor_business_group.py` 추가

```python
def test_opportunity_analysis_passes_business_type(test_db, monkeypatch):
    """OpportunityAnalysisService가 Project.business_type_code를 predict_price로 전달."""
    from app.models.models import Project
    from app.services.opportunity_analysis import OpportunityAnalysisService

    captured: dict = {}

    def fake_predict_price(**kwargs):
        captured.update(kwargs)
        return {
            "predicted_price": 90_000_000.0,
            "recommended_amount": 90_000_000.0,
            "probability_score": 0.6,
            "matched_score": 0.6,
        }

    project = Project(
        title="건축공사 시그널 검증",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()

    monkeypatch.setattr("app.services.opportunity_analysis.predict_price", fake_predict_price)
    service = OpportunityAnalysisService()
    service.analyze(test_db, project=project)

    assert captured["business_type_code"] == "0411"
    assert captured["business_group"] == "construction"
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_predictor_business_group.py::test_opportunity_analysis_passes_business_type
```

Expected: FAIL — KeyError 또는 assertion (현재는 인자가 전달되지 않음).

- [ ] **Step 3: 호출처 수정** — OpportunityAnalysisService 내부의 `predict_price(...)` 호출 라인

```python
from app.ai.business_group import resolve_business_group

# 호출 직전
business_type_code = getattr(project, "business_type_code", None)
business_group = resolve_business_group(business_type_code)

prediction = predict_price(
    budget=float(project.budget_estimate or 0.0),
    description=project.description or "",
    historical_bids=historical_bids,
    category=project.category or "other",
    business_type_code=business_type_code,
    business_group=business_group,
)
```

동일 패턴으로:
- `app/services/allocation.py`의 `BidDecisionService.evaluate_opportunity` (또는 내부 predict_price 호출 지점)
- `app/services/paper_bidding_backtest.py`의 predict_price 호출 지점 (있다면)

각 파일에서 정확한 호출 라인은 `grep -n "predict_price(" app/services/`로 확인.

- [ ] **Step 4: 테스트 통과 + 회귀**

```bash
pytest -q tests/test_predictor_business_group.py
pytest -q tests/test_paper_bidding_backtest.py tests/test_operations.py
```

Expected: 신규 PASS, 기존 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/opportunity_analysis.py app/services/allocation.py app/services/paper_bidding_backtest.py tests/test_predictor_business_group.py
git commit -m "feat(services): forward business_type to predict_price callers

OpportunityAnalysisService / BidDecisionService / PaperBiddingBacktestService가
Project.business_type_code 를 resolve_business_group()로 그룹으로 변환해
predict_price()로 전달. 본문 분기는 아직 비활성(no-op) — 다음 PR에서 활성."
```

---

### Task 11: PR-2 push + 리뷰 + 머지

- [ ] **Step 1: 회귀 확인**

```bash
pytest -q --tb=short
```

- [ ] **Step 2: Push + PR**

```bash
git push -u origin feature/phase-b-noop-context
gh pr create --base main --head feature/phase-b-noop-context \
  --title "Phase B no-op: Context/Predictor signature + business_group resolver" \
  --body "spec §4.1-4.2 + §4.5 prep.
Context/predict_price/호출처가 business_type_code/group을 전달하지만
본문은 아직 사용하지 않음. 운영 영향 0."
```

- [ ] **Step 3: /code-review + 사용자 승인 후 머지**

---

## PR-3 — Phase B (activate): predictor 분기 + guardrail + calibration

### Task 12: historical predictor — select_competitive_base_rate group 분기

**Files:**
- Modify: `app/ai/predictors/historical.py:328-362`

- [ ] **Step 1: 브랜치**

```bash
git switch -c feature/phase-b-activate-predictor
```

- [ ] **Step 2: 실패 테스트** — `tests/test_predictor_business_group.py` 추가

```python
def test_select_base_rate_construction_uses_recent_target_weight():
    """construction 그룹: 단봉 분포 → recent_target 비중 0.6."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="construction",
        description="OO 건축공사",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.903,
        recent_median_rate=0.905,
        competitive_quantile_rate=0.900,
        heuristic_rate=0.88,
        business_group="construction",
    )
    # 0.905*0.6 + 0.903*0.3 + 0.88*0.1 = 0.5430 + 0.2709 + 0.088 = 0.9019
    assert 0.900 <= rate <= 0.910


def test_select_base_rate_service_emphasizes_competitive_quantile():
    """service 그룹: 양봉 분포 → competitive_quantile_rate 비중 0.5."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="service",
        description="OO 연구개발용역",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.883,
        recent_median_rate=0.88,
        competitive_quantile_rate=0.83,
        heuristic_rate=0.85,
        business_group="service",
    )
    # 0.83*0.5 + 0.883*0.35 + 0.85*0.15 = 0.415 + 0.30905 + 0.1275 = 0.8516
    assert 0.83 <= rate <= 0.88


def test_select_base_rate_falls_back_when_group_missing():
    """business_group=None이면 기존 category-keyed 로직 사용."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="construction",
        description="OO 공사",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.903,
        recent_median_rate=0.905,
        competitive_quantile_rate=0.900,
        heuristic_rate=0.88,
        business_group=None,
    )
    assert 0.85 <= rate <= 0.95
```

- [ ] **Step 3: 실패 확인**

```bash
pytest -q tests/test_predictor_business_group.py::test_select_base_rate_construction_uses_recent_target_weight
```

Expected: FAIL — `unexpected keyword argument 'business_group'`.

- [ ] **Step 4: select_competitive_base_rate 시그니처 + 분기** — `app/ai/predictors/historical.py:328`

```python
def select_competitive_base_rate(
    *,
    category: str,
    description: str,
    sample_size: int,
    mean_rate: float,
    median_rate: float,
    recent_median_rate: float,
    competitive_quantile_rate: float,
    heuristic_rate: float,
    business_group: str | None = None,
) -> float:
    robust_median = median_rate or mean_rate
    recent_target = recent_median_rate or robust_median
    quantile_target = competitive_quantile_rate or robust_median

    if business_group == "construction" and sample_size >= 10:
        base_rate = (recent_target * 0.6) + (robust_median * 0.3) + (heuristic_rate * 0.1)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if business_group == "service" and sample_size >= 10:
        base_rate = (quantile_target * 0.5) + (robust_median * 0.35) + (heuristic_rate * 0.15)
        return apply_procurement_rate_band(base_rate, category=category, description=description)

    # 기존 로직 (fallback)
    normalized_category = normalize_category_key(category)
    if sample_size >= 10:
        if normalized_category in {"service", "technical-service", "general-service"}:
            base_rate = recent_target
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        if normalized_category == "construction":
            base_rate = quantile_target
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        base_rate = (robust_median * 0.7) + (recent_target * 0.2) + (mean_rate * 0.1)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= 5:
        base_rate = (robust_median * 0.55) + (mean_rate * 0.35) + (heuristic_rate * 0.10)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= 2:
        base_rate = (robust_median * 0.45) + (mean_rate * 0.35) + (heuristic_rate * 0.20)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    base_rate = (mean_rate * 0.55) + (heuristic_rate * 0.45)
    return apply_procurement_rate_band(base_rate, category=category, description=description)
```

- [ ] **Step 5: 호출처 갱신** — `HistoricalStatisticalPredictor.predict`가 위 함수를 호출하는 곳

```python
base_rate = select_competitive_base_rate(
    category=context.category,
    description=context.description,
    sample_size=sample_size,
    mean_rate=mean_rate,
    median_rate=median_rate,
    recent_median_rate=recent_median_rate,
    competitive_quantile_rate=competitive_quantile_rate,
    heuristic_rate=heuristic_rate,
    business_group=context.business_group,
)
```

- [ ] **Step 6: 테스트 + 회귀**

```bash
pytest -q tests/test_predictor_business_group.py
pytest -q tests/test_predictions.py tests/test_paper_bidding_backtest.py
```

Expected: 신규 3 PASS, 기존 PASS.

- [ ] **Step 7: Commit**

```bash
git add app/ai/predictors/historical.py tests/test_predictor_business_group.py
git commit -m "feat(predictor): branch select_competitive_base_rate by business_group

construction (단봉): recent 0.6 + median 0.3 + heuristic 0.1
service     (양봉): quantile 0.5 + median 0.35 + heuristic 0.15
group=None이면 기존 category 로직으로 안전 fallback. sample<10 그룹은
기존 경로 유지(데이터 부족 시 일반 prior에 의존)."
```

---

### Task 13: guardrail group 키 우선 resolve

**Files:**
- Modify: `app/ai/price_prediction.py:427-450` (`_resolve_floor_bid_rate`, `_resolve_ceiling_bid_rate`)

- [ ] **Step 1: 실패 테스트** — `tests/test_predictor_business_group.py` 추가

```python
def test_guardrail_uses_group_key_when_available():
    from app.ai.price_prediction import _resolve_floor_bid_rate, _resolve_ceiling_bid_rate

    # service 그룹의 floor가 0.70 (config 기본), construction max 0.93 유지.
    floor = _resolve_floor_bid_rate(category="service", business_group="service")
    ceiling = _resolve_ceiling_bid_rate(category="construction", business_group="construction")
    assert floor == 0.70
    assert ceiling == 0.93


def test_guardrail_falls_back_to_category_when_group_missing():
    from app.ai.price_prediction import _resolve_floor_bid_rate

    # business_group=None → 기존 PREDICTION_CATEGORY_MINIMUM_BID_RATES 사용
    floor = _resolve_floor_bid_rate(category="service", business_group=None)
    assert floor == 0.87  # 기존 config
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_predictor_business_group.py::test_guardrail_uses_group_key_when_available
```

Expected: FAIL — `unexpected keyword argument 'business_group'`.

- [ ] **Step 3: resolver 확장** — `app/ai/price_prediction.py:427`

```python
def _resolve_floor_bid_rate(category: str | None, business_group: str | None = None) -> float | None:
    if business_group and settings.BUSINESS_GROUP_CALIBRATION_ENABLED:
        group_rates = settings.PREDICTION_GROUP_MINIMUM_BID_RATES
        if business_group in group_rates:
            return float(group_rates[business_group])
    configured_floor_rates = settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES
    normalized_category = _normalize_category_key(category)
    for raw_category, raw_floor_rate in configured_floor_rates.items():
        if _normalize_category_key(raw_category) == normalized_category:
            return float(raw_floor_rate)
    return None


def _resolve_ceiling_bid_rate(category: str | None, business_group: str | None = None) -> float | None:
    if business_group and settings.BUSINESS_GROUP_CALIBRATION_ENABLED:
        group_rates = settings.PREDICTION_GROUP_MAXIMUM_BID_RATES
        if business_group in group_rates:
            return float(group_rates[business_group])
    configured_ceiling_rates = settings.PREDICTION_CATEGORY_MAXIMUM_BID_RATES
    normalized_category = _normalize_category_key(category)
    for raw_category, raw_ceiling_rate in configured_ceiling_rates.items():
        if _normalize_category_key(raw_category) == normalized_category:
            return float(raw_ceiling_rate)
    return None
```

- [ ] **Step 4: `_apply_prediction_guardrails` 시그니처 연결** — 동일 파일 :283

```python
def _apply_prediction_guardrails(
    prediction: Dict[str, Any],
    *,
    budget: float,
    category: str | None,
    business_group: str | None = None,
) -> Dict[str, Any]:
    floor_bid_rate = _resolve_floor_bid_rate(category, business_group=business_group)
    ceiling_bid_rate = _resolve_ceiling_bid_rate(category, business_group=business_group)
    # ... 기존 본문 그대로 (floor_bid_rate, ceiling_bid_rate를 그대로 사용)
```

`predict_price` 본문에서 `_apply_prediction_guardrails(...)` 호출에 `business_group=business_group` 추가.

- [ ] **Step 5: 테스트 + 회귀**

```bash
pytest -q tests/test_predictor_business_group.py tests/test_predictions.py
```

Expected: 신규 2 PASS + 기존 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/ai/price_prediction.py tests/test_predictor_business_group.py
git commit -m "feat(predictor): group-keyed guardrails take precedence over category

floor/ceiling resolver가 BUSINESS_GROUP_CALIBRATION_ENABLED일 때
PREDICTION_GROUP_*_BID_RATES를 먼저 보고, 그룹 키가 없으면 기존 category
키로 fallback. kill switch로 즉시 회귀 가능."
```

---

### Task 14: ml_training 매니페스트에 group_calibration 블록

**Files:**
- Modify: `app/services/ml_training.py:165-200`
- Modify: `tests/test_ml_release.py` (또는 신규)

- [ ] **Step 1: 실패 테스트** — `tests/test_ml_release.py` 또는 `tests/test_ml_training.py`에 추가

```python
def test_training_summary_includes_group_calibration(test_db, monkeypatch):
    """학습 결과 manifest에 그룹별 calibration 블록이 포함된다."""
    from app.services.ml_training import PricePredictionTrainingService

    service = PricePredictionTrainingService()

    # 그룹별 통계 계산은 dataset에서 winning_rate를 그룹별로 집계해야 함.
    # 본 테스트는 메서드를 직접 호출해 동작 확인.
    dataset = {
        "summary": {"sample_count": 100},
        "items": [
            {"business_group": "construction", "winning_rate": 0.903},
            {"business_group": "construction", "winning_rate": 0.902},
            {"business_group": "service", "winning_rate": 0.881},
            {"business_group": "service", "winning_rate": 0.930},
        ],
    }
    calibration = service._build_group_calibration(dataset)
    assert "construction" in calibration
    assert "service" in calibration
    assert calibration["construction"]["sample_count"] == 2
    assert 0.900 <= calibration["construction"]["median_rate"] <= 0.905
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_ml_release.py::test_training_summary_includes_group_calibration
```

Expected: FAIL — `AttributeError: ... no attribute '_build_group_calibration'`.

- [ ] **Step 3: helper + summary 통합** — `app/services/ml_training.py`

```python
import statistics

class PricePredictionTrainingService:
    ...
    def _build_group_calibration(self, dataset: dict[str, Any]) -> dict[str, dict[str, float | int]]:
        items = dataset.get("items") or []
        groups: dict[str, list[float]] = {}
        for item in items:
            group = item.get("business_group")
            rate = item.get("winning_rate")
            if not group or rate in (None, ""):
                continue
            groups.setdefault(group, []).append(float(rate))
        calibration: dict[str, dict[str, float | int]] = {}
        for group, values in groups.items():
            if len(values) < 1:
                continue
            sorted_values = sorted(values)
            n = len(sorted_values)
            calibration[group] = {
                "median_rate": round(statistics.median(sorted_values), 6),
                "std": round(statistics.pstdev(sorted_values), 6) if n > 1 else 0.0,
                "p25": sorted_values[n // 4],
                "p75": sorted_values[3 * n // 4],
                "sample_count": n,
            }
        return calibration
```

`_build_training_summary` 의 return dict에 다음 키 추가:

```python
            "group_calibration": self._build_group_calibration(dataset),
```

- [ ] **Step 4: dataset에 group 라벨 — `_build_dataset_quality_report` 또는 datasetbuilder가 row 단위로 business_group을 포함하도록**

`scripts/...`또는 `app/services/prediction_dataset.py`(있다면)에서 dataset 빌드 시 각 row에 다음 추가:

```python
from app.ai.business_group import resolve_business_group
item["business_group"] = resolve_business_group(item.get("business_type_code"))
```

(정확한 파일 — `grep -rn "winning_rate" app/services/prediction_*`로 확인. 추가는 단일 위치.)

- [ ] **Step 5: 테스트 + 회귀**

```bash
pytest -q tests/test_ml_training.py tests/test_ml_release.py
```

- [ ] **Step 6: Commit**

```bash
git add app/services/ml_training.py app/services/prediction_dataset.py tests/test_ml_release.py
git commit -m "feat(ml-training): emit group_calibration block in release summary

학습 시 dataset items의 business_group을 집계해 median/std/p25/p75/sample_count
를 manifest summary에 함께 기록. 런타임이 이 블록을 prior로 읽어 그룹별
보정에 사용 (다음 task)."
```

---

### Task 15: 런타임 — manifest의 group_calibration prior 소비

**Files:**
- Modify: `app/ai/predictors/historical.py` (또는 prediction 본문에서 prior를 읽는 곳)
- Modify: `app/core/config.py` — manifest 경로 → group_calibration 로딩 helper

- [ ] **Step 1: 실패 테스트** — `tests/test_predictor_business_group.py`

```python
def test_predictor_uses_group_calibration_prior(monkeypatch):
    """manifest group_calibration이 주어지면 base_rate가 prior median에 끌린다."""
    from app.ai.predictors.historical import HistoricalStatisticalPredictor
    from app.ai.predictors.base import PricePredictionContext

    monkeypatch.setattr(
        "app.ai.predictors.historical.load_group_calibration",
        lambda: {"service": {"median_rate": 0.883, "std": 0.059, "sample_count": 8000}},
        raising=False,
    )
    predictor = HistoricalStatisticalPredictor()
    context = PricePredictionContext(
        budget=100_000_000.0,
        description="OO 용역",
        historical_bids=[],  # 데이터 부족 → prior에 더 의존해야 함
        category="service",
        business_type_code="0621",
        business_group="service",
    )
    result = predictor.predict(context)
    bid_rate = float(result.get("predicted_bid_rate") or 0)
    # prior median 0.883 근처로 끌려야 함 (±0.05)
    assert 0.83 <= bid_rate <= 0.93
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_predictor_business_group.py::test_predictor_uses_group_calibration_prior
```

Expected: FAIL — `cannot import name 'load_group_calibration'`.

- [ ] **Step 3: helper + predict 본문 활용** — `app/ai/predictors/historical.py`

```python
import json
from pathlib import Path

def load_group_calibration() -> dict[str, dict[str, float | int]]:
    """Read group_calibration from the active manifest, if present."""
    from app.core.config import settings

    manifest_path_raw = (settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
    if not manifest_path_raw:
        return {}
    candidate = Path(manifest_path_raw)
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text())
    except Exception:
        return {}
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return {}
    calibration = summary.get("group_calibration")
    return calibration if isinstance(calibration, dict) else {}
```

`HistoricalStatisticalPredictor.predict`의 sample_size 작은 분기에서 prior를 활용:

```python
        calibration = load_group_calibration().get(context.business_group or "") or {}
        prior_median = float(calibration.get("median_rate") or 0.0) or None
        if sample_size < 5 and prior_median is not None:
            mean_rate = (mean_rate or prior_median) * 0.4 + prior_median * 0.6
            median_rate = (median_rate or prior_median) * 0.4 + prior_median * 0.6
```

(prior 블렌드 위치는 historical predictor의 통계 산출 직후. 정확한 라인은 mean_rate/median_rate가 계산된 직후 `select_competitive_base_rate` 호출 전.)

- [ ] **Step 4: 테스트 + 회귀**

```bash
pytest -q tests/test_predictor_business_group.py tests/test_predictions.py
```

- [ ] **Step 5: Commit**

```bash
git add app/ai/predictors/historical.py tests/test_predictor_business_group.py
git commit -m "feat(predictor): consume manifest group_calibration as prior

historical predictor가 manifest summary.group_calibration의 median_rate를
샘플 부족(<5) 시 prior로 사용. 데이터가 충분하면 분기는 비활성 — 기존
통계 우선. manifest 부재/잘못된 JSON은 빈 dict로 안전 처리."
```

---

### Task 16: PR-3 push + 리뷰 + 머지

- [ ] **Step 1: backtest 그룹별 회귀 확인**

```bash
source .venv/bin/activate && python scripts/backtest_price_predictors.py --json | jq '.by_group'
```

Expected: `by_group` 키 부재 또는 빈 dict (Task 17 전이라 정상). 이 단계에서는 **단위/통합 테스트 PASS**가 충분.

- [ ] **Step 2: 전체 회귀**

```bash
pytest -q --tb=short
```

- [ ] **Step 3: Push + PR**

```bash
git push -u origin feature/phase-b-activate-predictor
gh pr create --base main --head feature/phase-b-activate-predictor \
  --title "Phase B activate: predictor group branch + guardrail + calibration prior" \
  --body "spec §4.3–4.6. 그룹별 base_rate / guardrail / manifest prior 활성. kill switch=BUSINESS_GROUP_CALIBRATION_ENABLED."
```

- [ ] **Step 4: /code-review + 사용자 승인 후 머지**

---

## PR-4 — 평가 게이트

### Task 17: predictor_backtest에 by_group 차원

**Files:**
- Modify: `app/ai/predictor_backtest.py:12`

- [ ] **Step 1: 브랜치**

```bash
git switch -c feature/predictor-backtest-by-group
```

- [ ] **Step 2: 실패 테스트** — `tests/test_predictor_backtest.py` 신규 또는 기존 파일 추가

```python
def test_backtest_report_includes_by_group_dimension():
    from app.ai.predictor_backtest import build_predictor_backtest_report

    samples = [
        {"predicted_rate": 0.90, "winning_rate": 0.903, "business_group": "construction"},
        {"predicted_rate": 0.89, "winning_rate": 0.900, "business_group": "construction"},
        {"predicted_rate": 0.85, "winning_rate": 0.881, "business_group": "service"},
        {"predicted_rate": 0.92, "winning_rate": 0.930, "business_group": "service"},
        {"predicted_rate": 0.90, "winning_rate": 0.91,  "business_group": None},
    ]
    report = build_predictor_backtest_report(samples)
    assert "by_group" in report
    assert "construction" in report["by_group"]
    assert "service" in report["by_group"]
    assert "ungrouped" in report["by_group"]
    assert report["by_group"]["construction"]["sample_count"] == 2
    assert "avg_abs_error_rate" in report["by_group"]["service"]
```

- [ ] **Step 3: 실패 확인**

```bash
pytest -q tests/test_predictor_backtest.py::test_backtest_report_includes_by_group_dimension
```

Expected: FAIL — `KeyError: 'by_group'`.

- [ ] **Step 4: 리포트 빌더 확장**

```python
def build_predictor_backtest_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    # 기존 overall 집계는 유지
    overall = _aggregate_metrics(samples)

    by_group: dict[str, dict[str, Any]] = {}
    buckets: dict[str | None, list[dict[str, Any]]] = {}
    for sample in samples:
        key = sample.get("business_group") or None
        buckets.setdefault(key, []).append(sample)
    for key, group_samples in buckets.items():
        label = key if key else "ungrouped"
        by_group[label] = _aggregate_metrics(group_samples)

    return {
        "overall": overall,
        "by_group": by_group,
    }


def _aggregate_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"sample_count": 0}
    errors = [abs(float(s["predicted_rate"]) - float(s["winning_rate"])) for s in samples if "predicted_rate" in s and "winning_rate" in s]
    if not errors:
        return {"sample_count": 0}
    errors.sort()
    n = len(errors)
    return {
        "sample_count": n,
        "avg_abs_error_rate": sum(errors) / n,
        "median_abs_error_rate": errors[n // 2],
        "p90_abs_error_rate": errors[min(n - 1, int(n * 0.9))],
    }
```

- [ ] **Step 5: 테스트 + 회귀**

```bash
pytest -q tests/test_predictor_backtest.py
```

- [ ] **Step 6: Commit**

```bash
git add app/ai/predictor_backtest.py tests/test_predictor_backtest.py
git commit -m "feat(backtest): by_group dimension in predictor_backtest report

construction/service/ungrouped로 분할해 avg/median/p90 abs_error_rate를
독립 집계. spec §5.1의 합격 기준 메트릭을 직접 계산 가능."
```

---

### Task 18: promote_ml_release preflight-rollout group gate

**Files:**
- Modify: `scripts/promote_ml_release.py`
- Modify: `tests/test_ml_release.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_ml_release.py`

```python
def test_preflight_rollout_requires_group_calibration(tmp_path):
    """manifest에 group_calibration 누락 시 gate fail."""
    import json
    from scripts.promote_ml_release import evaluate_preflight_gate

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"summary": {"sample_count": 1000}}))

    result = evaluate_preflight_gate(manifest_path)
    assert result["passed"] is False
    assert "group_calibration" in result["reason"]


def test_preflight_rollout_passes_when_group_calibration_meets_minimum(tmp_path):
    import json
    from scripts.promote_ml_release import evaluate_preflight_gate

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({
            "summary": {
                "sample_count": 1000,
                "group_calibration": {
                    "construction": {"median_rate": 0.903, "sample_count": 9000, "std": 0.03},
                    "service": {"median_rate": 0.883, "sample_count": 8000, "std": 0.06},
                },
            }
        })
    )
    result = evaluate_preflight_gate(manifest_path)
    assert result["passed"] is True
```

- [ ] **Step 2: 실패 확인**

```bash
pytest -q tests/test_ml_release.py::test_preflight_rollout_requires_group_calibration
```

Expected: FAIL — `cannot import name 'evaluate_preflight_gate'`.

- [ ] **Step 3: gate 평가 함수 추가** — `scripts/promote_ml_release.py`

```python
GROUP_CALIBRATION_MIN_SAMPLES = {"construction": 500, "service": 500}


def evaluate_preflight_gate(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text())
    summary = payload.get("summary") or {}
    calibration = summary.get("group_calibration") or {}
    if not calibration:
        return {"passed": False, "reason": "group_calibration block missing"}
    deficits = []
    for group, minimum in GROUP_CALIBRATION_MIN_SAMPLES.items():
        block = calibration.get(group) or {}
        sample_count = int(block.get("sample_count") or 0)
        if sample_count < minimum:
            deficits.append(f"{group} samples={sample_count} < {minimum}")
    if deficits:
        return {"passed": False, "reason": "; ".join(deficits)}
    return {"passed": True, "reason": "ok"}
```

기존 `preflight-rollout` subcommand 본문에서 이 함수를 호출해 결과를 반영:

```python
gate_result = evaluate_preflight_gate(Path(args.manifest))
if not gate_result["passed"]:
    print(f"[preflight-rollout] FAIL: {gate_result['reason']}")
    sys.exit(1)
```

- [ ] **Step 4: 테스트 + 회귀**

```bash
pytest -q tests/test_ml_release.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/promote_ml_release.py tests/test_ml_release.py
git commit -m "feat(ml-release): preflight-rollout가 group_calibration 검증

GROUP_CALIBRATION_MIN_SAMPLES = {construction: 500, service: 500}
미달 시 gate fail. manifest에 group_calibration 블록이 없어도 fail.
spec §4.6의 회귀 차단 컨트랙트 적용."
```

---

### Task 19: PR-4 push + 리뷰 + 머지 + 운영 release 준비

- [ ] **Step 1: 전체 회귀**

```bash
pytest -q --tb=short
npm --prefix frontend run test
npm --prefix frontend run build
```

- [ ] **Step 2: Push + PR**

```bash
git push -u origin feature/predictor-backtest-by-group
gh pr create --base main --head feature/predictor-backtest-by-group \
  --title "Phase B gate: predictor_backtest by_group + ml-release preflight gate" \
  --body "spec §5. backtest 그룹 차원 + manifest group_calibration sample 최소치 회귀 차단."
```

- [ ] **Step 3: /code-review + 사용자 승인 후 머지**

- [ ] **Step 4: 운영 release 준비 (별도 PR/액션 아님 — 운영 절차)**

```bash
# 1. Phase A 백필 1차 dry-run
python scripts/backfill_business_type.py --dry-run --limit 200
cat reports/business-type-backfill/run.json | jq '.'

# 2. coverage 확인 — Postgres
psql -d bid_vector_db -c "SELECT
  count(*) FILTER (WHERE business_type_code IS NULL)::float / count(*) AS null_ratio
FROM projects;"
# null_ratio < 0.05 (0.95 coverage) 인지 확인

# 3. 본 백필 실행
python scripts/backfill_business_type.py --limit 20000

# 4. 새 manifest 생성 + group_calibration 검증
python scripts/promote_ml_release.py create-manifest --release-tag YYYY-MM-DD-business-type-v1 ...
python scripts/promote_ml_release.py preflight-rollout --manifest YYYY-MM-DD-business-type-v1 --require-signature

# 5. promote
python scripts/promote_ml_release.py apply-manifest --manifest YYYY-MM-DD-business-type-v1
```

각 단계는 사용자 검수 후 진행. 실패 시 spec §5.4 롤백 컨트랙트 적용.

---

## Self-review

### 1. Spec 커버리지

| spec 섹션 | 커버 task |
|---|---|
| §1.1 관측 / §1.2 누수 / §1.3 목표 | 진단을 plan header에 요약, 모든 task가 §1.3 목표를 달성 |
| §2 아키텍처 | 전체 file structure + PR-1~4 분할 |
| §3.1 스키마 | Task 1 |
| §3.2 collector | Task 3 |
| §3.3 영속화 | Task 4 |
| §3.4 백필 | Task 5 (detail), Task 6 (title-rule) |
| §3.5 테스트 | Task 1·3·4·5·6 모두 TDD |
| §4.1 Context | Task 8 |
| §4.2 group resolver | Task 9 |
| §4.3 predictor 분기 | Task 12 |
| §4.4 guardrail | Task 13 |
| §4.5 calibration prior | Task 15 |
| §4.6 manifest gate | Task 14 (write) + 18 (gate check) |
| §4.7 테스트 | Task 9·12·13·15 모두 TDD |
| §5.1 backtest 분할 | Task 17 |
| §5.2 합격 기준 | Task 18 (sample 최소치만 자동, 오차 -2%p는 운영 검수) |
| §5.3 롤아웃 단계 | PR-1~4 분할 + Task 19 운영 절차 |
| §5.4 롤백 | Task 13 kill switch + Task 19 절차 |
| §5.5 후속 | spec §5.5에 명시, plan에는 미포함 (의도적) |
| §6 SoT 매핑 | Task 2 (config) + Task 14 (manifest) + Task 18 (gate) |
| §7 오픈 질문 | KONEPS 셀 형식은 Task 3 dry-run에서 확인; title-rule 정확도는 Task 6 dry-run에서 확인; service 양봉 sub-group은 후속 (spec §5.5와 일치) |

### 2. Placeholder scan

`<auto>`/`<prev>` (alembic revision id) 두 곳은 alembic이 자동 채우는 값 — 엔지니어가 작성하는 부분 아니므로 placeholder 아님. 그 외 TBD/TODO 0건.

### 3. 타입/시그니처 일관성

- `business_type_code`: 모든 task에서 `str | None` (Task 1 String(8) + Task 8 dataclass + Task 9 resolver 인자).
- `business_group`: `str | None` 일관 (Task 8 dataclass + Task 9 resolver 반환 + Task 12 predictor 인자 + Task 13 guardrail 인자).
- `BackfillStats`: Task 5에서 정의, Task 6에서 동일 클래스 재사용 (`updated_from_title_rule` 필드는 Task 5 정의에 포함).
- `resolve_business_group` 단일 시그니처 — Task 9 정의, Task 10·12·17에서 같은 형태로 호출.
- `evaluate_preflight_gate(Path) -> dict[str, Any]` — Task 18에서만 정의되어 일관.
- `group_calibration` payload 키 (`median_rate`, `std`, `p25`, `p75`, `sample_count`) — Task 14 정의, Task 15·18에서 동일 키 사용.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-25-business-type-aware-prediction.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — task별 fresh subagent + 두 단계 리뷰

**2. Inline Execution** — 이 세션에서 batch 단위로 진행, 체크포인트에서 사용자 검수

Which approach?
