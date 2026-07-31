# PR-C "프론트 스냅샷 UX + 폴링" (feature/preview-snapshot-ui) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 설계(`docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md`) §7 PR-C — PR-B(#321)가 순수 읽기로 바꾼 `GET /operator/strategy/candidates`(+`computed_at`/`snapshot_status`/`stale` 메타)와 신설 `POST /candidates/refresh`(202)를 프론트가 **스냅샷 UX**로 소비한다: 스냅샷 즉시 렌더 + "N분 전 기준" 신선도 배지 + `snapshot_status=running` 동안만 도는 terminal-게이트 폴링 + 실패 표면(직전 후보 유지) + 새로고침 = 명시 재계산 디스패치. 온보딩 최초 진입은 진행 UI(경과 안내)로 대기한다. **백엔드는 한 줄도 바뀌지 않는다.**

**Architecture:** 폴링 판정의 모든 복잡도를 순수 모듈 `features/strategy/snapshotState.ts`(`isSnapshotSettled`/`snapshotPollInterval`/`hasComputedSnapshot` + 주기 상수)에 모으고, `useStrategyCandidatesQuery` 가 그것을 react-query `refetchInterval` 콜백에 꽂아 **서버 status 만을 근거로** 폴링한다(ExperimentRunProgress 패턴 재사용) — 로컬 "폴링 중" 플래그가 없으므로 `["strategy"]` 전면 invalidate(전략 저장·realtime `strategy.monitor.*`·온보딩 apply)가 쿼리를 리셋해도 판정이 동일해 결정적이다. `CandidatesPreview` 는 얇은 조립부로 남고 표시 책임은 `components/SnapshotFreshnessBadge`·`SnapshotStatusNotice`·`CandidateList` 3개로 분해한다(§4.5-4). 새로고침은 `query.refetch()` 대신 `useRefreshStrategyCandidatesMutation` → `POST /candidates/refresh` → 후보 쿼리 prefix invalidate 이며, 이후 폴링은 서버가 돌려주는 `running` 이 켠다.

**Tech Stack:** React 19 + TypeScript(strict) + @tanstack/react-query 5 + Tailwind 4/shadcn 래퍼 + vitest 4 + @testing-library/react 16 + Vite 8, openapi-typescript(타입 drift 검증).

## Global Constraints

