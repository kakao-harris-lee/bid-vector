# 잔여 과제 우선순위 및 실행 계획 (2026-05-13)

## 현재 검증 기준

- `pytest -q`: `156 passed, 1 skipped`
- `docker compose config --quiet`: 통과
- `docker compose --profile tasks config --quiet`: 통과

## 남은 작업

### 1순위 — 운영 배포 preflight 실환경 실행

ML release manifest 생성, signature 검증, object storage publish/apply, rollout preflight 경로는 구현되어 있다. 남은 작업은 실제 운영 credential/IAM 환경에서 preflight를 실행하고 배포 체크리스트에 결과를 반영하는 것이다.

#### 작업 범위

- 운영 `ML_RELEASE_OBJECT_STORAGE_URL`로 `preflight-rollout` 실행
- `ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE=true` 기준 signature required 모드 확인
- bucket/prefix 접근, write/delete probe, IAM 거부, credential 누락 실패 payload 점검
- 운영 배포 체크리스트에 preflight 실패 원인별 대응 절차 반영

#### 우선 검토 파일

- `app/services/ml_release.py`
- `scripts/promote_ml_release.py`
- `Makefile`
- `docs/ml-task-separation.md`
- `README.md`

#### 완료 기준

- 운영자가 실제 credential/IAM으로 rollout 전에 manifest, signature, bucket/prefix, write permission을 확인할 수 있어야 한다.
- 실패 시 `status`, `detail`, `failure_reasons`, `preflight.checks`만 보고 원인을 구분할 수 있어야 한다.
