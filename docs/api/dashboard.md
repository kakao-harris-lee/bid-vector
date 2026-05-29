# Dashboard API

> 베이스 경로: `/api/v1/dashboard` · 인증: operator Bearer 토큰 (모든 엔드포인트 필수)
> 예제 베이스 URL: `http://localhost:3000` · 인증 헤더: `Authorization: Bearer <TOKEN>`

단일 운영자(canonical operator) 모바일 대시보드의 요약 지표·목록 API입니다. 모든 응답은 토큰이 식별하는 활성 operator 한 명의 데이터로 한정됩니다. "입찰 후보"는 저장된 입찰 판단(`BidDecisionRecord`)을 우선으로, 부족하면 최신 forward_paper paper bidding 실행의 페이퍼 후보(`PaperBid`)로 보충하는 혼합 소스입니다.

## 목차
- [GET /api/v1/dashboard/opportunities](#get-apiv1dashboardopportunities) — 입찰 후보 목록(판단+페이퍼 혼합)
- [GET /api/v1/dashboard/bids](#get-apiv1dashboardbids) — 제출 투찰 목록(연결 판단 포함)
- [GET /api/v1/dashboard/results](#get-apiv1dashboardresults) — 낙찰 결과 + 예측/추천 오차
- [GET /api/v1/dashboard/summary](#get-apiv1dashboardsummary) — 대시보드 홈 통합 페이로드

---

## GET /api/v1/dashboard/opportunities

대시보드 "입찰" 탭에 노출할 입찰 후보 목록을 반환합니다. 운영자의 저장된 입찰 판단(`BidDecisionRecord`)을 priority_score 내림차순으로 먼저 채우고, limit이 남으면 같은 공고를 제외한 최신 forward_paper paper bidding 실행의 페이퍼 후보(`PaperBid`)로 보충합니다. 각 항목의 `source`로 판단(`decision`)인지 페이퍼 후보(`paper_bid`)인지 구분됩니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | status | string | 아니오 | `planned`\|`reviewing`\|`submitted`\|`skipped` 중 하나로 상태 필터. 미지정 시 전체 |
| query | limit | integer | 아니오 | 반환 개수 상한. 1~100, 기본 50 |

**요청 예시**
```bash
curl -H "Authorization: Bearer <TOKEN>" \
  "http://localhost:3000/api/v1/dashboard/opportunities?status=reviewing&limit=20"
```

**응답 200**
```json
{
  "operator_id": 1,
  "generated_at": "2026-05-29T03:12:45.120000Z",
  "returned_count": 2,
  "limit": 20,
  "items": [
    {
      "source": "decision",
      "source_label": "입찰 판단",
      "decision_record_id": 482,
      "paper_bid_id": null,
      "project": {
        "project_id": 10293,
        "title": "OO시 상수도 정보화 구축 사업",
        "category": "정보통신공사",
        "notice_number": "20260529-00321",
        "issuing_agency": "OO시청",
        "demand_agency": "OO시 상수도사업본부",
        "budget_estimate": 480000000.0,
        "deadline": "2026-05-30T05:00:00Z",
        "status": "open"
      },
      "action": "review",
      "decision_status": "reviewing",
      "recommended_amount": 432500000.0,
      "probability_score": 0.71,
      "matched_score": 0.83,
      "priority_score": 0.77,
      "urgency_score": 0.64,
      "deadline_hours_remaining": 25,
      "reasoning": "유사 낙찰 이력과 카테고리 적합도가 높아 검토 권장",
      "updated_at": "2026-05-29T02:55:10Z",
      "detail_href": "/api/v1/operations/bid-decisions/482"
    },
    {
      "source": "paper_bid",
      "source_label": "페이퍼 후보",
      "decision_record_id": null,
      "paper_bid_id": 7781,
      "project": {
        "project_id": 10477,
        "title": "OO군 청사 통합관제 시스템 유지보수",
        "category": "정보통신공사",
        "notice_number": "20260528-01180",
        "issuing_agency": "OO군청",
        "demand_agency": "OO군청",
        "budget_estimate": 92000000.0,
        "deadline": "2026-05-31T08:00:00Z",
        "status": "open"
      },
      "action": "bid_now",
      "decision_status": "planned",
      "recommended_amount": 84100000.0,
      "probability_score": 0.58,
      "matched_score": 0.69,
      "priority_score": 0.61,
      "urgency_score": 0.0,
      "deadline_hours_remaining": 53,
      "reasoning": "forward_paper run #214 후보",
      "updated_at": "2026-05-29T01:40:00Z",
      "detail_href": "/api/v1/backtests/paper-bidding/runs/214"
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰 누락/무효, 또는 비활성 운영자 |
| 422 | `status` 패턴 위반 또는 `limit` 범위(1~100) 위반 |

```json
{ "detail": "Missing bearer token" }
```

---

## GET /api/v1/dashboard/bids

운영자가 제출한 투찰(`Bid`) 목록을 최신 갱신 순으로 반환하고, 각 투찰을 같은 공고의 최신 입찰 판단(`BidDecisionRecord`)과 연결해 추천가·판단 상태를 함께 보여줍니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | status | string | 아니오 | `submitted`\|`reviewed`\|`accepted`\|`rejected` 중 하나로 상태 필터. 미지정 시 전체 |
| query | limit | integer | 아니오 | 반환 개수 상한. 1~100, 기본 50 |

**요청 예시**
```bash
curl -H "Authorization: Bearer <TOKEN>" \
  "http://localhost:3000/api/v1/dashboard/bids?status=submitted&limit=10"
```

**응답 200**
```json
{
  "operator_id": 1,
  "generated_at": "2026-05-29T03:12:45.120000Z",
  "returned_count": 1,
  "limit": 10,
  "items": [
    {
      "bid_id": 3120,
      "project": {
        "project_id": 10293,
        "title": "OO시 상수도 정보화 구축 사업",
        "category": "정보통신공사",
        "notice_number": "20260529-00321",
        "issuing_agency": "OO시청",
        "demand_agency": "OO시 상수도사업본부",
        "budget_estimate": 480000000.0,
        "deadline": "2026-05-30T05:00:00Z",
        "status": "open"
      },
      "decision_record_id": 482,
      "decision_status": "submitted",
      "bid_amount": 431800000.0,
      "recommended_amount": 432500000.0,
      "proposed_timeline": 120,
      "status": "submitted",
      "score": 0.74,
      "submitted_at": "2026-05-28T23:10:00Z",
      "updated_at": "2026-05-28T23:10:00Z",
      "detail_href": "/api/v1/bids/3120"
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰 누락/무효, 또는 비활성 운영자 |
| 422 | `status` 패턴 위반 또는 `limit` 범위(1~100) 위반 |

```json
{ "detail": "Invalid bearer token" }
```

---

## GET /api/v1/dashboard/results

낙찰 결과(`TenderResult`, winning_amount>0)를 공고별 최신 한 건씩 모아, 각 결과를 운영자의 최신 가격 예측(`PricePrediction`)·입찰 판단 추천가·제출 투찰과 대조한 컨텍스트로 반환합니다. 예측가/추천가 대비 낙찰가의 차이(delta)와 오차율(error_rate)을 계산해 함께 제공합니다.

`award_outcome`은 투찰이 accepted거나 낙찰사명이 운영자 회사명과 부분 일치하면 `won`, 결과가 awarded/closed이고 투찰이 있으면 `lost`, 그 외 `unknown`으로 산정합니다. error_rate는 `|후보가 - 낙찰가| / 낙찰가` 절대 오차율입니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer | 아니오 | 반환 개수 상한. 1~100, 기본 50 |

**요청 예시**
```bash
curl -H "Authorization: Bearer <TOKEN>" \
  "http://localhost:3000/api/v1/dashboard/results?limit=10"
```

**응답 200**
```json
{
  "operator_id": 1,
  "generated_at": "2026-05-29T03:12:45.120000Z",
  "returned_count": 1,
  "limit": 10,
  "items": [
    {
      "tender_result_id": 905,
      "project": {
        "project_id": 10120,
        "title": "OO도 교육청 무선망 고도화",
        "category": "정보통신공사",
        "notice_number": "20260510-00077",
        "issuing_agency": "OO도교육청",
        "demand_agency": "OO도교육청",
        "budget_estimate": 310000000.0,
        "deadline": "2026-05-12T05:00:00Z",
        "status": "closed"
      },
      "winning_company": "OO네트웍스",
      "winning_amount": 278900000.0,
      "winning_rate": 0.8997,
      "result_status": "awarded",
      "award_outcome": "lost",
      "announced_at": "2026-05-15T06:00:00Z",
      "latest_prediction_id": 6611,
      "predicted_price": 281200000.0,
      "prediction_delta_amount": 2300000.0,
      "prediction_error_rate": 0.0082,
      "latest_decision_record_id": 460,
      "recommended_amount": 283000000.0,
      "recommendation_delta_amount": 4100000.0,
      "recommendation_error_rate": 0.0147,
      "detail_href": "/api/v1/analytics/prediction-feedback?project_id=10120"
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰 누락/무효, 또는 비활성 운영자 |
| 422 | `limit` 범위(1~100) 위반 |

```json
{ "detail": "Operator is not active" }
```

---

## GET /api/v1/dashboard/summary

모바일 대시보드 홈 화면의 통합 페이로드를 반환합니다. "오늘 할 일" 중심으로 핵심 지표(metrics), 처리 대기 작업(work_items), 탭 요약(sections), 그리고 최근 입찰 후보·투찰·결과 미리보기를 한 번에 내려줍니다.

- `metrics`는 항상 5개입니다: `due_opportunities`(24시간 내 마감, 있으면 critical), `active_opportunities`(판단+페이퍼 합산), `active_bids`(submitted/reviewed 투찰), `recent_results`(최근 결과·예측 오차), `paper_backtest`(최신 PaperBidRun 상태/건수).
- `operational_status`는 최신 `OperatorStrategyRun` 상태를 DashboardMetric 형태로 표현합니다(no_run/completed/queued/running/failed).
- `work_items`는 마감 임박 후보(opportunity_due, deadline<=6h면 critical), 결과 대기 투찰(bid_pending_result), 예측 오차 검토 결과(result_review)를 limit 한도 내에서 합친 처리 큐입니다.
- `sections`는 항상 3개(opportunities/bids/results) 탭 카운트 요약입니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer | 아니오 | 각 미리보기 목록·work_items 개수 상한. 1~20, 기본 5 |

**요청 예시**
```bash
curl -H "Authorization: Bearer <TOKEN>" \
  "http://localhost:3000/api/v1/dashboard/summary?limit=5"
```

**응답 200**
```json
{
  "operator_id": 1,
  "generated_at": "2026-05-29T03:12:45.120000Z",
  "today": "2026-05-29",
  "operational_status": {
    "key": "operator_strategy",
    "label": "운영 상태",
    "value": "completed",
    "unit": "state",
    "status": "healthy",
    "detail": "최근 전략 모니터링이 정상 완료되었습니다."
  },
  "metrics": [
    {
      "key": "due_opportunities",
      "label": "오늘 마감",
      "value": 1,
      "unit": "count",
      "status": "critical",
      "detail": "24시간 이내 저장된 판단과 페이퍼 후보입니다."
    },
    {
      "key": "active_opportunities",
      "label": "판단 대기",
      "value": 6,
      "unit": "count",
      "status": "watch",
      "detail": "저장된 판단과 최신 페이퍼 후보를 합친 입찰 후보입니다."
    },
    {
      "key": "active_bids",
      "label": "결과 대기",
      "value": 2,
      "unit": "count",
      "status": "info",
      "detail": "submitted/reviewed 상태의 투찰입니다."
    },
    {
      "key": "recent_results",
      "label": "결과 확인",
      "value": 3,
      "unit": "count",
      "status": "watch",
      "detail": "최근 낙찰 결과와 예측 오차입니다."
    },
    {
      "key": "paper_backtest",
      "label": "페이퍼 검증",
      "value": 12,
      "unit": "count",
      "status": "healthy",
      "detail": "forward_paper 실행에서 후보 40건, 가상 투찰 12건, 정산 9건을 기록했습니다."
    }
  ],
  "work_items": [
    {
      "key": "opportunity:482",
      "item_type": "opportunity_due",
      "severity": "critical",
      "title": "OO시 상수도 정보화 구축 사업",
      "subtitle": "24시간 내 마감 입찰 판단",
      "project_id": 10293,
      "due_at": "2026-05-30T05:00:00Z",
      "status": "reviewing",
      "href": "/dashboard/opportunities"
    },
    {
      "key": "bid:3120",
      "item_type": "bid_pending_result",
      "severity": "info",
      "title": "OO시 상수도 정보화 구축 사업",
      "subtitle": "제출 후 결과 확인 대기",
      "project_id": 10293,
      "due_at": null,
      "status": "submitted",
      "href": "/dashboard/bids"
    }
  ],
  "sections": [
    {
      "key": "opportunities",
      "label": "입찰",
      "count": 6,
      "status": "watch",
      "href": "/dashboard/opportunities"
    },
    {
      "key": "bids",
      "label": "투찰",
      "count": 2,
      "status": "info",
      "href": "/dashboard/bids"
    },
    {
      "key": "results",
      "label": "결과",
      "count": 3,
      "status": "watch",
      "href": "/dashboard/results"
    }
  ],
  "recent_opportunities": [
    {
      "source": "decision",
      "source_label": "입찰 판단",
      "decision_record_id": 482,
      "paper_bid_id": null,
      "project": {
        "project_id": 10293,
        "title": "OO시 상수도 정보화 구축 사업",
        "category": "정보통신공사",
        "notice_number": "20260529-00321",
        "issuing_agency": "OO시청",
        "demand_agency": "OO시 상수도사업본부",
        "budget_estimate": 480000000.0,
        "deadline": "2026-05-30T05:00:00Z",
        "status": "open"
      },
      "action": "review",
      "decision_status": "reviewing",
      "recommended_amount": 432500000.0,
      "probability_score": 0.71,
      "matched_score": 0.83,
      "priority_score": 0.77,
      "urgency_score": 0.64,
      "deadline_hours_remaining": 25,
      "reasoning": "유사 낙찰 이력과 카테고리 적합도가 높아 검토 권장",
      "updated_at": "2026-05-29T02:55:10Z",
      "detail_href": "/api/v1/operations/bid-decisions/482"
    }
  ],
  "recent_bids": [
    {
      "bid_id": 3120,
      "project": {
        "project_id": 10293,
        "title": "OO시 상수도 정보화 구축 사업",
        "category": "정보통신공사",
        "notice_number": "20260529-00321",
        "issuing_agency": "OO시청",
        "demand_agency": "OO시 상수도사업본부",
        "budget_estimate": 480000000.0,
        "deadline": "2026-05-30T05:00:00Z",
        "status": "open"
      },
      "decision_record_id": 482,
      "decision_status": "submitted",
      "bid_amount": 431800000.0,
      "recommended_amount": 432500000.0,
      "proposed_timeline": 120,
      "status": "submitted",
      "score": 0.74,
      "submitted_at": "2026-05-28T23:10:00Z",
      "updated_at": "2026-05-28T23:10:00Z",
      "detail_href": "/api/v1/bids/3120"
    }
  ],
  "recent_results": [
    {
      "tender_result_id": 905,
      "project": {
        "project_id": 10120,
        "title": "OO도 교육청 무선망 고도화",
        "category": "정보통신공사",
        "notice_number": "20260510-00077",
        "issuing_agency": "OO도교육청",
        "demand_agency": "OO도교육청",
        "budget_estimate": 310000000.0,
        "deadline": "2026-05-12T05:00:00Z",
        "status": "closed"
      },
      "winning_company": "OO네트웍스",
      "winning_amount": 278900000.0,
      "winning_rate": 0.8997,
      "result_status": "awarded",
      "award_outcome": "lost",
      "announced_at": "2026-05-15T06:00:00Z",
      "latest_prediction_id": 6611,
      "predicted_price": 281200000.0,
      "prediction_delta_amount": 2300000.0,
      "prediction_error_rate": 0.0082,
      "latest_decision_record_id": 460,
      "recommended_amount": 283000000.0,
      "recommendation_delta_amount": 4100000.0,
      "recommendation_error_rate": 0.0147,
      "detail_href": "/api/v1/analytics/prediction-feedback?project_id=10120"
    }
  ],
  "realtime_href": "/api/v1/realtime/events"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰 누락/무효, 또는 비활성 운영자 |
| 422 | `limit` 범위(1~20) 위반 |

```json
{ "detail": "Missing bearer token" }
```
