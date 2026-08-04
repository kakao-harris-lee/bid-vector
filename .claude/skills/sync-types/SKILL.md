---
name: sync-types
description: 백엔드 OpenAPI 스키마를 frontend/src/shared/types/openapi.d.ts로 동기화. "타입 동기화", "openapi.d.ts 갱신", "백엔드 API 타입 프론트에 반영" 요청 시 사용. 백엔드 API 변경 후 필수.
---

# sync-types

백엔드 OpenAPI 스키마로부터 프론트엔드 타입을 생성한다. 생성은 **반드시
`scripts/sync_openapi_types.py` 경유**로 한다 — 스크립트가 `app.openapi()` 로드,
스키마 정규화(형태 미선언 객체의 `additionalProperties` 주입 — 생성 환경 간
플립플롭 차단), openapi-typescript 실행까지 담당한다. raw `/openapi.json`을
openapi-typescript에 직접 넣으면 정규화를 우회해 커밋된 산출물과 다른 타입이
나오고 `--check`가 실패한다.

> 백엔드 API 스키마 변경 직후 실행한다. 생성 파일은 `frontend/` 영역이므로
> `frontend-builder` 또는 메인 에이전트가 실행한다.

## 실행 흐름

1. `frontend/node_modules/.bin/openapi-typescript`가 없으면:

   ```bash
   npm --prefix frontend install --save-dev openapi-typescript
   ```

2. 타입 생성 (핀 고정된 `.venv` 인터프리터 사용 — 시스템 python은 pydantic
   버전이 달라 다른 산출을 만든다):

   ```bash
   source .venv/bin/activate
   python scripts/sync_openapi_types.py          # 생성
   python scripts/sync_openapi_types.py --check  # 드리프트 검증만
   ```

   (동등한 npm 래퍼: `npm --prefix frontend run sync-types` / `run check:sync-types`)

3. diff 확인:

   ```bash
   git diff frontend/src/shared/types/openapi.d.ts | head -200
   ```

4. 변경이 있으면 사용자에게 보고. 빌드/테스트 회귀 여부 확인:

   ```bash
   npm --prefix frontend run build
   ```

## 금지

- 생성된 `openapi.d.ts`를 손으로 수정 (보조 타입은 `shared/types/<domain>.ts`에 분리)
- `scripts/sync_openapi_types.py`를 우회한 직접 생성 (curl → openapi-typescript 직행)
- 운영 환경 OpenAPI 호출

## 보고

- 사용한 소스 (서버 / `app.openapi()`)
- 변경된 라인 수
- 빌드 결과
- 호환 깨짐 의심 시 그 위치
