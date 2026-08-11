---
name: bid-vector-orchestrator
description: bid-vector 개발 하네스 오케스트레이터 — Python 백엔드/React/ML/Kotlin service 기능 구현, 리뷰, 검증, 백테스트, 관찰·증적 작업을 전문 에이전트와 스킬로 조율한다. "기능 구현해줘", "Kotlin으로 이전", "Claude 구현 Codex 리뷰", "PR까지 진행", "하네스로 처리" 등 비-trivial 개발 작업 시작 시 사용.
---

# bid-vector Harness Orchestrator

bid-vector 프로젝트의 개발 작업을 **전문 에이전트 팀 + 작업 스킬**로 조율하는 상위 오케스트레이터.
한 에이전트가 다른 에이전트의 책임 영역을 침범하지 않도록 영역을 명시해 위임한다.

## 팀 구성 (에이전트)

| 에이전트 | 책임 영역 | 권한 | 비고 |
|---|---|---|---|
| `backend-builder` | `app/`(ML 제외), `tests/`, `alembic/`, `scripts/`(백엔드 의존) | Read/Write/Edit + pytest/py_compile | 프론트·ML 금지 |
| `frontend-builder` | `frontend/src/features/`, `shared/`, `app/` | Read/Write/Edit + npm/vitest | 백엔드 금지 |
| `ml-builder` | `app/ai/`, ML 서비스(`ml_training`/`ml_release`/`prediction_*`), ML 스크립트, ML 테스트 | Read/Write/Edit + pytest/py_compile | guardrail·차원·서명 안전 |
| `kotlin-builder` | `service-api/`, 필요한 `contracts/`, Kotlin 테스트 | Read/Write/Edit + Gradle | strict domain·금액 안전, Python/React 금지 |
| `api-reviewer` | 변경 라우터·스키마·서비스 일관성, OpenAPI drift, 테스트 누락 | **Read 전용** | 수정 금지 |
| `frontend-reviewer` | 프론트 화면/훅/타입 일관성, react-query·zod·shadcn 경계, vitest 커버리지, a11y | **Read 전용** | 수정 금지 |
| `ml-reviewer` | predictor guardrail·pgvector 차원·manifest 서명·leakage·drift 점검 | **Read 전용** | 수정 금지 |
| `test-runner` | pytest/vitest/playwright 실행·triage | 명령 실행 | **코드 수정 금지** |
| `data-seed-runner` | 시드/백테스트/preflight 스크립트 실행 | 명령 실행 | **코드 수정 금지** |
| `evidence-runner` | smoke test·G-2/G-3 증적 수집(읽기 전용 기본) | 명령 실행 | **코드 수정 금지**, write/telegram/live는 승인 후 |

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
| `smoke-evidence` | production smoke test + 증적 수집(읽기 전용 기본) | evidence-runner |
| `kotlin-service` | Kotlin vertical slice 구현 + Gradle 검증 + Codex 독립 리뷰 | kotlin-builder / main |

## 의무 워크플로우 (모든 비-trivial 작업)

> 글로벌 `~/.claude/CLAUDE.md` 및 프로젝트 `CLAUDE.md §10. 워크플로`와 일치.

```
main 확인 → feature branch 생성 → 작업/커밋 → push → PR 생성 → 코드 리뷰 → 리뷰 대응 → 머지
```

1. **상태 파악**: `git status` / `git log`. main이 origin과 동기인지 확인.
2. **브랜치 생성**: `git switch -c <type>/<slug>` (feature/fix/chore/docs/refactor). main 직접 커밋 금지.
3. **구현 위임**: 아래 시나리오 표대로 에이전트에 위임. atomic commit.
4. **회귀 검증**: `check` 스킬(또는 test-runner)로 pytest+vitest+build 그린 확인.
5. **PR 생성**: push → `gh pr create`. 본문에 무엇/왜/테스트/수용기준 포함.
6. **코드 리뷰**: 영역 reviewer를 사용하고, Kotlin 변경은 `scripts/codex-review-kotlin.sh`로 Codex 독립 리뷰를 추가한다.
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
  → frontend-reviewer (리뷰)
