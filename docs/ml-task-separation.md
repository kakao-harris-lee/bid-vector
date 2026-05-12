# ML 작업 분리 설계

## 목표

백필, 학습, 재평가 작업은 API request/response 경로에서 실행하지 않는다. API는 작업 파라미터를 검증하고 Celery task id를 반환하는 역할만 담당하며, 실제 실행은 RabbitMQ 기반 worker가 담당한다.

## 큐와 워커

| Queue | Worker | Docker target | 용도 |
| --- | --- | --- | --- |
| `CELERY_OPS_QUEUE` | `worker` | `api-runtime` | crawl, Telegram polling, strategy monitoring |
| `CELERY_ML_BACKFILL_QUEUE` | `ml-worker` | `api-ml-full` | project embedding backfill |
| `CELERY_ML_REEVALUATION_QUEUE` | `ml-worker` | `api-ml-full` | decision experiment re-evaluation |
| `CELERY_ML_TRAINING_QUEUE` | `training-worker` | `api-training` | price predictor training and artifact generation |

`CELERY_ALLOW_INLINE_ML_TASKS=false`가 기본값이다. 따라서 로컬 `memory://` broker 환경에서도 ML 작업은 API 프로세스에서 eager 실행되지 않고 `queued` 상태로 남는다. 실제 실행이 필요하면 `docker compose --profile tasks up -d`로 RabbitMQ와 전용 worker들을 실행한다.

## API 경계

- `POST /api/v1/ml/backfills/project-embeddings`는 임베딩 백필을 enqueue한다.
- `POST /api/v1/ml/training/price-predictor`는 학습 작업을 enqueue한다.
- `POST /api/v1/ml/reevaluations/decision-experiments/{id}`는 실험 재평가를 enqueue한다.
- 기존 `POST /api/v1/projects/embeddings/rebuild`는 호환성 alias지만 더 이상 inline rebuild를 실행하지 않는다.
- 기존 `POST /api/v1/analytics/decision-experiments/{id}/evaluate`도 직접 평가하지 않고 re-evaluation task id를 반환한다.

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
