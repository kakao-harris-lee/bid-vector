# 가상 회사 낙찰 실험실(Synthetic Experiment Lab) 웹 기능 계획

> 상태: 계획(draft) · 작성일 2026-05-31 · 대상 영역 `app/`(백엔드) + `frontend/src/features/synthetic-backtest/`
> 단일 출처 연계: 운영 원칙 `AGENT.md`, 웹 확장 `docs/web-development-plan.md`, Claude 작업 규칙 `CLAUDE.md`

## 0. 한 줄 요약

수집된 실데이터(KONEPS 공고·낙찰 기록)를 기준으로 **여러 가상 회사**(프리셋 12종 + 사용자가 직접 정의한 커스텀)의 **낙찰 실험을 웹에서 설정·실행·저장·비교**하는 "실험실"을 구축한다. 결과는 **리더보드/비교표**와 **카테고리·예산구간 분해**로 확인한다.

## 1. 배경 — 이미 있는 것 (재사용)

가상 회사 백테스트의 **엔진과 기본 웹 화면은 이미 존재**한다. 이 계획은 그 위에 "실험실 UX"를 얹는 것이지, 처음부터 만드는 게 아니다.

| 계층 | 현재 자산 | 비고 |
|---|---|---|
| 시드 | `scripts/seed_synthetic_operators.py` — 12개 아키타입(`synthetic-<slug>`) User+CompanyProfile+OperatorStrategy upsert | CLI/`seed-synthetic` 스킬 |
| 백테스트 엔진 | `app/services/paper_bidding_backtest.py` (`run_historical_backtest`), `app/services/synthetic_backtest.py` (`run_for_all` / 운영자별 조합) | 실데이터(`projects`/`tender_results`) 기준, AI 예측 + guardrail 경유 |
| API | `app/api/synthetic.py` — `GET /operators`, `POST /operators/seed`, `POST /backtests/run`(동기), `POST /backtests/run-async`(202+폴링), `GET /backtests/tasks/{id}` | 스키마는 `app/schemas/schemas.py` |
| 비동기 | `app/tasks/` — `run_synthetic_operator_backtest` Celery 태스크 (memory broker eager 호환) | |
| CLI 배치 | `scripts/backtest_synthetic_operators.py` → `models/reports/synthetic-operators/<ts>/comparison.{json,csv}` | |
| 프론트 | `frontend/src/features/synthetic-backtest/SyntheticBacktestScreen.tsx` — 시드 패널, 동기 실행, 비교표, 승률 막대차트, 드릴다운(정산 20샘플 + 오차 히스토그램) | 라우트 `/dashboard/synthetic-backtest` |
| 타입/API래퍼 | `frontend/src/shared/types/synthetic.ts`, `frontend/src/shared/api/synthetic.ts`(폴링 함수 포함) | |

> **결론**: 백테스트 "실행"은 거의 끝나 있다. 빠진 것은 **실험을 1급 객체로 저장·재현·비교**하는 영속 계층과, 그 위의 **실험실 UX**(폴링 진행, 리더보드, 분해, 커스텀 회사 빌더)다.

## 2. 갭 분석 — 없는 것 (이 계획의 범위)

1. **실험/결과 영속화 부재** — 웹 실행 결과가 메모리에만 남아 새로고침 시 소실. 과거 실행 재조회·비교 불가.
2. **비동기 폴링 UI 부재** — `run-async` API는 있으나 프론트는 동기만 사용 → 큰 실험에서 타임아웃 위험.
3. **커스텀 가상 회사 부재** — 12개 프리셋 고정. 사용자가 전략 파라미터로 새 회사를 정의·편집·복제 불가.
4. **리더보드/분해 시각화 부족** — 단일 비교표 + 승률 막대뿐. 정렬형 리더보드, 카테고리·예산구간 분해(히트맵/스택드 바) 없음.
5. **실험 비교(A/B) 및 내보내기 부재** — 두 실행을 나란히 비교하거나 CSV로 내보내기 불가.

## 3. 목표 UX — Experiment Lab

`/dashboard/synthetic-backtest`를 **실험실**로 확장(기존 화면 흡수). 좌측 "실험 목록/생성", 우측 "결과 뷰" 구성.

