# 설계: API 인라인 ML 전면 제거 — 스냅샷 + 온디맨드 갱신

- 작성일: 2026-07-30
- 상태: 승인됨 (운영자, 2026-07-30)
- 관련 사건: 2026-07-29 api OOM(8.4GiB)→`--reload` 좀비 리스너 17h 장애. 안전망은 #317(mem_limit 8g + --reload 제거)로 배포됨. 본 설계는 근본 원인(요청 경로 인라인 ML 스캔의 메모리 증가) 제거가 목표.

## 1. 배경과 근본 원인

`GET /api/v1/operator/strategy/candidates`(preview)와 `POST /api/v1/operator/strategy/monitor`(sync)가 API 프로세스 안에서 최대 250건(monitor는 무제한)의 공고를 인라인 ML 분석(임베딩 encode + pgvector KNN + 예측)한다. 관측된 메모리 증가 요인(심각도순):

- **S1 스레드풀×torch**: sync 핸들러가 anyio 40-스레드 풀에서 torch 추론 실행. `OMP_NUM_THREADS`/`MALLOC_ARENA_MAX` 미설정으로 glibc malloc 아레나가 스레드마다 비대해지고 OS로 반환되지 않음 — 사이클마다 단조 증가(1→3.7→5.4→8.4GiB)의 유력 주범.
- **S2 1년치 ORM 윈도우**: `PredictionFeedbackService._recent_window_cache`가 첫 후보 분석 시 365일치 TenderResult(+`joinedload` Project 전체 컬럼)를 `.all()`로 로드해 요청 종료까지 보유. 데이터 축적에 비례해 악화.
- **S4 스캔이 read-only가 아님**: `find_similar_projects`→`refresh_project_embedding`이 스캔 중 Project 행을 dirty로 만들어(`db.add`) 세션이 강참조로 고정(autoflush=False, 요청 끝 close까지).
- **S5 evaluations 낭비**: 분석 예산(≤250)만큼 ORM Project+전체 분석 dict를 정렬 시점까지 전부 보유, 실제 직렬화는 top ~10.
- **S3 중량 행**: Project 전 컬럼(embedding_payload ~8-9KB text, VECTOR(384) 등) 로드, `load_only`/`defer` 미사용, 스캔당 ~5,400행 워크.

## 2. 결정 사항 (운영자 승인)

1. **범위 = API 인라인 ML 스캔 전면 제거**: preview + sync monitor 모두. 메모리 위생 수정 동반.
2. **preview 서빙 = 스냅샷 + 온디맨드 갱신**: 마지막 계산 결과를 DB에 영속화해 화면 진입 시 즉시 표시("N분 전 기준" 배지). stale이거나 명시 갱신 시에만 재계산 task 디스패치 + 폴링.

## 3. 비목표 (Out of Scope)

- **단일 공고 분석 API**(`/operations/analyze`, `/operations/classify`, `/projects/{id}/similar`, `/predictions/*`)는 인라인 유지. 유계 1건 호출(인코딩 1-2회)로 OOM 동인이 아니며, 전환 비용 대비 이득이 없음. 단, S1의 스레드 튜닝 효과는 공유한다. API가 임베딩 모델 로드를 완전히 중단하는 것은 후속 판단(이 경로들이 남는 한 lazy-load는 유지).
- 스냅샷의 Redis 승격, 멀티테넌트 확장(로드맵 별도 단계).
- 인라인 스캔 유지 전제의 성능 최적화(예: 배치 인코딩).

## 4. PR 분할

| PR | 브랜치 | 내용 |
|---|---|---|
| A | `fix/scan-memory-hygiene` | 메모리 위생 4건, 아키텍처·산출 불변 |
| B | `feature/preview-snapshot-task` | 스냅샷 테이블+task 전환+워커 가드 (마이그레이션 1건) |
| C | `feature/preview-snapshot-ui` | 프론트 스냅샷 UX + 타입 동기화 |

## 5. PR-A: 메모리 위생 (산출 불변)

