---
name: api-doc-review
description: API 문서 파이프라인 4단계 절차 — 완성된 docs/api/<tag>.md를 OpenAPI·소스와 대조해 완성도·정확성·드리프트·시크릿·스타일을 점검한다. "API 문서 완성도 리뷰" 시 api-doc-reviewer가 사용.
---

# api-doc-review (4단계: 완성도 리뷰)

완성된 태그 문서를 OpenAPI 스펙·소스와 대조해 검수하는 절차. `api-doc-reviewer`가
따른다. **읽기 전용** — 문서를 고치지 않고 보고만 한다.

## 입력
- `docs/api/<tag>.md`
- `docs/api/.work/openapi.json`
- 필요 시 `app/api/`·`app/schemas/`

## 점검
1. **완성도**: 스펙의 해당 태그 엔드포인트가 전부 문서화됐는가? 각 섹션(설명/파라미터/요청/응답/에러) 완비?
2. **정확성/드리프트**: 경로·메서드·파라미터·스키마가 스펙과 일치? 예제 JSON이 스키마에 부합?
3. **안전/일관성**: 실제 시크릿/개인정보 없음? 한국어 단일·헤딩 구조 일관? 베이스 경로/앵커 정확?

## 출력 (보고)
- Verdict: APPROVE | APPROVE WITH NITS | REQUEST CHANGES
- 누락 엔드포인트 목록
- 드리프트/부정확 + **재작업 단계 지정**(analyzer/describer/example-generator)
- 안전/일관성 이슈

## 재위임 규칙
- 사실 누락/오류 → analyzer
- 설명 부족/오해 → describer
- 예제 스키마 불일치 → example-generator