```
[실험실]
├─ 가상 회사 (탭)
│   ├─ 프리셋 12종 (읽기)
│   └─ 커스텀 회사: 생성/편집/복제/삭제 (전략 파라미터 폼)
├─ 실험 (탭)
│   ├─ 새 실험: 이름·설명 + 조건(기간/카테고리/limit/scenario/cutoff) + 참여 회사 선택(프리셋·커스텀 체크)
│   ├─ 실행 → 비동기 폴링(진행률 "n/N 회사 처리 중") → 결과 저장
│   └─ 실험 이력 목록(최근순, 상태/요약 win_rate)
└─ 결과 (탭, 실험 선택 시)
    ├─ 리더보드: 회사별 win_rate / submission_rate / avg|err| 정렬·랭킹·강조
    ├─ 분해: 카테고리별·예산구간별 성과 (히트맵 또는 스택드 바)
    ├─ 드릴다운: 회사 선택 → 정산 샘플 + 오차 분포 (기존 재사용)
    ├─ 내보내기: comparison.csv 다운로드
    └─ 비교: 실험 A vs B (회사별 Δwin_rate)  ← 우선순위 중
```

우선순위(사용자 확정): **리더보드/비교표 강화 + 카테고리/예산구간 분해**가 1순위. 추이 차트(시간축)·head-to-head는 후순위.

## 4. 데이터 모델 변경 (`app/models/models.py` + alembic)

> 단일 운영자 모델·`synthetic-` 접두 규칙 유지. 커스텀 회사는 `synthetic-custom-<slug>`로 격리하고 canonical `operator`와 절대 충돌 금지.

1. **`SyntheticExperiment`** — 실험 정의(재현 단위)
   - `id`, `name`, `description`, `params(JSON: start_at,end_at,category,limit,scenario,cutoff_hours,history_limit,settle_actions)`, `operator_slugs(JSON list)`, `created_at`, `updated_at`
2. **`SyntheticExperimentRun`** — 한 번의 실행
   - `id`, `experiment_id(FK)`, `status(queued/running/completed/failed)`, `task_id`, `started_at`, `finished_at`, `error`, `summary(JSON: 평균 win_rate 등)`
3. **`SyntheticExperimentResult`** — 회사별 결과(런당 N행) 또는 런에 `results(JSON)` 단일 컬럼
   - 권장: 회사별 행 = `run_id(FK)`, `operator_slug`, `metrics(JSON)`, `breakdown(JSON: by_category/by_budget_band)`, `settlement_sample(JSON 20)`
4. **커스텀 회사**: 별도 테이블 없이 기존 `users`/`company_profiles`/`operator_strategies` 재사용.
   - `username = synthetic-custom-<slug>`, `CompanyProfile.user_id`/`OperatorStrategy.user_id` **unique=True** 준수(회사당 1행 upsert).
   - 메타 구분 위해 OperatorStrategy 또는 CompanyProfile에 `is_custom`/`archetype_source` 플래그 1개 추가(또는 username 접두로만 식별).

마이그레이션은 alembic 1개로 묶고 회귀 테스트 동반. (production이지만 실사용자 없음 → 승인 후 즉시 적용 가능, `CLAUDE.md §0`.)

## 5. 백엔드 API/서비스 설계

`app/api/synthetic.py`에 엔드포인트 추가. 신규 API는 **schema + route + service + test 4종 세트**(`CLAUDE.md §10`). OpenAPI 변경되므로 완료 후 `sync-types` 스킬 실행.

### 5.1 커스텀 가상 회사
- `GET  /api/v1/synthetic/operators` — 응답에 `is_custom`/`source` 포함하도록 확장(프리셋·커스텀 구분).
- `POST /api/v1/synthetic/custom-operators` — 생성. 본문: 회사 메타 + 전략 파라미터(focus_categories/regions, budget min/max, thresholds, keywords 등). 서비스: 새 User(`synthetic-custom-*`) + CompanyProfile + OperatorStrategy upsert.
- `PUT  /api/v1/synthetic/custom-operators/{slug}` — 편집.
- `POST /api/v1/synthetic/custom-operators/{slug}/clone` — 프리셋/커스텀 복제 후 편집 기반.
- `DELETE /api/v1/synthetic/custom-operators/{slug}` — 삭제(커스텀만, 프리셋 보호).

