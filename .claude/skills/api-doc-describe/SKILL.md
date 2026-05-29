---
name: api-doc-describe
description: API 문서 파이프라인 2단계 절차 — 인벤토리에 사람이 읽는 한국어 설명(무엇을/언제/도메인/에러)을 붙인다. "엔드포인트 설명 작성" 시 api-doc-describer가 사용.
---

# api-doc-describe (2단계: 설명 작성)

1단계 인벤토리에 사람이 읽는 한국어 설명을 입히는 절차. `api-doc-describer`가 따른다.

## 입력
- `docs/api/.work/<tag-slug>/01-inventory.md`

## 절차
1. 인벤토리를 `Read`. 각 엔드포인트마다 `### 설명` 블록 작성:
   - 무엇을 하는가(1문장) → 언제 쓰나 → 인증 → 도메인 맥락(해당 시) → 에러 의미.
2. 인벤토리 사실(메서드/경로/스키마)은 **그대로 보존**, 설명만 추가.
3. 불확실한 동작은 창작하지 말고 `(확인 필요)` 표시.
4. `docs/api/.work/<tag-slug>/02-described.md`에 저장.

## 도메인 맥락 키워드 (해당 시에만)
단일 운영자 모델 · synthetic 운영자(`synthetic-*`) · `BidDecisionRecord` 결정 ·
predictor guardrail(낙찰하한) · pgvector 유사도 · KONEPS 수집 · Telegram 알림.

## 출력
- `docs/api/.work/<tag-slug>/02-described.md` (사실 + 설명, 한국어)
