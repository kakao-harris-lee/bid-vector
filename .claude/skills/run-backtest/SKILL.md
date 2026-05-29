---
name: run-backtest
description: synthetic 운영자 백테스트 실행 (scripts/backtest_synthetic_operators.py). "백테스트 돌려줘", "synthetic 운영자 비교", "전략 backtest" 요청 시 사용. slug/날짜/limit 인자 선택.
---

# run-backtest

가상 운영자 백테스트를 실행한다.

> 스크립트 실행 전담이므로 `data-seed-runner` 에이전트가 이 스킬을 따라 실행할 수 있다.

## 입력 (모두 선택)

- 첫 번째 콤마 구분 토큰이 `--`로 시작하지 않으면 그것을 `--slug` 인자로 사용한다.
- 그 외 `--start-date`, `--end-date`, `--limit` 등은 그대로 전달한다.
- 인자가 비면 기본값(스크립트 default)을 사용.

예:
- (인자 없음)
- `aggressive,conservative`
- `--start-date 2025-01-01 --end-date 2025-12-31 --limit 200`
- `aggressive --start-date 2025-06-01 --end-date 2025-12-31`

## 실행

```bash
source .venv/bin/activate
python scripts/backtest_synthetic_operators.py [전달된 인자들]
```

- 시드가 비어 있으면 먼저 `seed-synthetic` 스킬을 권한다 (자동 실행하지 않음).
- 실행 시간이 길 수 있다 — Bash `run_in_background: true`로 돌리고 결과 파일
  경로(`reports/synthetic-backtests/<run_id>/comparison.{json,csv}`)를 출력한다.

## 금지

- 운영 DB URL을 사용한 실행
- 시드 스크립트 동시 실행 (충돌 가능)
- 결과 파일을 직접 git에 커밋

## 보고

- 실행한 명령 (sanitize된 형태)
- 결과 디렉토리 경로
- 사용자가 확인하면 좋은 컬럼 1–2개 (예: `win_rate_on_settled` top 3)
- `comparison.csv`의 win rate는 가격 기준 추정 낙찰임을 caveat로 표기
