# 웹 프론트엔드 확장 개발 계획 (with Claude Code)

작성일: 2026-05-25
대상 저장소: `bid-vector`
대상 산출물: `frontend/` (Vite + React + TypeScript) + 필요한 신규 백엔드 API

## 0. 배경과 목표

현재 `frontend/`는 단일 `App.tsx`(1,150 LOC) 기반 SPA로, 로그인·요약 대시보드·기본 입찰/결과 리스트만 노출합니다. 백엔드는 14개 API 라우터, 19개 모델, 25개 도메인 서비스를 가지고 있지만 운영자가 화면에서 직접 다룰 수 있는 영역이 일부에 그칩니다.

이번 작업의 목표는 다음 네 가지 영역을 화면으로 끌어올려, 운영자가 "텔레그램 + JSON" 의존 없이 웹만으로도 분석·결정·실험·운영을 돌릴 수 있게 만드는 것입니다.

1. **운영자 전략 편집 + 공고 탐색 UI** — `OperatorStrategy` 폼/리스트, 공고 검색·상세·유사공고
2. **결정 게이트웨이 + 실험 lifecycle UI** — decision funnel/recommendations, decision_experiments 적용·롤백
3. **가상 운영자 백테스트 비교 대시보드** — 시드된 12개 아키타입의 paper-bidding 결과를 한 화면에서 비교
4. **운영 대시보드** — `/api/v1/analytics/operations-dashboard` 카드를 시각화 (크롤·태스크·Telegram·ML release)

또한 이 작업은 **Claude Code와 짝지어 진행**되므로, 코드 컨벤션 · 서브에이전트 · 슬래시 커맨드 · MCP를 모두 정의해 반복 작업이 안정적으로 자동화되도록 합니다.

## 1. 기술 스택 결정

- **유지**: Vite 5, React 18, TypeScript (strict). 현재 `frontend/package.json`/`vite.config.ts` 그대로.
- **신규 도입**:
  - `tailwindcss` + `tailwindcss-animate` — 디자인 토큰/유틸리티
  - `shadcn/ui` 컴포넌트 (`button`, `card`, `dialog`, `form`, `input`, `table`, `tabs`, `toast`, `select`, `badge`, `tooltip` 우선)
  - `react-router-dom` v6 — 다중 화면 라우팅
  - `@tanstack/react-query` — 서버 상태 캐싱/리페치 (현재 `useEffect`+`useState`를 대체)
  - `react-hook-form` + `zod` — 폼/검증 (전략 편집, 실험 적용 등)
  - `recharts` — 비교 차트, funnel 차트
- **유지(점진 폐기)**: 기존 `styles.css`는 신규 화면이 Tailwind로 옮겨갈 때마다 해당 규칙을 제거. 마지막 화면 마이그레이션 시 통째로 삭제.
- **버전 고정 규칙**: `package.json`의 `latest` 표시는 Phase 0 첫 PR에서 명시적 SemVer로 고정 (CI 재현성 확보).

## 2. 디렉토리 구조 (Phase 0 완료 시점 목표)

```text
frontend/
├── src/
│   ├── app/
│   │   ├── router.tsx          # react-router 진입점
│   │   ├── providers.tsx       # QueryClient, Toast, Theme
│   │   └── layout/
│   │       ├── Shell.tsx       # Sidebar + Topbar + Outlet
│   │       └── AuthGate.tsx    # 토큰 보호 라우트
│   ├── features/
│   │   ├── auth/               # 로그인/패스워드 리셋
│   │   ├── dashboard/          # 기존 요약 대시보드 (재배치)
│   │   ├── strategy/           # Phase 1
│   │   ├── projects/           # Phase 2
│   │   ├── decisions/          # Phase 3
│   │   ├── experiments/        # Phase 4
│   │   ├── synthetic-backtest/ # Phase 5
│   │   ├── operations/         # Phase 6
│   │   └── realtime/           # Phase 7
│   ├── shared/
│   │   ├── api/                # fetch 래퍼, queryKeys, 타입 import
│   │   ├── components/         # shadcn 래퍼 + 도메인 공통 컴포넌트
│   │   ├── hooks/
│   │   ├── lib/                # 시간/금액 포매터 등
│   │   └── types/              # OpenAPI 생성 + 수기 보조 타입
│   ├── styles/
│   │   └── globals.css         # Tailwind base + 토큰
│   └── main.tsx
├── tests/                      # Vitest + RTL (feature 단위)
├── tailwind.config.ts
├── postcss.config.cjs
└── ...
```

