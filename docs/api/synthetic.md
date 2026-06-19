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
- [GET /experiments/sample-gaps](#get-apiv1syntheticexperimentssample-gaps) — G-1 sample gap/backfill 계획 조회
- [POST /experiments/sample-gaps/candidates](#post-apiv1syntheticexperimentssample-gapscandidates) — sample gap 실행 후보 생성

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

## GET /api/v1/synthetic/experiments/sample-gaps

최근 완료된 synthetic experiment run의 `summary.sample_report.lacking_groups`를 집계해 표본 부족 영역과 read-only 후속 실행 힌트를 반환한다. 이 API는 **계획 조회 전용**이며 DB backfill, 새 experiment 생성, 외부 호출을 수행하지 않는다. 운영자는 응답의 `recommendation`을 보고 preset 재실행/윈도우 확장/limit 증대 여부를 판단하거나, 아래 `POST /experiments/sample-gaps/candidates`로 실행 후보 payload를 만들 수 있다.

- 스캔 범위: `status=completed`인 최근 run만 `max_runs`개까지 조회한다.
- 제외/경고: `summary.sample_report`가 없는 legacy run은 gap 계산에서 제외하고 top-level `warnings`에 `legacy_summary_without_sample_report`를 남긴다. canonical operator 데이터가 섞인 report는 gap에는 포함하지만 `canonical_synthetic_mixed` warning과 `rerun_synthetic_only` action을 남긴다.
- 정렬: `total_missing_settled_count`, `missing_settled_count`, `source_run_count`, dimension 우선순위(`preset`, `category`, `business_type`, `budget_band`), key 순으로 우선순위를 매긴다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | max_runs | integer | 아니오 | 스캔할 최근 완료 run 수, 1~100 (기본 20) |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/synthetic/experiments/sample-gaps?max_runs=10"
```

**응답 200**
```json
{
  "generated_at": "2026-06-18T02:15:00Z",
  "max_runs": 10,
  "scanned_completed_run_count": 2,
  "source_run_count": 2,
  "legacy_summary_run_count": 0,
  "gap_count": 1,
  "warnings": [],
  "gaps": [
    {
      "priority": 1,
      "dimension": "category",
      "key": "software",
      "settled_count": 10,
      "sample_target": 30,
      "missing_settled_count": 20,
      "total_missing_settled_count": 32,
      "source_run_count": 2,
      "related_preset_names": ["g1-software-base-12m"],
      "related_run_ids": [102, 101],
      "related_runs": [
        {
          "run_id": 102,
          "experiment_id": 12,
          "preset_name": "g1-software-base-12m",
          "status": "completed",
          "finished_at": "2026-06-18T01:30:00Z",
          "start_at": "2025-01-01T00:00:00",
          "end_at": "2025-12-31T23:59:59",
          "category": "software",
          "limit": 100,
          "scenario": "base",
          "settle_actions": false,
          "operator_slugs": ["sw-small-seoul", "sw-mid-metro", "sw-large-national"],
          "params": {
            "start_at": "2025-01-01T00:00:00",
            "end_at": "2025-12-31T23:59:59",
            "category": "software",
            "limit": 100,
            "scenario": "base",
            "settle_actions": false
          },
          "synthetic_only": true,
          "report_status": "insufficient_sample",
          "warnings": []
        }
      ],
      "recommendation": {
        "preset_name": "g1-software-base-12m",
        "params": {
          "start_at": "2025-01-01T00:00:00",
          "end_at": "2025-12-31T23:59:59",
          "category": "software",
          "limit": 100,
          "scenario": "base",
          "settle_actions": false
        },
        "actions": [
          {
            "code": "rerun_related_preset",
            "label": "Rerun related preset",
            "detail": "Repeat the related synthetic experiment preset and keep the same result window first."
          },
          {
            "code": "expand_category_window",
            "label": "Expand category window",
            "detail": "If the rerun is still thin, widen the date window for this category before changing operators."
          }
        ]
      },
      "warnings": []
    }
  ]
}
```

**응답 필드**

| 필드 | 타입 | 설명 |
|---|---|---|
| generated_at | datetime | 계획 생성 시각 |
| max_runs | integer | 실제 적용된 스캔 상한(1~100으로 보정) |
| scanned_completed_run_count | integer | 조회한 완료 run 수 |
| source_run_count | integer | `summary.sample_report`가 있어 gap 계산에 사용된 run 수 |
| legacy_summary_run_count | integer | sample_report가 없어 제외된 완료 run 수 |
| gap_count | integer | 집계된 부족 그룹 수 |
| warnings[] | object[] | top-level 경고(`code`, `message`, `run_ids`, `operator_slugs`) |
| gaps[].dimension | enum | `preset`, `category`, `business_type`, `budget_band` |
| gaps[].missing_settled_count | integer | 단일 source run에서 가장 큰 부족 표본 수 |
| gaps[].total_missing_settled_count | integer | 관련 run들의 부족 표본 합계 |
| gaps[].related_runs[] | object[] | source run context, `operator_slugs`, `synthetic_only`/`report_status`/warning 정보 |
| gaps[].recommendation.params | object | 관련 preset 재실행에 사용할 start/end/category/limit/scenario/settle_actions 힌트 |
| gaps[].recommendation.actions[] | object[] | `rerun_related_preset`, `expand_category_window`, `increase_limit`, `review_operator_mix`, `rerun_synthetic_only` 중 해당 action |

warning code:

| code | 의미 |
|---|---|
| canonical_synthetic_mixed | synthetic report에 canonical/operator 데이터가 섞임. 반복 보고용으로 사용하기 전 synthetic-only로 재실행 필요 |
| legacy_summary_without_sample_report | 완료 run에 `summary.sample_report`가 없어 gap 계산에서 제외됨 |

**에러**

| 코드 | 의미 |
|---|---|
| 422 | `max_runs` 범위(1~100) 위반 |

---

## POST /api/v1/synthetic/experiments/sample-gaps/candidates

`GET /experiments/sample-gaps`의 특정 gap과 action을 선택해 read-only 실행 후보를 만든다. 이 API는 새 experiment를 저장하거나 run을 enqueue하지 않는다. UI/운영 CLI는 응답의 `next_step`, `run_allowed`, `experiment_payload`, `execution_plan`을 보고 기존 experiment 선택, preset 저장, 신규 experiment 생성, mixed data 정리 중 다음 동작을 결정한다.

- `dimension`/`key`는 sample-gap item을 식별한다.
- `action_code`를 생략하면 서비스가 기본 action을 고른다. mixed data warning이 있으면 `rerun_synthetic_only`를 우선한다.
- `canonical_synthetic_mixed` warning이 있으면 `run_allowed=false`, `next_step=resolve_mixed_data`로 반환한다.
- 실제 backfill DB write, experiment 저장, run enqueue는 하지 않는다. `execution_plan.run_request`는 기존 `/experiments/{id}/runs` 비동기 큐잉 API의 요청 payload만 제공한다.
- `execution_plan.source_context`는 run 요청 body의 `source_sample_gap_candidate`로 전달할 수 있으며, 완료된 run summary의 `summary.source_sample_gap_candidate`에 남는다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | dimension | enum | 예 | `preset`, `category`, `business_type`, `budget_band` |
| body | key | string | 예 | sample gap key |
| body | max_runs | integer | 아니오 | gap 계획 스캔 상한, 1~100 (기본 20) |
| body | action_code | string\|null | 아니오 | recommendation action code. 생략 시 기본 action |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/synthetic/experiments/sample-gaps/candidates" \
  -H "Content-Type: application/json" \
  -d '{
    "dimension": "category",
    "key": "software",
    "max_runs": 20,
    "action_code": "rerun_related_preset"
  }'
```

**응답 200**
```json
{
  "generated_at": "2026-06-18T02:20:00Z",
  "gap": {
    "priority": 1,
    "dimension": "category",
    "key": "software",
    "settled_count": 18,
    "sample_target": 30,
    "missing_settled_count": 12,
    "total_missing_settled_count": 12,
    "source_run_count": 1,
    "related_preset_names": ["g1-software-base-12m"],
    "related_run_ids": [102],
    "related_runs": [],
    "recommendation": {
      "preset_name": "g1-software-base-12m",
      "params": { "category": "software", "limit": 200, "scenario": "base", "settle_actions": false },
      "actions": [
        {
          "code": "rerun_related_preset",
          "label": "Rerun related preset",
          "detail": "Repeat the related synthetic experiment preset and keep the same result window first."
        }
      ]
    },
    "warnings": []
  },
  "action_code": "rerun_related_preset",
  "action_label": "Rerun related preset",
  "preset_name": "g1-software-base-12m",
  "params": {
    "start_at": "2025-01-01T00:00:00Z",
    "end_at": "2025-12-31T23:59:59Z",
    "category": "software",
    "limit": 200,
    "scenario": "base",
    "settle_actions": false
  },
  "operator_slugs": ["sw-small-seoul", "sw-mid-metro", "sw-large-national"],
  "experiment_payload": {
    "name": "g1-software-base-12m",
    "description": "Sample-gap follow-up candidate for category:software. Recommended action: Rerun related preset.",
    "params": {
      "start_at": "2025-01-01T00:00:00Z",
      "end_at": "2025-12-31T23:59:59Z",
      "category": "software",
      "limit": 200,
      "scenario": "base",
      "settle_actions": false
    },
    "operator_slugs": ["sw-small-seoul", "sw-mid-metro", "sw-large-national"]
  },
  "experiment_id": 12,
  "latest_run_id": 102,
  "latest_run_status": "completed",
  "next_step": "run_existing_experiment",
  "execution_plan": {
    "mode": "run_existing_experiment",
    "approval_required": true,
    "dry_run_default": true,
    "source_context": {
      "source": "sample_gap_candidate",
      "dimension": "category",
      "key": "software",
      "action_code": "rerun_related_preset",
      "action_label": "Rerun related preset",
      "preset_name": "g1-software-base-12m",
      "related_run_ids": [102],
      "missing_settled_count": 12,
      "sample_target": 30,
      "source_run_count": 1,
      "params": {
        "start_at": "2025-01-01T00:00:00Z",
        "end_at": "2025-12-31T23:59:59Z",
        "category": "software",
        "limit": 200,
        "scenario": "base",
        "settle_actions": false
      },
      "operator_slugs": ["sw-small-seoul", "sw-mid-metro", "sw-large-national"],
      "run_allowed": true,
      "blocked_by_warnings": [],
      "warnings": []
    },
    "preset_request": null,
    "experiment_request": null,
    "run_request": {
      "method": "POST",
      "path": "/api/v1/synthetic/experiments/12/runs",
      "body": {
        "source_sample_gap_candidate": {
          "source": "sample_gap_candidate",
          "dimension": "category",
          "key": "software",
          "action_code": "rerun_related_preset",
          "preset_name": "g1-software-base-12m",
          "related_run_ids": [102]
        }
      }
    },
    "cli_command": "python scripts/run_g2_synthetic_evidence.py --dry-run --preset g1-software-base-12m --action-code rerun_related_preset",
    "write_cli_command": "python scripts/run_g2_synthetic_evidence.py --write --preset g1-software-base-12m --action-code rerun_related_preset",
    "instructions": [
      "Dry-run this plan first; --write enqueues the asynchronous evidence run.",
      "The API request only queues the run and does not run the backtest inline."
    ]
  },
  "run_allowed": true,
  "blocked_by_warnings": [],
  "warnings": [],
  "message": "Existing experiment is ready to select and run asynchronously."
}
```

**응답 필드**

| 필드 | 타입 | 설명 |
|---|---|---|
| gap | object | 선택된 `SyntheticExperimentSampleGapItem` |
| action_code/action_label | string | 적용한 recommendation action |
| params | object | 후보 실행에 사용할 `SyntheticExperimentParams` |
| operator_slugs | string[] | 후보 실행 대상 synthetic operator slug |
| experiment_payload | object | 저장 가능한 `SyntheticExperimentCreate` payload |
| experiment_id/latest_run_id | integer\|null | 기존 experiment/run이 있으면 해당 id |
| next_step | enum | `resolve_mixed_data`, `run_existing_experiment`, `save_preset`, `create_experiment` |
| execution_plan | object | 저장/큐잉을 위한 후속 API 요청 payload. 후보 API 자체는 실행하지 않음 |
| execution_plan.source_context | object | run summary에 남길 sample-gap provenance (`source_sample_gap_candidate`) |
| execution_plan.run_request | object\|null | `POST /experiments/{experiment_id}/runs` 요청 payload. blocked 후보는 null |
| execution_plan.cli_command/write_cli_command | string\|null | dry-run 기본 명령과 승인 후 write 명령 |
| run_allowed | boolean | mixed data 등 blocking warning 없이 실행 후보로 사용할 수 있는지 |
| blocked_by_warnings | string[] | 실행을 막는 warning code |
| message | string | 운영자가 다음에 해야 할 일 |

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 요청한 `dimension`/`key` gap이 최근 sample-gap 계획에 없음 |
| 422 | body 검증 실패 또는 지원하지 않는 `action_code` |

---

## CLI: scripts/run_g2_synthetic_evidence.py

sample-gap 후보를 반복 가능한 G-1/G-2 synthetic evidence run으로 연결하는 운영 CLI다. 기본은 dry-run이며 DB 저장, run enqueue, 외부 호출을 하지 않는다.

```bash
python scripts/run_g2_synthetic_evidence.py --dry-run --preset g1-software-base-12m
```

승인된 운영 실행에서만 `--write`를 사용한다. `--write`는 필요하면 experiment/preset을 저장하고 기존 Celery synthetic experiment run을 enqueue한다. 요청-응답 경로에서 heavy backtest를 직접 실행하지 않고, 큐잉된 run은 기존 `/experiments/{experiment_id}/runs/{run_id}`로 폴링한다.

```bash
python scripts/run_g2_synthetic_evidence.py --write --preset g1-software-base-12m
```

옵션:

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 기본값. 최근 completed run summary를 읽어 candidate와 execution plan만 출력 |
| `--write` | 승인 필요. DB write 후 비동기 run enqueue |
| `--preset <name\|id>` | 관련 preset 이름 또는 저장된 experiment id로 gap 선택 (`g1-software-base-12m`, `12` 등) |
| `--dimension <dimension> --key <key>` | preset 대신 특정 sample-gap을 직접 선택 |
| `--action-code <code>` | recommendation action 명시 (`rerun_related_preset`, `increase_limit` 등) |
| `--max-runs <n>` | 최근 completed run scan 상한 |

`run_allowed=false` 또는 `canonical_synthetic_mixed` warning이 있으면 `--write`도 run을 enqueue하지 않고 blocked 결과를 출력한다.

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