1. **evaluations 슬림화** (`opportunity_monitoring/candidates.py`): 후보 분석 직후 직렬화(`_serialize_candidate` 상당)하고 정렬키+경량 dict만 보관, ORM/analysis dict 참조 해제. 정렬·top-N 선택 로직 불변.
2. **스캔 read-only 보장**: `find_similar_projects`에 read-only 플래그를 인자로 주입해 스캔 중 `refresh_project_embedding` 스킵. 임베딩 갱신은 수집 파이프라인 소관(기존 backfill 경로 유지). 분석 완료 행은 그 자리에서 세션 위생(expunge — 구현 계획의 이탈 노트 2 참조: 청크 경계 배치보다 행 단위가 참조를 덜 늘림).
3. **피드백 윈도우 슬림화** (`prediction_feedback.py`): `joinedload(TenderResult.project)` 전 컬럼 로드를 필요 컬럼 한정 조회로 전환. 반환값·계산 로직 불변.
4. **스레드/아레나 튜닝** (docker-compose.yml env, 선언적): `MALLOC_ARENA_MAX=2`, `OMP_NUM_THREADS=1`을 api·worker·ml-worker·training-worker에 공통 선언.

검증: 특성화 테스트(동일 fixture 입력에서 후보 목록·점수·정렬 불변), 기존 pytest 전체 green.

## 6. PR-B: 스냅샷 + task 전환

### 6.1 데이터 모델

새 테이블 `operator_preview_snapshots` (Alembic 마이그레이션 1건):

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | PK | |
| operator_id | FK, index | 운영자 |
| high_priority_only | Boolean | 키 차원 |
| status | String | `idle` / `running` / `failed` |
| task_id | String(155), nullable, index | celery task id (crawl_jobs 패턴) |
| payload_json | JSON | 직렬화 후보 top-100 + 스캔 메타(분석 수·스캔 수) |
| computed_at | DateTime(UTC), nullable | 마지막 성공 계산 시각 |
| last_error | Text, nullable | 마지막 실패 사유(성공 시 null) |
| updated_at | DateTime(UTC) | |

UNIQUE(operator_id, high_priority_only). **limit은 키 차원에서 제거**: 상한 예산(현 PREVIEW_SCAN_CEILING=250)으로 1회 계산해 top-100을 저장하고, 요청 limit(≤100)은 서빙 시 슬라이스. limit별 재계산·캐시 키 폭발 제거.

### 6.2 백엔드 API

- `GET /operator/strategy/candidates` → **순수 읽기**: 스냅샷 조회 후 limit 슬라이스 반환 + 메타(`computed_at`, `status`, `stale` 여부). 스냅샷이 stale(기본 `OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS=1800`, Settings 선언)이면 응답 전에 재계산 task를 **DB 단일비행 가드**(행 status=running이면 스킵, running이 stale-task-reconciler 임계 초과면 회수 후 재디스패치) 하에 자동 디스패치하고, 기존 스냅샷을 즉시 반환. 스냅샷 부재(최초) 시에도 동일 가드 하에 디스패치하고 빈 후보 + `status=running`을 반환.
- `POST /operator/strategy/candidates/refresh` → 명시 재계산 디스패치(202, `task_id`+`poll_url`). 단일비행 가드 동일. 프론트 "새로고침" 버튼이 사용.
- 폴링은 별도 task-status 엔드포인트 없이 **GET /candidates 재조회로 수행**(`status`·`computed_at` 변화 관찰). 기존 monitor task-status 패턴과 달리 스냅샷 행이 이미 상태를 가지므로 중복 표면 불필요.
- **sync `POST /monitor` 폐쇄**: 구현을 기존 async 쌍(`/monitor/async` + `/monitor/tasks/{id}`)으로 위임 — 요청 즉시 202 + poll_url 반환. 프론트 호출부 없음 확인됨(RecentRuns는 runs 읽기만).

### 6.3 Task와 갱신 트리거