## 3. Phase별 스프린트 계획

각 Phase는 약 **1주(5 working days)** 단위입니다. Claude Code와 짝 작업하는 것을 전제로 산정했으며, 백엔드가 이미 제공하는 API만 사용하는 Phase는 짧고, 새 API가 필요한 Phase는 더 깁니다.

### Phase 0 — 프론트엔드 기반 정비 (1주)

**목표**: 신규 화면을 안전하게 만들 수 있는 골격 마련.

작업 항목:
- `package.json` 버전 고정, `tailwindcss / postcss / autoprefixer` 도입, `tailwind.config.ts` 작성
- shadcn/ui 초기화 + 위 우선 컴포넌트 11종 설치
- `react-router-dom`, `@tanstack/react-query`, `react-hook-form`, `zod`, `recharts` 추가
- `src/app/router.tsx` 및 `Shell`/`AuthGate` 컴포넌트 작성
- 기존 `App.tsx`를 `features/dashboard/` + `features/auth/`로 분리, 라우트 등록 (기능 동작 변화 없음)
- `vitest` + Testing Library 셋업 점검, smoke test 1개 작성
- 백엔드 OpenAPI(`/openapi.json`)에서 `openapi-typescript`로 `shared/types/openapi.d.ts` 생성 스크립트 등록

수용 기준:
- `npm --prefix frontend run build` 성공
- `npm --prefix frontend run test` 통과
- `/dashboard`에서 기존 화면이 시각적으로 동일하게 동작 (회귀 없음)
- `shared/api/client.ts`가 `react-query` 기반으로 통일됨
- README의 frontend 빌드 절차 갱신

### Phase 1 — 운영자 전략 편집 UI (1주)

**목표**: `/api/v1/operator/strategy`를 폼/칩 UI로 안전하게 편집.

작업 항목:
- `features/strategy/StrategyEditor.tsx` — `react-hook-form` + `zod` 스키마
- 필드: `focus_categories`, `focus_regions`, `exclude_regions`, `required_keywords`, `exclude_keywords`, `min/max_budget_estimate`, `minimum_match_score`, `minimum_probability_score`, `bid_now_threshold`, `review_threshold`, `auto_workload_penalty_multiplier`, `max_recommended_candidates`, `notify_only_high_priority`
- 카테고리/지역은 칩 multi-select, 키워드는 chip-input, 임계값은 슬라이더 + 숫자 입력 듀얼
- 저장 시 dry-run preview (`/api/v1/operator/strategy/candidates`)로 영향 후보 수 즉시 표시
- 변경 이력은 우측 패널에 `OperatorStrategyRun` 최근 5건 표시
- 실패한 검증/저장은 `toast` 알림

수용 기준:
- 잘못된 값(예: `bid_now_threshold < review_threshold`)은 클라이언트 검증으로 차단
- 저장 후 candidates preview가 함께 갱신됨
- Vitest + RTL: 폼 검증/제출/실패 3개 테스트

### Phase 2 — 공고 탐색 UI (1주)

**목표**: 공고 리스트·상세·유사공고를 한 화면에서 탐색.

작업 항목:
- `features/projects/ProjectList.tsx` — 카테고리/지역/예산 필터 + 페이지네이션 (`/api/v1/projects/`)
- `features/projects/ProjectDetail.tsx` — 기본 정보 + 최근 `BidDecisionRecord` 타임라인 (`/api/v1/operations/projects/{id}/bid-decision-timeline`)
- "유사 공고" 사이드 패널 (`/api/v1/projects/{id}/similar`)
- 임베딩 미생성 공고는 "재계산" 버튼(`POST /api/v1/projects/{id}/embedding/refresh`)
- 검색 인풋은 디바운스(300ms), URL 쿼리 파라미터에 동기화 (북마크 가능)