- **TDD 필수** — 모든 변경은 실패하는 테스트(또는 실패하는 `build` 타입 체크) 먼저.
- **브랜치 `feature/preview-snapshot-ui`**, worktree `/home/deploy/project/bid-vector-preview-ui`, base `origin/main` = `9784ba3`(PR-B #321 + #326/#327 머지 이후, `HEAD == origin/main` 확인됨).
- **워크트리는 자체 `node_modules` 가 없다**(`.gitignore:101` 로 untracked). 메인 체크아웃 `frontend/node_modules`(276M, `openapi-typescript`/`vitest`/`vite` 바이너리 존재 확인됨)를 **심볼릭 링크로 재사용**하고 Task 1 에서 기존 스위트 1개를 돌려 검증한다. 실패 시 fallback = 워크트리 전용 오프라인 설치(`npm cache` 6.3G 존재 확인됨). 정확한 명령은 Task 1.
- **프론트 명령은 전부 절대경로 prefix 형태**:
  - `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test`
  - `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run build`
  - 단일 파일: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/CandidatesPreview.test.tsx`
- **sync-types(검증 목적)**: `` /home/deploy/project/bid-vector/.venv/bin/python /home/deploy/project/bid-vector-preview-ui/scripts/sync_openapi_types.py --check `` — 워크트리의 스크립트를 실행하면 `REPO_ROOT`/`--frontend-dir`/`--output` 기본값이 모두 워크트리로 잡히고, `npm --prefix <워크트리>/frontend exec openapi-typescript` 가 심링크된 node_modules 를 쓴다.
- **pytest(회귀 sanity)**: `cd /home/deploy/project/bid-vector-preview-ui && /home/deploy/project/bid-vector/.venv/bin/pytest -q` (워크트리에는 `.venv` 없음).
- **백엔드 계약은 고정 (HARD)** — PR-C 는 `app/`·`alembic/`·`tests/`(파이썬)를 **건드리지 않는다**. 마이그레이션 없음. 프론트가 백엔드 변경을 요구하는 결론이 나오면 그 자리에서 멈추고 사용자에게 보고한다.
- **UI 문구는 한국어, ko 단일 번들**(§4). 타이밍·문구·주기 같은 매직값은 컴포넌트 안 리터럴이 아니라 모듈 상수로(§4.5-1), 상태→라벨/톤 분기는 룩업 맵으로(§4.5-2), React 컴포넌트 ~250줄(§4.5-4).
- **폴링 조건은 서버 status 만** — 스펙 §7 의 명시 요구. `useState`/`ref` 기반 "폴링 중" 플래그 도입 금지(전면 invalidate 와 어긋난다).
- 배포는 프론트 산출물 재빌드 1건(`docker compose run --rm frontend-build`). api 재시작·마이그레이션 없음 — 근거는 Task 8 PR 본문.

## 설계 이탈 노트 (코드 실사 후 확정한 결정)

1. **`sync-types` 는 재생성이 아니라 drift 검증이다.** `frontend/src/shared/types/openapi.d.ts` 는 PR-B 가 이미 동기화했다(실사: `openapi.d.ts:7079` `OperatorStrategyCandidatesRefreshResponse`, `:7100-7128` `computed_at`/`snapshot_status`/`stale`). 따라서 PR-C 의 타입 산출물은 **수기 타입**(`shared/types/strategy.ts`)이고, sync-types 는 `--check` 로 "drift 0" 만 확인한다. 만약 drift 가 나오면 main 쪽 표류이므로 별도 커밋으로 재생성하고 PR 본문에 적는다.
2. **상대 시각 헬퍼가 프론트에 없다.** `to_kst`/`kst_now` 는 백엔드 전용이고 프론트 `shared/lib.ts` 에는 `formatDate`/`formatDateTime`(둘 다 `timeZone: "Asia/Seoul"`)만 있다. `Intl.RelativeTimeFormat` 사용 흔적도 0이다. 그래서 새로 만들되 **`shared/lib.ts` 단일 출처**에 `formatRelativeTime(value, now?)` 로 넣고 24시간 초과는 기존 `formatDateTime` 으로 폴백한다(중복 포맷터 금지 §4.5-6, `now` 주입 §4.7-3).
3. **공용 폴링 헬퍼를 추출하지 않는다.** `refetchInterval` 자체가 이미 공용 메커니즘이고, 두 기존 소비자(`ExperimentRunProgress`=`run.status`, `ExperimentDetailPanel`=`data.ready`)와 우리(`snapshot_status`+`computed_at` 2필드 조합)의 terminal 판정이 전부 다르다. 공용화하면 "terminal 판정 주입" 래퍼(로직 0줄)만 남고 실제 복잡도는 도메인 판정에 있다. 그래서 **도메인 판정만** `features/strategy/snapshotState.ts` 로 추출한다(§4.5-6 의 "두 번째로 같은 문제를 풀면 추출" 대상은 '스냅샷 상태 해석'이며 이는 컴포넌트/훅/테스트 3곳이 공유한다). `shared/hooks/` 는 손대지 않는다.
4. **`document.visibilityState` 게이트(OperationsScreen:47 패턴)는 채택하지 않는다.** 그 게이트는 숨은 탭에서 `false` 를 반환하는데, 전역 `refetchOnWindowFocus: false` 때문에 탭 복귀 시 상태 변화가 없어 인터벌이 재계산되지 않고 **폴링이 wedge** 될 수 있다(운영자가 돌아왔을 때 "갱신 중"이 영구 고착). react-query 네이티브 `refetchIntervalInBackground: false`(기본값이지만 의도 문서화를 위해 명시)가 숨은 탭 작업을 억제하면서 복귀 시 자동 재개하므로 그것만 쓴다.
5. **`fetchFailureCount` 는 "연속 실패" 게이트로 쓸 수 없다.** 실사: `node_modules/@tanstack/query-core/build/modern/query.js:411-416` 의 `fetchState()` 가 **fetch 시작마다 `fetchFailureCount: 0`** 으로 리셋한다(리트라이 내 카운터). 따라서 죽은 백엔드에 영구 재시도하는 것을 막는 근거는 `query.state.status === "error"`(같은 파일 `:374-388`, 마지막 fetch 실패 시 data 를 유지한 채 status 만 error)로 잡는다. 전역 `retry: 1` 이 일시 장애는 이미 한 번 흡수하고, 새로고침(mutation→invalidate)이 성공하면 폴링이 자동 재개된다.
6. **`Toaster` 가 토스트에 `role="status"`(info)/`role="alert"`(danger)를 붙인다**(`shared/components/ui/toast.tsx:93`). 그래서 진행/실패 표시를 `getByRole("status"|"alert")` 로 조회하면 토스트와 충돌한다. 카드의 접근성 역할(`role="status"`/`role="alert"`)은 **유지**하되(요구사항 1), 테스트 조회는 `data-testid="snapshot-progress"`/`"snapshot-failed"` 로 하고 `toHaveAttribute("role", ...)` 로 역할 자체를 단언한다(`strategy-readonly-notice` 선례).
7. **새로고침 버튼은 `running` 중에도 활성 상태로 둔다.** PR-B 는 `POST /candidates/refresh` 에만 짧은 force floor(`OPERATOR_PREVIEW_SNAPSHOT_FORCE_RECLAIM_SECONDS=60`, `operator_strategy.py:296-300`)를 주어 **고착 running 을 운영자가 UI 에서 회수**할 수 있게 설계했다. running 이면 버튼을 disable 하는 흔한 처리는 이 복구 경로를 막는다. 따라서 disable 은 `refresh.isPending`(POST 인플라이트) 동안만이고, "갱신 중"은 버튼 라벨이 아니라 `SnapshotStatusNotice` 가 말한다.
8. **온보딩 apply 는 스냅샷 재계산을 디스패치하지 않는다.** 실사: `dispatch_for_strategy_write` 호출부는 전략 PUT(`api/operator_strategy.py:241`)·텔레그램(`telegram_strategy.py:224`)·실험 적용 2곳(`decision_experiments/lifecycle.py:295,359`) 뿐이고 `services/onboarding/apply.py` 에는 없다. 즉 spec §9 의 완화책 "온보딩 적용 시점 선디스패치"는 백엔드에 없다. PR-C 는 백엔드를 바꾸지 않으므로, **PreviewStep 진입 첫 GET 이 `serve()` 안에서 자동 디스패치**하는 성질(`preview_snapshot.py:239-244`)에 기대고 프론트는 진행 UI + 경과 안내로 대응한다(요구사항 3).
9. **`stale=true` 는 시간 경과만이 아니다.** `_snapshot_is_stale`(`preview_snapshot.py:460-468`)은 경과(`_computed_age_exceeds`) **또는 전략 편집 후 미재계산**(`_strategy_changed_since_scan`)에도 켜진다. 게다가 실패 쿨다운 60s 동안은 stale 을 보고하면서 자동 디스패치가 억제된다(`_needs_recompute:488`). 그래서 배지 문구는 원인을 단정하지 않는 **"갱신 필요"** 로 통일하고, "갱신 중"은 `snapshot_status === "running"` 에서만 쓴다(소비자 주의 1).
10. **스냅샷이 없으면(`computed_at === null`) 통계·목록·"후보 없음" 문구를 전부 감춘다.** 부트스트랩 응답은 `evaluated_project_count: 0`/`candidates: []`(`_build_response:271-286`)인데 이를 그대로 그리면 "평가 0건 / 매칭되는 후보가 없습니다"라는 **거짓 사실**이 된다(§2 정직 명세). 렌더 게이트는 `stale` 이 아니라 `computed_at`(소비자 주의 3).
11. **`PreviewStep` 은 코드 변경이 없다.** 진행 UI 는 `CandidatesPreview` 가 데이터(`computed_at === null`)로 구동하므로 prop 을 추가할 필요가 없다. 문서 주석만 갱신한다(불필요한 변경 금지 §4.6).
12. **e2e `login-and-strategy.spec.ts` 는 변경 불필요.** 실사: 로그인→공고 탐색→전략 저장 토스트까지만 검증하고 후보 카드 문구를 단언하지 않는다.

---

### Task 1: 워크트리 생성 + node_modules 재사용 검증 + 계획 문서 커밋

**Files:**
- Create: `/home/deploy/project/bid-vector-preview-ui` (git worktree)
- Create: `/home/deploy/project/bid-vector-preview-ui/frontend/node_modules` (심볼릭 링크, untracked)
- Create(커밋): `docs/superpowers/plans/2026-07-30-preview-snapshot-ui.md` (본 문서)

**Interfaces:**
- Consumes: `origin/main` = `9784ba3` (PR-B #321 머지 이후)
- Produces: 이후 모든 Task 의 작업 디렉터리 + 동작하는 프론트 툴체인, 브랜치 `feature/preview-snapshot-ui`

- [ ] **Step 1: 워크트리 생성**

```bash
cd /home/deploy/project/bid-vector
git fetch origin
git worktree add ../bid-vector-preview-ui -b feature/preview-snapshot-ui origin/main
```

- [ ] **Step 2: node_modules 심링크 (메인 체크아웃 재사용, 오프라인)**

```bash
ln -s /home/deploy/project/bid-vector/frontend/node_modules \
      /home/deploy/project/bid-vector-preview-ui/frontend/node_modules
```

(`.gitignore:101` 이 `frontend/node_modules/` 를 무시하므로 워크트리 `git status` 는 깨끗하게 유지된다. 메인 체크아웃의 `frontend/dist`(api 가 서빙 중)는 건드리지 않고, 워크트리 빌드 산출물은 워크트리 `frontend/dist` 로만 나간다 — `.gitignore:102`.)

- [ ] **Step 3: 툴체인 검증 (기존 스위트 1개)**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/StrategyEditor.test.tsx`
Expected: `Test Files 1 passed` / `Tests 7 passed` — 심링크된 vitest/vite/jsdom 이 워크트리 소스를 정상 변환함을 증명.

**실패 시 fallback (워크트리 전용 오프라인 설치):**

```bash
rm /home/deploy/project/bid-vector-preview-ui/frontend/node_modules
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend ci --legacy-peer-deps --offline
```

그 뒤 Step 3 을 재실행한다. (오프라인이 실패하면 `--offline` 을 떼고 재시도 — 네트워크 사용은 사용자에게 알린다.)

- [ ] **Step 4: 계획 문서 복사**

```bash
cp /home/deploy/project/bid-vector/docs/superpowers/plans/2026-07-30-preview-snapshot-ui.md \
   /home/deploy/project/bid-vector-preview-ui/docs/superpowers/plans/
```

(spec `2026-07-30-inline-ml-memory-design.md` 는 PR-A 에서 이미 tracked — 복사 불필요.)

- [ ] **Step 5: 상태 확인**

Run: `git -C /home/deploy/project/bid-vector-preview-ui status --short`
Expected: `?? docs/superpowers/plans/2026-07-30-preview-snapshot-ui.md` 한 줄만. 브랜치는 `feature/preview-snapshot-ui`.

- [ ] **Step 6: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add docs/superpowers/plans/2026-07-30-preview-snapshot-ui.md
git commit -m "docs(plan): PR-C 프론트 스냅샷 UX + 폴링 구현 계획" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 수기 타입 확장 + openapi drift 확인 + 기존 fixture 정합

**Files:**
- Modify: `frontend/src/shared/types/strategy.ts:56-62` (`OperatorStrategyCandidatesResponse` 메타 추가 + `SnapshotStatus` + refresh 응답 타입)
- Modify: `frontend/src/features/strategy/StrategyEditor.test.tsx:35-41` (`baseCandidates` fixture 에 메타 3필드)

**Interfaces:**
- Consumes: `frontend/src/shared/types/openapi.d.ts:7079-7128` (PR-B 가 동기화한 생성 타입 — 수기 타입이 이것과 형태 일치해야 함)
- Produces (Task 3~7 전부가 의존):
  - `type SnapshotStatus = "idle" | "running" | "failed"`
  - `OperatorStrategyCandidatesResponse & { computed_at?: string | null; snapshot_status: SnapshotStatus; stale: boolean }`
  - `OperatorStrategyCandidatesRefreshResponse`

- [ ] **Step 1: 실패 유도 — 타입만 먼저 좁힌다**

`frontend/src/shared/types/strategy.ts` 의 `OperatorStrategyCandidatesResponse`(56-62행)를 교체하고 그 뒤에 refresh 응답을 추가한다:

```ts
/**
 * preview 스냅샷 상태 (백엔드 `OperatorPreviewSnapshot.status`).
 *
 * `idle` = 마지막 재계산이 성공했다(또는 아직 시작 전), `running` = ops 큐 task
 * 가 재계산 중, `failed` = 마지막 재계산이 실패했다. **`failed` 여도
 * `candidates`/`computed_at` 은 직전 성공분이 그대로 살아 있다**(설계 §6.2).
 */
export type SnapshotStatus = "idle" | "running" | "failed";

export interface OperatorStrategyCandidatesResponse {
  operator_id: number;
  /**
   * 스냅샷을 계산할 때 실제로 분석한 공고 수. **요청 limit 과 무관**하고 스냅샷의
   * 고정 분석 예산(PREVIEW_SCAN_CEILING=250)의 산물이다 — 표시 라벨도 그렇게
   * 붙인다(설계 §6.1: limit 은 키 차원이 아니라 서빙 슬라이스).
   */
  evaluated_project_count: number;
  returned_candidate_count: number;
  high_priority_only: boolean;
  candidates: OperatorStrategyCandidateItem[];
  /** 마지막 **성공** 계산 시각(ISO). `null` = 계산된 적 없음(부트스트랩). */
  computed_at?: string | null;
  snapshot_status: SnapshotStatus;
  /**
   * 저장된 계산이 낡았는가(시간 경과 **또는** 전략 편집 후 미재계산).
   *
   * 주의: `true` 가 "재계산이 큐에 있다"를 보장하지 않는다 — 실패 쿨다운(60s)
   * 동안은 stale 을 보고하면서 자동 디스패치가 억제된다. 또 `computed_at === null`
   * 은 `stale: false` 로 온다("낡음"이 아니라 "부트스트랩"). 신선도 분기는
   * `computed_at`/`snapshot_status` 로 하고 `stale` 단독으로 하지 않는다.
   */
  stale: boolean;
}

/**
 * `POST /operator/strategy/candidates/refresh` 202 응답.
 *
 * 별도 task-status 엔드포인트는 없다 — `poll_url` 이 후보 GET 자신을 가리키고
 * 폴링은 그 재조회로 한다(설계 §6.2). `detail` 은 디스패치/스킵 사유를 한국어로
 * 담으므로 그대로 사용자에게 보여준다.
 */
export interface OperatorStrategyCandidatesRefreshResponse {
  task_id?: string | null;
  operator_id: number;
  current_operator_id: number;
  current_operator_username: string;
  high_priority_only: boolean;
  snapshot_status: SnapshotStatus;
  detail: string;
  poll_url: string;
}
```

- [ ] **Step 2: 실패 확인 (타입 체크가 fixture 를 잡아낸다)**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run build`
Expected: FAIL — `src/features/strategy/StrategyEditor.test.tsx(35,7): error TS2739: Type '{ operator_id: number; ... }' is missing the following properties from type 'OperatorStrategyCandidatesResponse': snapshot_status, stale` (tsconfig `include: ["src"]` 이라 테스트 파일도 타입 체크 대상이다).

- [ ] **Step 3: fixture 정합 (Task 4 의 렌더 게이트까지 미리 만족시킨다)**

`frontend/src/features/strategy/StrategyEditor.test.tsx` 의 `baseCandidates`(35-41행)를 교체:

```ts
const baseCandidates: OperatorStrategyCandidatesResponse = {
  operator_id: 1,
  evaluated_project_count: 12,
  returned_candidate_count: 3,
  high_priority_only: false,
  candidates: [],
  // PR-B 이후 GET 은 스냅샷 순수 읽기다. 계산된 스냅샷(idle + computed_at)이라야
  // 카드가 통계·목록을 그리고 폴링을 멈춘다(부트스트랩=computed_at null 은
  // 진행 UI 로 빠진다 — features/strategy/snapshotState.ts).
  computed_at: "2026-07-30T02:00:00Z",
  snapshot_status: "idle",
  stale: false
};
```

- [ ] **Step 4: 통과 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run build`
Expected: `tsc --noEmit` 통과 + user/admin 두 번들 빌드 성공.

- [ ] **Step 5: openapi drift 확인 (재생성 아님 — 이탈 노트 1)**

Run:
```bash
/home/deploy/project/bid-vector/.venv/bin/python \
  /home/deploy/project/bid-vector-preview-ui/scripts/sync_openapi_types.py --check
```
Expected: `OpenAPI types are up to date.` (drift 가 보고되면 `--check` 없이 재생성 후 `chore(types): openapi.d.ts 재동기화 — main 표류 흡수` 로 별도 커밋하고 PR 본문에 명기한다.)

- [ ] **Step 6: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/shared/types/strategy.ts frontend/src/features/strategy/StrategyEditor.test.tsx
git commit -m "feat(types): 후보 응답 스냅샷 메타 + refresh 202 수기 타입 (§7)" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 순수 코어 — `formatRelativeTime` + `snapshotState`

**Files:**
- Modify: `frontend/src/shared/lib.ts` (`formatDateTime` 뒤, 62행 다음에 `formatRelativeTime` 추가)
- Create: `frontend/src/shared/lib.test.ts`
- Create: `frontend/src/features/strategy/snapshotState.ts`
- Create: `frontend/src/features/strategy/snapshotState.test.ts`

**Interfaces:**
- Consumes: `SnapshotStatus`, `OperatorStrategyCandidatesResponse` (Task 2)
- Produces (Task 4~7 이 의존):
  - `formatRelativeTime(value: string | Date | null | undefined, now?: Date): string`
  - `SNAPSHOT_POLL_INTERVAL_MS: number`
  - `isSnapshotSettled(data: OperatorStrategyCandidatesResponse | undefined): boolean`
  - `snapshotPollInterval(data: OperatorStrategyCandidatesResponse | undefined, errored: boolean, intervalMs?: number): number | false`
  - `hasComputedSnapshot(data: OperatorStrategyCandidatesResponse | undefined): boolean`

- [ ] **Step 1: 실패하는 테스트 작성 (2개 파일)**

`frontend/src/shared/lib.test.ts` 신규:

```ts
import { describe, expect, it } from "vitest";
import { formatDateTime, formatRelativeTime } from "./lib";

/**
 * 스냅샷 신선도 배지("N분 전 기준")가 이 버킷 경계에 의존한다. `now` 를 주입해
 * 벽시계와 무관하게 고정한다(§4.7-3).
 */
describe("formatRelativeTime", () => {
  const now = new Date("2026-07-30T12:00:00Z");

  it.each([
    ["2026-07-30T11:59:59Z", "방금"],
    ["2026-07-30T11:59:01Z", "방금"],
    ["2026-07-30T11:59:00Z", "1분 전"],
    ["2026-07-30T11:57:00Z", "3분 전"],
    ["2026-07-30T11:01:00Z", "59분 전"],
    ["2026-07-30T11:00:00Z", "1시간 전"],
    ["2026-07-29T13:00:00Z", "23시간 전"]
  ])("%s → %s", (value, expected) => {
    expect(formatRelativeTime(value, now)).toBe(expected);
  });

  it("24시간을 넘기면 KST 절대 시각으로 폴백한다", () => {
    const value = "2026-07-28T12:00:00Z";
    expect(formatRelativeTime(value, now)).toBe(formatDateTime(value));
  });

  it("미래 시각(클럭 스큐)은 '-분 전'이 아니라 '방금'으로 표기한다", () => {
    expect(formatRelativeTime("2026-07-30T12:05:00Z", now)).toBe("방금");
  });

  it("값이 없거나 파싱 불가면 '-'", () => {
    expect(formatRelativeTime(null, now)).toBe("-");
    expect(formatRelativeTime(undefined, now)).toBe("-");
    expect(formatRelativeTime("not-a-date", now)).toBe("-");
  });
});
```

`frontend/src/features/strategy/snapshotState.test.ts` 신규:

```ts
import { describe, expect, it } from "vitest";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import {
  SNAPSHOT_POLL_INTERVAL_MS,
  hasComputedSnapshot,
  isSnapshotSettled,
  snapshotPollInterval
} from "./snapshotState";

function snapshot(
  overrides: Partial<OperatorStrategyCandidatesResponse> = {}
): OperatorStrategyCandidatesResponse {
  return {
    operator_id: 1,
    evaluated_project_count: 250,
    returned_candidate_count: 0,
    high_priority_only: false,
    candidates: [],
    computed_at: "2026-07-30T02:00:00Z",
    snapshot_status: "idle",
    stale: false,
    ...overrides
  };
}

describe("isSnapshotSettled", () => {
  it("running 은 미정착 — 폴링을 계속한다", () => {
    expect(isSnapshotSettled(snapshot({ snapshot_status: "running" }))).toBe(false);
  });

  it("idle + computed_at 은 정착", () => {
    expect(isSnapshotSettled(snapshot())).toBe(true);
  });

  it("stale=true 여도 idle 이면 정착 — stale 이 재계산 큐를 보장하지 않는다(주의 1)", () => {
    expect(isSnapshotSettled(snapshot({ stale: true }))).toBe(true);
  });

  it("idle + computed_at=null 은 미정착 — stale=false 라도 '계산된 적 없음'이다(주의 3)", () => {
    expect(isSnapshotSettled(snapshot({ computed_at: null }))).toBe(false);
  });

  it("failed 는 정착 — 쿨다운 동안 재조회해도 바뀌지 않는다(주의 2)", () => {
    expect(isSnapshotSettled(snapshot({ snapshot_status: "failed" }))).toBe(true);
    expect(isSnapshotSettled(snapshot({ snapshot_status: "failed", computed_at: null }))).toBe(true);
  });

  it("응답 미도착(undefined)은 미정착", () => {
    expect(isSnapshotSettled(undefined)).toBe(false);
  });
});

describe("snapshotPollInterval", () => {
  it("미정착이면 주기를 돌려준다", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false)).toBe(
      SNAPSHOT_POLL_INTERVAL_MS
    );
  });

  it("주기는 주입으로 덮을 수 있다(테스트 가속)", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false, 20)).toBe(20);
  });

  it("정착이면 false", () => {
    expect(snapshotPollInterval(snapshot(), false)).toBe(false);
  });

  it("마지막 fetch 가 실패했으면 false — 죽은 백엔드에 영구 재시도하지 않는다", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), true)).toBe(false);
    expect(snapshotPollInterval(undefined, true)).toBe(false);
  });
});

describe("hasComputedSnapshot", () => {
  it("computed_at 이 있으면 통계·목록을 그릴 수 있다", () => {
    expect(hasComputedSnapshot(snapshot())).toBe(true);
    expect(hasComputedSnapshot(snapshot({ snapshot_status: "failed" }))).toBe(true);
  });

  it("computed_at 이 없으면 0건을 사실처럼 그리지 않는다", () => {
    expect(hasComputedSnapshot(snapshot({ computed_at: null }))).toBe(false);
    expect(hasComputedSnapshot(undefined)).toBe(false);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run:
```bash
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- \
  src/shared/lib.test.ts src/features/strategy/snapshotState.test.ts
```
Expected: 수집 오류 — `Failed to resolve import "./snapshotState"` 와 `"formatRelativeTime" is not exported by "src/shared/lib.ts"`.

- [ ] **Step 3: 구현**

(3a) `frontend/src/shared/lib.ts` — `formatDateTime`(51-62행) 바로 뒤에 추가:

```ts
/**
 * "3분 전" 형태의 상대 시각. 스냅샷 신선도 배지("N분 전 기준")가 쓴다.
 *
 * 하루를 넘기면 상대 표기가 정보를 잃으므로 KST 절대 시각(`formatDateTime`)으로
 * 폴백한다 — 포맷터를 새로 만들지 않고 기존 단일 출처를 재사용한다(§4.5-6).
 * 서버·클라이언트 시계 차이로 미래 시각이 오면 "-3분 전" 같은 거짓 표기 대신
 * "방금"으로 눕힌다. `now` 는 테스트 주입용(§4.7-3).
 */
export function formatRelativeTime(
  value: string | Date | null | undefined,
  now: Date = new Date()
): string {
  const date = parseDate(value);
  if (!date) return "-";
  const elapsedMs = now.getTime() - date.getTime();
  if (elapsedMs < 0) return "방금";
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return formatDateTime(date);
}
```

(3b) `frontend/src/features/strategy/snapshotState.ts` 신규:

```ts
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

/**
 * preview 스냅샷 상태 해석 — 폴링·렌더 게이트의 순수 코어 (설계 2026-07-30 §7).
 *
 * PR-B 이후 `GET /operator/strategy/candidates` 는 요청 경로에서 ML 스캔을 하지
 * 않고 스냅샷 행을 순수 읽기한다. 그래서 프론트의 판단 근거는 오직 응답 메타
 * (`snapshot_status`/`computed_at`/`stale`)이고, 그 해석에 소비자 주의 4건이
 * 전부 걸려 있어 컴포넌트 안 조건식이 아니라 이 모듈에 모아 테스트한다(§4.7-4).
 */

/**
 * 폴링 주기(ms). 스냅샷 재계산은 분석 예산(250건)에 묶여 수십 초 규모라
 * ExperimentRunProgress(1.5s)보다 느슨하게 잡는다 — 매직값을 컴포넌트에 두지
 * 않는다(§4.5-1).
 */
export const SNAPSHOT_POLL_INTERVAL_MS = 3_000;

/**
 * 더 물어봐도 상태가 바뀌지 않는가(= 폴링 정지 조건).
 *
 * - `running`: 미정착 — 재계산이 진행 중이다.
 * - `failed`: **정착**. 이전 성공 뒤 실패한 행은 `failed` + `stale=false` 로 오고
 *   실패 쿨다운(60s) 동안 자동 재디스패치도 안 되므로 계속 물어도 같은 답이다.
 *   복구는 사용자의 명시 갱신(POST /candidates/refresh)이 담당한다.
 * - `idle` + `computed_at === null`: **미정착**. 계산된 적 없는 행은 `stale=false`
 *   로 오지만 "최신"이 아니라 "부트스트랩"이다 — 다음 GET 이 자동 디스패치하므로
 *   한두 틱 뒤 `running` 또는 `failed` 로 정착한다(livelock 없음).
 */
export function isSnapshotSettled(
  data: OperatorStrategyCandidatesResponse | undefined
): boolean {
  if (!data) return false;
  if (data.snapshot_status === "running") return false;
  if (data.snapshot_status === "failed") return true;
  return data.computed_at != null;
}

/**
 * react-query `refetchInterval` 콜백의 순수 코어.
 *
 * 정착이면 멈춘다. 마지막 fetch 가 실패했으면(`errored`) 멈춘다 — 백엔드가 죽었을
 * 때 열린 탭이 영구 재시도하지 않게 한다. 전역 `retry: 1` 이 일시 장애를 이미 한
 * 번 흡수하고, 새로고침의 invalidate 가 성공하면 폴링은 자동 재개된다.
 */
export function snapshotPollInterval(
  data: OperatorStrategyCandidatesResponse | undefined,
  errored: boolean,
  intervalMs: number = SNAPSHOT_POLL_INTERVAL_MS
): number | false {
  if (errored) return false;
  return isSnapshotSettled(data) ? false : intervalMs;
}

/**
 * 한 번이라도 성공 계산된 스냅샷이 있는가 — 통계·후보 목록의 렌더 게이트.
 *
 * 부트스트랩 응답은 `evaluated_project_count: 0` / `candidates: []` 인데 그대로
 * 그리면 "평가 0건 / 매칭되는 후보가 없습니다"라는 거짓이 된다(§2 정직 명세).
 * `snapshot_status === "failed"` 여도 `computed_at` 이 있으면 직전 성공분은
 * 유효하므로 계속 보여준다(소비자 주의 2).
 */
export function hasComputedSnapshot(
  data: OperatorStrategyCandidatesResponse | undefined
): boolean {
  return data?.computed_at != null;
}
```

- [ ] **Step 4: 통과 확인**

Run:
```bash
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- \
  src/shared/lib.test.ts src/features/strategy/snapshotState.test.ts
```
Expected: `Test Files 2 passed` / `Tests 26 passed` (lib 12 + snapshotState 14).

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/shared/lib.ts frontend/src/shared/lib.test.ts \
        frontend/src/features/strategy/snapshotState.ts \
        frontend/src/features/strategy/snapshotState.test.ts
git commit -m "feat(strategy): 스냅샷 상태 해석 순수 코어 + 상대 시각 포맷터 (§7)" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `CandidatesPreview` 스냅샷 즉시 렌더 + 신선도 배지 + 진행/실패 표면 + terminal 게이트 폴링

**Files:**
- Create: `frontend/src/features/strategy/components/SnapshotFreshnessBadge.tsx`
- Create: `frontend/src/features/strategy/components/SnapshotStatusNotice.tsx`
- Create: `frontend/src/features/strategy/components/CandidateList.tsx`
- Modify: `frontend/src/features/strategy/components/index.ts` (3개 export 추가)
- Modify: `frontend/src/features/strategy/CandidatesPreview.tsx` (전면 개편, 새로고침 버튼은 Task 5 까지 그대로)
- Modify: `frontend/src/features/strategy/hooks.ts:37-51` (`useStrategyCandidatesQuery` 폴링 게이트)
- Create: `frontend/src/features/strategy/CandidatesPreview.test.tsx`

**Interfaces:**
- Consumes: `snapshotState`(Task 3), `formatRelativeTime`(Task 3), `OperatorStrategyCandidatesResponse`(Task 2), `EligibilityFeedbackButtons`, `useShellContext`
- Produces:
  - `useStrategyCandidatesQuery(session, params?, operatorId?, options?: { pollIntervalMs?: number })`
  - `CandidatesPreview(props: { pollIntervalMs?: number })`
  - `SnapshotFreshnessBadge({ snapshot })` / `SnapshotStatusNotice({ snapshot })` / `CandidateList({ candidates, session })`
  - 테스트 조회 계약 `data-testid="snapshot-progress"` / `"snapshot-failed"`

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/features/strategy/CandidatesPreview.test.tsx` 신규:

```tsx
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTestQueryClient } from "@/test-utils";
import { Toaster, toastApi } from "@/shared/components/ui";
import type { ShellOutletContext } from "@/app/dashboardContext";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import { CandidatesPreview } from "./CandidatesPreview";

/** 테스트 폴링 주기 — 실제 벽시계를 쓰는 ExperimentRunProgress.test 패턴. */
const POLL_MS = 20;
const session = { token: "token-candidates", username: "operator" };
const CANDIDATE_TITLE = "서울 AI 데이터 통합 플랫폼";

function minutesAgo(minutes: number): string {
  // 버킷 경계에서 흔들리지 않게 1초 더 뒤로 민다(floor 버킷).
  return new Date(Date.now() - minutes * 60_000 - 1_000).toISOString();
}

function snapshot(
  overrides: Partial<OperatorStrategyCandidatesResponse> = {}
): OperatorStrategyCandidatesResponse {
  return {
    operator_id: 1,
    evaluated_project_count: 250,
    returned_candidate_count: 1,
    high_priority_only: false,
    candidates: [
      {
        project_id: 77,
        title: CANDIDATE_TITLE,
        category: "software",
        budget_estimate: 130_000_000,
        deadline: null,
        matched_score: 0.7,
        probability_score: 0.8,
        priority_score: 0.9,
        action: "review",
        recommended_amount: 111_000_000,
        analysis_summary: "요약",
        strategy_reasons: []
      }
    ],
    computed_at: minutesAgo(3),
    snapshot_status: "idle",
    stale: false,
    ...overrides
  };
}

const bootstrapSnapshot = snapshot({
  computed_at: null,
  snapshot_status: "running",
  candidates: [],
  returned_candidate_count: 0,
  evaluated_project_count: 0
});

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

/** 후보 GET 응답을 순서대로 돌려준다(마지막 값에서 고정). */
function installFetchMock(payloads: OperatorStrategyCandidatesResponse[]) {
  let index = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/strategy/candidates")) {
      const payload = payloads[Math.min(index, payloads.length - 1)]!;
      index += 1;
      return jsonResponse(payload);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function candidatesCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url]) => String(url).includes("/strategy/candidates") && !String(url).includes("/refresh")
  );
}

async function settle(ms: number = POLL_MS * 5) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function renderPreview() {
  const queryClient = createTestQueryClient();
  const context = { session } as unknown as ShellOutletContext;
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/strategy"]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route
              path="/dashboard/strategy"
              element={<CandidatesPreview pollIntervalMs={POLL_MS} />}
            />
          </Route>
        </Routes>
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient };
}

beforeEach(() => {
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("CandidatesPreview 스냅샷 렌더", () => {
  it("저장된 스냅샷을 즉시 렌더하고 'N분 전 기준' 배지를 보여준다", async () => {
    const fetchMock = installFetchMock([snapshot()]);
    renderPreview();

    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();
    // evaluated_project_count 는 스냅샷의 고정 분석 예산(250)이고 요청 limit(5)과
    // 무관하다 — 라벨과 각주가 그렇게 말한다(소비자 주의 4).
    expect(screen.getByText("분석 대상")).toBeInTheDocument();
    expect(screen.getByText("250건")).toBeInTheDocument();
    expect(screen.getByText(/고정 분석 예산/)).toBeInTheDocument();
    // 정착 상태라 진행/실패 표시가 없고 폴링도 돌지 않는다.
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
    expect(screen.queryByTestId("snapshot-failed")).toBeNull();
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("stale=true 만으로 '갱신 중'을 말하지 않고 '갱신 필요'까지만 말한다", async () => {
    // 실패 쿨다운(60s) 중에는 stale 이면서 자동 디스패치가 억제된다(주의 1).
    installFetchMock([snapshot({ stale: true, computed_at: minutesAgo(40) })]);
    renderPreview();

    expect(await screen.findByText("40분 전 기준 · 갱신 필요")).toBeInTheDocument();
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
  });

  it("computed_at=null 이면 '첫 계산 대기' + 경과 안내를 보여주고 0건을 사실처럼 그리지 않는다", async () => {
    installFetchMock([bootstrapSnapshot]);
    renderPreview();

    expect(await screen.findByText("첫 계산 대기")).toBeInTheDocument();
    const progress = await screen.findByTestId("snapshot-progress");
    expect(progress).toHaveAttribute("role", "status");
    expect(progress).toHaveTextContent("다시 계산하고 있습니다");
    expect(progress).toHaveTextContent("초 경과");
    expect(progress).toHaveTextContent("최초 계산은 수십 초");
    // 부트스트랩 0건을 "매칭 후보 없음"으로 오도하지 않는다(§2 정직 명세).
    expect(screen.queryByText("현재 매칭되는 후보가 없습니다.")).toBeNull();
    expect(screen.queryByText("분석 대상")).toBeNull();
  });

  it("running → idle 로 전이하면 목록을 그리고 폴링을 멈춘다", async () => {
    const settled = snapshot();
    const fetchMock = installFetchMock([snapshot({ snapshot_status: "running" }), settled]);
    renderPreview();

    await screen.findByTestId("snapshot-progress");
    expect(await screen.findByText("3분 전 기준")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("snapshot-progress")).toBeNull());
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();

    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("snapshot_status=failed 는 경고를 띄우면서 직전 후보를 계속 보여준다", async () => {
    const fetchMock = installFetchMock([snapshot({ snapshot_status: "failed" })]);
    renderPreview();

    const alert = await screen.findByTestId("snapshot-failed");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent("최근 갱신이 실패했습니다");
    expect(alert).toHaveTextContent("직전에 성공한 계산 결과");
    // 이전 성공분은 유효하다 — 후보와 신선도를 지우지 않는다(주의 2).
    expect(screen.getByText(CANDIDATE_TITLE)).toBeInTheDocument();
    expect(screen.getByText("3분 전 기준")).toBeInTheDocument();
    // failed 는 terminal — 쿨다운 동안 재조회해도 답이 같으므로 폴링하지 않는다.
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });

  it("우선순위 높음만 체크는 high_priority_only=true 로 재조회한다", async () => {
    const fetchMock = installFetchMock([snapshot()]);
    renderPreview();
    await screen.findByText("3분 전 기준");

    const user = (await import("@testing-library/user-event")).default.setup();
    await user.click(screen.getByRole("checkbox", { name: "우선순위 높음만" }));

    await waitFor(() =>
      expect(
        candidatesCalls(fetchMock).some(([url]) =>
          String(url).includes("high_priority_only=true")
        )
      ).toBe(true)
    );
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/CandidatesPreview.test.tsx`
Expected: 6건 FAIL — `Unable to find an element with the text: 3분 전 기준` / `...text: 첫 계산 대기` / `Unable to find an element by: [data-testid="snapshot-progress"]` (현행 카드는 메타를 전혀 그리지 않는다).

- [ ] **Step 3: 구현**

(3a) `frontend/src/features/strategy/components/SnapshotFreshnessBadge.tsx` 신규:

```tsx
import { Badge } from "@/shared/components/ui";
import { formatRelativeTime } from "@/shared/lib";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

const NEVER_COMPUTED_LABEL = "첫 계산 대기";
const BASELINE_SUFFIX = " 기준";
const STALE_SUFFIX = " · 갱신 필요";

export interface SnapshotFreshnessBadgeProps {
  snapshot?: OperatorStrategyCandidatesResponse;
}

/**
 * "N분 전 기준" 신선도 배지 (설계 §7).
 *
 * `computed_at === null`(계산된 적 없음)은 `stale=false` 로 오지만 "최신"이 아니라
 * **부트스트랩**이라 별도 문구를 쓴다(소비자 주의 3). `stale=true` 는 시간 경과와
 * 전략 편집 둘 다에서 켜지고, 실패 쿨다운 중에는 재계산이 큐에 없을 수도 있어
 * "갱신 중"을 약속하지 못한다 — 그래서 "갱신 필요"까지만 말한다(주의 1).
 */
export function SnapshotFreshnessBadge({ snapshot }: SnapshotFreshnessBadgeProps) {
  if (!snapshot) return null;
  if (snapshot.computed_at == null) {
    return <Badge tone="muted">{NEVER_COMPUTED_LABEL}</Badge>;
  }
  return (
    <Badge tone={snapshot.stale ? "watch" : "muted"}>
      {`${formatRelativeTime(snapshot.computed_at)}${BASELINE_SUFFIX}${
        snapshot.stale ? STALE_SUFFIX : ""
      }`}
    </Badge>
  );
}
```

(3b) `frontend/src/features/strategy/components/SnapshotStatusNotice.tsx` 신규:

```tsx
import { useEffect, useState } from "react";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";

const RUNNING_LABEL = "미리보기를 다시 계산하고 있습니다…";
const FIRST_RUN_HINT = "최초 계산은 수십 초가 걸릴 수 있습니다.";
const FAILED_TITLE = "최근 갱신이 실패했습니다";
const FAILED_WITH_SNAPSHOT =
  "직전에 성공한 계산 결과를 그대로 표시합니다. 새로고침으로 다시 시도할 수 있습니다.";
const FAILED_WITHOUT_SNAPSHOT = "표시할 이전 결과가 없습니다. 새로고침으로 다시 시도해 주세요.";
const ELAPSED_TICK_MS = 1_000;

export interface SnapshotStatusNoticeProps {
  snapshot?: OperatorStrategyCandidatesResponse;
}

/**
 * 갱신 중 인디케이터(+경과 안내)와 실패 경고 (설계 §7, ExperimentRunProgress 패턴).
 *
 * "갱신 중"은 `snapshot_status === "running"` 에서만 말한다 — `stale` 로는 말하지
 * 않는다(소비자 주의 1). 실패는 접근성 경고로 띄우되 목록을 지우지 않는다: 이전
 * 성공분이 남아 있으면 그대로 유효하다(주의 2). 경과 안내는 스냅샷이 아직 없는
 * 최초 계산(온보딩 첫 진입)에서만 덧붙인다.
 */
export function SnapshotStatusNotice({ snapshot }: SnapshotStatusNoticeProps) {
  const running = snapshot?.snapshot_status === "running";
  const elapsedSeconds = useElapsedSeconds(running);

  if (!snapshot) return null;

  if (running) {
    const bootstrap = snapshot.computed_at == null;
    return (
      <p
        role="status"
        data-testid="snapshot-progress"
        className="flex items-center gap-2 text-xs text-[var(--color-muted)]"
      >
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]"
        />
        <span>
          {`${RUNNING_LABEL} ${elapsedSeconds}초 경과`}
          {bootstrap ? ` · ${FIRST_RUN_HINT}` : ""}
        </span>
      </p>
    );
  }

  if (snapshot.snapshot_status === "failed") {
    return (
      <div
        role="alert"
        data-testid="snapshot-failed"
        className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] p-2 text-xs"
      >
        <p className="font-medium text-[color-mix(in_oklch,var(--color-danger),black_30%)]">
          {FAILED_TITLE}
        </p>
        <p className="text-[var(--color-fg)]">
          {snapshot.computed_at == null ? FAILED_WITHOUT_SNAPSHOT : FAILED_WITH_SNAPSHOT}
        </p>
      </div>
    );
  }

  return null;
}

/**
 * `active` 가 켜진 시점부터의 경과 초. 폴링 재조회(running→running)로는 리셋되지
 * 않고 `active` 전이에만 반응한다. 소비자가 하나뿐이라 아직 공용화하지 않는다
 * (§4.5-6 — 두 번째 소비자가 생기면 `shared/hooks/` 로 승격).
 */
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!active) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    }, ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, [active]);
  return seconds;
}
```

(3c) `frontend/src/features/strategy/components/CandidateList.tsx` 신규 (기존 `CandidatesPreview` 의 `<ul>` + if-체인 2개를 룩업 맵으로 이관 — §4.5-2):

```tsx
import type { BadgeTone } from "@/shared/components/ui";
import { Badge } from "@/shared/components/ui";
import { formatCurrencyCompact } from "@/shared/lib";
import type { AuthSession } from "@/app/layout/AuthGate";
import type {
  OperatorStrategyCandidateItem,
  StrategyAction
} from "@/shared/types/strategy";
import { EligibilityFeedbackButtons } from "../EligibilityFeedbackButtons";

const EMPTY_LABEL = "현재 매칭되는 후보가 없습니다.";
const ACTION_LABEL: Record<StrategyAction, string> = {
  bid_now: "투찰",
  review: "검토",
  skip: "보류"
};
const ACTION_TONE: Record<StrategyAction, BadgeTone> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

export interface CandidateListProps {
  candidates: OperatorStrategyCandidateItem[];
  session: AuthSession | null;
}

/**
 * 스냅샷 상위 후보 목록. **성공 계산이 있는 스냅샷에서만** 렌더된다 —
 * 부트스트랩 0건을 "매칭 후보 없음"으로 오도하지 않기 위해 호출부가
 * `hasComputedSnapshot` 으로 게이트한다(§2 정직 명세).
 */
export function CandidateList({ candidates, session }: CandidateListProps) {
  return (
    <ul className="flex flex-col gap-2 pt-2" aria-label="상위 후보">
      {candidates.length === 0 ? (
        <li className="text-xs text-[var(--color-muted)]">{EMPTY_LABEL}</li>
      ) : (
        candidates.map((candidate) => (
          <li
            key={candidate.project_id}
            className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2 text-xs"
          >
            <div className="flex items-center justify-between gap-2">
              <span
                className="truncate font-medium text-[var(--color-fg)]"
                title={candidate.title}
              >
                {candidate.title}
              </span>
              <Badge tone={ACTION_TONE[candidate.action]}>
                {ACTION_LABEL[candidate.action]}
              </Badge>
            </div>
            <div className="flex items-center justify-between text-[var(--color-muted)]">
              <span>{candidate.category ?? "-"}</span>
              <span className="tabular-nums">
                {formatCurrencyCompact(candidate.budget_estimate)}
              </span>
            </div>
            <EligibilityFeedbackButtons projectId={candidate.project_id} session={session} />
          </li>
        ))
      )}
    </ul>
  );
}
```

(3d) `frontend/src/features/strategy/components/index.ts` 에 추가:

```ts
export { SnapshotFreshnessBadge } from "./SnapshotFreshnessBadge";
export { SnapshotStatusNotice } from "./SnapshotStatusNotice";
export { CandidateList } from "./CandidateList";
```

(3e) `frontend/src/features/strategy/hooks.ts` — `useStrategyCandidatesQuery`(37-51행) 교체. import 블록에 `import { SNAPSHOT_POLL_INTERVAL_MS, snapshotPollInterval } from "./snapshotState";` 추가:

```ts
/**
 * 후보 스냅샷 조회 + terminal 게이트 폴링 (설계 2026-07-30 §7).
 *
 * PR-B 이후 이 GET 은 스냅샷 순수 읽기이고 재계산은 ops 큐 task 다. 그래서 폴링
 * 조건은 **응답 메타(서버 status)뿐**이다 — 로컬 "폴링 중" 플래그를 두지 않는다.
 * 이 카드의 키는 `["strategy", "candidates", ...]` 이므로 전략 저장·realtime
 * `strategy.monitor.*`·온보딩 apply 의 `["strategy"]` 전면 invalidate 가 쿼리를
 * 리셋하지만, 리셋 후에도 판정 근거가 같은 서버 응답이라 동작이 결정적이다.
 */
export function useStrategyCandidatesQuery(
  session: AuthSession | null,
  params: StrategyCandidatesQuery = {},
  operatorId: number | null = null,
  options: { pollIntervalMs?: number } = {}
) {
  const pollIntervalMs = options.pollIntervalMs ?? SNAPSHOT_POLL_INTERVAL_MS;
  return useQuery({
    queryKey: queryKeys.strategy.candidates(
      params.limit,
      params.highPriorityOnly,
      operatorId
    ),
    queryFn: () => fetchStrategyCandidates(params, session?.token, operatorId),
    enabled: Boolean(session?.token),
    refetchInterval: (query) =>
      snapshotPollInterval(
        query.state.data,
        query.state.status === "error",
        pollIntervalMs
      ),
    // 숨은 탭에서는 인터벌을 쉬게 하되(react-query 네이티브) 복귀 시 자동
    // 재개된다. `document.visibilityState` 직접 게이트는 전역
    // `refetchOnWindowFocus: false` 와 맞물려 복귀 후 폴링이 고착될 수 있어 쓰지
    // 않는다.
    refetchIntervalInBackground: false
  });
}
```

(3f) `frontend/src/features/strategy/CandidatesPreview.tsx` 전면 교체:

```tsx
import { useState } from "react";
import { Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { useShellContext } from "@/app/dashboardContext";
import {
  CandidateList,
  SnapshotFreshnessBadge,
  SnapshotStatusNotice
} from "./components";
import { hasComputedSnapshot } from "./snapshotState";
import { useStrategyCandidatesQuery } from "./hooks";

const CANDIDATE_LIMIT = 5;
const ANALYZED_LABEL = "분석 대상";
const MATCHED_LABEL = "매칭 후보";
/** evaluated_project_count 의 정직한 각주 — 요청 limit 의 산물이 아니다. */
const ANALYZED_HINT = "스냅샷 계산 시 고정 분석 예산 기준 — 요청 개수와 무관합니다.";

export interface CandidatesPreviewProps {
  /** 폴링 주기(ms) 오버라이드. 테스트 가속용(ExperimentRunProgress 패턴). */
  pollIntervalMs?: number;
}

/**
 * 전략 preview 스냅샷 카드 (설계 2026-07-30 §7).
 *
 * 백엔드는 `GET /operator/strategy/candidates` 를 **순수 읽기**로 서빙한다(PR-B):
 * 마지막 계산 결과 + `computed_at`/`snapshot_status`/`stale` 메타. 그래서 이 카드는
 * "불러오는 중"이 아니라 **저장된 스냅샷을 즉시** 그리고, 서버가 `running` 인 동안만
 * 폴링한다(정착 판정은 `snapshotState.isSnapshotSettled`).
 *
 * 백엔드 리뷰가 PR-C 로 넘긴 소비자 주의 4건:
 * 1. `stale=true` 는 재계산이 큐에 있다는 보장이 아니다(실패 쿨다운 60s 동안
 *    stale 을 보고하면서 자동 디스패치는 억제) → stale 은 "갱신 필요"까지만,
 *    "갱신 중"은 `snapshot_status` 로만.
 * 2. 이전 성공 뒤 실패한 행은 `failed` + `stale=false` → 실패를 알리면서도
 *    직전(유효) 후보는 계속 보여준다.
 * 3. `computed_at === null`(최초/부트스트랩)도 `stale=false` → 신선도·렌더 분기는
 *    `computed_at`/`snapshot_status` 로 한다.
 * 4. `evaluated_project_count` 는 스냅샷의 고정 분석 예산(250) 산물이고 요청
 *    limit 과 무관하다 → 라벨·각주를 그렇게 붙인다.
 */
export function CandidatesPreview({ pollIntervalMs }: CandidatesPreviewProps) {
  const { session } = useShellContext();
  const [highPriorityOnly, setHighPriorityOnly] = useState(false);
  const query = useStrategyCandidatesQuery(
    session,
    { limit: CANDIDATE_LIMIT, highPriorityOnly },
    null,
    { pollIntervalMs }
  );
  const snapshot = query.data;

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="flex flex-col items-start gap-1">
          <CardTitle>영향 후보 미리보기</CardTitle>
          <SnapshotFreshnessBadge snapshot={snapshot} />
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
        >
          새로고침
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={highPriorityOnly}
            onChange={(event) => setHighPriorityOnly(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          <span>우선순위 높음만</span>
        </label>
        {query.error ? (
          <p className="text-xs text-[var(--color-danger)]" role="alert">
            {query.error.message ?? "후보를 불러오지 못했습니다."}
          </p>
        ) : null}
        <SnapshotStatusNotice snapshot={snapshot} />
        {snapshot && hasComputedSnapshot(snapshot) ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">{ANALYZED_LABEL}</span>
              <strong className="tabular-nums">{snapshot.evaluated_project_count}건</strong>
            </div>
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-[var(--color-muted)]">{MATCHED_LABEL}</span>
              <strong className="tabular-nums">{snapshot.returned_candidate_count}건</strong>
            </div>
            <p className="text-[11px] text-[var(--color-muted)]">{ANALYZED_HINT}</p>
            <CandidateList candidates={snapshot.candidates} session={session} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/CandidatesPreview.test.tsx src/features/strategy/StrategyEditor.test.tsx`
Expected: `Test Files 2 passed` / `Tests 13 passed` (신규 6 + StrategyEditor 기존 7). CandidatesPreview 는 116행(≤250, §4.5-4).

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/features/strategy/CandidatesPreview.tsx \
        frontend/src/features/strategy/CandidatesPreview.test.tsx \
        frontend/src/features/strategy/hooks.ts \
        frontend/src/features/strategy/components/
git commit -m "feat(strategy): 후보 카드 스냅샷 즉시 렌더 + 신선도 배지 + terminal 게이트 폴링 (§7)" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 새로고침 = `POST /candidates/refresh` 디스패치 + 폴링 인수

**Files:**
- Modify: `frontend/src/shared/api/strategy.ts` (`refreshStrategyCandidates` + `StrategyCandidatesRefreshQuery` 추가 — `shared/api/index.ts:5` 의 `export * from "./strategy"` 로 자동 노출)
- Modify: `frontend/src/shared/api/queryKeys.ts:71-82` (`strategy.candidatesAll` 추가)
- Modify: `frontend/src/features/strategy/hooks.ts` (`useRefreshStrategyCandidatesMutation` 추가)
- Modify: `frontend/src/features/strategy/CandidatesPreview.tsx` (버튼 배선 교체)
- Modify: `frontend/src/features/strategy/CandidatesPreview.test.tsx` (refresh 케이스 추가)

**Interfaces:**
- Consumes: `OperatorStrategyCandidatesRefreshResponse`(Task 2), `wrap`/`withOperator`/`apiRequest`(기존 `shared/api/strategy.ts`)
- Produces:
  - `refreshStrategyCandidates(params?: StrategyCandidatesRefreshQuery, token?: string | null, operatorId?: number | null): Promise<OperatorStrategyCandidatesRefreshResponse>`
  - `queryKeys.strategy.candidatesAll(): readonly ["strategy", "candidates"]`
  - `useRefreshStrategyCandidatesMutation(session)` → `UseMutationResult<OperatorStrategyCandidatesRefreshResponse, Error, StrategyCandidatesRefreshQuery>`

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/features/strategy/CandidatesPreview.test.tsx` — `installFetchMock` 을 refresh 라우트까지 다루도록 확장하고 케이스 2건을 추가한다.

`installFetchMock` 교체:

```tsx
/** 명시 갱신 202 응답 (백엔드 _CANDIDATES_REFRESH_DISPATCHED_DETAIL 그대로). */
const REFRESH_ACCEPTED = {
  task_id: "task-preview-1",
  operator_id: 1,
  current_operator_id: 1,
  current_operator_username: "operator",
  high_priority_only: false,
  snapshot_status: "running" as const,
  detail: "미리보기 재계산을 큐에 등록했습니다.",
  poll_url: "/api/v1/operator/strategy/candidates"
};

function installFetchMock(
  payloads: OperatorStrategyCandidatesResponse[],
  refresh: unknown = REFRESH_ACCEPTED
) {
  let index = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/strategy/candidates/refresh")) return jsonResponse(refresh, 202);
    if (url.includes("/strategy/candidates")) {
      const payload = payloads[Math.min(index, payloads.length - 1)]!;
      index += 1;
      return jsonResponse(payload);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function refreshCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes("/strategy/candidates/refresh") &&
      (init as RequestInit | undefined)?.method === "POST"
  );
}
```

새 describe 블록 추가:

```tsx
describe("CandidatesPreview 명시 갱신", () => {
  it("새로고침은 POST /candidates/refresh 를 보내고 그 뒤 폴링으로 결과를 반영한다", async () => {
    const fresh = snapshot({ computed_at: new Date().toISOString() });
    const fetchMock = installFetchMock([
      snapshot({ stale: true, computed_at: minutesAgo(40) }), // 최초: stale 이지만 정착
      snapshot({ snapshot_status: "running", computed_at: minutesAgo(40) }), // 202 직후
      fresh // 재계산 완료
    ]);
    renderPreview();
    await screen.findByText("40분 전 기준 · 갱신 필요");

    const user = (await import("@testing-library/user-event")).default.setup();
    await user.click(screen.getByRole("button", { name: "새로고침" }));

    await waitFor(() => expect(refreshCalls(fetchMock)).toHaveLength(1));
    const [url, init] = refreshCalls(fetchMock)[0]!;
    expect(String(url)).toBe(
      "/api/v1/operator/strategy/candidates/refresh?high_priority_only=false"
    );
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer token-candidates"
    );
    // 202 detail 을 그대로 보여준다(디스패치/스킵 사유가 서버 문구로 구분된다).
    expect(await screen.findByText("미리보기 재계산을 큐에 등록했습니다.")).toBeInTheDocument();
    // 폴링은 로컬 플래그가 아니라 서버가 돌려준 running 이 켠다.
    await screen.findByTestId("snapshot-progress");
    expect(await screen.findByText("방금 기준")).toBeInTheDocument();
  });

  it("갱신 중에도 새로고침 버튼은 활성 — 고착 running 회수 경로를 막지 않는다", async () => {
    installFetchMock([snapshot({ snapshot_status: "running" })]);
    renderPreview();

    await screen.findByTestId("snapshot-progress");
    expect(screen.getByRole("button", { name: "새로고침" })).toBeEnabled();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/CandidatesPreview.test.tsx`
Expected: 신규 2건 FAIL — `expected [] to have a length of 1`(현행 버튼은 `query.refetch()` 만 하므로 POST 가 없다) 와 `expected element to be enabled`(현행은 `query.isFetching` 으로 disable).

- [ ] **Step 3: 구현**

(3a) `frontend/src/shared/api/strategy.ts` — import 에 `OperatorStrategyCandidatesRefreshResponse` 추가하고 `fetchStrategyCandidates`(83행) 뒤에:

```ts
export interface StrategyCandidatesRefreshQuery {
  highPriorityOnly?: boolean;
}

/**
 * 미리보기 스냅샷 재계산 명시 디스패치(202).
 *
 * 별도 task-status 엔드포인트는 없다 — 응답 `poll_url` 이 후보 GET 자신을 가리키고
 * 상태 관찰은 그 재조회로 한다(설계 §6.2). 그래서 호출부는 202 를 받은 뒤 후보
 * 쿼리를 invalidate 해 폴링을 서버 status 에 인수한다.
 */
export function refreshStrategyCandidates(
  params: StrategyCandidatesRefreshQuery = {},
  token?: string | null,
  operatorId?: number | null
): Promise<OperatorStrategyCandidatesRefreshResponse> {
  const search = new URLSearchParams();
  if (typeof params.highPriorityOnly === "boolean") {
    search.set("high_priority_only", String(params.highPriorityOnly));
  }
  const query = search.toString();
  const path = query
    ? `/api/v1/operator/strategy/candidates/refresh?${query}`
    : "/api/v1/operator/strategy/candidates/refresh";
  return wrap(
    apiRequest<OperatorStrategyCandidatesRefreshResponse>(withOperator(path, operatorId), {
      method: "POST",
      token
    }),
    "미리보기 갱신 요청에 실패했습니다."
  );
}
```

(3b) `frontend/src/shared/api/queryKeys.ts` — `strategy.candidates`(71-81행) 뒤에 추가:

```ts
    /**
     * 후보 쿼리 전체(limit·우선순위·operator 변형)의 prefix. 재계산 디스패치 후
     * 캐시된 모든 변형을 한 번에 갱신하는 데 쓴다 — 변형별 폴링은 각자의 서버
     * status 가 결정한다.
     */
    candidatesAll: () => ["strategy", "candidates"] as const,
```

(3c) `frontend/src/features/strategy/hooks.ts` — import 에 `refreshStrategyCandidates`, `type StrategyCandidatesRefreshQuery` 를 추가하고 `type OperatorStrategyCandidatesRefreshResponse` 를 `@/shared/types/strategy` 에서 가져온 뒤, `useUpdateStrategyMutation` 아래에 추가:

```ts
/**
 * 미리보기 스냅샷 재계산 명시 디스패치 (설계 §7 — 구 `query.refetch()` 대체).
 *
 * 202 는 "큐에 넣었다"이지 "끝났다"가 아니므로, 성공 응답의 `detail`(디스패치 /
 * 이미 실행 중 재사용 / 큐잉 실패가 서버 문구로 구분된다)을 그대로 보여주고 후보
 * 쿼리를 invalidate 한다. 그 뒤의 폴링은 다음 GET 이 돌려주는 `snapshot_status`
 * 가 켠다 — 로컬 플래그를 두지 않는다. 401 침묵은 세션 만료 모달 소관
 * (`useEligibilityFeedbackMutation` 패턴).
 */
export function useRefreshStrategyCandidatesMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<
    OperatorStrategyCandidatesRefreshResponse,
    Error,
    StrategyCandidatesRefreshQuery
  >({
    mutationFn: (params) => refreshStrategyCandidates(params, session?.token),
    onSuccess: (data) => {
      toastApi.info({ title: "미리보기 갱신 요청", description: data.detail });
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategy.candidatesAll() });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) return;
      toastApi.danger({
        title: "미리보기 갱신 실패",
        description: error instanceof Error ? error.message : "잠시 후 다시 시도해 주세요."
      });
    }
  });
}
```

(3d) `frontend/src/features/strategy/CandidatesPreview.tsx` — import 를 `import { useRefreshStrategyCandidatesMutation, useStrategyCandidatesQuery } from "./hooks";` 로 바꾸고, `const snapshot = query.data;` 위에 추가:

```tsx
  const refresh = useRefreshStrategyCandidatesMutation(session);
