# ML Jobs API

> 베이스 경로: `/api/v1/ml` · 인증: **불필요** (이 라우터에는 operator 토큰·DB 의존성이 없음)
> 베이스 URL 예시: `http://localhost:3000`

이 라우터의 POST 엔드포인트는 무거운 ML 작업을 **직접 실행하지 않고 Celery 큐에 적재(enqueue)만** 한다. 즉시 `202 Accepted`와 함께 `task_id`·`poll_url`을 반환하며, 실제 학습/임베딩 재계산은 워커가 비동기로 수행한다. 진행·완료·실패는 짝이 되는 GET 상태 엔드포인트(`poll_url`)를 폴링해 확인한다.

상태 계약:

- `status` — Celery 원시 상태(`raw_status`)를 안정 5단계로 정규화: `queued` / `running` / `completed` / `failed` / `cancelled`.
- 매핑: `PENDING`,`RECEIVED`→queued · `STARTED`,`RETRY`→running · `SUCCESS`→completed · `FAILURE`→failed · `REVOKED`→cancelled.
- `ready` 작업 종료 여부, `successful` 성공 여부, `error` 실패 시 메시지(그 외 null), `result` 성공이고 dict 결과일 때만 채워짐(그 외 null).
- 알 수 없는 `task_id`도 Celery는 `PENDING`으로 조회하므로 상태 조회는 `404`가 아니라 `status="queued"`를 돌려준다.

