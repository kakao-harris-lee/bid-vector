---
name: smoke-evidence
description: production smoke test를 읽기 전용으로 실행하고 증적 JSON을 reports/에 남긴다. "스모크 돌려줘", "운영 증적 수집", "smoke evidence", "검증 스냅샷" 요청 시 사용. base-url/일자 인자 선택.
---

# smoke-evidence

운영 가능성 검증용 production smoke test를 **읽기 전용 기본**으로 실행하고 증적을
`reports/`에 남긴다. 현 단계(G-3 관찰·측정)의 일상 증적 도구다.

> 검증 스크립트 실행 전담이므로 `evidence-runner` 에이전트가 이 스킬을 따라 실행한다.

## 입력 (모두 선택)

- `--base-url` (기본 `http://localhost:3000`)
- `--days`, `--recent-limit` 등 스코프 조절 인자
- 부하를 줄이려면 `--skip-crawl` / `--skip-monitor`
- 증적 경로 `--evidence-out` (미지정 시 `reports/smoke/<date>-smoke.json` 권장)

## 실행 (읽기 전용 기본)

```bash
source .venv/bin/activate
mkdir -p reports/smoke
python scripts/production_smoke_test.py \
    --base-url http://localhost:3000 \
    --evidence-out reports/smoke/$(date +%F)-smoke.json
```

- 기본은 **읽기 전용**: `--write` / `--telegram-sync` / `--execution-mode live`를
  붙이지 않는다 (각각 DB write / Telegram 외부 송신 / 실제 KONEPS 호출).
- 위 세 가지가 필요하면 **사용자 승인 후에만** 추가한다 (CLAUDE.md §0).
- 실행이 길면 `run_in_background: true`로 돌리고 증적 경로만 먼저 보고한다.

## 관련 증적 (필요 시)

- 운영자별 G-2 증적 스냅샷: `scripts/collect_g2_evidence.py`
  (`docs/operations/g2-evidence-runbook.md` 절차)
- synthetic 증적 계획/실행: `scripts/run_g2_synthetic_evidence.py` (기본 `--dry-run`)

## 금지

- 승인 없이 `--write` / `--telegram-sync` / `--execution-mode live`
- 증적 JSON을 git에 직접 커밋
- smoke 실패를 가리거나 우회 (assertion 무시, 실패 stdout 숨기기)
- 증적에 시크릿/토큰/사업자 개인정보 노출

## 보고

- 실행 명령(sanitize) + mode(read-only / 승인된 쓰기 여부)
- check PASS/WARN/FAIL 카운트
- 증적 파일 경로(`reports/smoke/...`)
- WARN/FAIL 시 의심 원인 1줄 + 후속 위임 권고(oracle/ml-reviewer 등)