```

버튼을 교체(§이탈 노트 7 — running 중에도 활성):

```tsx
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refresh.mutate({ highPriorityOnly })}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? "요청 중" : "새로고침"}
        </Button>
```

- [ ] **Step 4: 통과 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/strategy/CandidatesPreview.test.tsx`
Expected: `Tests 8 passed`.

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/shared/api/strategy.ts frontend/src/shared/api/queryKeys.ts \
        frontend/src/features/strategy/hooks.ts \
        frontend/src/features/strategy/CandidatesPreview.tsx \
        frontend/src/features/strategy/CandidatesPreview.test.tsx
git commit -m "feat(strategy): 새로고침 → POST /candidates/refresh 디스패치 후 서버 status 폴링 (§6.2)" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `["strategy"]` 전면 invalidate ↔ 폴링 결정성 가드

**Files:**
- Modify: `frontend/src/features/strategy/CandidatesPreview.test.tsx` (invalidate 가드 케이스)
- Modify: `frontend/src/features/strategy/StrategyEditor.test.tsx` (저장→invalidate→서버 running 통합 케이스)

**Interfaces:**
- Consumes: Task 4·5 산출 전부, `useUpdateStrategyMutation`(`hooks.ts:61-71`, `["strategy"]` 전면 invalidate), `useRealtimeEvents`(`useRealtimeEvents.ts:129-131`, 동일 invalidate), `useApplyOnboardingMutation`(`onboarding/hooks.ts:69-73`, 동일)
- Produces: 스펙 §7 이 명시한 "invalidate 가 폴링을 리셋해도 결정적" 회귀 가드 2건