수용 기준:
- 리스트→상세→유사공고 클릭 흐름이 페이지 리로드 없이 동작
- 임베딩 재계산 후 유사공고 목록이 즉시 갱신됨 (`react-query.invalidate`)
- Vitest: 필터 적용/디바운스/유사공고 클릭 3개 테스트

### Phase 3 — 결정 게이트웨이 UI (1주)

**목표**: decision funnel + recommendations를 시각화하고 결정 상태를 손쉽게 변경.

작업 항목:
- `features/decisions/DecisionFunnel.tsx` — `recharts` 퍼널 차트 (`/api/v1/analytics/decision-funnel`)
- 세그먼트 breakdown(카테고리/agency/workload source) 토글
- 추천 카드 리스트 (`/api/v1/analytics/decision-recommendations`) — priority_score, parameter_recommendation 표시
- 결정 상세에서 `planned ↔ reviewing ↔ submitted ↔ skipped` 상태 전환 버튼 (`POST /api/v1/operations/bid-decisions`)
- 기간 비교 토글 (현재 기간 vs 직전 기간)

수용 기준:
- 결정 상태 전환이 낙관적 업데이트로 즉시 반영되고 실패 시 롤백
- 퍼널 차트가 카테고리 필터에 반응
- Vitest: 상태 전환/세그먼트 필터 2개 테스트

### Phase 4 — 실험 lifecycle UI (1주)

**목표**: `decision_experiments` run의 lifecycle을 화면에서 관리.

작업 항목:
- `features/experiments/ExperimentList.tsx` — review bucket/우선순위 정렬 (`GET /api/v1/analytics/decision-experiments`)
- `features/experiments/ExperimentDetail.tsx` — baseline vs latest evaluation 차트
- `apply-thresholds`, `apply-strategy` 적용 다이얼로그 (`force`/`dry-run` 토글, 적용 전 변경 diff 표시)
- 수동 outcome/note 업데이트 (`PATCH .../decision-experiments/{id}`)
- 비동기 re-evaluation 큐잉 + 진행 상태 폴링 (`/api/v1/ml/reevaluations/decision-experiments/...`)

수용 기준:
- 적용 다이얼로그가 dry-run 결과를 보여준 뒤에만 force 적용 가능
- 실패한 적용은 reason과 다음 액션을 toast로 표시
- Vitest: dry-run 흐름/실패 메시지 2개 테스트

### Phase 5 — 가상 운영자 백테스트 비교 대시보드 (1.5주)

**목표**: `scripts/backtest_synthetic_operators.py` 결과를 화면에서 실행/비교.

신규 백엔드 작업 (반드시 선행):
- `app/services/synthetic_backtest.py` — `PaperBiddingBacktestService`를 12개 운영자에 대해 실행하고 `comparison.json` 동일 payload를 반환하는 서비스
- `app/api/synthetic.py` (또는 `backtests.py`에 합류):
  - `POST /api/v1/synthetic/operators/seed` — 시드/리시드 (idempotent)
  - `GET /api/v1/synthetic/operators` — 현재 시드된 운영자 목록
  - `POST /api/v1/synthetic/backtests/run` — 백테스트 실행, task id 반환
  - `GET /api/v1/synthetic/backtests/runs` — 최근 비교 run 리스트
  - `GET /api/v1/synthetic/backtests/runs/{run_id}` — 단일 비교 상세
- Celery `tasks/jobs.py`에 `run_synthetic_operator_backtest` 추가 (기본 broker `memory://`도 동작하도록)
- `tests/test_synthetic_backtest.py` — 시드 idempotency, 빈 데이터 안전성, win_rate 계산 검증

프론트 작업:
- `features/synthetic-backtest/SeedPanel.tsx` — 12개 아키타입 카드 + "시드/리시드/삭제" 버튼
- `features/synthetic-backtest/RunDialog.tsx` — 기간/카테고리/limit/scenario 입력, 실행 후 progress 폴링
- `features/synthetic-backtest/ComparisonTable.tsx` — 정렬 가능한 테이블 (win_rate_on_settled desc 기본), `bid_submission_rate`/`average_absolute_bid_rate_error` 컬럼 토글
- `features/synthetic-backtest/ArchetypeDrilldown.tsx` — 운영자 1개 선택 시 settle list + error histogram
- 비교 차트: bar chart (win rate by archetype) + scatter (submission rate vs win rate)

