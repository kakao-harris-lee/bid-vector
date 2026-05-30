# Backtests API

> 베이스 경로: `/api/v1/backtests` · 인증: **operator Bearer 토큰 필요** (모든 엔드포인트)

인증된 운영자의 페이퍼(모의) 투찰 백테스트를 실행·조회하는 API다. 과거 공고로 전략/모델 성능을 검증하는 historical 모드와, 현재 열린 공고에 대해 미리 투찰을 만들어 보는 forward 모드를 제공한다.

예제의 베이스 URL은 `http://localhost:3000`을 사용하며, 모든 요청에 `-H "Authorization: Bearer <TOKEN>"` 헤더가 필요하다(`<TOKEN>`은 플레이스홀더).

> **caveat — "낙찰" 지표는 추정치다.** summary 의 `would_have_won_price_only_count`(개별 정산의 `would_have_won_price_only == "plausible"`)는 가격 기준 추정 낙찰 신호이며, win rate 프록시 = `would_have_won_price_only_count / settled_count` 로 계산된다. 이는 **실제 낙찰이 아니라 "가격만으로 봤을 때 이겼을 법한" 추정**이다. 분석·보고 시 항상 caveat 표기.

## 목차
- [GET /api/v1/backtests/data-audit](#get-apiv1backtestsdata-audit) — 백테스트용 DB 준비 상태 진단
- [POST /api/v1/backtests/paper-bidding/runs](#post-apiv1backtestspaper-biddingruns) — historical 페이퍼 투찰 백테스트 실행
- [POST /api/v1/backtests/paper-bidding/forward-runs](#post-apiv1backtestspaper-biddingforward-runs) — forward 페이퍼 투찰 생성
- [GET /api/v1/backtests/paper-bidding/runs](#get-apiv1backtestspaper-biddingruns) — 영속화된 실행 목록 조회
- [GET /api/v1/backtests/paper-bidding/summary](#get-apiv1backtestspaper-biddingsummary) — 최신 실행 요약
- [GET /api/v1/backtests/paper-bidding/runs/{run_id}](#get-apiv1backtestspaper-biddingrunsrun_id) — 실행 상세(투찰/정산 포함)

---

## GET /api/v1/backtests/data-audit

백테스트를 돌리기 전에 DB에 데이터가 충분한지 점검하는 사전 진단 엔드포인트다. 테이블별 행 수, 기간 윈도우 내 건수, 데이터 날짜 범위, 카테고리별 분해를 반환한다. 백테스트 실행 화면에서 "이 카테고리/기간으로 돌릴 만한 데이터가 있나"를 미리 보여줄 때 사용한다.

- 인증: operator Bearer 토큰 필요. 진단 결과는 운영자 개별 데이터가 아닌 DB 전반의 준비 상태이며, operator 객체 자체는 로직에 쓰이지 않는다(인증 게이트 용도).

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | category | string[] | 아니오 | 카테고리 필터(다중값: `?category=a&category=b`). 생략 시 전체 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/backtests/data-audit?category=시설공사&category=정보통신공사" \
  -H "Authorization: Bearer <TOKEN>"
```

**응답 200**
```json
{
  "generated_at": "2026-05-29T08:30:00Z",
  "filters": { "categories": ["시설공사", "정보통신공사"] },
  "table_counts": { "projects": 12840, "tender_results": 9120, "paper_bid_runs": 14 },
  "window_counts": { "last_30_days": 412, "last_90_days": 1180 },
  "date_range": { "min_announced_at": "2024-01-03T00:00:00Z", "max_announced_at": "2026-05-28T00:00:00Z" },
  "category_breakdown": [
    { "category": "시설공사", "project_count": 6400, "with_result_count": 4810 },
    { "category": "정보통신공사", "project_count": 2210, "with_result_count": 1705 }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |
| 422 | 쿼리 파라미터 타입 오류 |

---

## POST /api/v1/backtests/paper-bidding/runs

과거 공고 데이터를 기준으로 페이퍼(모의) 투찰 백테스트를 즉시 실행하고 결과를 반환한다. 운영자 전략으로 후보를 추리고, 마감 기준 cutoff 시점의 데이터만 사용해 예측가/투찰가를 산출한 뒤, 최종 낙찰 결과와 대조해 정산(settlement)한다. 전략·모델 변경의 성능을 과거 기간에 대해 검증할 때 호출한다.

- 인증: operator Bearer 토큰 필요. 백테스트는 인증 운영자(`operator.id`)의 전략으로 수행된다.
- 도메인:
  - `cutoff_hours_before_deadline` — 마감 몇 시간 전 시점의 데이터로 고정해 미래 정보 누수를 막는 cutoff 정책(기본 마감 2시간 전).
  - `settle_actions` — 어떤 결정(bid_now/review/skip)을 정산 대상으로 볼지(기본 `["bid_now"]`).
  - `persist=false`(기본)면 1회성 응답만 반환하고 저장하지 않으며, `true`면 `PaperBidRun`으로 영속화되어 목록/상세 조회 대상이 된다.
  - 응답 `summary`의 win rate 프록시는 문서 상단 caveat 참조(실제 낙찰 아님).

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | category | string \| null | 아니오 | 카테고리 필터(기본 null=전체) |
| body | start_at | datetime \| null | 아니오 | 백테스트 시작 시점(ISO8601) |
| body | end_at | datetime \| null | 아니오 | 백테스트 종료 시점(ISO8601) |
| body | limit | integer | 아니오 | 후보 상한, 1..5000(기본 100) |
| body | scenario | enum | 아니오 | conservative \| base \| aggressive(기본 base) |
| body | strategy_version | string | 아니오 | 전략 버전 태그(기본 "local-backtest") |
| body | model_version | string | 아니오 | 모델 버전 태그(기본 "current") |
| body | cutoff_hours_before_deadline | integer | 아니오 | 마감 N시간 전 cutoff, 0..168(기본 2) |
| body | history_limit | integer | 아니오 | 참조 이력 상한, 1..500(기본 80) |
| body | settle_actions | enum[] | 아니오 | 정산 대상 결정(bid_now\|review\|skip, 기본 ["bid_now"]) |
| body | persist | boolean | 아니오 | 결과 영속화 여부(기본 false) |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/backtests/paper-bidding/runs" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "category": "시설공사",
    "start_at": "2025-01-01T00:00:00Z",
    "end_at": "2025-12-31T23:59:59Z",
    "limit": 200,
    "scenario": "base",
    "cutoff_hours_before_deadline": 2,
    "history_limit": 80,
    "settle_actions": ["bid_now"],
    "persist": true
  }'
```

**응답 200**
```json
{
  "run_id": 31,
  "request": {
    "category": "시설공사",
    "scenario": "base",
    "cutoff_hours_before_deadline": 2,
    "persist": true
  },
  "summary": {
    "candidate_count": 200,
    "paper_bid_count": 142,
    "review_count": 38,
    "skip_count": 20,
    "skipped_by_strategy_count": 17,
    "settled_count": 118,
    "action_counts": { "bid_now": 104, "review": 38, "skip": 20 },
    "average_absolute_bid_rate_error": 0.0042,
    "average_absolute_amount_error_rate": 0.0061,
    "within_0_1pct_count": 49,
    "within_0_3pct_count": 88,
    "within_1pct_count": 110,
    "price_close_count": 71,
    "price_competitive_count": 96,
    "would_have_won_price_only_count": 63
  },
  "items": [
    {
      "project_id": 10241,
      "project_title": "OO청사 소방시설 개선공사",
      "action": "bid_now",
      "paper_bid_amount": 184500000,
      "paper_bid_rate": 0.8721,
      "predicted_price": 185100000,
      "confidence_score": 0.74
    }
  ],
  "settlements": [
    {
      "paper_bid_id": 8801,
      "winning_amount": 184900000,
      "absolute_bid_rate_error": 0.0011,
      "price_close": true,
      "would_have_won_price_only": "plausible"
    }
  ]
}
```

> `summary.would_have_won_price_only_count / summary.settled_count` = 63 / 118 ≈ 53.4%는 **가격 기준 추정 낙찰률(프록시)**이며 실제 낙찰률이 아니다.

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |
| 422 | 요청 스키마 위반(limit 1..5000 초과, scenario enum 외 값, cutoff 0..168 초과, settle_actions 허용 외 값 등) |

---

## POST /api/v1/backtests/paper-bidding/forward-runs

과거가 아니라 **현재 열려 있는(또는 재공고된) 공고**에 대해 페이퍼 투찰을 생성한다. 아직 결과가 나오지 않은 진행 중 공고에 전략을 적용해, 지금 투찰한다면 어떤 후보를 어떤 가격으로 낼지를 미리 만들어 보는 forward 모드다. 향후 결과가 수집되면 정산되어 forward 성능을 추적할 수 있다.

- 인증: operator Bearer 토큰 필요. 인증 운영자 전략으로 수행.
- 도메인:
  - historical 과 달리 기간(start_at/end_at)·cutoff·settle_actions 파라미터가 없다(현재 시점 기준).
  - `persist` 기본값이 `true` — forward 결과는 기본적으로 `PaperBidRun`으로 저장되어 목록/상세에서 추적된다.
  - 생성 직후에는 결과 미정 공고가 많아 `settlement_overview.status`가 `before_deadline`/`waiting_result`로 나오는 것이 정상이다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | category | string \| null | 아니오 | 카테고리 필터(기본 null=전체) |
| body | limit | integer | 아니오 | 후보 상한, 1..500(기본 100) |
| body | scenario | enum | 아니오 | conservative \| base \| aggressive(기본 base) |
| body | strategy_version | string | 아니오 | 전략 버전 태그(기본 "forward-paper") |
| body | model_version | string | 아니오 | 모델 버전 태그(기본 "current") |
| body | history_limit | integer | 아니오 | 참조 이력 상한, 1..500(기본 80) |
| body | persist | boolean | 아니오 | 결과 영속화 여부(기본 true) |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/backtests/paper-bidding/forward-runs" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "category": "정보통신공사",
    "limit": 50,
    "scenario": "aggressive",
    "history_limit": 80,
    "persist": true
  }'
```

**응답 200**
```json
{
  "run_id": 32,
  "request": { "category": "정보통신공사", "scenario": "aggressive", "persist": true },
  "summary": {
    "candidate_count": 50,
    "paper_bid_count": 41,
    "review_count": 6,
    "skip_count": 3,
    "settled_count": 0,
    "action_counts": { "bid_now": 35, "review": 6, "skip": 3 },
    "would_have_won_price_only_count": 0
  },
  "items": [
    {
      "project_id": 10880,
      "project_title": "OO시 통합관제센터 정보통신공사",
      "action": "bid_now",
      "paper_bid_amount": 92300000,
      "paper_bid_rate": 0.8650,
      "predicted_price": 92800000,
      "confidence_score": 0.69
    }
  ],
  "settlements": []
}
```

> forward 생성 직후에는 `settled_count`가 0이고 `settlements`가 비어 있는 것이 정상이다(결과 미수집).

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |
| 422 | 요청 스키마 위반(limit 1..500, scenario enum, history_limit 1..500) |

---

## GET /api/v1/backtests/paper-bidding/runs

인증 운영자가 영속화한(persist된) 페이퍼 투찰 실행 목록을 최신순으로 반환한다. 백테스트 이력 화면에서 과거 실행들을 나열할 때 사용한다.

- 인증: operator Bearer 토큰 필요. 본인(`operator.id`) 소유 run 만 조회된다(소유 격리).
- 도메인: 각 항목의 `settlement_overview`는 정산 진행 상황 요약이다. `status`는 `no_paper_bids`/`settled`/`ready_to_settle`/`waiting_result`/`before_deadline`/`deadline_missing` 중 하나이며, `settlement_basis`는 `"TenderResult.winning_amount > 0 matched by project_id"`(프로젝트 id로 양수 낙찰가가 매칭될 때 정산).

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | mode | enum \| null | 아니오 | historical_backtest \| forward_paper(생략 시 둘 다) |
| query | limit | integer | 아니오 | 최대 반환 건수, 1..100(기본 20) |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/backtests/paper-bidding/runs?mode=historical_backtest&limit=20" \
  -H "Authorization: Bearer <TOKEN>"
```

**응답 200**
```json
{
  "operator_id": 1,
  "returned_count": 2,
  "limit": 20,
  "items": [
    {
      "id": 31,
      "operator_id": 1,
      "status": "completed",
      "mode": "historical_backtest",
      "scenario": "base",
      "category_filter": "시설공사",
      "strategy_version": "local-backtest",
      "model_version": "current",
      "target_start_at": "2025-01-01T00:00:00Z",
      "target_end_at": "2025-12-31T23:59:59Z",
      "data_cutoff_policy": "2h_before_deadline",
      "started_at": "2026-05-29T07:55:00Z",
      "completed_at": "2026-05-29T07:56:12Z",
      "candidate_count": 200,
      "paper_bid_count": 142,
      "settled_count": 118,
      "settlement_overview": {
        "status": "settled",
        "label": "정산 완료",
        "detail": "118건 모두 최종 결과로 정산되었습니다.",
        "settlement_basis": "TenderResult.winning_amount > 0 matched by project_id",
        "paper_bid_count": 142,
        "settled_count": 118,
        "unsettled_count": 24,
        "next_confirmable_at": "2026-05-20T02:00:00Z"
      },
      "summary": { "settled_count": 118, "would_have_won_price_only_count": 63 }
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |
| 422 | mode 패턴 불일치 또는 limit 범위(1..100) 초과 |

---

## GET /api/v1/backtests/paper-bidding/summary

인증 운영자의 페이퍼 투찰 실행 현황을 한눈에 보여주는 요약이다. 가장 최근 run(`latest_run`), 전체 run 수(`run_count`), 완료(`status=="completed"`) run 수(`completed_count`), 최신 run 의 summary(`latest_summary`)를 반환한다. 대시보드 위젯이나 백테스트 진입 화면 상단 요약에 사용한다.

- 인증: operator Bearer 토큰 필요. 본인 소유 run 만 집계.
- 도메인: run 이 하나도 없으면 `latest_run=null`, `latest_summary={}`. `latest_summary`의 win rate 프록시는 문서 상단 caveat 참조.

**파라미터**

없음.

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/backtests/paper-bidding/summary" \
  -H "Authorization: Bearer <TOKEN>"
```

**응답 200**
```json
{
  "operator_id": 1,
  "latest_run": {
    "id": 32,
    "operator_id": 1,
    "status": "completed",
    "mode": "forward_paper",
    "scenario": "aggressive",
    "category_filter": "정보통신공사",
    "strategy_version": "forward-paper",
    "model_version": "current",
    "target_start_at": null,
    "target_end_at": null,
    "data_cutoff_policy": "forward",
    "started_at": "2026-05-29T08:10:00Z",
    "completed_at": "2026-05-29T08:10:45Z",
    "candidate_count": 50,
    "paper_bid_count": 41,
    "settled_count": 0,
    "settlement_overview": { "status": "before_deadline", "label": "마감 전" },
    "summary": { "settled_count": 0, "would_have_won_price_only_count": 0 }
  },
  "run_count": 14,
  "completed_count": 12,
  "latest_summary": { "settled_count": 0, "would_have_won_price_only_count": 0 }
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |

---

## GET /api/v1/backtests/paper-bidding/runs/{run_id}

영속화된 페이퍼 투찰 실행 하나의 상세를 반환한다. run 메타(목록 항목과 동일 필드) + 실행 요청 원본(`request`) + 개별 페이퍼 투찰 목록(`paper_bids`) + 정산 결과(`settlements`)를 모두 포함한다. 백테스트 결과 상세 화면에서 개별 후보의 예측가/투찰가/정산 판정을 들여다볼 때 사용한다.

- 인증: operator Bearer 토큰 필요. 본인 소유 run 만 조회 — 다른 운영자 소유 run 은 존재해도 404로 응답(소유 격리).
- 도메인:
  - `paper_bids` 각 항목: 예측가(`predicted_price`), 예측 투찰률(`predicted_bid_rate`), 적용 투찰가/률(`paper_bid_amount`/`paper_bid_rate`), 신뢰도(`confidence_score`), 결정(`action`), 사용 predictor(`predictor_name`), 입력 스냅샷 해시(`input_snapshot_hash`, 재현성용).
  - `settlements` 각 항목: 최종 낙찰 결과 대조. `would_have_won_price_only`는 `"plausible"`|`"competitive"`|`"unlikely"` 중 하나로, `"plausible"`이 가격 기준 추정 낙찰 신호다 — **실제 낙찰이 아닌 추정**(caveat 참조). settlement 은 정산이 끝난 페이퍼 투찰에 대해서만 포함된다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | run_id | integer | 예 | 조회할 실행 ID(본인 소유) |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/backtests/paper-bidding/runs/31" \
  -H "Authorization: Bearer <TOKEN>"
```

**응답 200**
```json
{
  "id": 31,
  "operator_id": 1,
  "status": "completed",
  "mode": "historical_backtest",
  "scenario": "base",
  "category_filter": "시설공사",
  "strategy_version": "local-backtest",
  "model_version": "current",
  "target_start_at": "2025-01-01T00:00:00Z",
  "target_end_at": "2025-12-31T23:59:59Z",
  "data_cutoff_policy": "2h_before_deadline",
  "started_at": "2026-05-29T07:55:00Z",
  "completed_at": "2026-05-29T07:56:12Z",
  "candidate_count": 200,
  "paper_bid_count": 142,
  "settled_count": 118,
  "settlement_overview": {
    "status": "settled",
    "label": "정산 완료",
    "detail": "118건 모두 최종 결과로 정산되었습니다.",
    "settlement_basis": "TenderResult.winning_amount > 0 matched by project_id",
    "paper_bid_count": 142,
    "settled_count": 118,
    "unsettled_count": 24
  },
  "summary": { "settled_count": 118, "would_have_won_price_only_count": 63 },
  "request": {
    "category": "시설공사",
    "scenario": "base",
    "cutoff_hours_before_deadline": 2,
    "persist": true
  },
  "paper_bids": [
    {
      "id": 8801,
      "run_id": 31,
      "project_id": 10241,
      "project_title": "OO청사 소방시설 개선공사",
      "notice_number": "20250300123-00",
      "category": "시설공사",
      "action": "bid_now",
      "decision_status": "planned",
      "data_cutoff_at": "2025-03-14T07:00:00Z",
      "paper_bid_amount": 184500000.0,
      "paper_bid_rate": 0.8721,
      "scenario": "base",
      "priority_score": 0.81,
      "probability_score": 0.66,
      "matched_score": 0.79,
      "predicted_price": 185100000.0,
      "predicted_bid_rate": 0.8745,
      "confidence_score": 0.74,
      "predictor_name": "category_ensemble_v3",
      "input_snapshot_hash": "a1b2c3d4e5f6",
      "created_at": "2026-05-29T07:55:40Z"
    }
  ],
  "settlements": [
    {
      "id": 5102,
      "paper_bid_id": 8801,
      "tender_result_id": 44120,
      "result_status": "awarded",
      "winning_company": "OO건설(주)",
      "winning_amount": 184900000.0,
      "winning_rate": 0.8740,
      "amount_delta": -400000.0,
      "absolute_error_rate": 0.0022,
      "bid_rate_delta": -0.0019,
      "absolute_bid_rate_error": 0.0011,
      "price_close": true,
      "price_competitive": true,
      "would_have_won_price_only": "plausible",
      "would_have_won_final": false,
      "settlement_reason": "winning_amount matched by project_id",
      "settled_at": "2026-05-20T02:00:00Z"
    }
  ]
}
```

> `would_have_won_price_only: "plausible"`는 가격 기준 추정 낙찰 신호이며, `would_have_won_final`이 실제 낙찰 여부에 더 가까운 판정이다. 추정 지표는 caveat와 함께 해석한다.

**에러**

| 코드 | 의미 |
|---|---|
| 401 | 토큰 없음 또는 무효 |
| 404 | 해당 run 이 없거나 본인 소유가 아님("Paper bidding run not found") |
| 422 | run_id 가 정수가 아님 |
