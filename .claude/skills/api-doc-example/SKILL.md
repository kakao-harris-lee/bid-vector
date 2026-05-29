---
name: api-doc-example
description: API 문서 파이프라인 3단계 절차 — 스키마에 부합하는 사용 예제(curl·요청/응답 JSON·에러)를 생성하고 최종 docs/api/<tag>.md를 조립한다. "사용 예제 생성", "API 문서 조립" 시 api-doc-example-generator가 사용.
---

# api-doc-example (3단계: 예제 생성 + 문서 조립)

설명이 붙은 엔드포인트에 예제를 생성하고 최종 태그 문서를 조립하는 절차.
`api-doc-example-generator`가 따른다.

## 입력
- `docs/api/.work/<tag-slug>/02-described.md`

## 절차
1. 각 엔드포인트에 예제 생성:
   - **curl** (베이스 `http://localhost:3000`, 인증 시 `-H "Authorization: Bearer <TOKEN>"`)
   - **요청 JSON** — request 스키마 필수 필드 + 대표 선택 필드, 타입/제약 준수
   - **응답 200 JSON** — `response_model` 구조에 맞는 그럴듯한 가짜 값
   - **에러 1~2개** — `{"detail": "..."}` 표준 형태
2. 도메인 현실성: username `operator`/`synthetic-aggressive`, 금액 KRW 정수, 날짜 ISO8601.
3. 시크릿은 항상 플레이스홀더(`<TOKEN>`).
4. `docs/api/<tag>.md` 조립 — 목차 + 엔드포인트별(설명/파라미터 표/요청예제/응답/에러).

## 출력
- `docs/api/<tag>.md` (최종, 한국어)
- 문서 형식은 `api-doc-example-generator` 에이전트 정의의 템플릿을 따른다.

## 금지
- 스키마와 모순되는 예제, 실제 시크릿/개인정보, 소스 코드 수정.