수용 기준:
- 시드/리시드/삭제가 idempotent로 동작
- 백테스트 실행 → 진행률 → 완료 → 비교표 렌더링까지 화면 이탈 없이 동작
- 컬럼 정렬/필터가 URL 쿼리에 동기화됨
- Vitest: 비교표 정렬 1개, 실행→완료 mock 1개
- Pytest: 서비스/엔드포인트 4개 이상

### Phase 6 — 운영 대시보드 (1주)

**목표**: `/api/v1/analytics/operations-dashboard` 카드를 시각화.

작업 항목:
- `features/operations/CrawlHealth.tsx` — 크롤 성공률, 최근 실패 원인 표
- `features/operations/TaskHealth.tsx` — 큐별 latency, stale/failed/retry 카운트
- `features/operations/TelegramHealth.tsx` — 전송률, 실패 원인, 최근 메시지 샘플
- `features/operations/MlReleaseCard.tsx` — manifest signature/promotion gate/backtest 상태
- 카드 단위 자동 새로고침(30s), 수동 새로고침 버튼
- 인시던트 토스트: status=`critical` 카드는 페이지 상단 알림 배너

수용 기준:
- 백엔드가 `info`/`watch`/`critical`로 분류한 status가 색상/아이콘으로 일관 표시
- 자동 새로고침은 탭 비활성 시 일시정지
- Vitest: 상태 매핑/배너 표시 2개 테스트

### Phase 7 — Realtime + 알림 통합 + 마감 (1주)

**목표**: WebSocket 실시간 이벤트와 알림함을 묶어 운영자가 화면을 떠나지 않게 함.

작업 항목:
- `features/realtime/useRealtimeEvents.ts` — `WS /api/v1/realtime/events` 구독, replay/after_event_id 지원
- 이벤트 타입(`bid_decision.*`, `crawl.*`, `strategy.monitor.*`, `bid.submitted`)에 따라 toast / 카드 invalidate
- `features/realtime/NotificationDrawer.tsx` — 알림 리스트 + 읽음 처리 (`PUT /api/v1/operator/notifications/{id}/read`)
- 토큰 만료 처리 (401 시 자동 재로그인 모달)
- 한국형(KONEPS) 서비스이므로 **다중 로케일은 지원하지 않습니다.** `shared/i18n/ko.json`은 외부 노출 문구를 한 곳에 모으기 위한 단일 한국어 번들로만 유지하고, 다른 로케일을 추가하지 않습니다.
- E2E: Playwright로 로그인→strategy 편집→synthetic backtest 실행→결과 확인까지의 happy path 1개

수용 기준:
- WebSocket 절단 후 재연결 + replay 동작 확인
- 토큰 만료 시 사용자가 작업을 잃지 않음
- Playwright happy path 1개 통과
- styles.css 잔여 규칙 0줄 (전부 Tailwind/shadcn으로 이주)

## 4. 마일스톤 요약

| Phase | 기간 | 주요 산출물 | 신규 API 필요 |
|---|---|---|---|
| 0 | 1주 | 라우터/Tailwind/shadcn/react-query 기반 + 회귀 동등 | 없음 |
| 1 | 1주 | Strategy 편집기 + candidates preview | 없음 |
| 2 | 1주 | 공고 리스트/상세/유사공고 | 없음 |
| 3 | 1주 | Decision funnel + recommendations + 상태 전환 | 없음 |
| 4 | 1주 | Experiment list/detail/apply/rollback | 없음 |
| 5 | 1.5주 | 가상 운영자 백테스트 비교 대시보드 | **있음 (5개 엔드포인트)** |
| 6 | 1주 | Operations 카드 시각화 | 없음 |
| 7 | 1주 | Realtime + 알림 + Playwright E2E | 없음 |

총 약 **8.5주**. Phase 5만 API 신설이 필요하므로, 백엔드 작업은 Phase 4 후반에 병렬로 시작합니다.

## 5. Claude Code 협업 모델

### 5.1 서브에이전트

`.claude/agents/` 하위에 정의 (이미 있다면 갱신):

