# Authentication API

> 베이스 경로: `/api/v1/auth` · 인증: 이 태그의 엔드포인트는 모두 인증 토큰을 요구하지 않습니다(자격증명·서버 reset token 검증으로 처리). 베이스 URL 예시는 `http://localhost:3000`.
>
> 도메인: 단일 운영자(single-operator) 모델. 서비스 전체가 하나의 canonical operator 계정으로 동작하며, 이 태그는 그 계정의 부트스트랩·로그인·비밀번호 재설정·조회를 담당합니다.

## 목차
- [POST /api/v1/auth/bootstrap](#post-apiv1authbootstrap) — 단일 운영자 계정 최초 생성
- [POST /api/v1/auth/register](#post-apiv1authregister-deprecated) — (deprecated) bootstrap 별칭
- [POST /api/v1/auth/session](#post-apiv1authsession) — 운영자 로그인, 토큰 발급
- [POST /api/v1/auth/login](#post-apiv1authlogin-deprecated) — (deprecated) session 별칭
- [POST /api/v1/auth/password-reset](#post-apiv1authpassword-reset) — reset token으로 비밀번호 재설정 + 토큰 발급
- [GET /api/v1/auth/me](#get-apiv1authme) — 현재 운영자 프로필 조회

---

## POST /api/v1/auth/bootstrap

서비스를 처음 셋업할 때 단일 운영자 계정을 생성합니다. 이미 운영자 계정이 있으면 거부하므로 사실상 최초 1회만 성공하는 초기화 엔드포인트입니다. 계정 갱신은 별도 `/api/v1/operator/profile`에서 합니다.

- 인증: 불필요(부트스트랩 단계).
- 도메인: 단일 운영자 모델 — 운영자는 하나만 존재.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | username | string | 예 | 운영자 로그인 ID |
| body | email | string(email) | 예 | 운영자 이메일 |
| body | full_name | string | 예 | 운영자 이름 |
| body | company | string \| null | 아니오 | 회사명 |
| body | password | string | 예 | 비밀번호 |

**요청 예시**

```bash
curl -X POST http://localhost:3000/api/v1/auth/bootstrap \
  -H "Content-Type: application/json" \
  -d '{
    "username": "operator",
    "email": "operator@example.com",
    "full_name": "주운영자",
    "company": "비드벡터",
    "password": "<PASSWORD>"
  }'
```

```json
{
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "주운영자",
  "company": "비드벡터",
  "password": "<PASSWORD>"
}
```

**응답 200**

```json
{
  "id": 1,
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "주운영자",
  "company": "비드벡터",
  "is_active": true,
  "is_admin": false,
  "created_at": "2026-05-29T09:00:00"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | 이미 operator 계정 존재(중복 부트스트랩) 또는 username/email 중복 |
| 422 | 본문 필드 누락·형식 오류(예: email 형식 오류) |

---

## POST /api/v1/auth/register (deprecated)

`/bootstrap`과 완전히 동일하게 동작하는 구버전 경로입니다. 요청·응답·에러가 모두 같습니다. 신규 호출은 `/bootstrap`을 사용하세요. 이 경로는 하위호환용으로만 유지됩니다.

- 인증: 불필요.
- 도메인: 단일 운영자 부트스트랩(`/bootstrap`과 같은 핸들러).

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | (UserCreate) | object | 예 | `/bootstrap`과 동일 |

**요청 예시**

```bash
curl -X POST http://localhost:3000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "operator",
    "email": "operator@example.com",
    "full_name": "주운영자",
    "company": "비드벡터",
    "password": "<PASSWORD>"
  }'
```

**응답 200**

```json
{
  "id": 1,
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "주운영자",
  "company": "비드벡터",
  "is_active": true,
  "is_admin": false,
  "created_at": "2026-05-29T09:00:00"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | 이미 operator 계정 존재 또는 username/email 중복 |
| 422 | 본문 형식 오류 |

---

## POST /api/v1/auth/session

운영자 자격증명(username/password)을 검증하고 access·refresh 토큰을 발급하는 로그인 엔드포인트입니다. 프론트엔드 로그인 화면에서 세션을 시작할 때 호출합니다. 자격증명은 JSON 본문(`OperatorLoginRequest`)으로 보내는 것이 기본이며, 본문 없이 쿼리 파라미터 `username`/`password`로도 받을 수 있습니다(fallback).

- 인증: 불필요(이 호출이 인증을 수행). 발급된 `access_token`을 이후 요청의 `Authorization: Bearer`에 사용합니다.
- 도메인: 단일 운영자 모델 — canonical operator 계정으로 로그인.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | username | string | 예* | 운영자 ID (본문 사용 시) |
| body | password | string | 예* | 비밀번호 (본문 사용 시) |
| query | username | string \| null | 아니오 | 본문 미사용 시 fallback ID |
| query | password | string \| null | 아니오 | 본문 미사용 시 fallback 비밀번호 |

\* 본문 또는 쿼리 중 하나로 username/password가 모두 채워져야 합니다(둘 다 비면 400).

**요청 예시**

```bash
curl -X POST http://localhost:3000/api/v1/auth/session \
  -H "Content-Type: application/json" \
  -d '{
    "username": "operator",
    "password": "<PASSWORD>"
  }'
```

```json
{
  "username": "operator",
  "password": "<PASSWORD>"
}
```

**응답 200**

```json
{
  "access_token": "<ACCESS_TOKEN>",
  "refresh_token": "<REFRESH_TOKEN>",
  "token_type": "bearer",
  "operator_id": 1,
  "username": "operator"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | username 또는 password가 비어 있음(본문·쿼리 모두 없음) |
| 401 | 해당 username 없음 또는 비밀번호 불일치 |
| 403 | 계정이 비활성(`is_active=false`) |
| 422 | 본문 형식 오류 |

---

## POST /api/v1/auth/login (deprecated)

`/session`과 동일하게 동작하는 구버전 로그인 경로입니다. 요청·응답·에러가 모두 같습니다. 신규 호출은 `/session`을 사용하세요. 이 경로는 하위호환용으로만 유지됩니다.

- 인증: 불필요(토큰 발급).
- 도메인: `/session`과 같은 핸들러.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | username | string | 예* | `/session`과 동일 |
| body | password | string | 예* | `/session`과 동일 |
| query | username | string \| null | 아니오 | fallback |
| query | password | string \| null | 아니오 | fallback |

**요청 예시**

```bash
curl -X POST http://localhost:3000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "operator",
    "password": "<PASSWORD>"
  }'
```

**응답 200**

```json
{
  "access_token": "<ACCESS_TOKEN>",
  "refresh_token": "<REFRESH_TOKEN>",
  "token_type": "bearer",
  "operator_id": 1,
  "username": "operator"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | username/password 누락 |
| 401 | 자격증명 불일치 |
| 403 | 비활성 계정 |
| 422 | 본문 형식 오류 |

---

## POST /api/v1/auth/password-reset

서버에 사전 설정된 reset token이 일치할 때 운영자 비밀번호를 재설정하고, 곧바로 새 세션 토큰을 발급합니다. 로그인 비밀번호를 잊었을 때 운영자가 직접 복구하는 경로입니다. 성공 시 계정이 활성화(`is_active=true`)되고 새 access/refresh 토큰이 반환됩니다.

- 인증: 운영자 토큰 대신 서버 측 `OPERATOR_PASSWORD_RESET_TOKEN`을 상수시간(`hmac.compare_digest`)으로 비교해 인가합니다. 새 비밀번호는 최소 8자.
- 도메인: 단일 운영자 모델 — `username` 미지정 시 canonical operator를 대상으로 합니다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | username | string \| null | 아니오 | 대상 운영자 username(미지정 시 canonical operator) |
| body | reset_token | string (min_length=1) | 예 | 서버에 설정된 reset token |
| body | new_password | string (min_length=8) | 예 | 새 비밀번호(8자 이상) |

**요청 예시**

```bash
curl -X POST http://localhost:3000/api/v1/auth/password-reset \
  -H "Content-Type: application/json" \
  -d '{
    "username": "operator",
    "reset_token": "<RESET_TOKEN>",
    "new_password": "<NEW_PASSWORD>"
  }'
```

```json
{
  "username": "operator",
  "reset_token": "<RESET_TOKEN>",
  "new_password": "<NEW_PASSWORD>"
}
```

**응답 200**

```json
{
  "access_token": "<ACCESS_TOKEN>",
  "refresh_token": "<REFRESH_TOKEN>",
  "token_type": "bearer",
  "operator_id": 1,
  "username": "operator"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | reset_token 불일치 |
| 404 | 요청 `username`이 실제 operator username과 다름 |
| 422 | 본문 형식 오류(reset_token 빈 값, new_password 8자 미만 등) |
| 503 | 서버에 reset token이 설정돼 있지 않아 기능 비활성 |

> 참고: 401/404/503 분기는 OpenAPI 스펙의 명시 응답에는 노출되지 않습니다(표준 FastAPI HTTPException). 경로·메서드·스키마는 스펙과 일치합니다.

---

## GET /api/v1/auth/me

현재 단일 운영자 계정의 프로필을 반환합니다. 프론트엔드가 현재 운영자 정보를 표시하거나 세션 부트스트랩 시 호출합니다. 단일 운영자 모델 특성상 토큰 검증 없이 항상 canonical operator를 반환하며, 계정이 하나도 없으면 기본값으로 새 operator를 생성해 반환합니다.

- 인증: 코드상 토큰 의존성이 없습니다(누구나 호출하면 동일한 canonical operator를 받음). (확인 필요 — 실사용자 없는 단일 검증 환경 전제의 의도된 동작인지)
- 도메인: 단일 운영자 모델 — `ensure_operator_account`가 항상 하나의 운영자를 보장.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| — | — | — | — | 파라미터 없음 |

**요청 예시**

```bash
curl -X GET http://localhost:3000/api/v1/auth/me
```

**응답 200**

```json
{
  "id": 1,
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "주운영자",
  "company": "비드벡터",
  "is_active": true,
  "is_admin": false,
  "created_at": "2026-05-29T09:00:00"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| — | 별도 에러 분기 없음(항상 200; 계정 없으면 기본 operator 자동 생성) |
