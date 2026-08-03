# ML-UX runtime 성능 기준선과 개선 계획

- 기준일: 2026-08-03
- 상태: 로컬 구현·재검증 완료, 테스트/운영 서버 기준선 대기
- 범위: 프로젝트 유사공고, 전략 후보 preview, Celery 큐 격리, API/worker 메모리
- 원칙: 현재 모듈형 모놀리스를 유지하고 요청/작업/read-model 경계를 먼저 분리한다.

## 결론

초기 기준선의 성능 저하는 ML 연산량 자체보다 **ML 생명주기가 UX 요청과 운영 큐에
결합된 것**이 핵심이었다.

1. 초기 구현에서 프로젝트 상세 화면이 자동 호출하는 `GET /projects/{id}/similar`는 저장
   벡터가 없거나 stale이면 API 프로세스에서 모델을 로드하고 encode한 뒤 commit했다.
2. preview snapshot 계산은 비동기화됐지만 크롤링·알림·reconciler와 같은 ops 큐를
   사용한다. worker 동시성을 preview가 모두 점유하면 짧은 운영 작업도 뒤에서 기다린다.
3. 현재 운영 리포트는 일부 task의 평균 queue wait만 제공하고 p95, preview/embedding
   작업, API route latency, container RSS 추이를 함께 보존하지 않는다.

따라서 우선순위는 **인라인 similarity ML 제거 → online inference 큐 분리 → durable
operation/outbox/read-model → scan 중복 제거** 순서다. 별도 마이크로서비스 분리는 이
단계의 목표가 아니다. 2026-08-03 아키텍처 경계 리팩터링에서 사용자 surface와 admin ML
control plane, 수집 transaction과 inference 실행, bulk scan과 artifact 로딩 경계를 코드로
분리했다.

## 2026-08-03 로컬 실측

### 측정 조건

- git 기준: `08aed5750b63aade8661ba3564b4f8a315ba4deb`
- Docker Desktop의 격리 compose project, 외부 트래픽·beat·Telegram 없음
- API와 ops worker는 embedding 의존성이 포함된 이미지
- worker concurrency 2, prefetch 1
- synthetic software 공고 30건
- SentenceTransformer 파일은 임시 디스크 캐시에 선다운로드해 네트워크 다운로드 시간을 제외
- HTTP p95는 클라이언트 wall time, RSS는 cgroup v2 `memory.stat`의 `anon` 합계

빈 PostgreSQL에서는 첫 Alembic migration이 기존 `projects` 테이블을 전제로 해 API가
기동하지 못했다. 로컬 측정 DB에만 ORM schema를 생성하고 Alembic head를 stamp한 뒤
측정했다. 이 우회는 성능 결과와 별개이며 fresh-install 결함으로 후속 수정해야 한다.

| 측정 | 표본/동시성 | 결과 |
|---|---:|---:|
| `GET /health` p95 | 100 / 4 | 2.112ms |
| 저장된 preview snapshot GET p95 | 100 / 4 | 15.698ms |
| warm/stored similarity GET p95 | 100 / 4 | 15.408ms |
| 서로 다른 missing-embedding similarity GET p95 | 20 / 1 | 116.981ms |
| 디스크 캐시 후 첫 similarity 응답 | 1 / 1 | **5,930.354ms** |
| idle ops queue wait p95 | 50 probes | 25.816ms |
| cold preview 2개 동시 실행 중 ops queue wait p95 | 5~20 probes | **2,939.145~3,099.846ms** |
| API RSS: 모델 로드 전 → 후 | 각 5~10회 | **192.781 → 1,216.215MiB** |
| worker RSS: idle → cold preview 2개 완료 후 | 각 5~10회 | **228.383 → 837.902MiB** |

cold preview 두 작업은 각각 공고 30건을 평가하며 약 2.9~3.1초 걸렸다. 동일 프로세스가
warm인 후에는 같은 로컬 표본을 약 0.22초에 처리했지만, probe 최대 wait가 5.341초인
표본도 있었다. 즉 평균만 보면 사라지는 cold-start와 pool tail을 p95/p99로 계속 봐야 한다.

이 수치는 운영 처리량을 대표하지 않는다. 실제 DB 크기, 모델 캐시, CPU, 동시 작업이
다른 테스트/운영 서버에서 아래 절차를 다시 실행해야 최종 기준선이 된다.

### 2026-08-03 재측정 — stored-only GET / pgvector order fix