- `frontend-builder` — Vite/React/TS/Tailwind/shadcn 화면 구현 전담. 도구: Read/Write/Edit/Glob/Grep + npm/vitest 실행.
- `backend-builder` — FastAPI route/schema/service 구현, alembic 마이그레이션. 도구: Read/Write/Edit + pytest/py_compile.
- `api-reviewer` — 변경된 라우터·스키마·서비스의 일관성·테스트 커버리지·OpenAPI drift 점검 (수정 권한 없음).
- `test-runner` — pytest/vitest/playwright 실행, 실패 triage 보고. 수정 권한 없음.
- `data-seed-runner` — `seed_synthetic_operators.py` 등 DB 시드/리셋만 실행.

각 에이전트의 시스템 프롬프트에 "관련 없는 영역은 건드리지 말 것"과 "테스트 실패 시 강제로 통과시키지 말 것"을 명시.

### 5.2 슬래시 커맨드

`.claude/commands/`에 마크다운으로 정의:

- `/screen <feature> <ScreenName>` — `features/<feature>/<ScreenName>.tsx`, `<ScreenName>.test.tsx`, `index.ts` 스캐폴드 + 라우터 등록 + react-query 훅 placeholder
- `/api-route <name>` — `app/api/<name>.py`, `app/schemas/<name>.py`, `tests/test_<name>.py` 스캐폴드 + `routes.py` 등록
- `/sync-types` — 백엔드 OpenAPI → `frontend/src/shared/types/openapi.d.ts` 재생성
- `/run-backtest [slug]` — 활성 venv로 `scripts/backtest_synthetic_operators.py` 실행 및 결과 경로 출력
- `/seed-synthetic` — `scripts/seed_synthetic_operators.py` 실행 (또는 `--purge`)
- `/check` — `pytest -q && npm --prefix frontend run test && npm --prefix frontend run build`

### 5.3 MCP 서버

`.claude/mcp.json`(또는 사용자 settings)에 등록:

- **PostgreSQL** (read-only) — 로컬 dev DB 직접 조회. 시드/마이그레이션은 별도 스크립트로만.
- **Filesystem** — repo 루트 한정 (기본).
- **(옵션) KONEPS mock** — 로컬 mock 응답 디렉토리를 노출해 Claude가 사양 변경을 즉시 확인.
- **(옵션) Telegram Bot API** — 실제 토큰이 있을 때만. 평소엔 비활성화하고 callback 디버깅 세션에서만 enable.

> 운영 DB/Telegram 자격증명은 절대 MCP에 직접 노출하지 않습니다. 항상 dev 자격증명만.

### 5.4 코드 변경 규칙 (요약)

- `npm --prefix frontend run build`와 `pytest -q`가 깨지면 PR 머지 금지
- 새 화면은 반드시 `features/<area>/` 하위에 둘 것 (App.tsx 다시 부풀리지 않음)
- 새 API는 schema/route/service/test 4종 세트
- styles.css에 새 규칙 추가 금지 — Tailwind/shadcn로 작성
- Playwright/E2E는 happy path만 유지 (회귀 비용 관리)

## 6. 리스크와 완화

- **App.tsx 회귀** — Phase 0에서 기존 화면을 동일하게 보존하기 위해 시각적 회귀 스냅샷 1장 비교 (Playwright screenshot).
- **API drift** — `/sync-types`를 매 Phase 시작 시 실행 항목으로 명문화.
- **신규 백엔드(Phase 5) 일정 지연** — Phase 5만 1.5주로 잡고, 백엔드는 Phase 4 후반에 병렬 시작.
- **shadcn 컴포넌트 카오스** — `shared/components/`에 도메인 래퍼만 두고, shadcn 원본은 `components/ui/`에 그대로 (수정 금지).
- **WebSocket 인증 만료** — Phase 7 토큰 만료 처리에서 통합 케어.

## 7. 진행 추적

- 본 문서의 "Phase별 스프린트 계획"을 단일 source of truth로 사용
- 각 Phase 종료 시 `AGENT.md`의 **"H. 웹 프론트엔드"** 섹션에 완료 항목 이동
- 백엔드 신규 API는 `README.md`의 API 섹션과 OpenAPI에 즉시 반영
- Phase 종료 PR 설명에는 "수용 기준" 체크리스트를 그대로 복사 후 ✅로 표시
