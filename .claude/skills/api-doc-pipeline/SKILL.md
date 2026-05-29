---
name: api-doc-pipeline
description: API 문서 자동 생성 하네스 오케스트레이터 — FastAPI 엔드포인트를 태그별로 분석→설명→예제→완성도 리뷰 파이프라인으로 처리해 docs/api/<tag>.md를 생성한다. "API 문서 생성해줘", "API 레퍼런스 만들어줘", "엔드포인트 문서화" 요청 시 사용.
---

# API Doc Pipeline Orchestrator

bid-vector의 FastAPI 엔드포인트로부터 사람이 읽는 한국어 API 레퍼런스
(`docs/api/<tag>.md`)를 자동 생성하는 4단계 파이프라인 하네스.

**아키텍처**: 태그 간 **팬아웃**(13개 라우터 독립) × 태그 내 **파이프라인**
(분석→설명→예제) + 마지막 **생성-검증**(리뷰 → 재위임).

## 팀 구성

| 단계 | 에이전트 | 스킬 | 권한 | 산출물 |
|---|---|---|---|---|
| 1 분석 | `api-doc-analyzer` | `api-doc-analyze` | Read+Write(.work만) | `.work/<tag>/01-inventory.md` |
| 2 설명 | `api-doc-describer` | `api-doc-describe` | Read/Write/Edit(.work) | `.work/<tag>/02-described.md` |
| 3 예제 | `api-doc-example-generator` | `api-doc-example` | Read/Write/Edit | `docs/api/<tag>.md` |
| 4 리뷰 | `api-doc-reviewer` | `api-doc-review` | **Read 전용** | 리뷰 보고(재위임) |

## 워크플로우

### Phase 0 — 준비 (오케스트레이터가 직접)
1. `docs/api/` 및 `docs/api/.work/` 생성. `.work/`는 중간 산출물 — `.gitignore`에 추가.
2. OpenAPI 스펙 1회 덤프:
   ```bash
   source .venv/bin/activate
   python -c "import json; from app.main import app; print(json.dumps(app.openapi(), ensure_ascii=False))" > docs/api/.work/openapi.json
   ```
3. `app/api/routes.py`에서 태그 ↔ 라우터 ↔ prefix 매핑을 읽어 작업 목록을 만든다.
   현재 태그: Authentication, Backtests, Dashboard, Operator, Projects, Bids,
   AI Predictions, ML Jobs, Analytics, Legacy Admin, Operations, Realtime, Synthetic.

### Phase 1~3 — 태그별 파이프라인 (팬아웃)
각 태그를 **독립적으로** 파이프라인 처리. 태그끼리 병렬 가능, 한 태그 안은 순차:
```
api-doc-analyzer(태그) → api-doc-describer → api-doc-example-generator → docs/api/<tag>.md
```
- 여러 태그를 동시에 돌리려면 단계별로 묶어 병렬 위임(같은 단계 여러 태그 동시).
- 각 위임 프롬프트에 **대상 태그·라우터 파일·이전 단계 산출물 경로**를 명시한다.

### Phase 4 — 완성도 리뷰 (생성-검증)
- 각 `docs/api/<tag>.md`에 `api-doc-reviewer` 위임.
- Verdict가 REQUEST CHANGES면 리뷰가 지정한 단계(analyzer/describer/example-generator)로
  **재위임**하고 다시 리뷰. APPROVE까지 반복(루프-until-clean).

### Phase 5 — 인덱스 조립 (오케스트레이터가 직접)
- `docs/api/index.md` 생성: 태그 표(태그·설명·엔드포인트 수·링크) + 공통 안내
  (베이스 경로 `/api/v1`, 인증 방식, 에러 형식).

## 데이터 흐름 원칙

- **사실 → 설명 → 예제 단방향.** 뒷 단계는 앞 단계 사실을 바꾸지 않는다. 사실 오류는
  리뷰를 통해 analyzer로 되돌린다.
- **OpenAPI가 진실의 원천.** 문서는 스펙과 드리프트되면 안 된다. 코드 변경 후 재생성 시
  Phase 0의 스펙 덤프부터 다시.
- **리뷰어는 수정하지 않는다.** 문제는 단계 지정과 함께 빌더 단계로 환류.
- **소스 코드 불가침.** 어떤 단계도 `app/`·`frontend/`를 수정하지 않는다(읽기만).
  쓰기는 `docs/api/` 및 `docs/api/.work/`로 한정.

## 안전

- 예제·문서에 실제 시크릿/토큰/개인정보 금지 — 항상 플레이스홀더.
- 한국어 단일(ko). 영어/기타 로케일 번역본 생성 금지.
- 이 하네스는 **문서 생성 전용** — 라우터/스키마/서비스 코드를 바꾸지 않는다.
  코드 변경이 필요하면 `bid-vector-orchestrator` 쪽 `backend-builder`로 분리한다.

## 위임 프롬프트 규칙
각 단계 위임 시 포함: (1) 대상 태그/라우터, (2) 사용할 스킬 이름, (3) 입력 파일 경로,
(4) 출력 파일 경로, (5) 소스 수정 금지·시크릿 금지, (6) 완료 보고 양식.
