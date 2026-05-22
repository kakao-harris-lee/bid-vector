# ML 이미지/의존성 분리 가이드

이 문서는 `bid-vector`의 Docker 이미지와 Python 의존성을 다음 4개 프로필로 나누는 운영 기준을 정리합니다.

- `api-runtime`: 기본 FastAPI 런타임, 크롤링/알림/가격예측 추론 유지, 임베딩 모델은 fallback 가능
- `api-embedding`: `api-runtime` + sentence-transformers 기반 임베딩/의미유사도 런타임
- `api-training`: `api-runtime` + 학습/데이터셋 정리용 pandas/scikit-learn 스택
- `api-ml-full`: `api-embedding` + `api-training` 전체 스택

## 왜 이렇게 나눴나

기본 API는 실제 운영에서 다음 기능만으로도 충분히 동작합니다.

- FastAPI/DB/API 라우팅
- Playwright 기반 라이브 크롤링
- Telegram 알림
- 통계 기반 가격예측 및 JSON artifact 기반 `LSTM`/`Ensemble` 추론
- sentence-transformer가 없을 때의 lexical/fallback embedding

반면 아래 기능은 이미지 크기와 빌드 시간을 크게 늘립니다.

- sentence-transformers / transformers / torch
- pandas / scikit-learn 기반 학습/분석 툴링
- pytest / lint / formatter

그래서 운영 기본값은 `api-runtime`으로 두고, 필요한 시점에만 ML 타깃을 선택합니다.

## Python 의존성 프로필

| 파일 | 용도 |
| --- | --- |
| `requirements/runtime.txt` | 기본 API 런타임 |
| `requirements/ml-embedding.txt` | sentence-transformer 기반 임베딩 런타임 |
| `requirements/ml-training.txt` | 학습/데이터셋 정리용 추가 스택 |
| `requirements/dev.txt` | pytest/black/flake8 |
| `requirements.txt` | 위 4개를 모두 포함하는 full 개발 번들 |

## Docker 타깃

| 타깃 | 설명 | 권장 사용처 |
| --- | --- | --- |
| `api-runtime` | 가장 가벼운 기본 API | 기본 개발/운영, smoke test |
| `api-embedding` | 임베딩 모델 로딩 가능 API | semantic classification, pgvector 재색인 |
| `api-training` | 학습 스택 포함 API | 오프라인 학습/데이터셋 준비 |
| `api-ml-full` | 임베딩 + 학습 전체 포함 | 실험/검증용 올인원 이미지 |

기본 compose 실행은 `.env`의 `API_DOCKER_TARGET` 값을 읽습니다.

```bash
# 기본 슬림 API
docker compose up -d --build

# 임베딩 모델이 필요한 경우
API_DOCKER_TARGET=api-embedding docker compose up -d --build

# 학습까지 한 이미지에서 다뤄야 하는 경우
API_DOCKER_TARGET=api-ml-full docker compose up -d --build
```

## 추천 아티팩트 레이아웃

`models/` 아래를 기능별로 나누면 rollout이 훨씬 단순해집니다.

```text
models/
├── embeddings/
│   └── <embedding-model-version>/
├── predictors/
│   ├── lstm/
│   │   └── <artifact-version>.json
│   └── ensemble/
│       └── <artifact-version>.json
└── manifests/
    └── <release-tag>.json
```

manifest에는 최소한 아래 정보가 있으면 좋습니다.

```json
{
  "release_tag": "2026-05-11-embedding-v3",
  "git_sha": "<commit>",
  "embedding_model_path": "models/embeddings/ko-sbert-v3",
  "lstm_artifact_path": "models/predictors/lstm/2026-05-11.json",
  "ensemble_artifact_path": "models/predictors/ensemble/2026-05-11.json",
  "validated_on": "2026-05-11T12:00:00Z"
}
```

## 학습 결과를 임베딩 런타임에 반영하는 절차

학습과 임베딩 런타임을 분리하면 핵심은 **학습 결과를 아티팩트로 만들고, 임베딩 런타임이 그 아티팩트를 읽도록 경로와 재색인 순서를 고정하는 것**입니다.

### 1) 학습 이미지에서 아티팩트 생성

학습 컨테이너는 `api-training` 또는 `api-ml-full` 타깃을 사용합니다.

```bash
docker build --target api-training -t bid-vector-api:training .
```

학습 산출물은 다음 둘 중 하나입니다.

- **가격예측 artifact**: `LSTM` / `Ensemble` JSON
- **임베딩 model snapshot**: fine-tuned sentence-transformer 디렉터리

학습이 끝나면 결과를 `models/` 아래의 버전 경로에 저장합니다.

### 2) 가격예측 artifact 반영

가격예측 artifact는 기본 `api-runtime` 이미지에서도 바로 읽을 수 있습니다. 런타임이 필요한 것은 `numpy`와 JSON artifact뿐이기 때문입니다.

`.env` 또는 배포 변수에 아래 경로를 반영합니다.

```env
PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS=true
PRICE_PREDICTION_LSTM_MODEL_PATH=/app/models/predictors/lstm/<artifact-version>.json
PRICE_PREDICTION_ENSEMBLE_MODEL_PATH=/app/models/predictors/ensemble/<artifact-version>.json
```

