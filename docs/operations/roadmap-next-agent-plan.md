# 다음 로드맵 병렬 작업 계획

기준 커밋: `ab5a6f5`
기준일: 2026-06-18

이 문서는 `docs/roadmap.md`의 다음 우선순위를 여러 에이전트가 동시에 처리할 수 있도록 나눈 작업 지시서입니다. 각 작업은 최신 `origin/main`에서 별도 worktree/branch를 만들고, main에는 직접 수정하지 않습니다.

## 공통 진행 규칙

1. 시작 전에 `git fetch origin`, `git status --short --branch`, `git worktree list`를 확인한다.
2. 각 에이전트는 독립 worktree와 독립 브랜치를 사용한다.
3. 서로의 worktree나 브랜치를 수정하지 않는다.
4. DB write, 실제 KONEPS 호출, 실제 Telegram 송신, 원격 push/merge는 사용자 승인 없이 하지 않는다.
5. 테스트 통과 후에도 diff를 읽어 리뷰한 뒤에만 merge 후보로 둔다.
6. 리뷰 결과와 잔여 리스크를 보고하고 사용자 승인 후 main merge를 진행한다.

권장 생성 명령:

```bash
git fetch origin
git worktree add ../bid-vector-<slug> -b <type>/<slug> origin/main
```

## 병렬 작업 요약

| 에이전트 | 브랜치 예시 | 주 목표 | 주 쓰기 범위 | 병렬성 |
|---|---|---|---|---|
| A | `docs/g2-api-contract-sync` | API 문서/OpenAPI 계약 정리 | `docs/api/`, `frontend/src/shared/types/openapi.d.ts` | 문서/타입 중심 |
| B | `feature/g2-notification-routing` | 가상 사업자별 알림 routing 격리 | `app/services/notifications/`, `app/api/operations.py`, notification tests | 알림 도메인 |
| C | `feature/g1-sample-gap-execution` | sample-gap 계획을 실행 후보/관리 화면에 연결 | `app/api/synthetic.py`, `app/services/synthetic_experiment.py`, `frontend/src/features/synthetic-backtest/` | synthetic 도메인 |
| D | `feature/g2-operations-evidence-isolation` | smoke/operations 증적의 operator context 격리 | `app/services/smoke_test.py`, `app/services/analytics_reporting.py`, 관련 tests | 운영 증적 도메인 |

## Agent A: API 계약/문서 동기화

목표:

- 최근 G-2/G-1 변경이 API 문서와 OpenAPI 타입에 반영되게 한다.
- 프론트 호출부가 `operator_id` target context를 어떤 규칙으로 전달하는지 문서화한다.

소유 범위:

- `docs/api/operator.md`
- `docs/api/analytics.md`
- `docs/api/synthetic.md`
- `docs/api/index.md`
- `frontend/src/shared/types/openapi.d.ts`는 생성 스크립트가 있는 경우에만 갱신

필수 반영:

- `/api/v1/operator/strategy/monitor*` query `operator_id`, `current_operator_id`, `current_operator_username`, 무인증 cross-operator `403`
- `/api/v1/analytics/decision-experiments*` query `operator_id`, target operator scope, strategy apply scope
- `/api/v1/synthetic/experiments/sample-gaps` 요청/응답 구조
- 관리자/사용자 surface에서 privileged operator만 cross-operator target 가능하다는 규칙

검증:

- `rg -n "sample-gaps|operator_id|current_operator_id|decision-experiments|strategy/monitor" docs/api frontend/src/shared/types/openapi.d.ts`
- OpenAPI 타입 생성 명령이 repo에 있으면 실행하고 diff 확인
- 문서 변경만 있더라도 curl 예시와 응답 예시가 실제 schema와 어긋나지 않는지 확인

리뷰 포인트:

- 실제 낙찰 확률처럼 보이는 표현 금지
- `probability_score`는 가격 적합도 추정이라고 유지
- 사용자/관리자 권한 규칙이 모호하지 않은지 확인

## Agent B: G-2 알림 라우팅 격리

목표:

- 가상 사업자별 알림 대상과 callback owner가 섞이지 않도록 routing key와 검증을 강화한다.
- operator context가 없는 알림 생성/전송 경로를 찾아 명시적으로 막거나 canonical legacy 경로로 한정한다.

소유 범위:

- `app/services/notifications/`
- `app/api/operations.py`의 Telegram callback/notification 관련 부분
- `app/models/models.py`는 필요한 경우에만 최소 변경, migration 필요 시 별도 명시
- `tests/test_notifications*.py`, `tests/test_operations*.py`, `tests/test_operator_context_api.py` 중 알림 관련 테스트

필수 작업:

- notification 생성 시 `user_id`/operator owner가 항상 명시되는지 점검
- Telegram callback payload가 다른 operator의 decision/notification을 조작하지 못하도록 테스트 추가
- synthetic operator의 알림은 실제 Telegram 송신 없이 skip 또는 dry-run evidence로 남기는 규칙 정리
- 기존 canonical operator Telegram 동작은 깨지지 않게 유지

검증:

- 관련 pytest 선별 실행
- `ENVIRONMENT=test`에서 Telegram 실제 송신이 발생하지 않는지 확인
- cross-operator callback 시 `403` 또는 `404`가 일관적인지 확인

리뷰 포인트:

- 개인정보/운영정보가 로그나 task payload에 과도하게 남지 않는지
- 실제 Telegram token/chat id를 문서나 테스트 fixture에 넣지 않았는지
- 알림 skip reason이 운영자가 이해할 수 있는지