```

### C. 풀스택 기능 (생성-검증 패턴)
```
Phase 1 (순차): backend-builder (api-route + 로직)
Phase 2 (순차): frontend-builder (sync-types → screen → API 연결)
Phase 3 (병렬): test-runner (check) + api-reviewer (백엔드 리뷰) + frontend-reviewer (프론트 리뷰)
Phase 4: 리뷰 지적 사항을 해당 builder에게 재위임
```

### D. 백테스트/검증 루프
```
data-seed-runner (seed-synthetic) → data-seed-runner (run-backtest) → 결과 보고
```

### E. ML predictor / 파이프라인 변경 (생성-검증)
```
Phase 1: ml-builder (predictor/학습/데이터셋 구현 + guardrail 회귀 테스트)
Phase 2 (병렬): test-runner (관련 pytest) + ml-reviewer (guardrail/차원/leakage 리뷰)
Phase 3: ML을 노출하는 API가 필요하면 → backend-builder (얇은 라우터, api-route 스킬)
Phase 4: 리뷰 지적 사항을 ml-builder에 재위임
```

### F. ML 모델 릴리스
```
ml-builder (manifest 작성/모델 학습) → ml-reviewer (서명·promotion gate 사전 검수)
  → data-seed-runner (release-preflight 스킬 실행) → gate별 PASS/FAIL
  → 사용자 promote 결정 (승인 필수)
```

### G. 관찰·검증 (G-3 실사용 검증 — 현 단계 기본)
```
evidence-runner (smoke-evidence 스킬, 읽기 전용) → 증적 reports/ 수집 → PASS/WARN/FAIL 보고
  → WARN/FAIL이면 oracle(원인 분석) 또는 해당 reviewer/builder에 위임
  → (write/telegram/live 증적이 필요하면 사용자 승인 후에만)
```
> 현 단계는 기능 빌드가 아니라 관찰·측정이다. 새 빌드 시나리오(A~F)는 명확한
> 필요가 있을 때만 들어가고, 기본은 이 관찰 루프로 증적을 누적한다.

### H. Kotlin service vertical slice (Claude 생성 · Codex 검증)
```
Phase 1: kotlin-builder (domain invariant test → value object/domain → application → adapter)
Phase 2: 저장소 Gradle wrapper로 targeted/architecture/전체 Kotlin test
Phase 3: scripts/codex-review-kotlin.sh --base origin/main
Phase 4: request_changes면 kotlin-builder 수정 → 재검증 → Codex 재리뷰(최대 2회)
Phase 5: approve + 검증 결과 + residual risk를 사용자에게 보고; merge/cutover는 별도 승인
```

## 데이터 흐름 원칙

- **OpenAPI drift**: backend-builder가 스키마를 바꾸면 → frontend-builder가 `sync-types`로 `openapi.d.ts` 갱신 → api-reviewer가 누락 점검.
- **ML ↔ API 경계**: ML을 노출하는 엔드포인트는 backend-builder가 얇은 라우터를, ml-builder가 predictor 호출 로직을 담당. 라우터/스키마는 backend, predictor 내부는 ML.
- **pgvector 차원 변경**: ml-builder가 설계 → ml-reviewer가 차원 호환성 승인 → backend-builder가 alembic 마이그레이션 실행.
- **영역 격리**: builder 4종(backend/frontend/ml/kotlin)은 서로의 디렉토리를 수정하지 않는다. 필요하면 보고 후 상대 builder에 재위임.
- **검증 게이트**: test-runner/api-reviewer/frontend-reviewer/ml-reviewer/evidence-runner는 절대 코드를 수정하지 않는다. 문제 발견 시 해당 builder에게 돌려보낸다. 프론트 변경은 frontend-reviewer, 백엔드는 api-reviewer, ML은 ml-reviewer가 각자 영역을 본다.
- **관찰 우선 (현 단계)**: 프로젝트는 G-3 실사용 검증(관찰·측정) 단계다. 빌드보다 evidence-runner(`smoke-evidence`)로 증적을 누적하는 것이 기본이고, 증적의 write/telegram/live 동작은 CLAUDE.md §0에 따라 사용자 승인 후에만 켠다.
- **ML 안전 게이트**: predictor 변경은 ml-reviewer 통과 전 머지 금지. guardrail 우회·차원 무단 변경·서명 누락은 blocking.
- **시드/스크립트**: data-seed-runner는 스크립트만 실행하고, 스크립트 수정이 필요하면 backend-builder(일반) 또는 ml-builder(ML)에 위임 보고.
- **Kotlin 독립 리뷰**: Claude는 Kotlin을 구현하지만 리뷰 판정은 Codex JSON 보고서를 그대로 사용한다. Codex에는 write 권한을 주지 않고, `request_changes`를 임의로 낮추지 않는다.

## 위임 시 프롬프트 규칙

각 에이전트 위임 프롬프트에 반드시 포함:
1. 책임 영역 (어떤 디렉토리를 건드려도 되는지)
2. 사용할 스킬 이름 (예: "`api-route` 스킬을 따라")
3. 금지 사항 (다른 영역 수정 금지)
4. 완료 후 보고 양식 (변경 파일 + 테스트 결과)