- 새 task `jobs.recompute_preview_snapshot(operator_id, high_priority_only)` — **ops 큐**(worker 컨테이너: 이미 monitor·g2 recheck로 동일 스캔을 임베딩 포함 타깃에서 실행 중). body는 자체 SessionLocal + `mark_running/completed/failed` 라이프사이클(synthetic experiment run 패턴), `celery_task_id` 멱등(crawl_jobs 패턴), stale-task-reconciler에 테이블 등록.
- 갱신 트리거(기존 preview_cache.invalidate 5경로 대체): 전략 웹 PUT · 텔레그램 set/clear/버튼 · 실험 적용 2곳 → 해당 운영자의 **기존 스냅샷 행이 있는 키만** 재계산 디스패치(행이 하나도 없으면 기본 키 `high_priority_only=false`만). 사용된 적 없는 키 변형의 불필요한 스캔을 방지.
- **`preview_cache.py` 모듈 삭제**(#315 대체), **API 웜업 제거**(#316 — `start_embedding_model_warmup` 호출 제거. API에 남는 단일 공고 ML은 lazy-load 유지).

### 6.4 워커 메모리 가드

- `worker_max_memory_per_child`(celery 네이티브, KB 단위)를 Settings로 선언(기본 3GB) — 초과 자식은 현재 task 완료 후 재생성되어 아레나 비대·잔류 증가를 주기적으로 리셋.
- compose `mem_limit`: worker 10g(현 상주 4.8GiB 관측 대비 여유), ml-worker 6g, training-worker 6g — #317과 동일한 컨테이너 스코프 격리 완성. 값은 배포 후 관측으로 조정.

## 7. PR-C: 프론트

- `CandidatesPreview`: 스냅샷 즉시 렌더 + "N분 전 기준" 배지(`computed_at`), `status=running`이면 갱신 중 인디케이터, `refetchInterval`을 terminal 상태 게이트로 폴링(ExperimentRunProgress 패턴 재사용). 새로고침 버튼 → `POST /candidates/refresh` 후 폴링.
- 온보딩 `PreviewStep`(최초 스냅샷 부재): 진행 UI(애니메이션 스피너 + 경과 안내)로 폴링 대기. 완료 시 목록 표시.
- `useUpdateStrategyMutation`의 `["strategy"]` 전면 invalidate와 폴링 쿼리 충돌 주의: 후보 폴링 쿼리 키를 `["strategy","candidates",...]` 유지하되 invalidate가 폴링을 리셋해도 동작이 결정적이도록 폴링 조건을 서버 status 기반으로 유지.
- 타입: `sync-types` 재생성 + `shared/types/strategy.ts` 수기 타입에 스냅샷 메타 추가. 페처는 `shared/api/strategy.ts`에 추가.
- 테스트: `CandidatesPreview.test.tsx` 신설(스냅샷 렌더·stale 배지·폴링 종료), 기존 StrategyEditor/Onboarding 테스트 갱신.

## 8. 검증 계획

- PR-A: 특성화 테스트로 스캔 산출 불변 증명 + pytest 전체.
- PR-B: 스냅샷 서비스·task 라이프사이클·단일비행·멱등 유닛 테스트, OpenAPI drift 확인, pytest 전체.
- PR-C: vitest + build + (수동) 전략 화면·온보딩 라이브 확인.
- 라이브(배포 후): 갱신 5회 반복 후 api RSS 평탄 확인(목표: 스캔에 의한 증가 0, 베이스라인 ≤1.5GiB), worker 자식 재생성 로그 확인, `docker stats` 추이 기록.

## 9. 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 마이그레이션 1건(§0 게이트) | 배포 시 사용자 승인, additive-only 테이블(롤백 = 테이블 drop) |
| 온보딩 최초 진입 폴링 대기(~수십 초) | 진행 UI + 온보딩 적용 시점에 선디스패치(적용→미리보기 단계 도달 전 계산 시작) |
| read-only 스캔으로 stale 임베딩 검색 | 수집 파이프라인이 주기 갱신, 허용 오차. 기존 backfill 경로 불변 |
| 워커로 이사한 스캔의 워커 OOM | §6.4 이중 가드(max_memory_per_child + mem_limit) |
| compose env·mem_limit 변경 반영 | `restart` 불가 — `up -d` 재생성 필요(#317 교훈, CLAUDE.md §0 명기됨) |