### 5.2 실험 lifecycle
- `POST /api/v1/synthetic/experiments` — 실험 정의 저장.
- `GET  /api/v1/synthetic/experiments` / `GET .../{id}` — 목록/상세(런 이력 포함).
- `POST /api/v1/synthetic/experiments/{id}/runs` — 비동기 실행 트리거(기존 `run-async` 재사용, `experiment_id` 연결) → 202 + `task_id`.
- `GET  /api/v1/synthetic/experiments/{id}/runs/{run_id}` — 런 상태/결과(폴링).
- `GET  /api/v1/synthetic/experiments/{id}/runs/{run_id}/export.csv` — CSV 내보내기(기존 comparison.csv 컬럼 재사용).
- `GET  /api/v1/synthetic/experiments/compare?a={runA}&b={runB}` — 회사별 Δ지표(후순위).

### 5.3 엔진 확장 (분해 지표)
- `synthetic_backtest.py`/`paper_bidding_backtest.py`의 summary 생성부에 **breakdown** 추가: 정산 항목을 `project.category`와 예산구간(band)으로 그룹핑해 win_rate/submission_rate/표본수 집계.
- 실행 완료 시 Celery 태스크가 `SyntheticExperimentRun(status, summary)` + 회사별 `SyntheticExperimentResult` 영속화.
- win_rate는 `would_have_won_price_only_count / settled_count` = **가격 기준 추정 낙찰**임을 응답·CSV·UI 모두에 caveat 유지(`CLAUDE.md §8`).

## 6. 프론트엔드 설계 (`frontend/src/features/synthetic-backtest/`)

- 기존 `SyntheticBacktestScreen`을 탭 컨테이너로 재구성(가상회사 / 실험 / 결과). 신규 화면은 `features/synthetic-backtest/` 하위, `screen` 스킬 패턴(.tsx + .test.tsx + index) 준수. shadcn/Tailwind만, `styles.css` 금지, ko 단일.
- 신규 컴포넌트(안):
  - `CustomOperatorForm.tsx` — 전략 파라미터 폼(react-hook-form + zod). 생성/편집/복제/삭제.
  - `ExperimentForm.tsx` — 조건 입력 + 참여 회사 체크 선택 + 저장/실행.
  - `ExperimentRunProgress.tsx` — `useQuery({refetchInterval})` 폴링, 진행률/상태/오류 표시(재사용 훅 `useAsyncTask` 신설 가능).
  - `Leaderboard.tsx` — 정렬형 랭킹(win_rate/submission_rate/avg|err|), 상위 강조.
  - `BreakdownView.tsx` — 카테고리·예산구간 분해(히트맵 또는 스택드 바, recharts).
  - 기존 `ComparisonTable`/`WinRateBarChart`/`ArchetypeDrilldown` 재사용.
- `shared/api/synthetic.ts`·`shared/types/synthetic.ts`에 신규 엔드포인트/타입 추가(생성 타입은 `openapi.d.ts` 동기화 후 보조 타입만 수기).
- react-query로 서버 상태 관리(`useEffect+fetch` 신규 금지).

## 7. 단계별 로드맵

각 단계 끝에 `check` 스킬(pytest+vitest+build) 그린, PR, `/code-review`.

### Phase 1 — 실험 영속화 + 비동기 폴링 (필수, 토대)
- 모델 `SyntheticExperiment`/`Run`/`Result` + alembic.
- API: 실험 CRUD(생성/목록/상세) + `runs`(비동기 연결) + 런 결과 영속화(Celery 태스크 확장).
- 프론트: ExperimentForm(기본 조건 + 프리셋 회사 선택), ExperimentRunProgress(폴링), 실험 이력 목록.
- **수용 기준**: 웹에서 실험 생성→비동기 실행→진행률 표시→완료 후 결과가 DB에 저장되고 새로고침해도 재조회됨. pytest 정상+실패 1쌍/엔드포인트.

