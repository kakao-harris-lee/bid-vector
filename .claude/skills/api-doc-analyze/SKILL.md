---
name: api-doc-analyze
description: API 문서 파이프라인 1단계 절차 — FastAPI 라우터·스키마·OpenAPI를 교차 분석해 엔드포인트 인벤토리(사실)를 추출한다. "엔드포인트 분석", "API 인벤토리 추출" 시 api-doc-analyzer가 사용.
---

# api-doc-analyze (1단계: 엔드포인트 분석)

한 태그/라우터의 엔드포인트 **사실**을 구조화 추출하는 절차. `api-doc-analyzer`가 따른다.

## 입력
- 태그명 + 라우터 파일 (예: `Projects` / `app/api/projects.py`)
- `docs/api/.work/openapi.json` (없으면 생성)

## 절차
1. OpenAPI 스펙 확보:
   ```bash
   source .venv/bin/activate
   python -c "import json; from app.main import app; print(json.dumps(app.openapi(), ensure_ascii=False))" > docs/api/.work/openapi.json
   ```
   (이미 있으면 재사용)
2. 라우터 파일 정독 → 메서드·경로·의존성·파라미터·request/response 스키마·status/에러·위임 service 추출.
3. `app/schemas/`에서 참조 스키마 필드까지 펼침.
4. OpenAPI와 경로/메서드/스키마 대조, 불일치 표시.
5. `docs/api/.work/<tag-slug>/01-inventory.md`에 사실만 기록 (산문·예제 금지).

## 경로 규칙
- `<tag-slug>` = 태그 소문자·공백→하이픈 (예: `AI Predictions` → `ai-predictions`).
- 전체 경로 = `routes.py`의 include prefix + router prefix + 함수 경로. (대부분 `/api/v1` 하위)

## 출력
- `docs/api/.work/<tag-slug>/01-inventory.md` (analyzer 정의의 인벤토리 형식)
