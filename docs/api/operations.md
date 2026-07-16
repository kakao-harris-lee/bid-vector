# Operations API

> 베이스 경로: `/api/v1/operations` · 베이스 URL 예시: `http://localhost:3000`
> 인증: 이 라우터의 엔드포인트는 별도 인증 토큰이 필요하지 않습니다. 단일 운영자 모델이라 작성/조회 시 서버가 내부적으로 canonical operator를 해결합니다.
> 도메인: KONEPS 공고 수집 → 분류 → 기회 분석(opportunity) → 입찰 결정(`BidDecisionRecord`) → 텔레그램 알림/운영 모니터링.
> 시크릿/토큰 값은 모두 플레이스홀더(`<...>`)입니다.

## 목차
- [POST /crawl](#post-apiv1operationscrawl) — KONEPS 공고 동기 수집
- [POST /crawl/async](#post-apiv1operationscrawlasync) — KONEPS 수집 비동기 큐잉
- [GET /crawl/tasks/{task_id}](#get-apiv1operationscrawltaskstask_id) — 수집 태스크 상태 조회
- [POST /classify](#post-apiv1operationsclassify) — 공고 참여 적합성 분류
- [POST /opportunity-analysis](#post-apiv1operationsopportunity-analysis) — 공고 다각도 종합 분석
- [POST /allocate](#post-apiv1operationsallocate-deprecated) — 입찰 우선순위 평가 (deprecated)
- [POST /bid-decision](#post-apiv1operationsbid-decision) — 입찰 우선순위 평가
- [POST /bid-decisions](#post-apiv1operationsbid-decisions) — 입찰 결정 평가+영속화
- [GET /bid-decisions](#get-apiv1operationsbid-decisions) — 입찰 결정 목록
- [GET /projects/{project_id}/bid-decision-timeline](#get-apiv1operationsprojectsproject_idbid-decision-timeline) — 공고별 결정 타임라인
- [PATCH /bid-decisions/{decision_record_id}/status](#patch-apiv1operationsbid-decisionsdecision_record_idstatus) — 결정 상태 전환
- [GET /bid-decisions/{decision_record_id}](#get-apiv1operationsbid-decisionsdecision_record_id) — 결정 상세 조회
- [POST /notify/telegram](#post-apiv1operationsnotifytelegram) — 텔레그램 알림 전송
- [POST /telegram/callback](#post-apiv1operationstelegramcallback) — 텔레그램 인라인 콜백 처리
- [POST /telegram/webhook](#post-apiv1operationstelegramwebhook) — 텔레그램 webhook 처리
- [POST /telegram/sync](#post-apiv1operationstelegramsync) — 텔레그램 업데이트 수동 동기화
- [GET /telegram/status](#get-apiv1operationstelegramstatus) — 텔레그램 진단 상태

---

## POST /api/v1/operations/crawl

KONEPS 공고를 동기적으로 즉시 수집하고 결과를 crawl 이력(`CrawlJob`)으로 영속화합니다. 운영자가 "지금 수집"으로 소량 공고를 바로 가져올 때 사용합니다. `execution_mode`가 `mock`이면 mock 응답, `live`/`auto`는 실제 KONEPS 경로이며 `max_items`로 1회 수집량을 제한합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | source | string | 아니오 | 수집 소스. 기본 `koneps` |
| body | category | string\|null | 아니오 | 카테고리 필터 |
| body | target_date | string\|null | 아니오 | 대상 날짜(ISO) |
| body | keyword | string\|null | 아니오 | 검색 키워드 |
| body | execution_mode | enum(mock,live,auto) | 아니오 | 실행 모드. 기본 `mock` |
| body | max_items | int(1..100) | 아니오 | 1회 최대 수집 수. 기본 10 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/crawl \
  -H "Content-Type: application/json" \
  -d '{"source":"koneps","execution_mode":"mock","max_items":10,"keyword":"소프트웨어 유지보수"}'
```
```json
{
  "source": "koneps",
  "execution_mode": "mock",
  "max_items": 10,
  "keyword": "소프트웨어 유지보수"
}
```

**응답 200**
```json
{
  "job_status": "completed",
  "source": "koneps",
  "collected_count": 2,
  "items": [
    {
      "notice_number": "20250529-00123",
      "title": "OO청 정보시스템 유지보수 용역",
      "base_amount": 120000000,
      "estimated_amount": 118500000,
      "closing_at": "2025-06-12T18:00:00+09:00",
      "business_type": "용역",
      "business_type_code": "1468",
      "business_type_label": "소프트웨어유지및지원서비스",
      "region": "서울특별시",
      "license_codes": [],
      "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
      "metadata": {}
    }
  ],
  "metadata": {
    "crawl_job_id": 41
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | 요청 검증 실패 (max_items 범위 등) |
| 502 | `crawl failed: <사유>` — 수집 자체 실패 (crawl job은 failed로 마킹) |

---

## POST /api/v1/operations/crawl/async

KONEPS 수집을 비동기 태스크로 큐잉하고 폴링용 `task_id`와 `poll_url`을 반환합니다. 대량 수집처럼 즉시 응답이 곤란한 경우에 사용합니다. memory broker에서 eager 실행되면 등록 직후 이미 `completed`/`failed`로 동기화돼 돌아올 수 있습니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | (CrawlRequest) | object | 예 | `/crawl`과 동일한 본문 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/crawl/async \
  -H "Content-Type: application/json" \
  -d '{"source":"koneps","execution_mode":"live","max_items":50}'
```
```json
{
  "source": "koneps",
  "execution_mode": "live",
  "max_items": 50
}
```

**응답 200**
```json
{
  "task_id": "0d6f2c1a-8b7e-4f3a-9c2d-1a2b3c4d5e6f",
  "task_name": "collect_koneps_notices",
  "status": "queued",
  "detail": "KONEPS 수집 태스크가 큐에 등록되었습니다.",
  "poll_url": "/api/v1/operations/crawl/tasks/0d6f2c1a-8b7e-4f3a-9c2d-1a2b3c4d5e6f",
  "crawl_job_id": 42
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | 요청 검증 실패 |
| 502 | `crawl task enqueue failed: <사유>` — 태스크 등록 실패 (crawl job failed 마킹) |

---

## GET /api/v1/operations/crawl/tasks/{task_id}

비동기 수집 태스크의 현재 상태와 결과를 조회합니다. `/crawl/async`로 받은 `task_id`로 진행 상황을 폴링하고, 완료 시 `result`에 `CrawlResponse`가 채워집니다. `status`는 정규화 값, `raw_status`는 태스크 백엔드 원본 상태입니다.

> 참고: 존재하지 않는 `task_id`에 대해서도 라우터는 404를 던지지 않고 태스크 상태 헬퍼의 반환값에 의존합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | 수집 태스크 식별자 |

**요청 예시**
```bash
curl http://localhost:3000/api/v1/operations/crawl/tasks/0d6f2c1a-8b7e-4f3a-9c2d-1a2b3c4d5e6f
```

**응답 200**
```json
{
  "task_id": "0d6f2c1a-8b7e-4f3a-9c2d-1a2b3c4d5e6f",
  "task_name": "collect_koneps_notices",
  "status": "completed",
  "raw_status": "SUCCESS",
  "ready": true,
  "successful": true,
  "detail": "수집이 완료되었습니다.",
  "crawl_job_id": 42,
  "error": null,
  "result": {
    "job_status": "completed",
    "source": "koneps",
    "collected_count": 37,
    "items": [],
    "metadata": {
      "crawl_job_id": 42
    }
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 200 | 알 수 없는 task_id에도 상태 객체로 응답 (별도 404 분기 없음) |

---

## POST /api/v1/operations/classify

한 공고(`project_id`)를 운영자 회사 프로필 기준으로 참여 적합성 분류합니다. 입찰할 만한지(`matched`)와 매칭 점수(`score`), 근거(`reasons`)를 반환합니다. `user_id`를 지정하면 해당 `CompanyProfile`로, 생략하면 단일 운영자 프로필로 분류합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 분류 대상 공고 ID |
| body | user_id | int\|null | 아니오 | 특정 프로필 지정(생략 시 운영자 프로필) |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/classify \
  -H "Content-Type: application/json" \
  -d '{"project_id":1024}'
```

**응답 200**
```json
{
  "matched": true,
  "score": 0.78,
  "reasons": [
    "업종 코드가 회사 프로필과 일치",
    "지역 조건 충족"
  ],
  "criteria": {
    "business_type": "pass",
    "region": "pass"
  },
  "score_breakdown": {
    "business_type_signal": 0.9,
    "region_signal": 0.6
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `Project not found` — 해당 공고 없음 |
| 422 | 요청 검증 실패 |

---

## POST /api/v1/operations/opportunity-analysis

한 공고에 대해 다각도 종합 분석을 수행해 권장 입찰 액션을 반환합니다. 분류·가격 예측·시장 인사이트·유사 공고(pgvector)·최종 결정을 한 응답에 묶어줍니다. `price_prediction`에는 카테고리/공고별 낙찰하한 가드레일(`guardrail_applied`/`floor_*`/`safe_floor_*`)이 반영됩니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 분석 대상 공고 ID |
| body | agency_name | string\|null | 아니오 | 발주 기관명 힌트 |
| body | legal_floor_bid_rate | number\|null | 아니오 | 메일/공고 분석에서 확인한 법정 낙찰하한율. `0.87995` 또는 `87.995` 모두 허용 |
| body | current_active_bids | int\|null | 아니오 | 현재 진행 중 입찰 수 |
| body | max_active_bids | int(>=1) | 아니오 | 동시 최대 입찰 수. 기본 3 |
| body | current_workload_score | number\|null | 아니오 | 현재 작업량 점수 |
| body | same_category_only | bool | 아니오 | 동일 카테고리만 유사 검색. 기본 true |
| body | similar_limit | int(1..10) | 아니오 | 유사 공고 수. 기본 3 |
| body | min_similarity | number(0..1) | 아니오 | 최소 유사도. 기본 0.15 |
| body | user_historical_data | object\|null | 아니오 | 사용자 과거 데이터 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/opportunity-analysis \
  -H "Content-Type: application/json" \
  -d '{"project_id":1024,"legal_floor_bid_rate":87.995,"max_active_bids":3,"same_category_only":true,"similar_limit":3,"min_similarity":0.2}'
```

**응답 200** (주요 필드 발췌)
```json
{
  "project_id": 1024,
  "project_title": "OO청 정보시스템 유지보수 용역",
  "operator_id": 1,
  "matched": true,
  "matched_score": 0.78,
  "probability_score": 0.61,
  "recommended_amount": 112000000,
  "deadline_hours_remaining": 320,
  "current_active_bids": 1,
  "max_active_bids": 3,
  "current_workload_score": 0.33,
  "workload_source": "auto",
  "strategy_adjustments": {},
  "analysis_summary": "참여 적합성과 낙찰 가능성이 모두 양호한 건입니다.",
  "strengths": ["업종 적합", "경쟁 강도 낮음"],
  "risk_flags": ["마감까지 여유 적음"],
  "market_insights": {
    "average_bid": 115000000,
    "median_bid": 114500000,
    "std_dev": 3200000,
    "min_bid": 108000000,
    "max_bid": 121000000,
    "competitiveness_score": 0.42
  },
  "classification": {
    "matched": true,
    "score": 0.78,
    "reasons": ["업종 코드 일치"],
    "criteria": {},
    "score_breakdown": {}
  },
  "price_prediction": {
    "predicted_price": 112000000,
    "price_range_min": 109000000,
    "price_range_max": 116000000,
    "confidence_score": 0.7,
    "model_version": "2025.05.0",
    "predictor_name": "historical_statistical",
    "predictor_family": "statistical",
    "pricing_mode": "historical_blend",
    "predicted_bid_rate": 0.933,
    "guardrail_applied": false,
    "bid_rate_candidates": [
      {"label": "conservative", "bid_rate": 0.95, "predicted_price": 114000000, "confidence_weight": 0.3},
      {"label": "base", "bid_rate": 0.933, "predicted_price": 112000000, "confidence_weight": 0.5},
      {"label": "aggressive", "bid_rate": 0.91, "predicted_price": 109200000, "confidence_weight": 0.2}
    ],
    "explanation": "최근 동일 업종 낙찰 패턴 기반 추정"
  },
  "similar_projects": {
    "target_project_id": 1024,
    "target_project_title": "OO청 정보시스템 유지보수 용역",
    "target_embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
    "search_mode": "postgres_vector",
    "same_category_only": true,
    "min_similarity": 0.2,
    "result_count": 1,
    "results": [
      {
        "project_id": 998,
        "title": "XX시 정보화 유지보수",
        "category": "용역",
        "status": "collected",
        "budget_estimate": 119000000,
        "deadline": "2025-05-20T18:00:00+09:00",
        "created_at": "2025-05-01T09:00:00+09:00",
        "similarity_score": 0.83,
        "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2"
      }
    ]
  },
  "decision": {
    "project_id": 1024,
    "pursue_bid": true,
    "action": "bid_now",
    "priority_score": 0.66,
    "recommended_amount": 112000000,
    "probability_score": 0.61,
    "urgency_score": 0.7,
    "competitiveness_score": 0.58,
    "budget_capture_score": 0.5,
    "expected_margin_score": 0.5,
    "execution_complexity_score": 0.35,
    "workload_source": "auto",
    "score_breakdown": {
      "probability_signal": 0.61,
      "matched_signal": 0.78,
      "urgency_signal": 0.7,
      "competitiveness_signal": 0.58,
      "opportunity_score": 0.64,
      "total_penalty": 0.05
    },
    "reasoning": "적합성·낙찰 가능성·마감 임박을 종합해 추진 권장"
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `Project not found` |
| 422 | 요청 검증 실패 |

---

## POST /api/v1/operations/allocate *(deprecated)*

> **deprecated** — `/bid-decision`과 동일한 핸들러에 매핑된 레거시 경로입니다. 신규 호출은 [`/bid-decision`](#post-apiv1operationsbid-decision)을 사용하세요.

입찰을 지금 추진할지 우선순위 평가합니다(영속화 없음). 입력 신호(확률/매칭/긴급도/경쟁도/예산포착/마진/실행복잡도 + 현재 작업량)를 결합해 `pursue_bid`/`action`/`priority_score`를 산출합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 평가 대상 공고 ID |
| body | recommended_amount | number | 예 | 권장 투찰 금액(KRW) |
| body | probability_score | number | 예 | 낙찰 가능성 점수 |
| body | matched_score | number(0..1) | 아니오 | 적합성 점수. 기본 0.0 |
| body | deadline_hours_remaining | int\|null | 아니오 | 마감까지 남은 시간 |
| body | current_active_bids | int(>=0) | 아니오 | 현재 진행 입찰 수. 기본 0 |
| body | max_active_bids | int(>=1) | 아니오 | 동시 최대 입찰. 기본 3 |
| body | current_workload_score | number(0..1) | 아니오 | 작업량 점수. 기본 0.0 |
| body | budget_estimate | number\|null | 아니오 | 예산 추정액 |
| body | competitiveness_score | number(0..1) | 아니오 | 경쟁도. 기본 0.5 |
| body | expected_margin_score | number\|null | 아니오 | 기대 마진 점수 |
| body | execution_complexity_score | number\|null | 아니오 | 실행 복잡도 점수 |
| body | workload_source | enum(provided,auto) | 아니오 | 작업량 출처. 기본 provided |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/allocate \
  -H "Content-Type: application/json" \
  -d '{"project_id":1024,"recommended_amount":112000000,"probability_score":0.61,"matched_score":0.78,"deadline_hours_remaining":320,"current_active_bids":1,"max_active_bids":3}'
```

**응답 200**
```json
{
  "project_id": 1024,
  "pursue_bid": true,
  "action": "bid_now",
  "priority_score": 0.66,
  "recommended_amount": 112000000,
  "probability_score": 0.61,
  "urgency_score": 0.7,
  "competitiveness_score": 0.58,
  "budget_capture_score": 0.5,
  "expected_margin_score": 0.5,
  "execution_complexity_score": 0.35,
  "workload_source": "provided",
  "score_breakdown": {
    "probability_signal": 0.61,
    "matched_signal": 0.78,
    "urgency_signal": 0.7,
    "competitiveness_signal": 0.58,
    "opportunity_score": 0.64,
    "total_penalty": 0.05
  },
  "reasoning": "적합성과 낙찰 가능성이 높아 추진 권장"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | 요청 검증 실패 |

---

## POST /api/v1/operations/bid-decision

입찰 추진 여부를 우선순위 평가하는 정식 경로입니다. 평가만 하고 결과를 저장하지 않습니다(저장은 [`/bid-decisions`](#post-apiv1operationsbid-decisions)). `/allocate`와 동일한 결정 엔진을 사용합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | (BidDecisionRequest) | object | 예 | `/allocate`와 동일한 본문 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/bid-decision \
  -H "Content-Type: application/json" \
  -d '{"project_id":1024,"recommended_amount":112000000,"probability_score":0.61,"matched_score":0.78}'
```

**응답 200**
```json
{
  "project_id": 1024,
  "pursue_bid": true,
  "action": "bid_now",
  "priority_score": 0.66,
  "recommended_amount": 112000000,
  "probability_score": 0.61,
  "urgency_score": 0.7,
  "competitiveness_score": 0.58,
  "budget_capture_score": 0.5,
  "expected_margin_score": 0.5,
  "execution_complexity_score": 0.35,
  "workload_source": "provided",
  "score_breakdown": {
    "probability_signal": 0.61,
    "matched_signal": 0.78,
    "opportunity_score": 0.64,
    "total_penalty": 0.05
  },
  "reasoning": "적합성과 낙찰 가능성이 높아 추진 권장"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | 요청 검증 실패 |

---

## POST /api/v1/operations/bid-decisions

입찰 결정을 평가한 뒤 `BidDecisionRecord`로 영속화하고 저장된 레코드를 반환합니다. 운영자가 결정을 확정해 기록으로 남길 때 사용하며, 저장과 함께 운영자 알림도 생성합니다. 감사 가능하도록 `reasoning`에 판단 근거가 남습니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | (BidDecisionRequest 전체) | object | 예 | `/allocate`와 동일 필드 |
| body | decision_status | string\|null | 아니오 | 초기 상태(planned/reviewing/submitted/skipped) |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/bid-decisions \
  -H "Content-Type: application/json" \
  -d '{"project_id":1024,"recommended_amount":112000000,"probability_score":0.61,"matched_score":0.78,"decision_status":"planned"}'
```

**응답 200**
```json
{
  "id": 57,
  "project_id": 1024,
  "operator_id": 1,
  "pursue_bid": true,
  "action": "bid_now",
  "decision_status": "planned",
  "initial_action": "bid_now",
  "initial_decision_status": "planned",
  "first_decided_at": "2025-05-29T10:15:00+00:00",
  "priority_score": 0.66,
  "urgency_score": 0.7,
  "competitiveness_score": 0.58,
  "budget_capture_score": 0.5,
  "expected_margin_score": 0.5,
  "execution_complexity_score": 0.35,
  "recommended_amount": 112000000,
  "probability_score": 0.61,
  "matched_score": 0.78,
  "deadline_hours_remaining": 320,
  "current_active_bids": 1,
  "max_active_bids": 3,
  "current_workload_score": 0.33,
  "workload_source": "provided",
  "score_breakdown": {
    "probability_signal": 0.61,
    "matched_signal": 0.78,
    "opportunity_score": 0.64,
    "total_penalty": 0.05
  },
  "reasoning": "적합성과 낙찰 가능성이 높아 추진 권장",
  "created_at": "2025-05-29T10:15:00+00:00",
  "updated_at": "2025-05-29T10:15:00+00:00"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `Project not found` |
| 422 | 요청 검증 실패 |

---

## GET /api/v1/operations/bid-decisions

저장된 입찰 결정 레코드 목록을 반환합니다. decisions 화면에서 운영자의 결정 이력을 나열할 때 사용하며, `updated_at` 내림차순(동일 시 id 내림차순)으로 정렬합니다. 항상 canonical operator의 레코드만 조회합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | decision_status | string\|null | 아니오 | 상태 필터 |
| query | project_id | int\|null | 아니오 | 공고 필터 |
| query | limit | int(1..200) | 아니오 | 최대 개수. 기본 50 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operations/bid-decisions?decision_status=planned&limit=20"
```

**응답 200**
```json
[
  {
    "id": 57,
    "project_id": 1024,
    "operator_id": 1,
    "pursue_bid": true,
    "action": "bid_now",
    "decision_status": "planned",
    "initial_action": "bid_now",
    "initial_decision_status": "planned",
    "first_decided_at": "2025-05-29T10:15:00+00:00",
    "priority_score": 0.66,
    "urgency_score": 0.7,
    "competitiveness_score": 0.58,
    "budget_capture_score": 0.5,
    "expected_margin_score": 0.5,
    "execution_complexity_score": 0.35,
    "recommended_amount": 112000000,
    "probability_score": 0.61,
    "matched_score": 0.78,
    "deadline_hours_remaining": 320,
    "current_active_bids": 1,
    "max_active_bids": 3,
    "current_workload_score": 0.33,
    "workload_source": "provided",
    "score_breakdown": {},
    "reasoning": "적합성과 낙찰 가능성이 높아 추진 권장",
    "created_at": "2025-05-29T10:15:00+00:00",
    "updated_at": "2025-05-29T10:15:00+00:00"
  }
]
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | limit 범위 위반 등 쿼리 검증 실패 |

---

## GET /api/v1/operations/projects/{project_id}/bid-decision-timeline

한 공고에 대한 결정 이력 타임라인을 반환합니다. 공고 상세 화면에서 그 공고에 쌓인 결정들을 시계열로 볼 때 사용하며, 공고 스냅샷(`project`)과 최신 결정 id를 함께 제공합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | project_id | int | 예 | 공고 ID |
| query | limit | int(1..100) | 아니오 | 타임라인 개수. 기본 20 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operations/projects/1024/bid-decision-timeline?limit=20"
```

**응답 200**
```json
{
  "operator_id": 1,
  "project": {
    "id": 1024,
    "title": "OO청 정보시스템 유지보수 용역",
    "category": "용역",
    "status": "collected",
    "budget_estimate": 120000000,
    "deadline": "2025-06-12T18:00:00+09:00",
    "notice_number": "20250529-00123",
    "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
    "issuing_agency": "OO청",
    "demand_agency": "OO청 정보화담당관"
  },
  "result_count": 1,
  "limit_applied": 20,
  "latest_decision_record_id": 57,
  "timeline": [
    {
      "id": 57,
      "project_id": 1024,
      "operator_id": 1,
      "pursue_bid": true,
      "action": "bid_now",
      "decision_status": "planned",
      "initial_action": "bid_now",
      "initial_decision_status": "planned",
      "first_decided_at": "2025-05-29T10:15:00+00:00",
      "priority_score": 0.66,
      "recommended_amount": 112000000,
      "probability_score": 0.61,
      "matched_score": 0.78,
      "current_active_bids": 1,
      "max_active_bids": 3,
      "current_workload_score": 0.33,
      "reasoning": "추진 권장",
      "created_at": "2025-05-29T10:15:00+00:00",
      "updated_at": "2025-05-29T10:15:00+00:00"
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `Project not found` |
| 422 | limit 범위 위반 |

---

## PATCH /api/v1/operations/bid-decisions/{decision_record_id}/status

저장된 입찰 결정 레코드의 상태만 전환합니다(planned/reviewing/submitted/skipped). decisions 화면에서 전체 본문 재전송 없이 상태 플래그만 빠르게 바꾸기 위한 경량 엔드포인트입니다. 현재 상태와 같은 값이면 변경 없이 그대로 반환합니다(no-op).

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | decision_record_id | int | 예 | 결정 레코드 ID |
| body | decision_status | enum(planned,reviewing,submitted,skipped) | 예 | 전환할 상태 |

**요청 예시**
```bash
curl -X PATCH http://localhost:3000/api/v1/operations/bid-decisions/57/status \
  -H "Content-Type: application/json" \
  -d '{"decision_status":"submitted"}'
```

**응답 200**
```json
{
  "id": 57,
  "project_id": 1024,
  "operator_id": 1,
  "pursue_bid": true,
  "action": "bid_now",
  "decision_status": "submitted",
  "initial_action": "bid_now",
  "initial_decision_status": "planned",
  "first_decided_at": "2025-05-29T10:15:00+00:00",
  "priority_score": 0.66,
  "recommended_amount": 112000000,
  "probability_score": 0.61,
  "matched_score": 0.78,
  "current_active_bids": 1,
  "max_active_bids": 3,
  "current_workload_score": 0.33,
  "workload_source": "provided",
  "score_breakdown": {},
  "reasoning": "추진 권장",
  "created_at": "2025-05-29T10:15:00+00:00",
  "updated_at": "2025-05-29T11:02:00+00:00"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `Bid decision record not found` — 레코드 없음 또는 다른 운영자 소유 |
| 422 | 허용되지 않은 status 값 |

---

## GET /api/v1/operations/bid-decisions/{decision_record_id}

입찰 결정 레코드 하나를 공고 맥락 + 동일 공고 이력과 함께 상세 조회합니다. decisions 상세 화면에서 결정 본문(`record`), 공고 정보(`project`), 같은 공고의 다른 결정 타임라인을 한 번에 볼 때 사용합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | decision_record_id | int | 예 | 결정 레코드 ID |
| query | timeline_limit | int(1..100) | 아니오 | 동일 공고 이력 개수. 기본 10 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operations/bid-decisions/57?timeline_limit=10"
```

**응답 200**
```json
{
  "record": {
    "id": 57,
    "project_id": 1024,
    "operator_id": 1,
    "pursue_bid": true,
    "action": "bid_now",
    "decision_status": "submitted",
    "priority_score": 0.66,
    "recommended_amount": 112000000,
    "probability_score": 0.61,
    "matched_score": 0.78,
    "current_active_bids": 1,
    "max_active_bids": 3,
    "current_workload_score": 0.33,
    "reasoning": "추진 권장",
    "created_at": "2025-05-29T10:15:00+00:00",
    "updated_at": "2025-05-29T11:02:00+00:00"
  },
  "project": {
    "id": 1024,
    "title": "OO청 정보시스템 유지보수 용역",
    "category": "용역",
    "status": "collected",
    "budget_estimate": 120000000,
    "deadline": "2025-06-12T18:00:00+09:00",
    "notice_number": "20250529-00123",
    "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
    "issuing_agency": "OO청",
    "demand_agency": "OO청 정보화담당관"
  },
  "timeline_count": 1,
  "timeline_limit_applied": 10,
  "timeline": []
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 결정 레코드 없음 (서비스 ValueError → 404) |
| 422 | 쿼리 검증 실패 |

---

## POST /api/v1/operations/notify/telegram

텔레그램 알림 메시지를 구성하고 best-effort로 전송합니다. 운영자에게 임의 알림을 보내거나 송신 경로를 점검할 때 사용합니다. 텔레그램이 구성돼 있지 않으면 스켈레톤 메시지만 만들고 전송은 생략하며, `ENVIRONMENT=test`에서는 실제 송신이 자동 스킵됩니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | title | string | 예 | 알림 제목 |
| body | message | string | 예 | 본문 |
| body | url | string\|null | 아니오 | 첨부 링크 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/notify/telegram \
  -H "Content-Type: application/json" \
  -d '{"title":"신규 추진 후보","message":"OO청 유지보수 용역 추진 권장","url":"https://www.g2b.go.kr/notice/20250529-00123"}'
```

**응답 200** (구성된 경우)
```json
{
  "task_name": "send_telegram_notification",
  "status": "ready",
  "detail": "Telegram delivery attempted.\n\n신규 추진 후보\nOO청 유지보수 용역 추진 권장"
}
```

**응답 200** (미구성)
```json
{
  "task_name": "send_telegram_notification",
  "status": "pending_configuration",
  "detail": "Telegram is not configured yet. Skeleton message created."
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | title/message 누락 등 검증 실패 |

---

## POST /api/v1/operations/telegram/callback

텔레그램 인라인 버튼 콜백을 처리합니다. 운영자가 알림 메시지의 버튼(예: 추진/스킵)을 누르면 텔레그램이 보내는 콜백을 받아 해당 입찰 결정 액션을 적용합니다. `callback_query.data`로 어떤 결정/액션인지 식별합니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | update_id | int\|null | 아니오 | 텔레그램 업데이트 id |
| body | callback_query | object | 예 | `{ id, data, message{ message_id, chat{ id } } }` |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/telegram/callback \
  -H "Content-Type: application/json" \
  -d '{"update_id":900001,"callback_query":{"id":"cb-12345","data":"bid_decision:57:submitted","message":{"message_id":42,"chat":{"id":123456789}}}}'
```

**응답 200**
```json
{
  "status": "processed",
  "detail": "결정 상태를 submitted로 갱신했습니다.",
  "decision_record_id": 57,
  "action": "update_status",
  "decision_status": "submitted"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 대상 결정 레코드를 찾지 못함 (서비스 ValueError → 404) |
| 422 | 콜백 페이로드 검증 실패 |

---

## POST /api/v1/operations/telegram/webhook

텔레그램이 푸시하는 원시 webhook 업데이트(메시지/인라인 콜백)를 처리합니다. 텔레그램 webhook URL에 등록해 두면 텔레그램이 이 엔드포인트로 업데이트를 보냅니다. 요청 본문은 자유형 object입니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| header | X-Telegram-Bot-Api-Secret-Token | string\|null | 조건부 | 텔레그램+시크릿 구성 시 검증 |
| body | (raw update) | object | 예 | 텔레그램 원시 업데이트 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/operations/telegram/webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: <WEBHOOK_SECRET>" \
  -d '{"update_id":900002,"callback_query":{"id":"cb-22222","data":"bid_decision:57:skipped","message":{"message_id":43,"chat":{"id":123456789}}}}'
```

**응답 200**
```json
{
  "status": "processed",
  "detail": "Telegram webhook update handled.",
  "processed_count": 1,
  "processed_update_ids": [900002],
  "known_chat_ids": ["*****6789"]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 403 | `Invalid Telegram webhook secret` — 시크릿 헤더 불일치 |
| 422 | 본문 파싱 실패 |

---

## POST /api/v1/operations/telegram/sync

대기 중인 텔레그램 업데이트를 수동으로 가져와 즉시 처리합니다(long-poll 방식). webhook을 쓰지 않는 환경이나 밀린 업데이트를 직접 비울 때 사용합니다. 운영에서 webhook 없이 버튼/명령을 자동 처리하려면 `TELEGRAM_POLLING_SCHEDULE_ENABLED=true`와 `TELEGRAM_POLLING_INTERVAL_SECONDS`로 Celery beat polling을 켭니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | int(1..100) | 아니오 | 가져올 업데이트 수. 기본 20 |
| query | timeout_seconds | int(0..60) | 아니오 | long-poll 대기 시간(초). 기본 0 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/operations/telegram/sync?limit=20&timeout_seconds=0"
```

**응답 200**
```json
{
  "status": "processed",
  "detail": "2건의 업데이트를 처리했습니다.",
  "processed_count": 2,
  "processed_update_ids": [900003, 900004],
  "known_chat_ids": ["*****6789"]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | limit/timeout_seconds 범위 위반 |

---

## GET /api/v1/operations/telegram/status

텔레그램 전송/webhook 진단 정보를 노출합니다. 로컬 디버깅에서 구성 여부, 대기 업데이트 수, webhook URL 등을 확인할 때 사용합니다. 구성 시 `status="healthy"`, 미구성 시 `"watch"`, 진단 중 오류가 나면 `"error"`(HTTP는 200 유지)입니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| — | (없음) | — | — | 파라미터 없음 |

**요청 예시**
```bash
curl http://localhost:3000/api/v1/operations/telegram/status
```

**응답 200**
```json
{
  "configured": true,
  "status": "healthy",
  "detail": "Telegram is configured.",
  "delivery_chat_id": "*****6789",
  "pending_update_count": 0,
  "webhook_url": "https://example.com/api/v1/operations/telegram/webhook",
  "has_custom_certificate": false,
  "known_chat_ids": ["*****6789"]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 200 | 진단 실패도 200으로 응답하며 `status="error"` + detail에 사유 |
