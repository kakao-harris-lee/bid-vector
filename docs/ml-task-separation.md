# ML 작업 분리 설계

## 목표

백필, 학습, 재평가 작업은 API request/response 경로에서 실행하지 않는다. API는 작업 파라미터를 검증하고 Celery task id를 반환하는 역할만 담당하며, 실제 실행은 RabbitMQ 기반 worker가 담당한다.

## 큐와 워커

| Queue | Worker | Docker target | 용도 |
| --- | --- | --- | --- |
| `CELERY_OPS_QUEUE` | `worker` | `api-runtime` | crawl, Telegram polling, notification, reconciler |
| `CELERY_ML_INFERENCE_QUEUE` | `inference-worker` | `api-ml-full` | semantic-input embedding, similarity projection, strategy monitoring, preview snapshot recompute |
| `CELERY_ML_BACKFILL_QUEUE` | `ml-worker` | `api-ml-full` | project embedding backfill |
| `CELERY_ML_REEVALUATION_QUEUE` | `ml-worker` | `api-ml-full` | decision experiment re-evaluation |
| `CELERY_ML_TRAINING_QUEUE` | `training-worker` | `api-training` | price predictor training and artifact generation |

`CELERY_ALLOW_INLINE_ML_TASKS=false`가 기본값이다. 따라서 로컬 `memory://` broker 환경에서도 ML 작업은 API 프로세스에서 eager 실행되지 않고 `queued` 상태로 남는다. 실제 실행이 필요하면 `docker compose --profile tasks up -d`로 RabbitMQ와 전용 worker들을 실행한다.

## API 경계

- 사용자 UI는 trainer·embedding task를 직접 호출하지 않는다. `POST /api/v1/projects/{id}/similar/refresh`가 프로젝트/요청자에 묶인 opaque operation을 반환하며, 사용자는 그 operation만 폴링한다.
- `POST /api/v1/admin/ml/backfills/project-embeddings`는 임베딩 백필을 enqueue한다.
- `POST /api/v1/admin/ml/training/price-predictor`는 학습 작업을 enqueue한다.
- `POST /api/v1/admin/ml/reevaluations/decision-experiments/{id}`는 실험 재평가를 enqueue한다.
- `/api/v1/admin/ml/**`와 기존 `/api/v1/ml/**`, `/api/v1/projects/**/embedding/**` 호환 경로는 모두 privileged operator 전용이다.
- 기존 `POST /api/v1/projects/embeddings/rebuild`는 deprecated 호환성 alias이며 더 이상 inline rebuild를 실행하지 않는다.
- 기존 `POST /api/v1/analytics/decision-experiments/{id}/evaluate`도 직접 평가하지 않고 re-evaluation task id를 반환한다.

## 수집·추론 경계

- 수집기는 외부 데이터를 정규화해 canonical project facts를 저장하고, 같은 DB transaction에 `semantic_input.changed` outbox event를 기록한다.
- 수동 project 생성과 의미 필드 수정도 같은 application service를 통해 facts·embedding invalidation·`semantic_input.changed`를 원자적으로 기록한다. source URL 같은 비의미 변경은 event와 inference notification을 만들지 않는다.
- commit 이후 task enqueue는 지연을 줄이는 best-effort 알림일 뿐이다. broker 장애 시에도 주기적 outbox sweep과 stale-claim 회수가 전달을 복구한다.
- inference worker가 embedding을 저장한 뒤 `embedding.ready`를 기록하고 similarity read model을 갱신한다. KONEPS source별 inline embedding 분기는 두지 않는다.
- bulk opportunity scan은 저장된 similarity/features만 소비한다. 단일 프로젝트의 명시적 분석 경로만 기존 refresh-capable 동작을 유지한다.
- strategy monitor는 후보마다 opportunity analysis를 한 번만 실행하고 typed `CandidateDecisionInputs`만 유지한다. top-N 저장 직전에는 predictor/similarity를 재실행하지 않고 경량 `OpportunityWorkloadContext`만 갱신해 active-bid capacity와 auto workload guardrail을 보존한다.
- release artifact는 파일 identity별 bounded process-local cache를 사용하며, worker fork 이후 cache와 lock을 재초기화한다.

## Release Manifest 정책

새 release manifest는 다음 정보를 포함한다.

- artifact path와 checksum 또는 directory tree checksum
- 추천 runtime env와 Docker target
- embedding backfill 기본 파라미터와 enqueue endpoint
- `HMAC-SHA256` signature와 signing key id
- local retention limit과 archive directory
- remote object storage 설정 여부

운영 환경에서는 `ML_RELEASE_MANIFEST_SIGNING_KEY`를 반드시 설정해야 한다. `ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE=true`를 켜면 기존 manifest도 유효한 signature가 없으면 로드하지 않는다.

## Object Storage

`ML_RELEASE_OBJECT_STORAGE_URL`은 다음 형식을 지원한다.

- `file:///absolute/path`: 로컬 또는 마운트된 파일 스토리지에 manifest/artifact를 복사한다.
- `s3://bucket/prefix`: `boto3`가 설치되어 있고 IAM/환경 credential이 설정된 경우 S3에 업로드한다.

`scripts/promote_ml_release.py create-manifest --publish-remote` 또는 `apply-manifest --publish-remote`를 사용하면 signed manifest와 참조 artifact가 함께 업로드된다.

운영 rollout 전에는 `scripts/promote_ml_release.py preflight-rollout --manifest <release-tag> --require-signature`로 아래 조건을 먼저 확인한다.

- manifest JSON 로드 및 HMAC signature 검증
- manifest가 참조하는 artifact 경로 실존 여부
- `file://` target 또는 `s3://bucket/prefix` 연결 가능 여부
- object storage write/delete probe를 통한 credential/IAM 권한

preflight payload는 실패한 check별 `status`, `detail`, `failure_reasons`를 반환하므로 bucket/prefix 오류, IAM 거부, signature required 모드 누락을 publish/apply 전에 구분할 수 있다.