- [ ] **Step 1: 실패하는 테스트 작성**

(1a) `frontend/src/features/strategy/CandidatesPreview.test.tsx` — 새 describe 추가:

```tsx
describe("CandidatesPreview 폴링 결정성", () => {
  it("['strategy'] 전면 invalidate 는 1회 재조회만 하고 폴링을 만들지 않는다", async () => {
    // 전략 저장 / realtime strategy.monitor.* / 온보딩 apply 가 모두 이 전면
    // invalidate 를 쏜다(설계 §7). 폴링 근거가 서버 status 뿐이므로, 정착 상태에서
    // 리셋되어도 폴링이 켜지지 않는다.
    const fetchMock = installFetchMock([snapshot()]);
    const { queryClient } = renderPreview();
    await screen.findByText("3분 전 기준");
    const before = candidatesCalls(fetchMock).length;

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["strategy"] });
    });

    await waitFor(() => expect(candidatesCalls(fetchMock).length).toBe(before + 1));
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(before + 1);
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
  });

  it("invalidate 뒤 서버가 running 을 보고하면 그때 폴링이 켜진다", async () => {
    const fetchMock = installFetchMock([
      snapshot(),
      snapshot({ snapshot_status: "running" }),
      snapshot({ computed_at: new Date().toISOString() })
    ]);
    const { queryClient } = renderPreview();
    await screen.findByText("3분 전 기준");

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["strategy"] });
    });

    await screen.findByTestId("snapshot-progress");
    expect(await screen.findByText("방금 기준")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("snapshot-progress")).toBeNull());
    const settledCalls = candidatesCalls(fetchMock).length;
    await settle();
    expect(candidatesCalls(fetchMock).length).toBe(settledCalls);
  });
});
```

