# 최근 로드맵 병렬 작업 기록

시작 기준 커밋: `ab5a6f5`
완료 기준 커밋: `06eeee5`
완료일: 2026-06-19

이 문서는 `docs/roadmap.md`의 다음 우선순위를 여러 에이전트가 동시에 처리했던 작업 기록입니다. 현재 기준의 단계 판단과 다음 우선순위는 `docs/roadmap.md`를 우선합니다.

## 현재 결론

`06eeee5` 병합으로 G-2 진입에 필요한 API 계약 정리, 1차 알림 routing 격리, sample-gap 실행 후보 생성, operations evidence operator scope 보강이 `main`에 반영되었습니다.

아직 G-2 exit gate를 통과한 것은 아닙니다. 다음 병목은 3개 이상 가상 사업자에 대해 독립 ID/사업자 정보/전략/알림/증적이 실제 운영처럼 분리되는지 N일 단위로 확인하는 것입니다.

## 완료된 병렬 작업

| 에이전트 | 병합 커밋 | 결과 | 남은 gap |
|---|---|---|---|
| A. G-2 API 계약/문서 동기화 | `e26a1e9` | `docs/api/operator.md`, `docs/api/analytics.md`, `docs/api/synthetic.md`, `docs/api/index.md`가 operator target context, decision experiment, sample-gap/candidates API를 반영 | OpenAPI 타입 생성/문서 자동화는 API 변경 시 계속 확인 |
| B. G-2 알림 routing 격리 | `531761b` | synthetic/non-canonical Telegram 실제 송신을 dry-run evidence로 남기고 callback owner 검증을 강화 | 사업자별 실제 Telegram chat/channel 또는 app device 매핑은 미구현 |
| C. G-1 sample-gap 실행 후보 연결 | `1eca851` | `/api/v1/synthetic/experiments/sample-gaps/candidates`와 프론트 후보 선택/저장 흐름 추가 | 실제 preset 실행, DB backfill/write, settled sample 축적은 별도 승인 후 진행 |
| D. G-2 operations evidence isolation | `51dcd3f` | smoke/analytics evidence에 `operator_scope`, `current_operator_id`, `source_run_type`, `source_run_id`를 남김 | N일 운영 증적 축적과 사업자별 dashboard 검증이 필요 |

## 통합 검증 결과

통합 브랜치 `integration/roadmap-next`에서 다음 검증 후 `main`에 병합했습니다.

- `python -m py_compile` 대상 API 모듈 통과
- `git diff --check origin/main..HEAD` 통과
- backend 선택 테스트 178개 통과
- frontend 테스트 12개 통과
- `npm --prefix frontend run build` 통과

`npm ci --legacy-peer-deps` 실행 시 기존 audit 경고 3건이 확인되었습니다. 이 문서의 작업 범위에서는 새 취약점 조치가 아니라 기존 의존성 상태 확인으로 남겼습니다.

## 후속 작업 후보

다음 병렬 작업 계획을 새로 작성할 때는 아래 gap을 기준으로 분리합니다.

1. G-2 운영 증적 축적: 3개 이상 가상 사업자별 scheduled smoke, strategy monitor, decision experiment, synthetic experiment 결과를 N일 단위로 저장하고 dashboard에서 구분한다.
2. G-2 알림 대상 매핑: 사업자별 Telegram chat/channel 또는 app notification 대상 식별자 모델과 마이그레이션, 송신 정책, masking/logging 규칙을 설계한다.
3. G-1 표본 실행: sample-gap candidates를 실제 synthetic preset run과 settled sample 증적으로 연결한다. DB write/backfill은 사용자 승인 후 수행한다.
4. G-2 사용자/관리자 정보 구조 분리: 사용자 화면은 공고 추천/투찰 선택/결과 확인, 관리자 화면은 backtest/smoke/statistics/data 상태로 역할을 분리한다.
5. G-0 관찰: canonical scheduled smoke 핵심 phase green을 7일 이상 확보하고 실패 원인을 credential, KONEPS 응답, 후보 없음, Telegram, task/broker로 분류한다.

## 다음 병렬 작업 작성 규칙

새 병렬 작업을 시작할 때는 이 파일을 그대로 재사용하지 말고 `docs/roadmap.md`의 최신 "다음 우선순위"에서 새 작업 지시서를 작성합니다.

공통 규칙:

1. 시작 전에 `git fetch origin`, `git status --short --branch`, `git worktree list`를 확인한다.
2. 각 에이전트는 독립 worktree와 독립 브랜치를 사용한다.
3. 서로의 worktree나 브랜치를 수정하지 않는다.
4. DB write, 실제 KONEPS 호출, 실제 Telegram 송신, 원격 push/merge는 사용자 승인 없이 하지 않는다.
5. 테스트 통과 후에도 diff를 읽어 리뷰한 뒤에만 merge 후보로 둔다.
6. 리뷰 결과와 잔여 리스크를 보고하고 사용자 승인 후 main merge를 진행한다.

권장 생성 명령:

```bash
git fetch origin
git worktree add ../bid-vector-<slug> -b <type>/<slug> origin/main
```
