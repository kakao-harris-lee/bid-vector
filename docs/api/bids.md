# Bids API

> 베이스 경로: `/api/v1/bids` · 인증: **별도 토큰 불필요** (모든 엔드포인트가 단일 운영자 모델로 동작하며 내부에서 canonical operator 계정을 자동 사용)

단일 운영자가 나라장터(KONEPS) 공고에 대한 입찰을 제출·조회·수정하는 API다. 입찰 제출/수정 시 해당 공고의 입찰 결정 레코드(`BidDecisionRecord`)가 자동으로 `submitted` 상태로 동기화되고, 운영자 알림이 생성된다. 호출자는 운영자를 지정할 수 없으며 항상 단일 운영자의 데이터만 다룬다.

예제의 베이스 URL은 `http://localhost:3000`을 사용한다.

## 목차
- [POST /api/v1/bids/](#post-apiv1bids) — 입찰 제출
- [GET /api/v1/bids/](#get-apiv1bids) — 입찰 목록 조회
- [GET /api/v1/bids/{bid_id}](#get-apiv1bidsbid_id) — 입찰 상세 조회
- [PUT /api/v1/bids/{bid_id}](#put-apiv1bidsbid_id) — 입찰 수정

---

## POST /api/v1/bids/

지정한 공고(`project_id`)에 대해 단일 운영자 명의로 입찰을 제출하고, 관련 입찰 결정 레코드를 `submitted` 상태로 동기화한다. 운영자가 투찰가·납기를 확정해 실제 입찰을 추진할 때 호출한다.

- 인증: 토큰 불필요. 내부적으로 항상 canonical operator 계정을 사용한다(단일 운영자 모델).
- 도메인: 제출과 동시에 해당 공고의 활성 `BidDecisionRecord`를 `submitted`로 승격하고, 없으면 `bid_now`/`submitted`로 새 결정 레코드를 생성한다. 결정은 감사 가능하게 영속화되며, 제출 알림(Telegram/실시간)이 함께 생성된다. 응답의 `operator_id`와 `user_id`는 동일한 단일 운영자 id다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | bid_amount | number | 예 | 투찰가 (KRW) |
| body | proposed_timeline | integer | 예 | 제안 납기(일 등 정수) |
| body | project_id | integer | 예 | 대상 공고 ID |
| body | description | string | 예 | 입찰 설명/메모 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/bids/" \
  -H "Content-Type: application/json" \
  -d '{
    "bid_amount": 184500000,
    "proposed_timeline": 90,
    "project_id": 1024,
    "description": "정밀안전진단 용역 - 기준가 대비 보수적 투찰"
  }'
```
```json
{
  "bid_amount": 184500000,
  "proposed_timeline": 90,
  "project_id": 1024,
  "description": "정밀안전진단 용역 - 기준가 대비 보수적 투찰"
}
```

**응답 200**
```json
{
  "bid_amount": 184500000,
  "proposed_timeline": 90,
  "id": 57,
  "project_id": 1024,
  "operator_id": 1,
  "user_id": 1,
  "status": "submitted",
  "decision_record_id": 312,
  "decision_status": "submitted",
  "score": 0.0,
  "created_at": "2026-05-29T08:15:42.512000Z"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | `project_id`에 해당하는 공고가 없음 (`{"detail": "Project not found"}`) |
| 422 | 요청 본문 필수 필드 누락 또는 타입 불일치 |

---

## GET /api/v1/bids/

단일 운영자의 입찰 목록을 페이지네이션·필터와 함께 조회한다. 입찰 내역 화면이나 특정 공고의 입찰 이력을 확인할 때 호출한다.

- 인증: 토큰 불필요. 항상 단일 운영자의 입찰만 반환한다.
- 도메인: 각 입찰의 공고별 최신 `BidDecisionRecord`를 일괄 조회해 `decision_status` 등을 함께 채운다. `project_id`·`status`를 주면 AND 조건으로 좁힌다(`status`는 `Bid.status` 문자열 일치).

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | skip | integer | 아니오 | 건너뛸 개수 (기본 0) |
| query | limit | integer | 아니오 | 최대 반환 개수 (기본 100) |
| query | project_id | integer | 아니오 | 특정 공고로 필터 |
| query | status | string | 아니오 | `Bid.status` 값으로 필터 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/bids/?skip=0&limit=20&project_id=1024&status=submitted"
```

**응답 200**
```json
[
  {
    "bid_amount": 184500000,
    "proposed_timeline": 90,
    "id": 57,
    "project_id": 1024,
    "operator_id": 1,
    "user_id": 1,
    "status": "submitted",
    "decision_record_id": 312,
    "decision_status": "submitted",
    "score": 0.72,
    "created_at": "2026-05-29T08:15:42.512000Z"
  },
  {
    "bid_amount": 51200000,
    "proposed_timeline": 45,
    "id": 53,
    "project_id": 1011,
    "operator_id": 1,
    "user_id": 1,
    "status": "planned",
    "decision_record_id": 298,
    "decision_status": "reviewing",
    "score": null,
    "created_at": "2026-05-21T01:02:10.000000Z"
  }
]
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 쿼리 파라미터 타입 불일치 (예: `skip`에 정수가 아닌 값) |

---

## GET /api/v1/bids/{bid_id}

입찰 ID로 단일 운영자의 입찰 1건 상세를 조회한다. 입찰 상세 화면 진입 시 호출한다.

- 인증: 토큰 불필요. 조회 범위는 단일 운영자 소유 입찰로 제한된다.
- 도메인: 해당 입찰 공고의 최신 `BidDecisionRecord`를 합쳐 결정 상태를 함께 반환한다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | bid_id | integer | 예 | 입찰 ID |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/bids/57"
```

**응답 200**
```json
{
  "bid_amount": 184500000,
  "proposed_timeline": 90,
  "id": 57,
  "project_id": 1024,
  "operator_id": 1,
  "user_id": 1,
  "status": "submitted",
  "decision_record_id": 312,
  "decision_status": "submitted",
  "score": 0.72,
  "created_at": "2026-05-29T08:15:42.512000Z"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 입찰이 없거나 단일 운영자 소유가 아님 (`{"detail": "Bid not found"}`) |
| 422 | `bid_id`가 정수가 아님 |

---

## PUT /api/v1/bids/{bid_id}

단일 운영자 소유 입찰의 투찰가/설명을 수정하고, 변경된 투찰가로 입찰 결정 레코드를 다시 동기화한다. 제출 후 투찰가를 조정할 때 호출한다.

- 인증: 토큰 불필요. 단일 운영자 소유 입찰만 수정 가능.
- 도메인: 전달된 필드만 반영한다(미전달 필드는 변경 없음). 수정 후 `sync_submitted_bid`로 결정 레코드를 `submitted`로 재동기화하고, 공고가 존재하면 알림을 생성한다. `BidUpdate`에는 `bid_amount`·`description`만 있어 `proposed_timeline`·`status`는 이 엔드포인트로 변경할 수 없다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | bid_id | integer | 예 | 입찰 ID |
| body | bid_amount | number \| null | 아니오 | 새 투찰가 (KRW) |
| body | description | string \| null | 아니오 | 새 설명/메모 |

**요청 예시**
```bash
curl -X PUT "http://localhost:3000/api/v1/bids/57" \
  -H "Content-Type: application/json" \
  -d '{
    "bid_amount": 182000000,
    "description": "경쟁사 동향 반영 투찰가 하향 조정"
  }'
```
```json
{
  "bid_amount": 182000000,
  "description": "경쟁사 동향 반영 투찰가 하향 조정"
}
```

**응답 200**
```json
{
  "bid_amount": 182000000,
  "proposed_timeline": 90,
  "id": 57,
  "project_id": 1024,
  "operator_id": 1,
  "user_id": 1,
  "status": "submitted",
  "decision_record_id": 312,
  "decision_status": "submitted",
  "score": 0.72,
  "created_at": "2026-05-29T08:15:42.512000Z"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 입찰이 없거나 단일 운영자 소유가 아님 (`{"detail": "Bid not found"}`) |
| 422 | 요청 본문 타입 불일치 |

---

## 참고

- `/api/v1/dashboard/bids`(GET)는 `Dashboard` 태그 소속이며 본 Bids 라우터 범위 밖이다.
- predictor 낙찰하한 guardrail은 가격 예측 경로(`app/ai/price_prediction.py`)에서 적용된다. Bids 라우터는 전달된 `bid_amount`를 직접 하한 검증하지 않으므로, 적정 투찰가 산정은 예측 API를 거쳐 결정하는 것을 전제로 한다.