(1b) `frontend/src/features/strategy/StrategyEditor.test.tsx` — `describe("StrategyEditor")` 안, "정상 저장 시 PUT 호출 + candidates 쿼리 재요청" 다음에 추가:

```tsx
  it("저장 후 서버가 running 을 보고하면 갱신 중 표시가 뜨고 이전 스냅샷은 유지된다", async () => {
    // 전략 PUT 은 백엔드에서 스냅샷 재계산을 디스패치하고(dispatch_for_strategy_write)
    // 프론트는 ["strategy"] 를 전면 invalidate 한다. 두 경로가 만나 다음 GET 이
    // running 을 돌려주는 것이 정상 흐름이다(설계 §6.3·§7).
    let candidatesCallCount = 0;
    const candidatesOverride: RouteOverride = {
      matcher: (url) => url.startsWith("/api/v1/operator/strategy/candidates"),
      handler: () => {
        candidatesCallCount += 1;
        return jsonResponse(
          candidatesCallCount === 1
            ? baseCandidates
            : { ...baseCandidates, snapshot_status: "running" }
        );
      }
    };
    const putOverride: RouteOverride = {
      matcher: (url, init) => url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT",
      handler: () => jsonResponse(baseStrategy)
    };
    const fetchMock = buildFetchMock({ overrides: [putOverride, candidatesOverride] });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(findNumberByLabel("최소 매칭 점수").value).toBe("0.6");
    });

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(findPutCall(fetchMock)).toBeDefined());
    const progress = await screen.findByTestId("snapshot-progress");
    expect(progress).toHaveTextContent("다시 계산하고 있습니다");
    // 재계산 중에도 직전 스냅샷의 통계는 살아 있다(즉시 렌더 계약).
    expect(screen.getByText("3건")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 실패 확인**

Run:
```bash
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- \
  src/features/strategy/CandidatesPreview.test.tsx src/features/strategy/StrategyEditor.test.tsx
