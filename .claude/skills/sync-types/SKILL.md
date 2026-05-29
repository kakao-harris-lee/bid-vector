---
name: sync-types
description: 백엔드 OpenAPI 스키마를 frontend/src/shared/types/openapi.d.ts로 동기화. "타입 동기화", "openapi.d.ts 갱신", "백엔드 API 타입 프론트에 반영" 요청 시 사용. 백엔드 API 변경 후 필수.
---

# sync-types

백엔드 OpenAPI(`/openapi.json`)로부터 프론트엔드 타입을 생성한다.

> 백엔드 API 스키마 변경 직후 실행한다. 생성 파일은 `frontend/` 영역이므로
> `frontend-builder` 또는 메인 에이전트가 실행한다.

## 실행 흐름

1. 가능하면 백엔드를 백그라운드로 띄우고 `curl -s http://localhost:3000/openapi.json > /tmp/openapi.json`.
   이미 띄워져 있으면 그 인스턴스를 사용한다.
   백엔드가 띄워져 있지 않고 띄우는 게 부담스러우면 다음 fallback을 사용한다:

   ```bash
   source .venv/bin/activate
   python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" > /tmp/openapi.json
   ```

2. `frontend/node_modules/.bin/openapi-typescript`가 없으면:

   ```bash
   npm --prefix frontend install --save-dev openapi-typescript
   ```

3. 타입 생성:

   ```bash
   npx --prefix frontend openapi-typescript /tmp/openapi.json \
       -o frontend/src/shared/types/openapi.d.ts
   ```

4. diff 확인:

   ```bash
   git diff frontend/src/shared/types/openapi.d.ts | head -200
   ```

5. 변경이 있으면 사용자에게 보고. 빌드/테스트 회귀 여부 확인:

   ```bash
   npm --prefix frontend run build
   ```

## 금지

- 생성된 `openapi.d.ts`를 손으로 수정 (보조 타입은 `shared/types/<domain>.ts`에 분리)
- 운영 환경 OpenAPI 호출

## 보고

- 사용한 소스 (서버 / `app.openapi()`)
- 변경된 라인 수
- 빌드 결과
- 호환 깨짐 의심 시 그 위치
