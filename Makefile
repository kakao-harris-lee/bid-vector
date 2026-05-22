.PHONY: help install install-runtime install-ml-embedding install-ml-training install-dev install-browser dev test lint format clean docker-build docker-build-runtime docker-build-embedding docker-build-training docker-build-ml-full docker-up docker-up-tasks docker-up-server docker-down docker-logs docker-logs-tasks docker-logs-server production-smoke ml-release-manifest ml-release-preflight ml-release-apply ml-release-rebuild ml-release-rollout

REBUILD_LIMIT ?= 100
REBUILD_OFFSET ?= 0
REBUILD_FORCE ?= true
ENV_FILE ?=
REQUIRE_SIGNATURE ?= false
WRITE_PROBE ?= true
CELERY_COMPOSE_BROKER_URL ?= amqp://bidvector:bidvector@rabbitmq:5672/bidvector
SMOKE_BASE_URL ?= http://localhost:3000
SMOKE_WRITE ?= false
SMOKE_EVIDENCE ?=

help:
	@echo "Available commands:"
	@echo "  make install      - Install the full development dependency set"
	@echo "  make install-runtime - Install the slim API runtime dependency set"
	@echo "  make install-ml-embedding - Add the embedding stack on top of the runtime set"
	@echo "  make install-ml-training - Add the training/data-science stack"
	@echo "  make install-dev  - Install test/lint dependencies only"
	@echo "  make install-browser - Install Chromium for live crawling"
	@echo "  make dev          - Run development server"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run linting checks"
	@echo "  make format       - Format code"
	@echo "  make clean        - Clean cache and temporary files"
	@echo "  make docker-build - Build the compose-selected Docker image target"
	@echo "  make docker-build-runtime - Build the slim runtime image target"
	@echo "  make docker-build-embedding - Build the embedding-enabled image target"
	@echo "  make docker-build-training - Build the training image target"
	@echo "  make docker-build-ml-full - Build the full embedding+training image target"
	@echo "  make docker-up    - Start Docker containers"
	@echo "  make docker-up-tasks - Start Docker containers plus the optional RabbitMQ/worker/beat task stack"
	@echo "  make docker-up-server - Start production-like server stack (API + broker + workers + beat)"
	@echo "  make docker-down  - Stop Docker containers"
	@echo "  make docker-logs-tasks - Tail API + ops/ML/training worker + beat + RabbitMQ logs"
	@echo "  make docker-logs-server - Tail logs for the server stack started with docker-up-server"
	@echo "  make production-smoke - Run the short production smoke test script"
	@echo "  make ml-release-manifest - Validate artifacts and write a release manifest"
	@echo "  make ml-release-preflight - Check manifest signature/artifacts and object storage before rollout"
	@echo "  make ml-release-apply - Print the runtime settings stored in a manifest"
	@echo "  make ml-release-rebuild - Apply a manifest and rebuild project embeddings"
	@echo "  make ml-release-rollout - Apply a manifest, restart compose, and queue remote embedding backfill"

install:
	pip install -r requirements.txt

install-runtime:
	pip install -r requirements/runtime.txt

install-ml-embedding:
	pip install -r requirements/ml-embedding.txt

install-ml-training:
	pip install -r requirements/ml-training.txt

install-dev:
	pip install -r requirements/dev.txt

install-browser:
	python -m playwright install chromium

dev:
	python run.py

test:
	pytest -v --cov=app

lint:
	flake8 app/
	black --check app/

format:
	black app/

clean:
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -delete
	find . -type d -name '.pytest_cache' -delete
	find . -type d -name '.coverage' -delete

docker-build:
	docker compose build

docker-build-runtime:
	docker build --target api-runtime -t bid-vector-api:runtime .

docker-build-embedding:
	docker build --target api-embedding -t bid-vector-api:embedding .

docker-build-training:
	docker build --target api-training -t bid-vector-api:training .

docker-build-ml-full:
	docker build --target api-ml-full -t bid-vector-api:ml-full .

docker-up:
	docker compose up -d

docker-up-tasks:
	CELERY_BROKER_URL="$(CELERY_COMPOSE_BROKER_URL)" docker compose --profile tasks up -d

docker-up-server:
	docker compose -f docker-compose.yml -f docker-compose.server.yml up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f api

docker-logs-tasks:
	docker compose logs -f api worker ml-worker training-worker beat rabbitmq

docker-logs-server:
	docker compose -f docker-compose.yml -f docker-compose.server.yml logs -f api worker ml-worker training-worker beat rabbitmq

production-smoke:
	python scripts/production_smoke_test.py --base-url "$(SMOKE_BASE_URL)" $(if $(filter true,$(SMOKE_WRITE)),--write) $(if $(SMOKE_EVIDENCE),--evidence-out "$(SMOKE_EVIDENCE)")

ml-release-manifest:
	python scripts/promote_ml_release.py create-manifest --release-tag "$(RELEASE_TAG)" --embedding-model-path "$(EMBEDDING_MODEL_PATH)" --lstm-artifact-path "$(LSTM_ARTIFACT_PATH)" --ensemble-artifact-path "$(ENSEMBLE_ARTIFACT_PATH)" --git-sha "$(GIT_SHA)" --notes "$(NOTES)" --rebuild-limit $(REBUILD_LIMIT) --rebuild-offset $(REBUILD_OFFSET) --category "$(REBUILD_CATEGORY)" --project-status "$(REBUILD_PROJECT_STATUS)" $(if $(filter true,$(PUBLISH_REMOTE)),--publish-remote)

ml-release-preflight:
	python scripts/promote_ml_release.py preflight-rollout --manifest "$(MANIFEST_REF)" $(if $(filter true,$(REQUIRE_SIGNATURE)),--require-signature) $(if $(filter false,$(WRITE_PROBE)),--no-write-probe)

ml-release-apply:
	python scripts/promote_ml_release.py apply-manifest --manifest "$(MANIFEST_REF)" $(if $(ENV_FILE),--write-env-file "$(ENV_FILE)") $(if $(filter true,$(PUBLISH_REMOTE)),--publish-remote)

ml-release-rebuild:
	python scripts/promote_ml_release.py apply-manifest --manifest "$(MANIFEST_REF)" --rebuild-embeddings --limit $(REBUILD_LIMIT) --offset $(REBUILD_OFFSET) --category "$(REBUILD_CATEGORY)" --project-status "$(REBUILD_PROJECT_STATUS)" $(if $(ENV_FILE),--write-env-file "$(ENV_FILE)") $(if $(filter true,$(REBUILD_FORCE)),--force,--no-force) $(if $(filter true,$(PUBLISH_REMOTE)),--publish-remote)

ml-release-rollout:
	python scripts/promote_ml_release.py apply-manifest --manifest "$(MANIFEST_REF)" $(if $(ENV_FILE),--write-env-file "$(ENV_FILE)",--write-env-file .env) --restart-compose --rebuild-embeddings-via-api --limit $(REBUILD_LIMIT) --offset $(REBUILD_OFFSET) --category "$(REBUILD_CATEGORY)" --project-status "$(REBUILD_PROJECT_STATUS)" $(if $(filter true,$(REBUILD_FORCE)),--force,--no-force) $(if $(filter true,$(PUBLISH_REMOTE)),--publish-remote)