```
Expected: 신규 3건 중 최소 1건 FAIL 이어야 한다. 3건 모두 PASS 로 나오면 **구현이 이미 요구를 만족한다는 증거**이므로(Task 4·5 의 서버-status-only 설계가 목표였다) 테스트가 실제로 회귀를 잡는지 확인한다: `hooks.ts` 의 `refetchInterval` 을 일시적으로 `pollIntervalMs`(무조건 폴링)로 바꿔 첫 케이스가 FAIL 하는지 보고 되돌린다. 이 확인 없이 통과를 주장하지 않는다.

- [ ] **Step 3: 구현**

구현 변경 없음(Task 4·5 의 설계가 이 요구를 이미 충족한다 — 이 Task 는 그 성질을 **고정하는 회귀 가드**다). Step 2 의 뒤집기 확인 결과를 커밋 메시지에 남긴다.

- [ ] **Step 4: 통과 확인**

Run:
```bash
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- \
  src/features/strategy/CandidatesPreview.test.tsx src/features/strategy/StrategyEditor.test.tsx
```
Expected: `Test Files 2 passed` / `Tests 18 passed` (CandidatesPreview 10 + StrategyEditor 8).

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/features/strategy/CandidatesPreview.test.tsx \
        frontend/src/features/strategy/StrategyEditor.test.tsx
git commit -m "test(strategy): 전면 invalidate ↔ 폴링 결정성 회귀 가드 (§7)" \
  -m "refetchInterval 을 무조건 폴링으로 뒤집어 가드가 실제로 FAIL 하는지 확인 후 되돌림." \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 온보딩 최초 진입 — 진행 UI 대기 → 목록

**Files:**
- Modify: `frontend/src/features/onboarding/PreviewStep.tsx:26` (주석만 — 스냅샷 계약 명시)
- Modify: `frontend/src/features/onboarding/OnboardingWizard.test.tsx:138-146` (candidates 목 시퀀스화) + 신규 케이스 2건

**Interfaces:**
- Consumes: `CandidatesPreview`(Task 4·5, 직접 파일 import 유지 — barrel 이 무거운 `StrategyEditor` 를 온보딩 청크로 끌어오는 것을 막는 기존 결정), `ResultStep` 의 "공고 미리보기" 버튼
- Produces: 온보딩 최초 진입(스냅샷 부재)의 진행 UI 회귀 가드

- [ ] **Step 1: 실패하는 테스트 작성**

(1a) `frontend/src/features/onboarding/OnboardingWizard.test.tsx` — `installFetchMock` 의 candidates 분기(138-146행)를 시퀀스 가능하게 교체하고 `MockOptions` 에 필드를 추가:

```tsx
/** PR-B 이후 후보 GET 은 스냅샷 순수 읽기다 — 최초 진입은 계산 이력이 없다. */
const bootstrapCandidates = {
  operator_id: 1,
  evaluated_project_count: 0,
  returned_candidate_count: 0,
  high_priority_only: false,
  candidates: [],
  computed_at: null,
  snapshot_status: "running",
  stale: false
};

