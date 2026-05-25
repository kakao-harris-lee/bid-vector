---
description: 새 백엔드 API 4종(schema/route/service/test) 스캐폴드 + routes.py 등록
argument-hint: "<name>"
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# /api-route

새 FastAPI 라우트를 4종 세트(schema + route + service + test)로 스캐폴드하고
`app/api/routes.py`에 등록한다.

## 사용

```
/api-route synthetic
/api-route operator-strategy-runs
```

인자:
1. `<name>` — 모듈명(snake_case 또는 kebab-case). 다음 파일이 만들어진다:
   - `app/api/<name>.py`
   - `app/schemas/<name>.py`
   - `app/services/<name>.py` (이미 있으면 건드리지 않음)
   - `tests/test_<name>.py`

## 작업 절차

1. 이미 같은 이름의 파일이 있으면 **덮어쓰지 말고** 사용자에게 보고한다.
2. `app/schemas/<name>.py`:
   - Pydantic v2 (`BaseModel` + `ConfigDict(from_attributes=True)`)
   - 요청/응답 한 쌍 placeholder
3. `app/services/<name>.py`:
   - `class <Name>Service:` 골격
   - DB 세션 의존성 주입 패턴 (`__init__(self, db: Session)`)
4. `app/api/<name>.py`:
   - `router = APIRouter(prefix="/<name>", tags=["<name>"])`
   - 의존성: `get_current_operator` (인증 필요 시), `get_db`
   - 1개 GET placeholder 엔드포인트
5. `app/api/routes.py`에 `from . import <name>` + `api_router.include_router(<name>.router, prefix="/api/v1")` 추가.
6. `tests/test_<name>.py`:
   - `pytest.fixture` 기반 TestClient 사용
   - 401 경로 1개 + 정상 경로 1개 placeholder

## 검증

- `python -m py_compile app/api/<name>.py app/schemas/<name>.py app/services/<name>.py`
- `pytest -q tests/test_<name>.py` (placeholder가 통과하는 최소 형태)

## 금지

- 운영자 인증 우회
- 시크릿 하드코딩
- `app/main.py`에 라우터 직접 등록
- 기존 같은 이름 파일 덮어쓰기

## 보고

- 생성/수정된 파일 목록
- 등록된 prefix
- 사용자가 다음에 채울 부분 (스키마 필드, 비즈니스 로직, 추가 테스트)
