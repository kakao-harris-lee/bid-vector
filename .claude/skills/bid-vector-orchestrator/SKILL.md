---
name: bid-vector-orchestrator
description: bid-vector 개발 하네스 오케스트레이터 — 백엔드/프론트엔드 기능 구현, 리뷰, 검증, 백테스트 작업을 전문 에이전트(backend-builder, frontend-builder, api-reviewer, test-runner, data-seed-runner)와 스킬로 조율한다. "기능 구현해줘", "이 작업 어떻게 진행", "PR까지 진행", "하네스로 처리" 등 비-trivial 개발 작업 시작 시 사용.
---

# bid-vector Harness Orchestrator

bid-vector 프로젝트의 개발 작업을 **전문 에이전트 팀 + 작업 스킬**로 조율하는 상위 오케스트레이터.
한 에이전트가 다른 에이전트의 책임 영역을 침범하지 않도록 영역을 명시해 위임한다.

## 팀 구성 (에이전트)

| 에이전트 | 책임 영역 | 권한 | 비고 |
|---|---|---|---|
| `backend-builder` | `app/`, `tests/`, `alembic/`, `scripts/`(백엔드 의존) | Read/Write/Edit + pytest/py_compile | 프론트 금지 |
| `frontend-builder` | `frontend/src/features/`, `shared/`, `app/` | Read/Write/Edit + npm/vitest | 백엔드 금지 |
| `api-reviewer` | 변경 라우터·스키마·서비스 일관성, OpenAPI drift, 테스트 누락 | **Read 전용** | 수정 금지 |
| `test-runner` | pytest/vitest/playwright 실행·triage | 명령 실행 | **코드 수정 금지** |
| `data-seed-runner` | 시드/백테스트/preflight 스크립트 실행 | 명령 실행 | **코드 수정 금지** |

## 작업 스킬 (어떻게 하는가)

| 스킬 | 용도 | 주 사용 에이전트 |
|---|---|---|
| `api-route` | 백엔드 API 4종(schema/route/service/test) 스캐폴드 | backend-builder |
| `screen` | 프론트 화면 스캐폴드 + 라우트 등록 | frontend-builder |
| `sync-types` | OpenAPI → `openapi.d.ts` 동기화 | frontend-builder / main |
| `check` | pytest + vitest + build 회귀 1세트 | test-runner |
| `seed-synthetic` | synthetic 운영자 시드 | data-seed-runner |
| `run-backtest` | synthetic 백테스트 실행 | data-seed-runner |
| `release-preflight` | ML release manifest promotion gate 점검 | data-seed-runner |

## 의무 워크플로우 (모든 비-trivial 작업)

> 글로벌 `~/.claude/CLAUDE.md` 및 프로젝트 `CLAUDE.md §11`과 일치.

```
main 확인 → feature branch 생성 → 작업/커밋 → push → PR 생성 → 코드 리뷰 → 리뷰 대응 → 머지
```

1. **상태 파악**: `git status` / `git log`. main이 origin과 동기인지 확인.
2. **브랜치 생성**: `git switch -c <type>/<slug>` (feature/fix/chore/docs/refactor). main 직접 커밋 금지.
3. **구현 위임**: 아래 시나리오 표대로 에이전트에 위임. atomic commit.
4. **회귀 검증**: `check` 스킬(또는 test-runner)로 pytest+vitest+build 그린 확인.
5. **PR 생성**: push → `gh pr create`. 본문에 무엇/왜/테스트/수용기준 포함.
6. **코드 리뷰**: `api-reviewer` 에이전트 + `/code-review` 스킬로 자동 리뷰.
7. **리뷰 대응**: 같은 브랜치에 추가 커밋. 회귀 방지 테스트 동반.
8. **머지**: 사용자가 "merge"/"land" 명시할 때만.

## 시나리오별 구성

### A. 새 백엔드 API
```
backend-builder (api-route 스킬) → 구현
  → test-runner (관련 pytest)
  → frontend-builder (sync-types 스킬, OpenAPI 변경 시)
  → api-reviewer (리뷰)
```

### B. 새 프론트엔드 화면
```
frontend-builder (screen 스킬) → 구현 + vitest smoke
  → test-runner (check 스킬)
```

### C. 풀스택 기능 (생성-검증 패턴)
```
Phase 1 (순차): backend-builder (api-route + 로직)
Phase 2 (순차): frontend-builder (sync-types → screen → API 연결)
Phase 3 (병렬): test-runner (check) + api-reviewer (리뷰)
Phase 4: 리뷰 지적 사항을 해당 builder에게 재위임
```

### D. 백테스트/검증 루프
```
data-seed-runner (seed-synthetic) → data-seed-runner (run-backtest) → 결과 보고
```

### E. ML 모델 릴리스 점검
```
data-seed-runner (release-preflight) → gate별 PASS/FAIL → 사용자 promote 결정
```

## 데이터 흐름 원칙

- **OpenAPI drift**: backend-builder가 스키마를 바꾸면 → frontend-builder가 `sync-types`로 `openapi.d.ts` 갱신 → api-reviewer가 누락 점검.
- **영역 격리**: builder끼리 서로의 디렉토리를 수정하지 않는다. 필요하면 보고 후 상대 builder에 재위임.
- **검증 게이트**: test-runner/api-reviewer는 절대 코드를 수정하지 않는다. 문제 발견 시 builder에게 돌려보낸다.
- **시드/스크립트**: data-seed-runner는 스크립트만 실행하고, 스크립트 수정이 필요하면 backend-builder에 위임 보고.

## 위임 시 프롬프트 규칙

각 에이전트 위임 프롬프트에 반드시 포함:
1. 책임 영역 (어떤 디렉토리를 건드려도 되는지)
2. 사용할 스킬 이름 (예: "`api-route` 스킬을 따라")
3. 금지 사항 (다른 영역 수정 금지)
4. 완료 후 보고 양식 (변경 파일 + 테스트 결과)