const computedCandidates = {
  ...bootstrapCandidates,
  evaluated_project_count: 250,
  returned_candidate_count: 1,
  candidates: [
    {
      project_id: 501,
      title: "부산항 준설 감리 용역",
      category: "engineering_service",
      budget_estimate: 480_000_000,
      deadline: null,
      matched_score: 0.72,
      probability_score: 0.66,
      priority_score: 0.81,
      action: "review",
      recommended_amount: 430_000_000,
      analysis_summary: "요약",
      strategy_reasons: []
    }
  ],
  computed_at: "2026-07-30T02:00:00Z",
  snapshot_status: "idle"
};
```

`MockOptions` 에 `candidatesPayload?: unknown` 을 더하고, `installFetchMock` 의 candidates 분기를

```tsx
    if (url.includes("/strategy/candidates")) {
      return jsonResponse(opts.candidatesPayload ?? bootstrapCandidates);
    }
```

로 바꾼다. `renderWizard` 는 `queryClient` 를 함께 반환하도록 `return { ...render(...), queryClient };` 로 수정한다.

케이스 2건 추가:

```tsx
  it("최초 진입(스냅샷 부재)은 진행 UI + 경과 안내로 대기한다", async () => {
    installFetchMock();
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("공사");
    const businessCard = screen.getByRole("listitem", { name: "업무 구분 후보" });
    await user.click(within(businessCard).getByRole("button", { name: "수락" }));
    await user.click(screen.getByRole("button", { name: /수락한 1건 반영/ }));
    await screen.findByText("반영된 필드 1건");
    await user.click(screen.getByRole("button", { name: /공고 미리보기/ }));

    // 계산된 적 없는 스냅샷 = 부트스트랩. 0건을 "후보 없음"으로 오도하지 않고
    // 진행 UI 로 기다린다(설계 §7, 리스크 완화 §9).
    expect(await screen.findByText("첫 계산 대기")).toBeInTheDocument();
    const progress = await screen.findByTestId("snapshot-progress");
    expect(progress).toHaveTextContent("다시 계산하고 있습니다");
    expect(progress).toHaveTextContent("초 경과");
    expect(progress).toHaveTextContent("최초 계산은 수십 초");
    expect(screen.queryByText("현재 매칭되는 후보가 없습니다.")).toBeNull();
  });

  it("계산이 끝난 스냅샷은 미리보기 단계에서 목록으로 렌더된다", async () => {
    installFetchMock({ candidatesPayload: computedCandidates });
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("공사");
    const businessCard = screen.getByRole("listitem", { name: "업무 구분 후보" });
    await user.click(within(businessCard).getByRole("button", { name: "수락" }));
    await user.click(screen.getByRole("button", { name: /수락한 1건 반영/ }));
    await screen.findByText("반영된 필드 1건");
    await user.click(screen.getByRole("button", { name: /공고 미리보기/ }));

    expect(await screen.findByText("부산항 준설 감리 용역")).toBeInTheDocument();
    expect(screen.getByText("250건")).toBeInTheDocument();
    expect(screen.queryByTestId("snapshot-progress")).toBeNull();
  });
```

(running→idle 전이 자체는 `CandidatesPreview.test.tsx` 가 짧은 주기로 이미 고정한다 — 위저드 레벨에서 기본 3s 주기를 기다리게 만들지 않는다.)

- [ ] **Step 2: 실패 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/onboarding/OnboardingWizard.test.tsx`
Expected: 신규 2건 FAIL — `Unable to find an element with the text: 첫 계산 대기` 는 목 페이로드 교체 전이라면 나오고, 교체 후에는 통과해야 한다. 기존 10건은 PASS 유지.

- [ ] **Step 3: 구현 (주석만)**

`frontend/src/features/onboarding/PreviewStep.tsx:26` 의 주석을 교체:

```tsx
        {/*
          확정 직후 기존 전략 candidates 재사용(설계 §UI 4단계). PR-B 이후 이 GET 은
          스냅샷 순수 읽기이고, 온보딩 apply 는 스냅샷 재계산을 디스패치하지 않으므로
          (services/onboarding/apply.py) **이 단계 진입의 첫 GET 이 자동 디스패치**를
          겸한다. 그래서 최초 진입은 CandidatesPreview 의 진행 UI(경과 안내)로
          대기하고, 계산이 끝나면 같은 카드가 목록으로 바뀐다(설계 §7·§9).
        */}
```

- [ ] **Step 4: 통과 확인**

Run: `npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test -- src/features/onboarding/OnboardingWizard.test.tsx`
Expected: `Tests 12 passed`.

- [ ] **Step 5: 커밋**

```bash
cd /home/deploy/project/bid-vector-preview-ui
git add frontend/src/features/onboarding/PreviewStep.tsx \
        frontend/src/features/onboarding/OnboardingWizard.test.tsx
git commit -m "feat(onboarding): 미리보기 최초 진입 진행 UI 회귀 가드 + 스냅샷 계약 주석 (§7)" \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 전체 회귀(vitest + build + sync-types + pytest sanity) + PR 본문

**Files:**
- 변경 없음(검증 전용). 예외: Step 2 에서 drift 가 나오면 `frontend/src/shared/types/openapi.d.ts` 재생성 커밋.

**Interfaces:**
- Consumes: Task 2~7 전부
- Produces: green 전체 스위트 + PR 본문 스켈레톤 + 배포 절차 근거

- [ ] **Step 1: 프론트 전체 테스트 + 빌드**

```bash
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run test
npm --prefix /home/deploy/project/bid-vector-preview-ui/frontend run build
```
Expected: 전 파일 PASS(신규 4 파일 — `shared/lib.test.ts`, `features/strategy/snapshotState.test.ts`, `features/strategy/CandidatesPreview.test.tsx` + 갱신 2 파일), `tsc --noEmit` 통과 후 user/admin 두 번들 생성. 실패 시 테스트를 느슨하게 고치지 말고 원인 Task 를 수정한다(§check 금지 조항).

- [ ] **Step 2: OpenAPI drift 확인**

```bash
/home/deploy/project/bid-vector/.venv/bin/python \
  /home/deploy/project/bid-vector-preview-ui/scripts/sync_openapi_types.py --check