WP2 적용 후 `GET /projects/{id}/similar`는 missing/stale target에서 API 모델 로드 없이
`pending`/`stale`을 반환했다. 이후 stored target p95가 여전히 500ms대였고,
`EXPLAIN ANALYZE` 확인 결과 `ORDER BY embedding <=> query_vector, id`가 HNSW 인덱스
사용을 막아 seq scan + top-N sort를 수행했다. PostgreSQL pgvector 경로를
`ORDER BY embedding <=> query_vector LIMIT` 형태로 좁힌 뒤 같은 로컬 DB(79,022 embedded
projects)에서 다음 수치를 확인했다.

| 측정 | 표본/동시성 | 결과 |
|---|---:|---:|
| missing-embedding similarity GET p95 | 20 / 1 | 17.319ms |
| API 재시작 후 최초 missing similarity | 1 / 1 | 70.313ms |
| stored similarity GET p95, 단독 | 100 / 4 | 35.153ms |
| stored similarity GET p95, full idle run | 100 / 4 | 29.615ms |
| API RSS: 최초 missing similarity 전 → 후 | 각 1회 | 157.465 → 160.688MiB |

증거 파일:

- `reports/performance/runtime-first-missing-similarity-after-restart-20260803.json`
- `reports/performance/runtime-missing-similarity-20260803.json`
- `reports/performance/runtime-stored-similarity-pgvector-order-fix-20260803.json`
- `reports/performance/runtime-idle-after-pgvector-order-fix-20260803.json`

### 2026-08-03 재측정 — similarity read-model/outbox 적용

schema/outbox 변경 후 API·worker 이미지를 재빌드하고 컨테이너를 재생성했다. Alembic head
`e6a9d4c2b7f8`가 적용됐고 `project_similarity_snapshots`,
`project_similarity_edges`, `inference_outbox_events` 테이블이 현재 PostgreSQL에 생성됐다. 기존 embedded project
`80934`에 `embedding.ready` outbox row를 기록한 뒤 inference outbox processor가
`pgvector_hnsw` source edge 20개를 materialize했다. 이후 동일 GET은
`search_mode=read_model`으로 응답했다.

주의: 측정 JSON의 `git_sha`는 측정 당시 HEAD
(`f4bc633924c6468022a9f27e8f1c436dc15427fc`)를 가리킨다. 이후 outbox 복구와 UX 폴링
보강분은 같은 로컬 조건에서 회귀 테스트했으며, 서버 배포 후 동일 probe로 다시 측정한다.

| 측정 | 표본/동시성 | 결과 |
|---|---:|---:|
| `GET /health` p95 | 40 / 1 | 1.337ms |
| read-model similarity GET p95 | 40 / 1 | **6.195ms** |
| ops queue wait p95 | 20 probes | 101.566ms |
| inference queue wait p95 | 20 probes | 107.569ms |
| backfill queue wait p95 | 20 probes | 116.554ms |
| training queue wait p95 | 20 probes | 109.278ms |
| reevaluation queue wait p95 | 20 probes | 84.579ms |
| API RSS p95 | 5 samples | 161.469MiB |
| inference worker RSS p95 | 5 samples | 209.750MiB |

증거 파일:

- `reports/performance/runtime-read-model-outbox-20260803.json`

## 문제별 코드 연결

### P0 — 프로젝트 상세 GET에서 인라인 ML 실행

`ProjectDetailScreen` → `SimilarPanel` → `GET /projects/{id}/similar` 흐름이 자동 실행된다.
초기 기준선 당시 라우터는 `ProjectSimilarityService.find_similar_projects()`를 기본
`read_only=False`로 호출하고 GET 안에서 commit했다. missing/stale target이면
`NoticeClassifierService`의 SentenceTransformer를 lazy-load하고 encode했다. 2026-08-03
후속 변경으로 GET은 저장 embedding/read model만 읽고 빈 결과를 반환한다. embedding
model/status와 storage search mode는 더 이상 사용자 응답에 노출하지 않으며, 명시적
`POST /projects/{id}/similar/refresh`가 opaque domain operation을 반환한다.

영향:

- cold 모델 로드가 첫 화면 응답에 포함된다.
- API RSS가 로컬에서 약 1.0GiB 증가한 뒤 유지된다.
- GET이 조회와 상태 변경을 동시에 수행해 캐시·재시도·운영 추적이 어려워진다.
- API worker 수가 늘면 모델 메모리가 프로세스별로 중복될 수 있다.

