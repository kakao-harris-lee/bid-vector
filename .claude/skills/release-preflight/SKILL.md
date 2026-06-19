---
name: release-preflight
description: ML release manifest preflight (서명/promotion gate/백테스트 상태 확인). "릴리스 프리플라이트", "promotion gate 점검", "모델 배포 전 검증" 요청 시 사용. 인자로 manifest-ref 전달.
---

# release-preflight

ML release manifest의 promotion gate를 사전 점검한다.

> 스크립트 실행 전담이므로 `data-seed-runner` 에이전트가 이 스킬을 따라 실행할 수 있다.

## 입력

- `<manifest-ref>` — manifest 식별자(파일 경로 또는 release id)

예: `latest`, `reports/ml-releases/2026-05-20-price-v3.json`

## 실행

```bash
source .venv/bin/activate
python scripts/promote_ml_release.py preflight-rollout \
    --manifest <manifest-ref> \
    --require-signature
```

## 검증 항목 (스크립트가 보고)

- manifest signature 유효성 (`ML_RELEASE_MANIFEST_SIGNING_KEY`로 검증)
- promotion gate: pgvector 차원 호환성, 백테스트 결과 임계값, drift 검사
- canary scope 정의

## 금지

- `--require-signature`를 꺼서 통과시키는 우회
- 운영 release manifest를 dev 키로 서명
- 결과를 가지고 직접 promote (사용자 확인 후 별도 명령으로)

## 보고

- gate별 PASS/FAIL
- 실패 시 root cause 1줄
- 다음 액션 (signature 재발급 / 백테스트 재실행 / 모델 회귀)
