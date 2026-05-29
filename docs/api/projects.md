# Projects API

> 베이스 경로: `/api/v1/projects` · 인증: 불필요 (이 태그의 모든 엔드포인트는 인증 토큰 없이 호출)
> 베이스 URL 예시: `http://localhost:3000`
> 도메인: "Project"는 KONEPS(나라장터) 공고를 시스템 내부에서 다루는 단위다. 공고 텍스트는 `paraphrase-multilingual-MiniLM-L12-v2`(384차원) 임베딩으로 pgvector에 저장되어 유사 공고 검색에 쓰인다.

## 목차
- [POST /api/v1/projects/](#post-apiv1projects) — 공고 생성(임베딩 동반)
- [GET /api/v1/projects/](#get-apiv1projects) — 공고 목록 조회(필터/페이지네이션)
- [POST /api/v1/projects/embeddings/rebuild](#post-apiv1projectsembeddingsrebuild-deprecated) — 임베딩 배치 재계산(**deprecated**)
- [POST /api/v1/projects/embeddings/rebuild/async](#post-apiv1projectsembeddingsrebuildasync) — 임베딩 배치 재계산(권장)
- [GET /api/v1/projects/embeddings/rebuild/tasks/{task_id}](#get-apiv1projectsembeddingsrebuildtaskstask_id) — 배치 태스크 상태 조회
- [GET /api/v1/projects/{project_id}](#get-apiv1projectsproject_id) — 공고 상세 조회
- [GET /api/v1/projects/{project_id}/similar](#get-apiv1projectsproject_idsimilar) — 유사 공고 검색
- [POST /api/v1/projects/{project_id}/embedding/refresh](#post-apiv1projectsproject_idembeddingrefresh) — 단일 공고 임베딩 재생성
- [PUT /api/v1/projects/{project_id}](#put-apiv1projectsproject_id) — 공고 수정(부분 갱신)

---

## POST /api/v1/projects/

새 공고(Project)를 생성한다. 저장 직전 semantic 임베딩을 만들어 pgvector에 적재한 뒤 커밋하므로, 생성 직후 바로 유사도 검색 대상에 포함된다. 수집 파이프라인이 아닌 경로로 공고를 수동 등록할 때 사용한다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | title | string | 예 | 공고 제목 |
| body | description | string | 예 | 공고 설명 |
| body | requirements | string | 예 | 요구사항 |
| body | budget_estimate | number | 예 | 추정가(KRW) |
| body | category | string | 예 | 카테고리 |
| body | notice_number | string\|null | 아니오 | 공고번호 |
| body | source_url | string\|null | 아니오 | 원본 URL |
| body | issuing_agency | string\|null | 아니오 | 발주 기관 |
| body | demand_agency | string\|null | 아니오 | 수요 기관 |
| body | budget_min | number\|null | 아니오 | 예산 하한 |
| body | budget_max | number\|null | 아니오 | 예산 상한 |
| body | deadline | string(date-time)\|null | 아니오 | 마감 일시(ISO8601) |

**요청 예시**

```bash
curl -X POST "http://localhost:3000/api/v1/projects/" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "교내 통합 보안관제 시스템 구축",
    "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
    "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
    "budget_estimate": 480000000,
    "category": "정보통신",
    "notice_number": "20250529-00123",
    "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
    "issuing_agency": "한국대학교",
    "demand_agency": "한국대학교 정보화본부",
    "budget_min": 430000000,
    "budget_max": 480000000,
    "deadline": "2025-06-20T18:00:00"
  }'
```

**응답 200**

```json
{
  "id": 1024,
  "status": "open",
  "created_at": "2025-05-29T09:15:00",
  "title": "교내 통합 보안관제 시스템 구축",
  "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
  "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
  "budget_estimate": 480000000,
  "category": "정보통신",
  "notice_number": "20250529-00123",
  "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
  "issuing_agency": "한국대학교",
  "demand_agency": "한국대학교 정보화본부"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 필수 필드 누락 또는 타입 위반 |

---

## GET /api/v1/projects/

공고 목록을 텍스트·기관·카테고리·상태·예산 범위로 필터링해 조회한다. 생성일 내림차순(동률 시 id 내림차순) 정렬. 응답 본문은 배열을 유지하고, 페이지네이션용 전체 매칭 건수는 `X-Total-Count` 응답 헤더로 노출한다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | skip | integer (ge 0) | 아니오 | 오프셋(기본 0) |
| query | limit | integer (1–200) | 아니오 | 페이지 크기(기본 100) |
| query | category | string | 아니오 | 카테고리 정확 일치 |
| query | status | string | 아니오 | 상태 정확 일치 |
| query | q | string | 아니오 | 제목/공고번호 부분일치(LIKE) |
| query | agency | string | 아니오 | 발주/수요 기관명 부분일치 |
| query | budget_min | number (ge 0) | 아니오 | budget_estimate >= 값 |
| query | budget_max | number (ge 0) | 아니오 | budget_estimate <= 값 |

> 응답 헤더 `X-Total-Count`: 필터 적용 후 전체 매칭 건수. (코드에서 항상 설정 — OpenAPI 스펙에는 미문서화)

**요청 예시**

```bash
curl -X GET "http://localhost:3000/api/v1/projects/?q=보안관제&category=정보통신&budget_min=400000000&skip=0&limit=20"
```

**응답 200**

```
X-Total-Count: 37
```
```json
[
  {
    "id": 1024,
    "status": "open",
    "created_at": "2025-05-29T09:15:00",
    "title": "교내 통합 보안관제 시스템 구축",
    "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
    "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
    "budget_estimate": 480000000,
    "category": "정보통신",
    "notice_number": "20250529-00123",
    "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
    "issuing_agency": "한국대학교",
    "demand_agency": "한국대학교 정보화본부"
  }
]
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 쿼리 제약 위반(limit 200 초과, 음수 등) |

---

## POST /api/v1/projects/embeddings/rebuild (deprecated)

> **Deprecated** — 신규 호출은 `/embeddings/rebuild/async`를 사용.

다수 공고의 semantic 임베딩을 백필하는 배치 작업을 큐에 등록하고, 폴링 가능한 task 정보를 즉시 반환한다. API 요청 안에서 임베딩을 직접 계산하지 않으므로 요청이 빠르게 끝난다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer (1–1000) | 아니오 | 처리 상한(기본 100) |
| query | offset | integer (ge 0) | 아니오 | 시작 오프셋(기본 0) |
| query | category | string | 아니오 | 카테고리 필터 |
| query | project_status | string | 아니오 | 상태 필터 |
| query | force | boolean | 아니오 | 기존 임베딩도 재계산(기본 false) |

**요청 예시**

```bash
curl -X POST "http://localhost:3000/api/v1/projects/embeddings/rebuild?limit=200&offset=0&force=false"
```

**응답 202**

```json
{
  "task_id": "9f2c7a1e-3b44-4e8d-bb21-2a5c9d0f1e77",
  "task_name": "rebuild_project_embeddings",
  "status": "queued",
  "detail": "임베딩 재계산 작업이 큐에 등록되었습니다.",
  "poll_url": "/api/v1/projects/embeddings/rebuild/tasks/9f2c7a1e-3b44-4e8d-bb21-2a5c9d0f1e77"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 쿼리 제약 위반 |

---

## POST /api/v1/projects/embeddings/rebuild/async

배치 임베딩 재계산 작업을 큐에 등록하고 폴링 가능한 task_id를 반환한다(정식 권장 경로). 응답의 `poll_url`로 진행 상태를 조회한다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer (1–1000) | 아니오 | 처리 상한(기본 100) |
| query | offset | integer (ge 0) | 아니오 | 시작 오프셋(기본 0) |
| query | category | string | 아니오 | 카테고리 필터 |
| query | project_status | string | 아니오 | 상태 필터 |
| query | force | boolean | 아니오 | 기존 임베딩도 재계산(기본 false) |

**요청 예시**

```bash
curl -X POST "http://localhost:3000/api/v1/projects/embeddings/rebuild/async?limit=500&force=true"
```

**응답 202**

```json
{
  "task_id": "1b7d9e0c-5a22-4f31-9c8a-7e63b4d2f019",
  "task_name": "rebuild_project_embeddings",
  "status": "queued",
  "detail": "임베딩 재계산 작업이 큐에 등록되었습니다.",
  "poll_url": "/api/v1/projects/embeddings/rebuild/tasks/1b7d9e0c-5a22-4f31-9c8a-7e63b4d2f019"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 쿼리 제약 위반 |

---

## GET /api/v1/projects/embeddings/rebuild/tasks/{task_id}

큐에 등록된 임베딩 재계산 태스크의 현재 상태와 결과를 조회한다. rebuild 계열이 반환한 task_id를 폴링하는 용도다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | 폴링 대상 태스크 id |

**요청 예시**

```bash
curl -X GET "http://localhost:3000/api/v1/projects/embeddings/rebuild/tasks/1b7d9e0c-5a22-4f31-9c8a-7e63b4d2f019"
```

**응답 200**

```json
{
  "task_id": "1b7d9e0c-5a22-4f31-9c8a-7e63b4d2f019",
  "task_name": "rebuild_project_embeddings",
  "status": "completed",
  "raw_status": "SUCCESS",
  "ready": true,
  "successful": true,
  "detail": "임베딩 재계산이 완료되었습니다.",
  "error": null,
  "result": {
    "processed_count": 2,
    "limit": 500,
    "offset": 0,
    "category": null,
    "project_status": null,
    "force": true,
    "vector_storage_enabled": true,
    "project_ids": [1024, 1025],
    "results": [
      {
        "project_id": 1024,
        "title": "교내 통합 보안관제 시스템 구축",
        "category": "정보통신",
        "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
        "semantic_text_length": 184,
        "embedding_dimensions": 384,
        "embedding_updated_at": "2025-05-29T09:20:00",
        "vector_storage_enabled": true,
        "vector_persisted": true
      }
    ]
  }
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | 경로 파라미터 검증 실패 |

---

## GET /api/v1/projects/{project_id}

단일 공고의 상세를 조회한다. 공고 상세 화면 진입 등에서 사용한다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | project_id | integer | 예 | 공고 id |

**요청 예시**

```bash
curl -X GET "http://localhost:3000/api/v1/projects/1024"
```

**응답 200**

```json
{
  "id": 1024,
  "status": "open",
  "created_at": "2025-05-29T09:15:00",
  "title": "교내 통합 보안관제 시스템 구축",
  "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
  "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
  "budget_estimate": 480000000,
  "category": "정보통신",
  "notice_number": "20250529-00123",
  "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
  "issuing_agency": "한국대학교",
  "demand_agency": "한국대학교 정보화본부"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 해당 id의 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | project_id가 정수가 아님 |

---

## GET /api/v1/projects/{project_id}/similar

주어진 공고와 의미적으로 유사한 공고들을 pgvector 임베딩 유사도로 검색한다. pgvector 사용 가능 시 `postgres_vector`, 아니면 `python_fallback` 모드로 동작한다. 특정 공고와 비슷한 과거/현재 공고를 참고가·경쟁 양상 추정에 활용할 때 쓴다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | project_id | integer | 예 | 기준 공고 id |
| query | limit | integer (1–20) | 아니오 | 최대 결과 수(기본 5) |
| query | min_similarity | number (0.0–1.0) | 아니오 | 최소 유사도(기본 0.15) |
| query | same_category_only | boolean | 아니오 | 동일 카테고리만(기본 true) |

**요청 예시**

```bash
curl -X GET "http://localhost:3000/api/v1/projects/1024/similar?limit=5&min_similarity=0.2&same_category_only=true"
```

**응답 200**

```json
{
  "target_project_id": 1024,
  "target_project_title": "교내 통합 보안관제 시스템 구축",
  "target_embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
  "search_mode": "postgres_vector",
  "same_category_only": true,
  "min_similarity": 0.2,
  "result_count": 2,
  "results": [
    {
      "project_id": 1011,
      "title": "공공기관 통합 보안관제센터 운영 용역",
      "category": "정보통신",
      "status": "open",
      "budget_estimate": 510000000,
      "deadline": "2025-06-10T18:00:00",
      "created_at": "2025-05-21T11:00:00",
      "similarity_score": 0.83,
      "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2"
    },
    {
      "project_id": 987,
      "title": "SIEM 기반 보안 모니터링 시스템 고도화",
      "category": "정보통신",
      "status": "closed",
      "budget_estimate": 320000000,
      "deadline": null,
      "created_at": "2025-04-30T15:30:00",
      "similarity_score": 0.61,
      "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2"
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 기준 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 쿼리 제약 위반(limit 20 초과, min_similarity 범위 밖 등) |

---

## POST /api/v1/projects/{project_id}/embedding/refresh

단일 공고의 semantic 임베딩을 재생성하고 최신 벡터 메타데이터를 영속화한 뒤 결과를 반환한다. 공고 텍스트가 갱신됐거나 임베딩이 비어 있어 즉시 한 건만 재계산하고 싶을 때 사용한다. 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | project_id | integer | 예 | 공고 id |
| query | force | boolean | 아니오 | 캐시된 벡터가 있어도 재계산(기본 false) |

**요청 예시**

```bash
curl -X POST "http://localhost:3000/api/v1/projects/1024/embedding/refresh?force=true"
```

**응답 200**

```json
{
  "project_id": 1024,
  "title": "교내 통합 보안관제 시스템 구축",
  "category": "정보통신",
  "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
  "semantic_text_length": 184,
  "embedding_dimensions": 384,
  "embedding_updated_at": "2025-05-29T09:25:00",
  "vector_storage_enabled": true,
  "vector_persisted": true
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 대상 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 경로/쿼리 검증 실패 |

---

## PUT /api/v1/projects/{project_id}

기존 공고를 수정한다. 요청에 포함된 필드만 갱신하며(`exclude_unset`), 수정 후 임베딩을 강제 재생성해 유사도 검색이 최신 내용을 반영하도록 한다. 본문 스키마가 `ProjectCreate`라 OpenAPI상 모든 필드가 표시되지만 실제로는 전달된 필드만 반영된다(부분 갱신). 인증 불필요.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | project_id | integer | 예 | 공고 id |
| body | (ProjectCreate 필드) | — | 아니오 | 갱신할 필드만 전달 |

**요청 예시**

```bash
curl -X PUT "http://localhost:3000/api/v1/projects/1024" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "교내 통합 보안관제 시스템 구축(2차)",
    "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
    "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
    "budget_estimate": 500000000,
    "category": "정보통신"
  }'
```

**응답 200**

```json
{
  "id": 1024,
  "status": "open",
  "created_at": "2025-05-29T09:15:00",
  "title": "교내 통합 보안관제 시스템 구축(2차)",
  "description": "캠퍼스 네트워크 통합 보안관제 및 SIEM 구축 용역",
  "requirements": "ISMS-P 인증 경험, 24x7 관제 인력 상주",
  "budget_estimate": 500000000,
  "category": "정보통신",
  "notice_number": "20250529-00123",
  "source_url": "https://www.g2b.go.kr/notice/20250529-00123",
  "issuing_agency": "한국대학교",
  "demand_agency": "한국대학교 정보화본부"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 대상 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 본문 타입 위반 |
