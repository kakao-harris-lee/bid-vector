---
name: seed-synthetic
description: synthetic 운영자 시드 (scripts/seed_synthetic_operators.py). "synthetic 운영자 시드", "가상 운영자 리시드", "백테스트용 데이터 준비" 요청 시 사용. --purge 옵션 선택.
---

# seed-synthetic

12개 가상 운영자(`synthetic-*`)를 시드한다.

> 스크립트 실행 전담이므로 `data-seed-runner` 에이전트가 이 스킬을 따라 실행할 수 있다.

## 입력 (선택)

- `--purge` — 기존 synthetic 운영자를 제거한 뒤 재시드 (스크립트가 지원할 때).
  canonical `operator` 계정은 영향받지 않음.

## 실행

```bash
source .venv/bin/activate
python scripts/seed_synthetic_operators.py [--purge]
```

## 금지

- canonical `operator` 계정 삭제
- 운영 DB URL로 실행
- 시드 이후 자동 백테스트 실행 (사용자가 명시적으로 `run-backtest` 스킬을 요청할 때만)

## 보고

- 실행한 명령
- 생성/갱신/삭제된 row 수 (스크립트 stdout에서 추출)
- 다음 권고: `run-backtest` 스킬로 결과 비교 가능
