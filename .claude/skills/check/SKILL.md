---
name: check
description: 백엔드 pytest + 프론트엔드 vitest + 프론트엔드 build 회귀 검증 1세트. "회귀 검증", "전체 테스트 돌려줘", "머지 전 체크", "/check" 요청 시 사용.
---

# check

PR 머지 전 회귀 검증 1세트를 실행한다.

> 명령 실행·triage 전담이므로 `test-runner` 에이전트가 이 스킬을 따라 실행할 수 있다.

## 실행 명령 (순서대로)

```bash
source .venv/bin/activate && pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
```

## 실행 규칙

- 하나가 실패해도 나머지를 끝까지 실행한다 (사용자에게 전체 그림을 보여주기 위해).
- 백엔드와 프론트엔드 명령은 병렬 실행해도 좋다 (의존성 없음).
- 실행 시간이 길면 background로 돌리고 결과만 보고한다.

## 보고 양식

```
## check results

| Suite | Status | Detail |
|---|---|---|
| pytest | PASS | 412 passed in 32s |
| vitest | PASS | 27 passed in 4s |
| frontend build | PASS | dist/ generated, no TS errors |

## Failures
(none)
```

실패가 있으면 첫 5개 실패에 대해:
- 파일:라인
- 에러 타입
- 에러 메시지 첫 줄
- 의심 원인 1줄

## 금지

- 실패를 무시하거나 가리기 위한 우회 (skip, snapshot 갱신 등)
- 테스트 자체를 수정해서 통과시키기