그 뒤 `api-runtime` 이미지로 API를 재시작하면 됩니다.

```bash
API_DOCKER_TARGET=api-runtime docker compose up -d --build
```

즉, **가격예측 학습 결과는 embedding 스택과 독립적으로 rollout 가능**합니다.

artifact를 runtime에 반영하기 전에 아래 CLI로 manifest를 남기면 추후 롤백과 검증이 쉬워집니다.

```bash
python scripts/promote_ml_release.py create-manifest \
  --release-tag 2026-05-11-runtime-v2 \
  --lstm-artifact-path models/predictors/lstm/2026-05-11.json \
  --ensemble-artifact-path models/predictors/ensemble/2026-05-11.json
```

### 3) 임베딩 model snapshot 반영

sentence-transformer를 fine-tuning 했거나 다른 embedding 모델로 교체할 때는 `api-embedding` 또는 `api-ml-full` 타깃이 필요합니다.

1. 학습 컨테이너가 만든 snapshot을 `models/embeddings/<version>/`에 복사
2. `.env`에서 `CLASSIFIER_EMBEDDING_MODEL`을 해당 로컬 경로로 변경
3. `CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY=true`를 유지한 채 임베딩 타깃으로 재기동

```env
CLASSIFIER_EMBEDDING_MODEL=/app/models/embeddings/<version>
CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY=true
```

```bash
API_DOCKER_TARGET=api-embedding docker compose up -d --build
```

### 4) pgvector 재색인 / 저장 임베딩 갱신

모델 교체만으로 기존 `projects.embedding` 값이 바뀌지는 않습니다. **반드시 저장된 임베딩을 재생성**해야 합니다.

가장 안전한 순서는 아래입니다.

1. 임베딩 타깃 API 기동 확인
2. `POST /api/v1/ml/backfills/project-embeddings?force=true&limit=...` 실행
3. task status API로 완료 확인
4. 샘플 공고에서 `/api/v1/projects/{id}/similar` 결과 검증

예시:

```bash
curl -X POST "http://localhost:3000/api/v1/ml/backfills/project-embeddings?force=true&limit=100"
curl "http://localhost:3000/api/v1/ml/backfills/project-embeddings/tasks/<task_id>"
```

여기서 핵심은:

- **학습 컨테이너는 모델을 만든다**
- **임베딩 컨테이너는 그 모델을 읽는다**
- **DB에 저장된 project vectors는 별도 재생성한다**

즉, 학습 결과가 자동으로 임베딩에 반영되는 것이 아니라, **아티팩트 경로 전환 + 재색인**까지 해야 완전한 반영입니다.

이 과정을 자동화하려면 먼저 manifest를 만든 뒤, 같은 manifest를 기준으로 재색인을 실행하면 됩니다.

```bash
python scripts/promote_ml_release.py apply-manifest --manifest 2026-05-11-embedding-v3

python scripts/promote_ml_release.py apply-manifest \
  --manifest 2026-05-11-embedding-v3 \
  --write-env-file .env \
  --rebuild-embeddings \
  --force

python scripts/promote_ml_release.py apply-manifest \
  --manifest 2026-05-11-embedding-v3 \
  --write-env-file .env \
  --restart-compose \
  --rebuild-embeddings-via-api \
  --force
```

스크립트는 다음을 보장합니다.

- embedding snapshot / predictor artifact 경로 실존 여부 확인
- ensemble artifact가 외부 LSTM artifact를 참조할 때 연결 경로까지 검증
- `models/manifests/<release-tag>.json`에 추천 env, 기본 rebuild 파라미터, checksum, HMAC-SHA256 signature 저장
- `ML_RELEASE_MANIFEST_RETENTION_LIMIT`에 따라 오래된 manifest를 archive 디렉터리로 이동
- `ML_RELEASE_OBJECT_STORAGE_URL`이 설정되어 있으면 `--publish-remote`로 manifest와 artifact를 원격 object storage에 보관
- 필요 시 `.env`에 `API_DOCKER_TARGET`, predictor path, embedding model path를 직접 반영
- 재색인 시 manifest의 embedding 모델 경로를 임시 적용한 뒤 project vectors 재생성
- rollout 모드에서는 `docker compose up -d --build api` 후 `/health`를 확인하고 `/api/v1/ml/backfills/project-embeddings`에 재색인 작업을 enqueue

### 5) 검증 체크리스트

- `GET /health` 정상
- `GET /api/v1/operator/strategy` 정상
- `worker`, `ml-worker`, `training-worker`, `beat`, `rabbitmq`가 의도한 queue만 소비
- embedding backfill task 완료
- `/api/v1/projects/{id}/similar` 결과가 새 모델 기준으로 정상
- 필요 시 `CLASSIFIER_EMBEDDING_MODEL`과 predictor artifact 경로를 manifest에 기록

### 6) 롤백 절차

문제가 생기면 아래 두 가지만 되돌리면 됩니다.

1. `.env`의 artifact/model path를 이전 버전으로 복원
2. 이전 타깃으로 다시 기동 후 임베딩 rebuild 재실행

```bash
API_DOCKER_TARGET=api-embedding docker compose up -d --build
```

가격예측 artifact만 롤백할 경우에는 embedding rebuild가 필요 없고, predictor path만 되돌리면 됩니다.
