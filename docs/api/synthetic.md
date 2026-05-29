# Synthetic API

> 베이스 경로: `/api/v1/synthetic` · 인증: 불필요(operator 토큰 없이 호출 가능) · 베이스 URL 예시: `http://localhost:3000`
>
> **도메인**: synthetic 운영자는 username이 `synthetic-*`인 가상 운영자 카탈로그(12개 아키타입)로, canonical `operator` 계정과 분리되어 전략 임계값을 달리한 백테스트 비교에 쓰인다. (OpenAPI 태그는 `Synthetic`/`synthetic` 양쪽으로 노출되나 동일 라우터다.)

## 목차
- [GET /operators](#get-apiv1syntheticoperators) — 시드된 synthetic 운영자 목록 조회
- [POST /operators/seed](#post-apiv1syntheticoperatorsseed) — 12개 아키타입 운영자 멱등 시드
- [POST /backtests/run-async](#post-apiv1syntheticbacktestsrun-async) — 백테스트 비동기 큐잉(Celery)
- [GET /backtests/tasks/{task_id}](#get-apiv1syntheticbackteststaskstask_id) — 비동기 백테스트 상태/결과 조회
- [POST /backtests/run](#post-apiv1syntheticbacktestsrun) — 전체 운영자 백테스트 동기 실행

---

## GET /api/v1/synthetic/operators

시드된 synthetic 운영자(`synthetic-*`) 목록과 개수를 반환한다. 백테스트 비교 화면에서 대상 운영자 풀을 표시하거나, 백테스트 실행 전 시드 여부를 확인할 때 사용한다. 가상 운영자 메타데이터만 노출하므로 인증이 필요 없으며, 시드 전이면 `operator_count: 0`에 빈 배열을 반환한다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| — | — | — | — | 파라미터 없음 |

**요청 예시**
```bash
curl http://localhost:3000/api/v1/synthetic/operators
```

**응답 200**
```json
{
  "operator_count": 2,
  "operators": [
    {
      "user_id": 101,
      "username": "synthetic-aggressive",
      "slug": "synthetic-aggressive",
      "display_name": "공격형 운영자",
      "company": "가상건설 A",
      "business_type": "토목공사업",
      "annual_revenue": 5000000000.0,
      "capacity_score": 0.82,
      "bid_now_threshold": 0.6,
      "review_threshold": 0.4
    },
    {
      "user_id": 102,
      "username": "synthetic-conservative",
      "slug": "synthetic-conservative",
      "display_name": "보수형 운영자",
      "company": "가상건설 B",
      "business_type": "건축공사업",
      "annual_revenue": 3000000000.0,
      "capacity_score": 0.65,
      "bid_now_threshold": 0.8,
      "review_threshold": 0.6
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| — | 에러 분기 없음 (항상 200) |

---

## POST /api/v1/synthetic/operators/seed

12개 아키타입 synthetic 운영자 카탈로그를 멱등(idempotent) upsert로 시드한다. `purge=true`면 기존 synthetic 행을 먼저 삭제한 뒤 다시 시드한다. 백테스트 실행 전 가상 운영자 데이터를 준비하거나 리시드할 때 사용한다. `synthetic-*` username으로만 한정되며 canonical `operator` 계정은 건드리지 않는다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | purge | bool | 아니오(기본 false) | true면 기존 synthetic 행을 먼저 삭제 후 재시드 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/synthetic/operators/seed \
  -H "Content-Type: application/json" \
  -d '{"purge": true}'
```
```json
{
  "purge": true
}
```

**응답 200**
```json
{
  "seeded_count": 12,
  "purged_count": 12,
  "operators": [
    {
      "user_id": 101,
      "username": "synthetic-aggressive",
      "slug": "synthetic-aggressive",
      "display_name": "공격형 운영자",
      "company": "가상건설 A",
      "business_type": "토목공사업",
      "annual_revenue": 5000000000.0,
      "capacity_score": 0.82,
      "bid_now_threshold": 0.6,
      "review_threshold": 0.4
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 요청 본문 검증 실패 (예: `purge`가 불리언이 아님) |

---

## POST /api/v1/synthetic/backtests/run-async

synthetic 백테스트를 Celery 워커에 큐잉하고 폴링 가능한 task id를 반환한다. 동기 경로(`/backtests/run`)가 API 요청 타임아웃을 초과할 수 있을 때(운영자 12명 × 큰 `limit`, predictor 워밍업 지연 등) 사용한다. 반환된 `poll_url`로 상태를 조회한다. synthetic 운영자가 한 명도 시드되지 않았으면 큐잉하지 않고 404로 차단하므로, 먼저 `POST /operators/seed`를 호출해야 한다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | start_at | datetime(ISO8601) | 아니오 | 백테스트 시작 시각 |
| body | end_at | datetime(ISO8601) | 아니오 | 백테스트 종료 시각 |
| body | category | string | 아니오 | 대상 공고 카테고리 |
| body | limit | int(1~1000) | 아니오(기본 100) | 백테스트 대상 공고 상한 |
| body | scenario | string | 아니오(기본 "base") | 시나리오 식별자 |
| body | slugs | string[] | 아니오 | 대상 운영자 slug 부분집합 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/synthetic/backtests/run-async \
  -H "Content-Type: application/json" \
  -d '{
    "start_at": "2025-01-01T00:00:00",
    "end_at": "2025-12-31T23:59:59",
    "limit": 200,
    "scenario": "base",
    "slugs": ["synthetic-aggressive", "synthetic-conservative"]
  }'
```
```json
{
  "start_at": "2025-01-01T00:00:00",
  "end_at": "2025-12-31T23:59:59",
  "limit": 200,
  "scenario": "base",
  "slugs": ["synthetic-aggressive", "synthetic-conservative"]
}
```

**응답 202**
```json
{
  "task_id": "3f0c2c1a-9d4e-4b2a-8b1f-7c2e9a0d5e11",
  "task_name": "synthetic.operator_backtest",
  "queue": "ml",
  "status": "queued",
  "detail": "Synthetic backtest queued.",
  "poll_url": "/api/v1/synthetic/backtests/tasks/3f0c2c1a-9d4e-4b2a-8b1f-7c2e9a0d5e11"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | synthetic 운영자 미시드 — 먼저 `POST /operators/seed` 호출 (코드상 raise, OpenAPI 자동 문서엔 미노출) |
| 422 | 요청 본문 검증 실패 (예: `limit` 범위 초과) |

404 응답 형태:
```json
{
  "detail": "No synthetic operators seeded. Seed via POST /operators/seed first."
}
```

---

## GET /api/v1/synthetic/backtests/tasks/{task_id}

큐잉된 synthetic 백테스트 태스크의 상태와(완료 시) 결과를 조회한다. `run-async`로 받은 `task_id`(또는 `poll_url`)를 주기적으로 폴링해 진행 상황을 확인하고, 완료되면 `result`에 담긴 백테스트 결과를 읽는다. `ready=true`이고 `successful=true`면 `result`가 채워지며, 실패 시 `error`에 사유가 담기고 `result`는 null이다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | `run-async`가 반환한 태스크 id |

**요청 예시**
```bash
curl http://localhost:3000/api/v1/synthetic/backtests/tasks/3f0c2c1a-9d4e-4b2a-8b1f-7c2e9a0d5e11
```

**응답 200 (완료)**
```json
{
  "task_id": "3f0c2c1a-9d4e-4b2a-8b1f-7c2e9a0d5e11",
  "task_name": "synthetic.operator_backtest",
  "queue": "ml",
  "status": "success",
  "raw_status": "SUCCESS",
  "ready": true,
  "successful": true,
  "detail": "Synthetic backtest completed.",
  "error": null,
  "result": {
    "operator_count": 2,
    "category": null,
    "start_at": "2025-01-01T00:00:00",
    "end_at": "2025-12-31T23:59:59",
    "limit": 200,
    "scenario": "base",
    "results": [
      {
        "user_id": 101,
        "username": "synthetic-aggressive",
        "slug": "synthetic-aggressive",
        "display_name": "공격형 운영자",
        "company": "가상건설 A",
        "business_type": "토목공사업",
        "annual_revenue": 5000000000.0,
        "capacity_score": 0.82,
        "bid_now_threshold": 0.6,
        "review_threshold": 0.4,
        "candidate_count": 180,
        "paper_bid_count": 120,
        "settled_count": 95,
        "would_have_won_count": 23,
        "win_rate_on_settled": 0.242,
        "bid_submission_rate": 0.667,
        "average_absolute_bid_rate_error": 0.013,
        "settlement_sample_count": 20,
        "settlement_items": [
          {
            "project_id": 884412,
            "project_title": "○○시 상수도관 정비공사",
            "category": "토목공사업",
            "paper_bid_id": 5521,
            "decision_action": "bid_now",
            "bid_amount": 482000000.0,
            "winning_amount": 479500000.0,
            "absolute_bid_rate_error": 0.0052,
            "would_have_won": false,
            "settled_at": "2025-03-14T10:00:00"
          }
        ]
      }
    ]
  }
}
```

**응답 200 (대기 중)**
```json
{
  "task_id": "3f0c2c1a-9d4e-4b2a-8b1f-7c2e9a0d5e11",
  "task_name": "synthetic.operator_backtest",
  "queue": "ml",
  "status": "pending",
  "raw_status": "PENDING",
  "ready": false,
  "successful": false,
  "detail": "Synthetic backtest is still running.",
  "error": null,
  "result": null
}
```

> 참고: `win_rate_on_settled`는 `would_have_won_price_only_count / settled_count`로 산출한 **가격 기준 추정 낙찰률**이며 실제 낙찰이 아니다. 분석 시 caveat를 함께 표기한다.

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 경로 파라미터 검증 실패 |

> 존재하지 않는 `task_id`에 대한 별도 404 분기는 코드에 없다. (확인 필요 — `get_synthetic_backtest_task_status`가 미발견 시 PENDING 유사 상태를 반환할 수 있음)

---

## POST /api/v1/synthetic/backtests/run

모든 synthetic 운영자에 대해 과거 paper-bidding 백테스트를 **동기**로 실행하고, 운영자별 성과(`results[]`)를 반환한다. 운영자 수가 적고 `limit`이 제한적일 때 적합하며, 프론트는 로딩 상태로 응답을 대기한다. 장시간 예상 시 `run-async`를 사용한다. synthetic 운영자 미시드 시 404로 차단하므로 먼저 `POST /operators/seed`를 호출한다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | start_at | datetime(ISO8601) | 아니오 | 백테스트 시작 시각 |
| body | end_at | datetime(ISO8601) | 아니오 | 백테스트 종료 시각 |
| body | category | string | 아니오 | 대상 공고 카테고리 |
| body | limit | int(1~1000) | 아니오(기본 100) | 백테스트 대상 공고 상한 |
| body | scenario | string | 아니오(기본 "base") | 시나리오 식별자 |
| body | slugs | string[] | 아니오 | 대상 운영자 slug 부분집합 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/synthetic/backtests/run \
  -H "Content-Type: application/json" \
  -d '{
    "category": "토목공사업",
    "limit": 100,
    "scenario": "base",
    "slugs": ["synthetic-aggressive"]
  }'
```
```json
{
  "category": "토목공사업",
  "limit": 100,
  "scenario": "base",
  "slugs": ["synthetic-aggressive"]
}
```

**응답 200**
```json
{
  "operator_count": 1,
  "category": "토목공사업",
  "start_at": null,
  "end_at": null,
  "limit": 100,
  "scenario": "base",
  "results": [
    {
      "user_id": 101,
      "username": "synthetic-aggressive",
      "slug": "synthetic-aggressive",
      "display_name": "공격형 운영자",
      "company": "가상건설 A",
      "business_type": "토목공사업",
      "annual_revenue": 5000000000.0,
      "capacity_score": 0.82,
      "bid_now_threshold": 0.6,
      "review_threshold": 0.4,
      "candidate_count": 90,
      "paper_bid_count": 60,
      "settled_count": 48,
      "would_have_won_count": 11,
      "win_rate_on_settled": 0.229,
      "bid_submission_rate": 0.667,
      "average_absolute_bid_rate_error": 0.014,
      "settlement_sample_count": 20,
      "settlement_items": [
        {
          "project_id": 884412,
          "project_title": "○○시 상수도관 정비공사",
          "category": "토목공사업",
          "paper_bid_id": 5521,
          "decision_action": "bid_now",
          "bid_amount": 482000000.0,
          "winning_amount": 479500000.0,
          "absolute_bid_rate_error": 0.0052,
          "would_have_won": false,
          "settled_at": "2025-03-14T10:00:00"
        }
      ]
    }
  ]
}
```

> 참고: `win_rate_on_settled`는 `would_have_won_price_only_count / settled_count`로 산출한 **가격 기준 추정 낙찰률**이며 실제 낙찰이 아니다. 분석 시 caveat를 함께 표기한다.

**에러**

| 코드 | 의미 |
|---|---|
| 404 | synthetic 운영자 미시드 — 먼저 `POST /operators/seed` 호출 (코드상 raise, OpenAPI 자동 문서엔 미노출) |
| 422 | 요청 본문 검증 실패 (예: `limit` 범위 초과) |

404 응답 형태:
```json
{
  "detail": "No synthetic operators seeded. Seed via POST /operators/seed first."
}
```