## 목차
- [POST /api/v1/ml/backfills/project-embeddings](#post-apiv1mlbackfillsproject-embeddings) — 프로젝트 임베딩 백필 적재
- [GET /api/v1/ml/backfills/project-embeddings/tasks/{task_id}](#get-apiv1mlbackfillsproject-embeddingstaskstask_id) — 백필 작업 상태 조회
- [POST /api/v1/ml/training/price-predictor](#post-apiv1mltrainingprice-predictor) — 가격 예측기 학습 적재
- [GET /api/v1/ml/training/price-predictor/tasks/{task_id}](#get-apiv1mltrainingprice-predictortaskstask_id) — 학습 작업 상태 조회
- [POST /api/v1/ml/reevaluations/decision-experiments/{experiment_run_id}](#post-apiv1mlreevaluationsdecision-experimentsexperiment_run_id) — 결정 실험 재평가 적재
- [GET /api/v1/ml/reevaluations/decision-experiments/tasks/{task_id}](#get-apiv1mlreevaluationsdecision-experimentstaskstask_id) — 재평가 작업 상태 조회

---

## POST /api/v1/ml/backfills/project-embeddings

프로젝트(공고) 임베딩을 일괄 재계산하는 백필 작업을 ML 백필 큐(`bid_vector_ml_backfill`)에 적재한다. 즉시 실행하지 않고 `202`와 함께 `task_id`·`poll_url`을 반환한다.

- 언제 쓰나: 임베딩 모델 교체·필드 보강 후 기존 프로젝트의 pgvector 임베딩을 다시 채울 때. `force=true`면 이미 임베딩이 있는 프로젝트도 강제 재계산한다. `limit`/`offset`으로 배치를 나누고 `category`/`project_status`로 대상을 좁힌다.
- 도메인: 임베딩 차원 384 고정(`Project.embedding`, `paraphrase-multilingual-MiniLM-L12-v2`). 백필은 이 차원을 유지한다.
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer (ge=1, le=1000) | 아니오 (기본 100) | 처리할 프로젝트 최대 개수 |
| query | offset | integer (ge=0) | 아니오 (기본 0) | 시작 오프셋 |
| query | category | string | 아니오 | 대상 카테고리 필터 |
| query | project_status | string | 아니오 | 대상 프로젝트 상태 필터 |
| query | force | boolean | 아니오 (기본 false) | 기존 임베딩도 강제 재계산 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/ml/backfills/project-embeddings?limit=200&offset=0&category=용역&force=true"
```

**응답 202**
```json
{
  "task_id": "3f1c2a4e-8b9d-4c1a-9e21-6d0f7a2b3c4d",
  "task_name": "jobs.rebuild_project_embeddings",
  "queue": "bid_vector_ml_backfill",
  "status": "queued",
  "detail": "Task status is available.",
  "poll_url": "/api/v1/ml/backfills/project-embeddings/tasks/3f1c2a4e-8b9d-4c1a-9e21-6d0f7a2b3c4d"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | `limit`이 1~1000 범위를 벗어나거나 `offset`이 음수 등 쿼리 제약 위반 |

```json
{
  "detail": [
    {
      "loc": ["query", "limit"],
      "msg": "Input should be less than or equal to 1000",
      "type": "less_than_equal"
    }
  ]
}
```

---

## GET /api/v1/ml/backfills/project-embeddings/tasks/{task_id}

위 백필 작업의 현재 상태를 조회한다. POST 응답의 `poll_url`로 주기적으로 폴링한다.

- 언제 쓰나: 백필 적재 후 완료 여부와 결과(`result`)를 확인할 때.
- 도메인: 알 수 없는 `task_id`도 `status="queued"`로 반환된다(404 아님) — 잘못된 ID와 시작 전 작업은 구분되지 않는다.
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | 적재 시 반환된 작업 ID |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/ml/backfills/project-embeddings/tasks/3f1c2a4e-8b9d-4c1a-9e21-6d0f7a2b3c4d"
```

**응답 200**
```json
{
  "task_id": "3f1c2a4e-8b9d-4c1a-9e21-6d0f7a2b3c4d",
  "task_name": "jobs.rebuild_project_embeddings",
  "queue": "bid_vector_ml_backfill",
  "status": "completed",
  "raw_status": "SUCCESS",
  "ready": true,
  "successful": true,
  "detail": "Task completed successfully.",
  "error": null,
  "result": {
    "processed": 200,
    "updated": 187,
    "skipped": 13
  }
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 200 | 정상. 실패한 작업은 `status="failed"`, `error`에 메시지가 담긴다 |

---

## POST /api/v1/ml/training/price-predictor

가격 예측기 학습 작업을 전용 학습 큐(`bid_vector_ml_training`)에 적재한다. 모든 필드가 기본값을 가지므로 본문 없이(`{}`)도 호출 가능하다.

- 언제 쓰나: 새 가격 예측 모델을 학습·릴리스할 때. `release_tag`로 릴리스 태그를 지정하고, `category`/`agency_name`으로 학습 데이터 범위를 좁히며, `limit`으로 표본 수를 제한한다. `create_manifest=true`면 릴리스 manifest를 생성하고, `publish_remote=true`면 원격 발행한다.
- 도메인: 학습 산출물은 manifest 서명·promotion gate(차원 호환성 검증 포함)를 거쳐 배포된다. 적재 단계에서는 서명/게이트를 수행하지 않고 작업만 발행하며, 실제 처리는 워커·릴리스 파이프라인에서 이뤄진다. 서명키 등 시크릿은 이 API로 전달하지 않는다.
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | release_tag | string \| null | 아니오 (기본 null) | 산출 모델 릴리스 태그 |
| body | category | string \| null | 아니오 (기본 null) | 학습 데이터 카테고리 범위 |
| body | agency_name | string \| null | 아니오 (기본 null) | 학습 데이터 발주기관 범위 |
| body | limit | integer (ge=1, le=5000) | 아니오 (기본 500) | 학습 표본 최대 수 |
| body | notes | string \| null | 아니오 (기본 null) | 릴리스 메모 |
| body | create_manifest | boolean | 아니오 (기본 true) | 릴리스 manifest 생성 여부 |
| body | publish_remote | boolean | 아니오 (기본 true) | 원격 발행 여부 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/ml/training/price-predictor" \
  -H "Content-Type: application/json" \
  -d '{
    "release_tag": "price-2026-05-29",
    "category": "용역",
    "limit": 1000,
    "notes": "5월 데이터 반영 재학습",
    "create_manifest": true,
    "publish_remote": false
  }'
```
```json
{
  "release_tag": "price-2026-05-29",
  "category": "용역",
  "agency_name": null,
  "limit": 1000,
  "notes": "5월 데이터 반영 재학습",
  "create_manifest": true,
  "publish_remote": false
}
```

**응답 202**
```json
{
  "task_id": "a7b2c9d4-1e3f-4a5b-8c6d-9f0a1b2c3d4e",
  "task_name": "ml.train_price_predictor",
  "queue": "bid_vector_ml_training",
  "status": "queued",
  "detail": "Task status is available.",
  "poll_url": "/api/v1/ml/training/price-predictor/tasks/a7b2c9d4-1e3f-4a5b-8c6d-9f0a1b2c3d4e"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | `limit`이 1~5000 범위를 벗어나는 등 본문 제약 위반 |

```json
{
  "detail": [
    {
      "loc": ["body", "limit"],
      "msg": "Input should be less than or equal to 5000",
      "type": "less_than_equal"
    }
  ]
}
```

---

## GET /api/v1/ml/training/price-predictor/tasks/{task_id}

가격 예측기 학습 작업의 상태를 조회한다. POST 응답의 `poll_url`로 폴링한다.

- 언제 쓰나: 학습 완료·실패 여부, 결과(`result`)·오류(`error`)를 확인할 때.
- 도메인: 학습 성공 시 `result`에 작업 산출 요약(dict)이 담길 수 있다. 알 수 없는 task_id는 `queued`로 보고된다(404 아님).
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | 적재 시 반환된 작업 ID |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/ml/training/price-predictor/tasks/a7b2c9d4-1e3f-4a5b-8c6d-9f0a1b2c3d4e"
```

**응답 200**
```json
{
  "task_id": "a7b2c9d4-1e3f-4a5b-8c6d-9f0a1b2c3d4e",
  "task_name": "ml.train_price_predictor",
  "queue": "bid_vector_ml_training",
  "status": "running",
  "raw_status": "STARTED",
  "ready": false,
  "successful": false,
  "detail": "Task is currently running.",
  "error": null,
  "result": null
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 200 | 정상. 실패한 작업은 `status="failed"`, `error`에 메시지가 담긴다 |

---

## POST /api/v1/ml/reevaluations/decision-experiments/{experiment_run_id}

특정 결정 실험 실행(`experiment_run_id`)에 대한 재평가 작업을 ML 재평가 큐(`bid_vector_ml_reevaluation`)에 적재한다.

- 언제 쓰나: 예측기·전략 변경 후 과거 결정 실험을 다시 평가해 결과를 비교할 때.
- 도메인: 적재 시점에는 `experiment_run_id` 존재 여부를 검증하지 않고 즉시 작업을 발행한다 — 존재하지 않는 ID 처리는 워커 실행 단계에서 이뤄지며 작업 실패로 귀결될 수 있다.
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 재평가할 결정 실험 실행 ID |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/ml/reevaluations/decision-experiments/42"
```

**응답 202**
```json
{
  "task_id": "c3d4e5f6-7a8b-4c9d-0e1f-2a3b4c5d6e7f",
  "task_name": "ml.reevaluate_decision_experiment",
  "queue": "bid_vector_ml_reevaluation",
  "status": "queued",
  "detail": "Task status is available.",
  "poll_url": "/api/v1/ml/reevaluations/decision-experiments/tasks/c3d4e5f6-7a8b-4c9d-0e1f-2a3b4c5d6e7f"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | `experiment_run_id`가 정수로 파싱되지 않음 (경로 타입 위반) |

```json
{
  "detail": [
    {
      "loc": ["path", "experiment_run_id"],
      "msg": "Input should be a valid integer, unable to parse string as an integer",
      "type": "int_parsing"
    }
  ]
}
```

---

## GET /api/v1/ml/reevaluations/decision-experiments/tasks/{task_id}

결정 실험 재평가 작업의 상태를 조회한다. POST 응답의 `poll_url`로 폴링한다.

- 언제 쓰나: 재평가 완료·실패 여부와 결과를 확인할 때.
- 도메인: 알 수 없는 task_id는 `queued`로 보고된다(404 아님).
- 인증: 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | 적재 시 반환된 작업 ID |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/ml/reevaluations/decision-experiments/tasks/c3d4e5f6-7a8b-4c9d-0e1f-2a3b4c5d6e7f"
```

**응답 200**
```json
{
  "task_id": "c3d4e5f6-7a8b-4c9d-0e1f-2a3b4c5d6e7f",
  "task_name": "ml.reevaluate_decision_experiment",
  "queue": "bid_vector_ml_reevaluation",
  "status": "failed",
  "raw_status": "FAILURE",
  "ready": true,
  "successful": false,
  "detail": "Task failed.",
  "error": "DecisionExperimentRun 42 not found",
  "result": null
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 200 | 정상. 실패한 작업은 `status="failed"`, `error`에 메시지가 담긴다 |