### P0 — ops 큐의 head-of-line blocking

`jobs.recompute_preview_snapshot`과 strategy monitor는 크롤링, Telegram, 알림,
reconciler와 같은 `bid_vector_ops` 큐를 사용한다. concurrency 2를 preview 두 개가
점유한 로컬 재현에서 no-op task의 p95 wait가 약 25.8ms에서 3.10초로 증가했다.
운영 데이터가 많으면 이 지연은 preview 실행시간만큼 커질 수 있다.

### P1 — scan 단위 중복 계산 (top-N 재분석 해결, 공통 query 공유 대기)

후보별 최초 분석 결과는 ORM/전체 analysis tree 대신 typed `CandidateDecisionInputs`로
축약해 유지하며, 선택된 top-N은 classifier·predictor·similarity를 다시 실행하지 않는다.
다만 순차 저장 사이에 변하는 active-bid capacity와 auto workload는 공개된 경량
`OpportunityWorkloadContext`로 저장 직전에 다시 조회해 guardrail을 보존한다. 남은 문제는
시장 집계·calibration·category historical series 같은 run 공통 query가 후보별로 반복되는
부분이다.

### P1 — 갱신 전달과 관측의 내구성 부족 (전달 경계 해결, 서버 관측 대기)

공고 생성/수정은 canonical project facts와 `semantic_input.changed`를 같은 transaction에
기록한다. commit 후 즉시 enqueue가 실패해도 beat sweep과 stale-claim 복구가 embedding을
생성하고, 이어지는 `embedding.ready`가 similarity projection을 갱신한다. 동일 재수집은
healthy current embedding이면 no-op이고 vectorless/stale/failed event면 복구한다. 남은 문제는
analytics의 queue wait 평균이 `OperatorStrategyRun` 일부만 집계해 preview/embedding queue의
p95를 설명하지 못한다는 점이다.

### P1 — 빈 DB migration 불가

문서의 `docker compose up` 경로에서 첫 migration이 아직 생성되지 않은 `projects`에
컬럼을 추가한다. 새 개발자·CI·재해복구 rehearsal이 현재 migration만으로 기동되지 않아
개발/운영 재현성이 떨어진다.

## 목표 구조

```text
KONEPS 수집 / 전략 변경
        │
        ▼
DB transaction + outbox
        │
        ├── online inference queue ──► embedding/similarity/preview worker
        │                                  │
        │                                  ▼
        │                            versioned read models
        │                                  │
        └── ops queue ───────────────► crawl/notification/reconcile
                                           │
                                           ▼
                                   read-only FastAPI ──► React UI

사용자 명시 갱신 POST ──► 202 + opaque operation ──► domain 상태 polling
관리자 ML API ──► backfill/training/reevaluation queue ──► versioned artifact
```

## 구현 계획

### Work package 0 — 서버 기준선과 회귀 게이트

- [x] no-op Celery queue probe 추가
- [x] HTTP p50/p95/p99, queue p50/p95/p99, cgroup anon RSS를 JSON으로 저장하는 CLI 추가
- [x] 로컬 idle/cold/warm/queue-contention 기준선 기록
- [ ] 테스트/운영 서버에서 idle 기준선 수집
- [ ] 승인된 synthetic operator의 preview 부하 기준선 수집
- [ ] 변경 전 evidence JSON과 컨테이너/worker 로그 시각을 같은 측정 창으로 보존

완료 조건: 서버에서 동일 git SHA로 HTTP, 모든 queue, API/worker RSS 측정 JSON을 한 번에
재생성할 수 있다.

### Work package 1 — online inference 큐 분리

- [x] `CELERY_ML_INFERENCE_QUEUE`와 전용 `inference-worker`를 추가한다.
- [x] preview recompute와 operator strategy monitor를 inference 큐로 옮긴다.
- [x] 단일 공고 embedding refresh를 inference 큐로 옮긴다.
- [x] 사용자 요청 similarity projection을 versioned read-model 갱신 작업으로 옮긴다.
- [ ] ops 큐에는 짧은 orchestration, 크롤링, 알림, reconciler만 남긴다.
- [ ] 초기값은 concurrency 1~2, prefetch 1, child RSS recycle을 유지하고 서버 실측 후
  조정한다.