```
Expected: `OpenAPI types are up to date.` (PR-C 는 백엔드 스키마를 바꾸지 않으므로 drift 0 이 정상이다.)

- [ ] **Step 3: 백엔드 pytest sanity**

```bash
cd /home/deploy/project/bid-vector-preview-ui && /home/deploy/project/bid-vector/.venv/bin/pytest -q
```
Expected: 전체 green. PR-C 는 `app/` 을 건드리지 않으므로 결과가 `origin/main` 과 동일해야 한다 — 다르면 워크트리 오염을 의심하고 `git -C /home/deploy/project/bid-vector-preview-ui diff --stat origin/main -- app/ tests/ alembic/` 이 비어 있는지 확인한다.

- [ ] **Step 4: 변경 범위 확인 (백엔드 무변경 HARD 제약)**

Run: `git -C /home/deploy/project/bid-vector-preview-ui diff --stat origin/main`
Expected: `frontend/src/**` + `docs/superpowers/plans/2026-07-30-preview-snapshot-ui.md` 만. `app/`·`alembic/`·`tests/`·`docker-compose.yml` 항목이 있으면 제약 위반이다.

- [ ] **Step 5: PR 본문 스켈레톤 (push 후 `gh pr create --title "feat(preview): 프론트 스냅샷 UX + 폴링 (PR-C)" --body ...`)**

```markdown
## 무엇
설계 `docs/superpowers/specs/2026-07-30-inline-ml-memory-design.md` §7 PR-C — PR-B(#321)가 만든 스냅샷 계약을 프론트가 소비한다. **백엔드 변경 0**(`app/`·`alembic/`·`tests/` 무수정).

1. `CandidatesPreview`: 스냅샷 즉시 렌더 + "N분 전 기준" 신선도 배지(`computed_at`) + `snapshot_status=running` 동안만 도는 terminal 게이트 폴링(ExperimentRunProgress 패턴) + 실패 표면(`role="alert"`, **직전 후보 유지**) + 우선순위 토글 유지
2. 새로고침 = `POST /candidates/refresh`(202) 디스패치 → 서버 `detail` 토스트 → 후보 쿼리 invalidate → 서버 status 가 폴링을 켠다 (구 `query.refetch()` 대체)
3. 온보딩 `PreviewStep` 최초 진입(스냅샷 부재): 진행 UI + 경과 안내로 대기 후 목록
4. 상태 해석은 순수 모듈 `features/strategy/snapshotState.ts` 로 분리, 카드는 `components/SnapshotFreshnessBadge`·`SnapshotStatusNotice`·`CandidateList` 로 분해(§4.5-4, 카드 116행)
5. 수기 타입 확장(`shared/types/strategy.ts`: `SnapshotStatus`·메타 3필드·refresh 응답), 페처 `refreshStrategyCandidates`, `queryKeys.strategy.candidatesAll`, `shared/lib.ts::formatRelativeTime`

## 왜
PR-B 는 preview 를 "스냅샷 + 온디맨드 재계산"으로 바꿨지만 현행 UI 는 상태를 폴링하지 않아 **배포 직후 첫 화면이 빈 후보**이고 최초 계산 완료를 수동 새로고침으로만 확인할 수 있었다(#321 PR 본문의 과도기 항목). 이 PR 이 그 UX 공백을 닫는다.

## 소비자 주의 4건을 UI 가 어떻게 다루는가 (백엔드 리뷰 지적 반영)
1. **`stale: true` ≠ 재계산 큐잉** — 실패 쿨다운(60s) 중에는 stale 을 보고하면서 자동 디스패치가 억제된다. 배지는 "갱신 필요"까지만 말하고 "갱신 중"은 `snapshot_status` 로만 말한다.
2. **`failed` + `stale: false`(이전 성공 뒤 실패)** — 경고를 띄우면서 직전(유효) 후보와 `computed_at` 을 그대로 보여준다. `failed` 는 폴링 terminal(쿨다운 동안 답이 같다).
3. **`computed_at === null` 은 `stale: false`** — "최신"이 아니라 부트스트랩. 신선도·렌더 게이트를 `computed_at`/`snapshot_status` 로 두고, 부트스트랩에서는 통계·목록·"후보 없음" 문구를 전부 감춘다(0건을 사실처럼 그리지 않음, §2 정직 명세).
4. **`evaluated_project_count` 는 스냅샷의 고정 분석 예산(250) 산물** — 라벨을 "분석 대상"으로 바꾸고 "요청 개수와 무관" 각주를 달았다.

## 설계 판단(리뷰 포인트)
- **공용 폴링 헬퍼를 만들지 않았다**: `refetchInterval` 자체가 공용 메커니즘이고 기존 두 소비자와 terminal 판정이 전부 달라 래퍼에 남을 로직이 0이다. 대신 도메인 판정(`snapshotState`)만 추출해 컴포넌트·훅·테스트가 공유한다.
- **`document.visibilityState` 게이트 미채택**: 전역 `refetchOnWindowFocus: false` 와 맞물려 탭 복귀 시 폴링이 고착될 수 있어 react-query 네이티브 `refetchIntervalInBackground: false` 만 사용.
- **실패 시 폴링 정지 근거는 `query.state.status === "error"`**: `fetchFailureCount` 는 fetch 시작마다 0 으로 리셋되어(query-core `fetchState()`) 연속 실패 게이트로 쓸 수 없다.
- **새로고침 버튼은 running 중에도 활성**: PR-B 의 force-reclaim(60s)이 고착 running 회수 경로이므로 UI 가 그것을 막지 않는다.
- **온보딩 선디스패치는 미구현**: spec §9 완화책이지만 백엔드 apply 경로에 디스패치가 없고 PR-C 는 백엔드를 바꾸지 않는다. PreviewStep 진입 첫 GET 의 자동 디스패치 + 진행 UI 로 대응했다(후속 백엔드 PR 후보).

## 테스트
- 신규 `features/strategy/CandidatesPreview.test.tsx` 10건: 스냅샷 즉시 렌더·"3분 전 기준" 배지·`computed_at=null` 부트스트랩·running→idle 폴링 정지·failed 경고+후보 유지·우선순위 토글·refresh POST(URL/메서드/토큰/detail 토스트)→폴링·running 중 버튼 활성·`["strategy"]` 전면 invalidate 결정성 2건
- 신규 `features/strategy/snapshotState.test.ts` 14건 / `shared/lib.test.ts` 12건(상대 시각 버킷·24h 폴백·클럭 스큐)
- 갱신 `StrategyEditor.test.tsx`(7→8: 저장→invalidate→서버 running 표시, 이전 스냅샷 유지) / `OnboardingWizard.test.tsx`(10→12: 최초 진입 진행 UI, 계산 완료 목록)
- `npm --prefix frontend run test` + `run build` green, `check:sync-types` drift 0, 백엔드 `pytest -q` green(무변경 sanity)
- e2e `login-and-strategy.spec.ts` 변경 불필요(후보 카드 문구 미검증 — 실사 확인)

## 배포 체크리스트
- [ ] **마이그레이션 없음** — DB 스키마·백엔드 코드 무변경
- [ ] **배포 = 프론트 재빌드 1건**: `docker compose run --rm frontend-build` (컨테이너가 `npm ci --legacy-peer-deps && npm run build` 로 `frontend/dist/{dashboard,admin}` 를 바인드 마운트(`.:/app`)에 다시 쓴다)
- [ ] **api 재시작 불필요** — 근거: api 는 SPA 를 **요청 시점에 파일로** 서빙한다(`app/main.py:95-114` `_spa_file_response` → `FileResponse(index_path|requested_path)`), 해시된 에셋도 `/dashboard/assets` 에 마운트된 `StaticFiles(directory=...)`(`app/main.py:135-140`)가 요청마다 디렉터리에서 해석한다. 파이썬 코드가 그대로이므로 프로세스 상태에 캐시된 것이 없다. **단 하나의 전제**: `dist/dashboard/assets` 디렉터리가 api 기동 시점에 이미 존재해야 마운트가 생성된다(현재 운영 환경은 존재 — 마운트 보유 상태). 만약 `dist` 가 없는 상태에서 api 가 떠 있었다면 그때는 `docker compose restart api` 가 필요하다.
- [ ] worker/beat/ml-worker 재시작 불필요(백엔드 무변경, compose 무변경)
- [ ] 수동 확인(spec §8): 전략 화면 진입 시 스냅샷 즉시 표시 + "N분 전 기준" 배지 → 새로고침 클릭 → "미리보기 재계산을 큐에 등록했습니다." 토스트 → 갱신 중 인디케이터 → 완료 후 "방금 기준" 전이. 온보딩 최초 진입에서 진행 UI(경과 초 증가) → 목록.
- [ ] 롤백 = 이전 커밋으로 `frontend-build` 재실행(백엔드 무영향)

## 로드맵 연결
설계 §4 3-PR 분할의 C(마지막). PR-A #318(메모리 위생) → PR-B #321(스냅샷+task 전환) → 본 PR(프론트 UX). 후속 후보: 온보딩 apply 시점 선디스패치(백엔드), 스냅샷 Redis 승격(로드맵 별도 단계).
```

- [ ] **Step 6: 최종 상태 보고**

`git -C /home/deploy/project/bid-vector-preview-ui log --oneline origin/main..` (커밋 7개 내외)과 vitest/build/pytest/sync-types 4종 요약을 사용자에게 보고하고, push/PR 생성 승인을 받는다(§0 — 원격 push 는 승인 필요).

---

## Self-Review 결과 (계획 작성 후 점검)

- **요구사항 커버리지:** 요구 1→Task 4(+3), 2→Task 5, 3→Task 7, 4→Task 6(+Task 4 의 `refetchInterval` 설계), 5→Task 2·5, 6→Task 3~7 전부(신규 3 파일 36건 + 갱신 2 파일 +3건), 7→Task 4(3개 서브컴포넌트, 카드 116행, 문구 전부 한국어 상수).
- **소비자 주의 4건 추적성:** (1) `SnapshotFreshnessBadge` 의 "갱신 필요" 문구 + `snapshotState.test.ts` "stale=true 여도 idle 이면 정착" + `CandidatesPreview.test.tsx` "stale 만으로 갱신 중을 말하지 않는다". (2) `isSnapshotSettled` 의 failed=terminal + "failed 는 경고를 띄우면서 직전 후보를 계속 보여준다". (3) `hasComputedSnapshot` 게이트 + "computed_at=null 이면 첫 계산 대기" + snapshotState 의 idle+null 미정착 케이스. (4) `ANALYZED_LABEL`/`ANALYZED_HINT` + "250건"·"고정 분석 예산" 단언.
- **하드 제약 재확인:** 백엔드 무변경은 Task 8 Step 4 의 `diff --stat` 로 강제. 계약은 실사한 실제 코드(`app/schemas/operator_strategy.py:69-95`, `app/api/operator.py:145-171`, `app/services/preview_snapshot.py:218-286,460-490`)에서 인용했고, 프론트 타입은 생성 타입(`openapi.d.ts:7079-7128`)과 필드 단위로 대조했다.
- **placeholder 스캔:** 모든 코드 스텝이 실제 코드/명령/기대 출력을 담고 "TBD" 없음. 인용한 행 번호·기존 테스트 수(StrategyEditor 7, OnboardingWizard 10)·문구("미리보기 재계산을 큐에 등록했습니다.", "공고 미리보기", "첫 계산 대기")·설정값·라이브러리 동작(query-core `fetchState`)은 실측값이다.
- **명칭 일관성:** `snapshotState.{isSnapshotSettled,snapshotPollInterval,hasComputedSnapshot,SNAPSHOT_POLL_INTERVAL_MS}`, `formatRelativeTime`, `refreshStrategyCandidates`/`StrategyCandidatesRefreshQuery`, `useRefreshStrategyCandidatesMutation`, `queryKeys.strategy.candidatesAll`, `data-testid="snapshot-progress"|"snapshot-failed"` 가 Task 3↔4↔5↔6↔7 에서 일치함을 확인.
- **TDD 정직성:** Task 6 은 구현 변경이 없는 회귀 가드이므로 "테스트가 실제로 실패를 잡는지" 뒤집기 확인 스텝을 명시했다(무의미한 green 방지). Task 2 는 유닛 테스트 대신 `tsc` 실패→통과 사이클을 쓴다.
- **리스크:** 최대 리스크는 워크트리 node_modules 심링크(Task 1 Step 3 에서 기존 스위트로 즉시 검증 + 오프라인 fallback 명시). 두 번째는 폴링 테스트의 실시간 타이머 의존(기존 `ExperimentRunProgress.test.tsx` 와 동일 전략, `POLL_MS=20` + `act` 감싼 sleep 으로 결정성 확보, fake timer 미사용).

### Critical Files for Implementation

- /home/deploy/project/bid-vector/frontend/src/features/strategy/CandidatesPreview.tsx (+ 신규 /home/deploy/project/bid-vector/frontend/src/features/strategy/CandidatesPreview.test.tsx)
- /home/deploy/project/bid-vector/frontend/src/features/strategy/snapshotState.ts (신규 — 폴링·렌더 게이트 순수 코어)
- /home/deploy/project/bid-vector/frontend/src/features/strategy/hooks.ts (폴링 게이트 + refresh mutation)
- /home/deploy/project/bid-vector/frontend/src/shared/types/strategy.ts (+ /home/deploy/project/bid-vector/frontend/src/shared/api/strategy.ts, /home/deploy/project/bid-vector/frontend/src/shared/api/queryKeys.ts)
- /home/deploy/project/bid-vector/app/services/preview_snapshot.py (읽기 전용 참조 — 서버 계약의 진실: `serve`/`_build_response`/`_snapshot_is_stale`/`_needs_recompute`)