---
name: kotlin-service
description: Kotlin service-api vertical slice를 kotlin-builder로 구현하고 Gradle 검증 후 Codex 독립 리뷰를 반복하는 하네스. "Kotlin 서비스 구현", "Kotlin으로 이전", "Claude 구현 Codex 리뷰", "/kotlin-service" 요청 시 사용.
---

# Kotlin service implementation harness

이 스킬은 **Claude Code가 구현하고 Codex가 독립 리뷰**하는 생성-검증 루프다.
Codex 리뷰는 변경을 수정하지 않으며, Claude의 자체 점검으로 대체할 수 없다.

## 입력

- 구현할 하나의 domain vertical slice
- 보존할 Python API/동작과 고칠 regression fixture
- 허용된 module/table/event 소유권
- 예상 테스트와 cutover 단계(read-only, shadow, writer 중 하나)

입력에서 aggregate writer나 cutover 단계가 불명확하면 구현을 넓히지 않고 먼저 보고한다.

## 실행 순서

1. `git branch --show-current`, `git status --short`, 기준 SHA를 확인한다.
2. 비-trivial 작업이면 최신 `origin/main` 기반 isolated worktree/feature branch에서 한다.
3. `kotlin-builder`에게 책임 경계와 수용 기준을 전달해 구현한다.
4. 저장소 Gradle wrapper로 targeted test, architecture test, 전체 Kotlin test를 실행한다.
5. 구현을 atomic commit으로 만든 뒤 다음 gate를 실행한다.

```bash
scripts/codex-review-kotlin.sh --base origin/main
```

커밋 전 점검이 꼭 필요하면 isolated clean worktree에서만 다음을 사용한다.

```bash
scripts/codex-review-kotlin.sh --uncommitted
```

6. `reports/codex-review/kotlin-*.json`을 읽는다.
   - `approve`: 검증과 residual risk를 사용자에게 보고한다.
   - `request_changes`: blocker/high finding을 같은 branch의 `kotlin-builder`에 돌려보낸다.
7. 수정 후 Gradle 검증과 Codex review를 다시 실행한다.
8. 자동 수정-재리뷰는 최대 2회다. 그래도 `request_changes`면 보고하고 멈춘다.

## 독립성·안전 규칙

- Codex review report를 Claude가 편집하거나 finding severity를 낮추지 않는다.
- Codex에게 write sandbox나 자동 수정 권한을 주지 않는다.
- dirty main에서 `--uncommitted`를 실행하지 않는다. 다른 작업의 변경이 review scope에
  섞일 수 있기 때문이다.
- 테스트 green과 Codex `approve`가 모두 있어야 리뷰 완료로 보고한다.
- `approve`는 merge 승인이나 운영 배포 승인이 아니다. push/merge/cutover는 기존
  `CLAUDE.md` 승인 게이트를 따른다.

## 완료 보고

```text
Kotlin harness result
- branch/base/commit:
- vertical slice and writer:
- Gradle verification:
- Codex report:
- Codex verdict:
- findings fixed:
- remaining findings/risks:
- push/merge/cutover status:
```