완료 조건: preview 두 개를 실행하는 동안 ops queue probe p95가 250ms 이하이며 알림/
reconciler 작업이 inference 실행시간만큼 기다리지 않는다.

### Work package 2 — similarity를 snapshot/read-model로 전환

- [x] GET `/projects/{id}/similar`는 저장된 target vector와 저장 embedding만 읽고
  commit하지 않는다.
- [x] 사용자 GET 응답에서 embedding 상태/model과 storage mode를 제거한다.
- [x] POST `/projects/{id}/similar/refresh`는 project/operator에 묶인 opaque operation을 반환한다.
- [x] inference worker가 단일 project embedding refresh를 처리한다.
- [x] pgvector 경로의 `ORDER BY`를 distance 단일 정렬로 제한해 HNSW 인덱스를 사용한다.
- [x] similarity result 자체를 versioned read-model로 저장하고 embedding-ready recompute를
  inference worker로 연결한다.
- [x] `SimilarPanel`은 ML/Celery 명칭 없이 domain operation의 terminal 상태까지 polling하며,
  최초 자동 진입이 모델 로드를 일으키지 않게 한다.

완료 조건:

- [x] 자동 UI GET에서 `SentenceTransformer.encode`에 도달하지 않는다.
- [x] stored similarity GET 서버 p95 300ms 이하
- [ ] first-seen 프로젝트 20개를 열어도 API RSS 증가 100MiB 이하
- [x] GET route에서 DB commit 또는 task enqueue가 없다.

Read-model 후속 계획:

1. [x] `project_similarity_snapshots` header와 `project_similarity_edges`를 분리한다. snapshot은
   target embedding version, query bucket, corpus embedding count/max timestamp, 0건 결과까지
   기록하고 edge는 snapshot별 rank/candidate/score만 보유한다.
2. [x] GET은 target embedding이 ready이고 read model freshness가 맞으면 edge rows만 읽는다.
   miss/stale이면 현재 pgvector HNSW 경로로 즉시 응답하되, refresh/recompute job은 GET이
   직접 enqueue하지 않고 outbox/inference worker가 담당한다.
3. [x] `embedding.ready` 이벤트가 같은 transaction의 outbox에 기록되고 inference worker가
   해당 target의 read model을 재계산한다. 후보 semantic/category 변경 경로는 기존 embedding을
   먼저 무효화해 corpus watermark가 오래된 snapshot 사용을 차단하고 비동기 backfill한다.
   closed/awarded 전용 debounce/batch는 후속 범위로 남긴다.
4. [ ] API는 read-model hit/miss, pgvector fallback latency, edge freshness age를
   runtime performance report에 남긴다.

### Work package 3 — durable outbox와 projection invalidation

- [x] 수집 project facts와 `semantic_input.changed`를 같은 transaction에 기록한다.
- [x] 수동 project 생성/의미 필드 수정도 facts·embedding invalidation·
  `semantic_input.changed`를 같은 transaction에 기록하며, 비의미 변경은 outbox no-op이다.
- [x] KONEPS source별 inline embedding/defer 분기를 제거하고 inference task로 통일한다.
- [x] 동일 재수집의 pending dedupe와 vectorless/stale/failed event 복구를 적용한다.
- [x] embedding refresh와 같은 transaction에 `embedding.ready` outbox row를 기록한다.
- [x] event version별 dedupe, bounded retry/backoff, stale running claim 회수를 적용한다.
- [x] 즉시 enqueue 실패를 보완하는 30초 기본 Celery beat sweep을 연결한다.
- [x] 수동 수정·수집 갱신·category 재분류에서 stale embedding을 먼저 제거하고 backfill한다.
- [ ] `project.ingested`, `project.closed/awarded`, `strategy.changed`를 별도 event type과
  debounce/batch로 연결한다.
- project 대량 유입은 debounce/batch로 preview projection을 갱신한다.
- task idempotency와 stale-job reconciler 범위를 embedding/preview까지 통합한다.

완료 조건: broker 일시 장애 후 재시작해도 missing embedding job이 유실되지 않고 UI GET이
동기 복구를 담당하지 않는다.

### Work package 4 — scan 실행 컨텍스트와 중복 제거

- [x] bulk opportunity scan은 missing/ready 여부와 관계없이 저장 similarity만 사용하며 inline
  encode를 실행하지 않는다.
- [x] LSTM/ensemble release artifact parsing·validation은 파일 identity별 bounded worker cache로
  재사용하고 artifact 교체와 worker fork에 안전하게 무효화한다.