## Agent C: G-1 sample-gap 실행 연결

목표:

- `/synthetic/experiments/sample-gaps`의 read-only 계획을 운영자가 실행 가능한 preset/run 후보로 연결한다.
- 표본 부족 영역을 수동으로 해석하지 않아도 다음 synthetic experiment를 만들 수 있게 한다.

소유 범위:

- `app/api/synthetic.py`
- `app/services/synthetic_experiment.py`
- `app/schemas/schemas.py`의 synthetic experiment 영역
- `frontend/src/features/synthetic-backtest/`
- `tests/test_synthetic_experiment.py`
- 프론트 synthetic-backtest 관련 테스트

권장 구현:

- sample-gap item의 recommendation을 기반으로 "run candidate" payload를 만드는 helper 추가
- 선택한 gap에서 experiment preset 생성 또는 기존 preset run으로 이어지는 API/화면 동선 추가
- mixed canonical/synthetic data warning이 있으면 실행 버튼보다 재실행/정리 경고를 우선 노출
- 실제 backfill DB write는 사용자 승인 없이 실행하지 않고, 계획/후보 생성까지만 구현

검증:

- `pytest tests/test_synthetic_experiment.py -q`
- `npm --prefix frontend test -- src/features/synthetic-backtest/SyntheticBacktestScreen.test.tsx`
- `npm --prefix frontend run build`

리뷰 포인트:

- canonical operator 데이터와 synthetic 데이터가 섞인 run을 reporting-ready처럼 보이지 않게 처리
- run 생성 API가 무거운 실행을 요청-응답 경로에서 직접 수행하지 않는지
- UI 문구가 sample 부족과 실제 낙찰 성과를 혼동하지 않는지

## Agent D: G-2 운영 증적 operator isolation

목표:

- smoke, operations dashboard, analytics evidence가 target operator와 source run을 명확히 남기게 한다.
- 관리자 화면에서 사업자별 smoke/backtest/통계를 나눠 볼 수 있는 기반을 강화한다.

소유 범위:

- `app/services/smoke_test.py`
- `app/services/analytics_reporting.py`
- `app/api/operator.py`의 dashboard evidence 링크는 필요한 경우만
- `tests/test_smoke_test_service.py`
- `tests/test_analytics_reporting.py`

필수 작업:

- smoke phase evidence에 operator scope가 없는 항목을 식별한다.
- strategy monitor, synthetic experiment, decision experiment evidence에 `operator_id` 또는 명시적 "canonical only" reason을 남긴다.
- operations dashboard에서 최근 monitor/smoke 실패를 operator별로 구분할 수 있는 최소 응답 필드를 검토한다.
- G-0 scheduled smoke와 G-2 per-operator smoke를 혼동하지 않도록 문서/응답 이름을 정리한다.

검증:

- `pytest tests/test_smoke_test_service.py tests/test_analytics_reporting.py -q`
- operator context 관련 기존 테스트 중 영향 범위 선별 실행

리뷰 포인트:

- G-0 canonical smoke와 G-2 per-operator evidence가 같은 지표 이름으로 섞이지 않는지
- 운영자가 실패 원인을 credential, KONEPS 응답, 후보 없음, Telegram, task/broker로 구분할 수 있는지
- 실제 외부 호출 없이 테스트 가능한 구조인지

## 통합 순서

권장 merge 순서:

1. Agent A: 문서/API 타입 sync
2. Agent B: 알림 라우팅 격리
3. Agent D: 운영 증적 격리
4. Agent C: sample-gap 실행 연결

이유:

- Agent B/D는 notification/evidence 의미가 맞아야 하고, Agent C는 synthetic API/UI 변경이 커질 수 있어 마지막 통합이 안전하다.
- `app/schemas/schemas.py`는 Agent A/C가 동시에 만질 수 있으므로, Agent A가 타입 생성만 하고 schema 원본은 건드리지 않는 것이 가장 안전하다.

## 통합 검증 세트

각 브랜치 리뷰 후 integration worktree를 만들어 병합 검증한다.

```bash
git worktree add ../bid-vector-integration-next -b integration/roadmap-next origin/main
cd ../bid-vector-integration-next
git merge --no-ff <branch-a>
git merge --no-ff <branch-b>
git merge --no-ff <branch-c>
git merge --no-ff <branch-d>
```

권장 통합 검증:

```bash
python -m py_compile app/api/operator.py app/api/analytics.py app/api/synthetic.py app/api/operations.py
pytest tests/test_operator_context_api.py tests/test_operator_reporting_context_api.py -q
pytest tests/test_synthetic_experiment.py tests/test_smoke_test_service.py tests/test_analytics_reporting.py -q
npm --prefix frontend test -- src/app/layout/OperatorSwitcher.test.tsx src/features/synthetic-backtest/SyntheticBacktestScreen.test.tsx
npm --prefix frontend run build
git diff --check origin/main..HEAD
```

## 완료 기준

- 각 작업 브랜치가 clean 상태이고 관련 테스트가 통과한다.
- 리뷰에서 blocking issue가 없거나, 같은 브랜치에서 수정 후 재검증했다.
- integration worktree에서 충돌 없이 병합되고 통합 검증 세트가 통과한다.
- 사용자에게 리뷰 결과와 잔여 리스크를 보고한 뒤 merge 승인을 받는다.