### Phase 2 — 리더보드 + 카테고리/예산구간 분해 (1순위 시각화)
- 엔진 summary에 breakdown(by_category/by_budget_band) 추가 + 결과 영속화에 포함.
- 프론트: Leaderboard(정렬/랭킹), BreakdownView(히트맵/스택드 바). 기존 비교표/드릴다운 통합.
- **수용 기준**: 한 실험 결과에서 회사 랭킹과 카테고리·예산구간별 성과를 한 화면에서 확인. win_rate 추정 caveat 노출.

### Phase 3 — 커스텀 가상 회사 (프리셋 + 커스텀)
- API: custom-operators CRUD + clone. 서비스: `synthetic-custom-*` User/CompanyProfile/OperatorStrategy upsert(unique 준수).
- 프론트: CustomOperatorForm + 가상회사 탭에서 생성/편집/복제/삭제, 실험 참여 선택에 커스텀 포함.
- **수용 기준**: 웹에서 새 가상 회사를 정의→실험에 투입→리더보드에 등장. 프리셋은 보호(편집/삭제 불가), canonical operator 미충돌.

### Phase 4 — 비교/내보내기 (마감)
- API: `export.csv`, `compare?a=&b=`.
- 프론트: CSV 다운로드 버튼, 실험 A/B 비교 뷰(회사별 Δwin_rate).
- **수용 기준**: 두 실험을 나란히 비교하고 결과를 CSV로 내려받음.

## 8. 리스크 & 주의

- **win_rate는 가격 기준 추정**(실제 낙찰 아님) — 모든 표·차트·CSV에 caveat 라벨 고정(`CLAUDE.md §8`).
- **동기 실행 타임아웃** — 웹 실행은 비동기 폴링을 기본 경로로(동기는 작은 limit 미리보기로만 유지).
- **unique 제약** — 회사당 CompanyProfile/OperatorStrategy 1행. 커스텀 생성/편집은 upsert로 처리, `ensure_operator_strategy(db)`(canonical 고정) 대신 operator 객체 기준 조회 사용.
- **Celery memory broker** — 신규 태스크는 `memory://`에서 eager 실행 가능해야 함(테스트).
- **커스텀 회사 정리** — 삭제 시 연결 데이터(paper_bid*/실험 결과) 처리 정책 명시(soft delete 권장, 실 입찰 기록 `BidDecisionRecord`는 보존).
- **OpenAPI drift** — API 변경마다 `sync-types`로 `openapi.d.ts` 갱신 + `api-reviewer`/`ml-reviewer`(예측 영향 시) 리뷰.
- **다운타임 허용** — 마이그레이션/컨테이너 재시작은 승인 후 즉시 가능(`CLAUDE.md §0`).

## 9. 테스트 전략

- 백엔드: 엔드포인트별 정상+실패(401/404/409/422) 쌍, breakdown 집계 단위 테스트, Celery eager 실행 테스트, 커스텀 회사 unique 충돌 회귀.
- 프론트: 각 신규 화면 vitest smoke(폴링/리더보드/분해/폼) + 빌드 타입체크.
- 회귀: `check` 스킬로 pytest+vitest+build 그린 후 PR.

## 10. 비범위(Out of scope) / 후속

- 추이 차트(시간축), head-to-head 상세 비교(Phase 4의 A/B로 일부 대체) — 후속.
- 경쟁자 모델링으로 win_rate 정밀도 향상(별도 정밀도 트랙) — 후속.
- 실험 스케줄링/정기 실행 — 후속.

## 11. 산출물 매핑(파일 영역 요약)

- 백엔드: `app/models/models.py`(+alembic), `app/api/synthetic.py`, `app/schemas/schemas.py`, `app/services/synthetic_backtest.py`/`paper_bidding_backtest.py`, `app/tasks/`(태스크 확장), `tests/`.
- 프론트: `frontend/src/features/synthetic-backtest/*`, `frontend/src/shared/{api,types}/synthetic.ts`, `frontend/src/shared/types/openapi.d.ts`(생성).
- 담당 에이전트: `backend-builder`(엔진/모델/API 일반), `ml-builder`/`ml-reviewer`(예측·breakdown이 predictor에 닿을 때), `frontend-builder`(화면), `api-reviewer`(API drift), `test-runner`(회귀).
