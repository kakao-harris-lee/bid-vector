# Legacy Admin API

> 베이스 경로: `/api/v1/admin` · 베이스 URL 예시: `http://localhost:3000`
> 인증: 이 태그의 엔드포인트는 코드상 **인증 의존성이 없다**(legacy 호환). 운영 노출 시 주의가 필요하다.

이 태그의 3개 엔드포인트는 모두 **단일 운영자(single operator) 모델 이전의 다중 사용자/관리자 API와 호환하기 위한 legacy 엔드포인트**다. 모든 핸들러 docstring이 "Legacy compatibility endpoint"로 시작한다. 현재 시스템은 운영자 1인 모델이므로 사용자 목록은 항상 canonical operator 1건만, 통계의 `total_users`는 항상 `1`을 반환한다. 코드와 OpenAPI 스펙에 명시적 `deprecated` 플래그는 없으나, 태그명("Legacy Admin")과 docstring상 구버전 호환 용도로 보는 것이 적절하다.

## 목차
- [GET /api/v1/admin/users](#get-apiv1adminusers) — singleton operator를 사용자 목록(배열)으로 반환
- [GET /api/v1/admin/stats](#get-apiv1adminstats) — 단일 운영자 시스템 집계 통계 반환
- [PUT /api/v1/admin/users/{user_id}/deactivate](#put-apiv1adminusersuser_iddeactivate) — singleton operator 비활성화

---

## GET /api/v1/admin/users

singleton operator 계정 1건을 사용자 목록 형태(배열)로 반환하는 legacy 호환 엔드포인트다. 과거 다중 사용자 시스템의 "사용자 목록 조회" API 시그니처(`skip`/`limit` 페이지네이션, 사용자 배열 응답)를 유지하기 위해 존재한다. 단일 운영자 모델에서는 항상 canonical operator 계정 하나만 담긴 배열을 반환한다. `skip > 0` 또는 `limit <= 0` 이면 빈 배열 `[]`을 반환한다.

- 인증: 불필요(코드상 인증 의존성 없음).
- 도메인: 단일 운영자 모델. `ensure_operator_account(db)`로 canonical operator를 확보해 반환.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | skip | integer | 아니오 | 페이지 오프셋. 기본값 `0`. `> 0`이면 빈 배열 반환 |
| query | limit | integer | 아니오 | 페이지 크기. 기본값 `100`. `<= 0`이면 빈 배열 반환 |

**요청 예시**

```bash
curl "http://localhost:3000/api/v1/admin/users?skip=0&limit=100"
```

**응답 200**

```json
[
  {
    "username": "operator",
    "email": "operator@example.com",
    "full_name": "단일 운영자",
    "company": "BidVector",
    "id": 1,
    "is_active": true,
    "is_admin": true,
    "created_at": "2025-01-02T09:00:00Z"
  }
]
```

`skip=1` 처럼 범위를 벗어나는 경우:

```json
[]
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | `skip`/`limit`가 정수로 변환되지 않는 등 쿼리 타입 검증 실패 |

```json
{
  "detail": [
    {
      "loc": ["query", "limit"],
      "msg": "Input should be a valid integer, unable to parse string as an integer",
      "type": "int_parsing"
    }
  ]
}
```

---

## GET /api/v1/admin/stats

단일 운영자 시스템의 집계 통계를 반환하는 legacy 호환 엔드포인트다. 과거 관리자 대시보드의 "시스템 통계" API 형태를 유지한다.

- 인증: 불필요(코드상 인증 의존성 없음).
- 도메인: 단일 운영자 모델. `total_users`는 항상 `1`, `active_users`는 operator의 `is_active`에 따라 `1` 또는 `0`, `mode`는 항상 `"single_operator"`. `total_projects`는 전체 `Project` 행 수, `total_bids`는 해당 operator의 `Bid` 행 수.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| — | — | — | — | (파라미터 없음) |

**요청 예시**

```bash
curl "http://localhost:3000/api/v1/admin/stats"
```

**응답 200**

```json
{
  "operator_id": 1,
  "total_users": 1,
  "active_users": 1,
  "total_projects": 4821,
  "total_bids": 137,
  "mode": "single_operator"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| — | 정상 경로에서 200만 정의됨. 별도 검증 분기 없음 |

---

## PUT /api/v1/admin/users/{user_id}/deactivate

지정한 `user_id`가 singleton operator의 ID와 일치할 때 해당 운영자 계정을 비활성화(`is_active=False`)하고 DB에 커밋하는 legacy 호환 엔드포인트다. 과거 관리자 "사용자 비활성화" API 시그니처를 유지하되, 단일 운영자 모델에서는 오직 canonical operator만 대상이 될 수 있다.

- 인증: 불필요(코드상 인증 의존성 없음). 상태를 변경하는 쓰기 엔드포인트인데 인증이 없으므로 운영 노출 시 주의가 필요하다.
- 도메인: 단일 운영자 모델. operator 외 다른 사용자가 없으므로 일치하지 않는 `user_id`는 404로 거부된다. 호출 시 운영자 계정 비활성화가 DB에 커밋되는 부수효과가 있다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | user_id | integer | 예 | 비활성화 대상 사용자 ID. singleton operator의 ID와 일치해야 함 |

**요청 예시**

```bash
curl -X PUT "http://localhost:3000/api/v1/admin/users/1/deactivate"
```

**응답 200**

```json
{
  "status": "operator deactivated",
  "operator_id": 1,
  "requested_user_id": 1,
  "mode": "single_operator"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | `user_id`가 singleton operator의 ID와 다름(단일 운영자만 존재하므로 다른 사용자 비활성화 불가) |
| 422 | path의 `user_id`가 정수로 변환되지 않음 |

```json
{
  "detail": "Only the singleton operator account is available in single-user mode"
}
```

> 비고: 404 분기는 핸들러 코드에 존재하나 OpenAPI `responses`에는 `200`/`422`만 등록되어 있다(문서화 누락 수준이며, 실제 동작 불일치는 아님).