- run-scoped `AnalysisContext`에 workload, 시장 집계, calibration, category historical
  series를 캐시한다.
- [x] 최초 분석 결과를 typed `CandidateDecisionInputs`로 유지하고 top-N 전체 재분석을
  없앤다. 저장 직전에는 ML 재실행 없이 `OpportunityWorkloadContext`만 갱신해 순차 capacity
  guardrail을 유지한다.
- 수동 scan에도 기본 분석 상한을 적용하고 full audit는 별도 offline task로 둔다.
- query count, analyzed candidate 수, task runtime을 같은 run id로 기록한다.

완료 조건: 후보 수 증가에 대해 공통 query가 후보별로 선형 증가하지 않고, 동일 fixture의
후보 순서·점수·결정 결과가 유지된다.

### Work package 5 — fresh-install migration과 운영 재현성

- 빈 PostgreSQL에서 `alembic upgrade head`만으로 전체 schema가 생성되게 baseline
  migration을 보완하거나 정식 bootstrap revision을 만든다.
- ORM `create_all + stamp` 우회를 runbook에서 제거한다.
- CI에 blank PostgreSQL migration smoke를 추가한다.

완료 조건: 새 volume에서 server compose가 수동 DB 조작 없이 health 상태가 된다.

## 테스트/운영 서버 측정 절차

새 probe task가 worker registry에 들어가야 하므로 배포 후 API와 대상 worker를 먼저
재시작한다. 실제 토큰이 필요한 경로는 `BID_VECTOR_PERF_TOKEN` 환경 변수로만 전달하고
명령행이나 evidence에 기록하지 않는다. similarity GET은 저장 embedding만 읽으므로
missing/stale target도 API 모델 로드나 DB write를 일으키지 않는다. stored similarity
p95 비교에는 embedding이 이미 저장된 project를 별도로 지정한다.

Idle 기준선 예시:

```bash
.venv/bin/python scripts/measure_runtime_performance.py \
  --base-url http://127.0.0.1:3000 \
  --http-path /health \
  --http-path '/api/v1/operator/strategy/candidates?limit=10' \
  --http-path '/api/v1/projects/<safe-project-id>/similar?limit=5' \
  --http-samples 100 \
  --http-concurrency 4 \
  --queue bid_vector_ops \
  --queue bid_vector_ml_inference \
  --queue bid_vector_ml_backfill \
  --queue bid_vector_ml_training \
  --queue bid_vector_ml_reevaluation \
  --container bid_vector_api \
  --container bid_vector_worker \
  --container bid_vector_inference_worker \
  --container bid_vector_ml_worker \
  --container bid_vector_training_worker \
  --environment-label test-server-idle \
  --output reports/performance/runtime-idle.json
```

승인된 synthetic operator에 preview 부하를 넣는 측정은 snapshot을 실제로 갱신하므로
명시적인 실행 창에서만 수행한다.

```bash
.venv/bin/python scripts/measure_runtime_performance.py \
  --skip-http \
  --queue bid_vector_ops \
  --queue bid_vector_ml_inference \
  --queue-samples 50 \
  --queue-timeout-seconds 180 \
  --preview-load-operator-id <approved-synthetic-operator-id> \
  --container bid_vector_worker \
  --container bid_vector_inference_worker \
  --environment-label test-server-preview-load \
  --output reports/performance/runtime-preview-load.json
```

`reports/`는 gitignore 대상이다. evidence에는 토큰, DB URL, 사업자정보, raw Telegram target을
넣지 않고 측정 환경, git SHA, 표본 수, p95/p99, RSS만 남긴다.

## 중단·롤백 기준

- 측정 probe는 외부 호출이나 DB write를 하지 않는다. 단,
  `--preview-load-operator-id`는 명시한 operator의 preview snapshot을 재계산한다.
- inference 큐 전환 후 task 유실 또는 snapshot 갱신 실패가 증가하면 라우팅만 기존 큐로
  되돌릴 수 있어야 한다. API 인라인 ML은 롤백 경로로 복원하지 않는다.
- API RSS가 1.5GiB를 넘거나 연속 5회 UX 흐름에서 단조 증가하면 배포를 중단한다.
- worker RSS recycle보다 container OOM이 먼저 발생하면 concurrency/child limit을 낮추고
  evidence를 다시 수집한다.
