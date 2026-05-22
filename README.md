# 나라 장터 AI 입찰 서비스 (Korea Marketplace AI Bidding Service)

FastAPI-based backend service for intelligent bidding in the Korea Marketplace platform.

The product is being aligned around a **single operator workflow** rather than a multi-tenant marketplace. Public procurement data is collected and scored so one operator can identify, prioritize, and pursue the best bid opportunities quickly.

## Features

- **Price Prediction AI**: ML-based price prediction for projects
- **Bid Recommendation Engine**: AI-powered bidding recommendations based on historical data
- **Hybrid Notice Classification**: Rule-based filtering plus semantic similarity scoring for operator-company fit
- **Document Analysis**: Automatic requirement extraction and complexity analysis
- **Operator Notifications**: Telegram alerts plus persisted web notifications with callback/polling support
- **Single Operator Workspace**: Centralized operator profile, workload overview, and bid planning surface
- **Decision Analytics & Experiment Tracking**: Funnel analytics, recommendation experiments, outcome evaluation, and strategy feedback APIs

## Technology Stack

- **Backend**: FastAPI 0.104+
- **Database**: PostgreSQL 16+ with pgvector
- **Background Jobs**: Celery with in-memory local defaults plus an optional RabbitMQ broker + PostgreSQL result-backend path
- **ML Profiles**: slim runtime / embedding runtime / training runtime / full ML runtime
- **Container**: Docker & Docker Compose

## Project Structure

```text
bid-vector/
├── app/
│   ├── api/              # API route handlers
│   │   ├── auth.py       # Single-operator authentication routes
│   │   ├── operator.py   # Operator profile and overview
│   │   ├── projects.py   # Project management
│   │   ├── bids.py       # Bid operations
│   │   ├── predictions.py # AI predictions
│   │   ├── analytics.py  # Analytics
│   │   └── admin.py      # Legacy admin compatibility routes
│   ├── ai/               # AI/ML modules
│   │   ├── price_prediction.py
│   │   ├── bid_recommendation.py
│   │   └── document_analyzer.py
│   ├── core/             # Core configuration
│   │   ├── config.py     # Settings
│   │   ├── database.py   # DB setup
│   │   └── security.py   # Auth utilities
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── services/         # Business logic
│   └── main.py           # Application entry
├── requirements.txt      # Full development dependency bundle
├── requirements/         # Split runtime / embedding / training / dev dependencies
├── Dockerfile            # Container configuration
├── docker-compose.yml    # Service orchestration
└── README.md            # This file
```

## Setup Instructions

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (optional)
- PostgreSQL 16+ with pgvector (or use Docker)

### Local Development

1. **Clone the repository**

   ```bash
   cd bid-vector
   ```

2. **Create virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   `requirements.txt` installs the full developer bundle. If you want a slimmer local setup, the dependency groups are now split like this:

   ```bash
   pip install -r requirements/runtime.txt
   pip install -r requirements/ml-embedding.txt
   pip install -r requirements/ml-training.txt
   pip install -r requirements/dev.txt
   ```

   On Linux/Docker, preinstalling a CPU-only PyTorch wheel before `requirements/ml-embedding.txt` avoids large CUDA downloads.

4. **Install the Playwright browser for live crawling**

   ```bash
   make install-browser
   ```

   This is only required when you want `execution_mode=live` for the KONEPS crawler.

5. **Configure environment**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

6. **Initialize database**

   ```bash
   # The database tables will be created automatically on first run
   # Or manually run migrations if using Alembic
   ```

7. **Run the application**

   ```bash
   python -m app.main
   # Or with uvicorn
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

8. **Access the API**
   - API: <http://localhost:8000>
   - Docs: <http://localhost:8000/docs>
   - ReDoc: <http://localhost:8000/redoc>

### Docker Setup

1. **Build and run with Docker Compose**

   ```bash
   cp .env.example .env
   docker compose up -d --build
   ```

   The API image is based on the official Playwright Python image, so Chromium and the required Linux browser dependencies are already available for `execution_mode=live` inside the container. The bundled database now uses a PostgreSQL image with `pgvector` preinstalled, and Docker initializes the `vector` extension automatically.
   Compose reads `.env`, keeps PostgreSQL data in a named volume, and now exposes healthchecks for both the database and API so startup sequencing is more reliable.
   The Dockerfile now exposes separate image targets for `api-runtime`, `api-embedding`, `api-training`, and `api-ml-full`. The default compose path uses the slimmer `api-runtime` target, while the embedding target preinstalls a CPU-only PyTorch wheel and uses the BuildKit pip cache to avoid huge CUDA downloads.

   Because the `api` service bind-mounts the repository into `/app`, code-only changes usually do **not** require a full rebuild. Use `docker compose up -d` for normal restarts, and reserve `docker compose up -d --build` for dependency or Dockerfile changes.

   You can override the build target with `API_DOCKER_TARGET`:

   ```bash
   # Slim default API
   docker compose up -d --build

   # API with sentence-transformer embedding runtime
   API_DOCKER_TARGET=api-embedding docker compose up -d --build

   # API with both embedding and training stacks
   API_DOCKER_TARGET=api-ml-full docker compose up -d --build
   ```

    Dedicated rollout and queue-separation playbooks live in `docs/ml-image-separation.md` and `docs/ml-task-separation.md`.

## Manifest-Backed ML Promotion

To reduce drift between training outputs and runtime settings, the repository now includes `scripts/promote_ml_release.py`.

- `create-manifest` validates local embedding snapshots and predictor artifacts, then writes `models/manifests/<release-tag>.json`
- `apply-manifest` prints the recommended runtime env values from an existing manifest
- `apply-manifest --write-env-file .env` writes the recommended runtime keys directly into a dotenv file
- `apply-manifest --rebuild-embeddings` temporarily applies the manifest's embedding model path in-process and rebuilds stored project vectors
- `apply-manifest --restart-compose --rebuild-embeddings-via-api` rolls the manifest into Docker Compose, waits for `/health`, and queues the remote embedding backfill through `/api/v1/ml/backfills/project-embeddings`
- `preflight-rollout --manifest <release-tag>` checks manifest signature/artifact paths and object-storage bucket/prefix write access before publish/apply rollout
- `create-manifest --predictor-backtest-report <report.json>` embeds rolling backtest evidence and creates a predictor promotion gate
- Queued price-predictor training writes `dataset-quality.json` and `artifact-comparison.json` under `models/training-runs/<release-tag>/`, then passes the comparison report into the manifest promotion gate when manifest creation is enabled
- Predictor promotion gates support `standard`, `canary`, `strict`, and `advisory` rollout policies through `ML_RELEASE_PREDICTOR_GATE_POLICY`; gate payloads include the active policy and dataset quality status when the report provides it
- New manifests include artifact checksums, an HMAC-SHA256 signature, local retention policy metadata, and optional remote object-storage publishing via `ML_RELEASE_OBJECT_STORAGE_URL`
- Applying a manifest validates the embedded predictor promotion gate. Failed gates block rollout unless `--skip-promotion-gate` is passed.
- Remote publish failures return structured `status`, `detail`, `failure_reasons`, and `preflight` payloads; CLI publish/preflight exits non-zero when rollout checks fail.

Example flow:

```bash
python scripts/promote_ml_release.py create-manifest \
   --release-tag 2026-05-11-embedding-v4 \
   --embedding-model-path models/embeddings/ko-sbert-v4 \
   --lstm-artifact-path models/predictors/lstm/2026-05-11.json \
   --ensemble-artifact-path models/predictors/ensemble/2026-05-11.json \
   --predictor-backtest-report models/reports/2026-05-11-backtest.json

python scripts/promote_ml_release.py apply-manifest --manifest 2026-05-11-embedding-v4

python scripts/promote_ml_release.py preflight-rollout \
   --manifest 2026-05-11-embedding-v4 \
   --require-signature

python scripts/promote_ml_release.py apply-manifest \
   --manifest 2026-05-11-embedding-v4 \
   --write-env-file .env \
   --rebuild-embeddings \
   --force

python scripts/promote_ml_release.py apply-manifest \
   --manifest 2026-05-11-embedding-v4 \
   --write-env-file .env \
   --restart-compose \
   --rebuild-embeddings-via-api \
   --force
```

Equivalent shortcuts are available in the `Makefile` via `make ml-release-manifest`, `make ml-release-preflight`, `make ml-release-apply`, `make ml-release-rebuild`, and `make ml-release-rollout`. If you set `ENV_FILE=.env`, the apply/rebuild targets also update the dotenv file in place.

## Dependency Profiles and Image Targets

| Profile / target | Includes | Typical use |
| --- | --- | --- |
| `requirements/runtime.txt` / `api-runtime` | FastAPI, DB, crawler, Telegram, numpy-based inference | Default API, health checks, production baseline |
| `requirements/ml-embedding.txt` / `api-embedding` | sentence-transformers, transformers, CPU-only torch in Docker | semantic classification, project embedding rebuild |
| `requirements/ml-training.txt` / `api-training` | pandas, scikit-learn | offline training and dataset preparation |
| `requirements/dev.txt` | pytest, black, flake8 | local/containerized developer tooling |
| `requirements.txt` / `api-ml-full` | everything above | full-stack local experimentation |

### Optional broker-backed task stack

The default repository experience keeps `CELERY_BROKER_URL=memory://`, so lightweight ops async endpoints can run eagerly for local development. ML endpoints are stricter: they return queued task handles and do not execute backfill/training/re-evaluation work inside the API process unless `CELERY_ALLOW_INLINE_ML_TASKS=true` is explicitly set.

For a production-style task path, the compose file now includes an optional `tasks` profile with separated queues and workers:

- `rabbitmq` - durable AMQP broker for queued work
- `worker` - ops-only Celery worker for crawling, Telegram polling, and operator strategy monitoring
- `ml-worker` - ML backfill/re-evaluation worker for embedding backfills and experiment re-evaluation
- `training-worker` - training-only worker built from the training image target and consuming only the training queue
- `beat` - periodic scheduler for `jobs.monitor_operator_strategy` and Celery backend cleanup

When `CELERY_BROKER_URL` is switched away from `memory://` and `CELERY_RESULT_BACKEND` is still left at `cache+memory://`, the FastAPI settings layer now automatically upgrades the result backend to `db+${DATABASE_URL}` so task polling works against PostgreSQL without an extra Redis dependency.

`GET /api/v1/analytics/operations-dashboard` includes a `tasks` summary for this stack: redacted broker/backend diagnostics, queue-to-task route mapping, worker runtime settings, stale queued/running task detection, failed/retry task counts, and task health cards.

For Docker Compose, the broker URL should point at the internal RabbitMQ service:

```bash
CELERY_BROKER_URL=amqp://bidvector:bidvector@rabbitmq:5672/bidvector
```

The repository shortcut below injects that broker URL and starts the optional task profile:

```bash
make docker-up-tasks
```

ML API endpoints enqueue work only. With the default `memory://` broker, ML jobs remain `queued` instead of executing inside the API process; use the RabbitMQ-backed task profile for actual out-of-process execution. If background embedding refreshes need the real sentence-transformer model instead of the hashed fallback, set `ML_WORKER_DOCKER_TARGET=api-ml-full` or another image target that includes the embedding stack.

### Quick Docker Operations

1. **Check services**

   ```bash
   docker compose ps
   ```

2. **View logs**

   ```bash
   docker compose logs -f api
   ```

   For the optional broker-backed task stack:

   ```bash
   make docker-logs-tasks
   ```

3. **Stop services**

   ```bash
   docker compose down
   # Optional full reset including Postgres volume
   docker compose down -v
   ```

### Server-ready training + serving with one compose command

`docker-compose.yml` is optimized for local development (`--reload`, bind mounts, optional task profile).
For server deployment, this repository now includes `docker-compose.server.yml` to run a production-like stack without code bind mounts and with broker-backed workers enabled by default.

1. **Prepare server env**

   ```bash
   cp .env.example .env
   # Required: change DATABASE_PASSWORD, JWT_SECRET_KEY, KONEPS_OPENAPI_SERVICE_KEY
   # Optional: set API_WORKERS, ML worker-related envs
   ```

2. **Start API + broker + workers + beat**

   ```bash
   make docker-up-server
   ```

   Equivalent raw compose command:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.server.yml up -d --build
   ```

3. **Check health and logs**

   ```bash
   docker compose ps
   make docker-logs-server
   ```

4. **Run the short smoke test**

   ```bash
   python scripts/production_smoke_test.py \
     --base-url http://localhost:8000 \
     --evidence-out smoke-read.json
   ```

   To verify real KONEPS/Telegram integration, run the mutating smoke after confirming production credentials:

   ```bash
   python scripts/production_smoke_test.py \
     --base-url http://localhost:8000 \
     --write \
     --max-items 3 \
     --monitor-limit 3 \
     --evidence-out smoke-write.json
   ```

   The full compact guide is in `docs/production-smoke-test.md`.

With this server override, training queue consumers (`training-worker`) and serving API (`api`) run together, so queued ML training and runtime inference can work on the same host immediately after startup.

## API Endpoints

### Authentication

- `POST /api/v1/auth/bootstrap` - Initialize the singleton operator account
- `POST /api/v1/auth/register` - Deprecated compatibility alias for bootstrap
- `POST /api/v1/auth/session` - Create a login session for the singleton operator account
- `POST /api/v1/auth/login` - Deprecated compatibility alias for session creation
- `POST /api/v1/auth/password-reset` - Reset the singleton operator password when `OPERATOR_PASSWORD_RESET_TOKEN` is configured and supplied
- `GET /api/v1/auth/me` - Get the current operator account

Password reset is disabled by default. Set `OPERATOR_PASSWORD_RESET_TOKEN` in `.env`, recreate the API service so the setting is loaded, reset the password from the dashboard login screen or API endpoint, then clear or rotate the reset token.

### Operator

- `GET /api/v1/operator/profile` - Get the singleton operator profile and company fit settings
- `PUT /api/v1/operator/profile` - Update company fit settings used by classification and planning
- `GET /api/v1/operator/strategy` - Get the singleton operator's watch strategy for monitoring and alerts
- `PUT /api/v1/operator/strategy` - Update watch rules such as focus categories, regions, keywords, budgets, thresholds, automatic workload penalty multiplier, and category priority overrides
- `GET /api/v1/operator/strategy/candidates` - Preview currently open projects that match the stored strategy and are ranked by bid priority
- `POST /api/v1/operator/strategy/monitor` - Execute the stored strategy, persist bid decisions for selected candidates, and create operator notifications/Telegram alerts when applicable
- `POST /api/v1/operator/strategy/monitor/async` - Queue the strategy monitor in the background and return a pollable task id
- `GET /api/v1/operator/strategy/monitor/tasks/{task_id}` - Check the background strategy monitor status and fetch the final persisted result
- `GET /api/v1/operator/strategy/monitor/runs` - List recent strategy monitor execution history with status, counts, and trigger source
- `GET /api/v1/operator/strategy/monitor/runs/{run_id}` - Inspect one strategy monitor run with full payloads and new/continuing/dropped candidate diff details
- `GET /api/v1/operator/dashboard` - Return a card-ready web dashboard payload connecting analysis entrypoints, recent bid decisions, monitor runs, notifications, and prediction feedback
- `GET /api/v1/operator/overview` - Get a compact operator dashboard summary
- `GET /api/v1/operator/notifications` - Get recent Telegram-style notifications for the web dashboard
- `PUT /api/v1/operator/notifications/{id}/read` - Mark a notification as read on the web dashboard

### Projects

- `GET /api/v1/projects/` - List projects
- `POST /api/v1/projects/` - Create project and persist semantic embedding metadata (optionally including `notice_number`, `source_url`, `issuing_agency`, and `demand_agency` for later crawl linkage)
- `GET /api/v1/projects/{id}` - Get project details
- `PUT /api/v1/projects/{id}` - Update project
- `GET /api/v1/projects/{id}/similar` - Find similar procurement notices using stored project embeddings
- `POST /api/v1/projects/{id}/embedding/refresh` - Rebuild one project's semantic embedding and vector metadata
- `POST /api/v1/projects/embeddings/rebuild` - Deprecated compatibility alias that queues an embedding backfill instead of running inline
- `POST /api/v1/projects/embeddings/rebuild/async` - Queue a batch embedding rebuild task and return a pollable task id
- `GET /api/v1/projects/embeddings/rebuild/tasks/{task_id}` - Check async embedding rebuild progress and fetch the final batch summary

### ML Jobs

- `POST /api/v1/ml/backfills/project-embeddings` - Queue a project embedding backfill on the ML backfill queue
- `GET /api/v1/ml/backfills/project-embeddings/tasks/{task_id}` - Check embedding backfill status
- `POST /api/v1/ml/training/price-predictor` - Queue price-predictor training on the dedicated training queue, writing dataset quality and artifact comparison reports
- `GET /api/v1/ml/training/price-predictor/tasks/{task_id}` - Check training status and fetch artifact/manifest output when complete
- `POST /api/v1/ml/reevaluations/decision-experiments/{experiment_run_id}` - Queue decision experiment re-evaluation
- `GET /api/v1/ml/reevaluations/decision-experiments/tasks/{task_id}` - Check re-evaluation status

### Bids

- `GET /api/v1/bids/` - List bids
- `POST /api/v1/bids/` - Submit bid for the singleton operator (no `user_id` query parameter required, and auto-sync any matching bid decision to `submitted`)
- `GET /api/v1/bids/{id}` - Get bid details
- `PUT /api/v1/bids/{id}` - Update bid

### AI Predictions

- `POST /api/v1/predictions/price` - Predict project price and persist it for the singleton operator
- `POST /api/v1/predictions/bid-recommendation` - Get bid recommendation
- `POST /api/v1/predictions/analyze-document` - Analyze project document

The price prediction response now returns a `pricing_mode`, the number of historical samples used, and three bid-rate scenarios (`conservative`, `base`, `aggressive`). When matching `historical_data` rows exist for the same category, the service blends their bid-rate distribution into the prediction; otherwise it falls back to the existing category-and-description heuristic.

If the caller provides an optional `agency_name`, same-agency history receives additional weight during the bid-rate calculation. The response also includes `agency_match_sample_size` and an optional `reserve_price_context` summary, so the web UI can explain whether reserve-price bands and selected-number patterns influenced the recommendation.

When recent linked `TenderResult` rows already exist for the same category, the prediction service now also computes a lightweight `feedback_calibration` bias from past signed prediction errors. That bias is applied automatically to the new `predicted_price` and scenario band so the service can gradually correct category-level overestimation or underestimation.

Set `PRICE_PREDICTION_PREFERRED_PREDICTOR=auto` to enable rolling backtest selection. In that mode the service compares currently runnable predictors over the recent historical holdout window, selects the lowest average absolute bid-rate error, and returns selector/backtest metadata in the prediction response.

### Analytics

- `POST /api/v1/analytics/event` - Log an operator analytics event
- `GET /api/v1/analytics/summary` - Get operator workflow analytics summary
- `GET /api/v1/analytics/operator-stats` - Get singleton operator statistics
- `GET /api/v1/analytics/prediction-feedback` - Compare stored predictions and bid-decision recommendations against linked tender results
- `GET /api/v1/analytics/prediction-observability` - Compare predictor selection, fallback, guardrail, linked-result accuracy metrics, and time-bucketed performance trend
- `GET /api/v1/analytics/operations-dashboard` - Return card-ready crawl health, strategy monitoring, task/broker, Telegram delivery, and ML release metrics
- `GET /api/v1/analytics/decision-insights` - Summarize persisted bid decision signals such as priority, margin, complexity, and workload source
- `GET /api/v1/analytics/decision-funnel` - Track how initial bid decisions move from review/bid_now into submitted workflow states, including trend and segment breakdowns
- `GET /api/v1/analytics/decision-recommendations` - Convert funnel signals and prior experiment outcomes into ranked recommendations plus bounded experiment plans
- `POST /api/v1/analytics/decision-experiments` - Persist one experiment plan as an executable tracked run with a saved baseline snapshot
- `GET /api/v1/analytics/decision-experiments` - List tracked decision experiment runs for the operator dashboard with status/outcome/application filters and review-priority sorting
- `GET /api/v1/analytics/decision-experiments/{experiment_run_id}` - Inspect one experiment run with its baseline summary and latest evaluation
- `POST /api/v1/analytics/decision-experiments/{experiment_run_id}/evaluate` - Queue current-vs-baseline re-evaluation and return a pollable task id
- `PATCH /api/v1/analytics/decision-experiments/{experiment_run_id}` - Manually update experiment lifecycle state, outcome, and notes
- `POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-thresholds` - Apply a successful threshold experiment to persisted bid/review thresholds with dry-run and force support
- `POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-strategy` - Apply successful workload/category experiments to persisted strategy tuning values with dry-run and force support
- `GET /api/v1/analytics/user-stats/{user_id}` - Deprecated compatibility alias for operator statistics

The prediction feedback analytics endpoint summarizes how close the latest stored `predicted_price` and `recommended_amount` were to the final `winning_amount` for projects that already have a linked `TenderResult`. It reports average absolute error rates, counts within 1% and 3%, and whether the latest bid-decision recommendation outperformed the raw price prediction. The prediction observability endpoint groups persisted prediction metadata by predictor and pricing mode, including fallback frequency, guardrail frequency, linked-result absolute error rates, and backtest selector metadata. The operations dashboard endpoint summarizes crawl success/failure, recent failure reasons, strategy monitoring completion, candidate selection, persistence, notification rates, task queue risk, broker/backend health, worker separation state, Telegram delivery health, and ML release manifest/promotion-gate status for dashboard cards.

Decision analytics now also include persisted funnel telemetry, current-vs-previous period comparisons, segment breakdowns by category / workload source / agency, recommendation payloads with `experiment_plan`, `priority_score`, `history_adjustment`, and concrete `parameter_recommendation` deltas, plus saved experiment runs that can be evaluated later against baseline target and guardrail metrics. Experiment run responses include dashboard-ready `application_status`, `application_history`, `next_actions`, `review_bucket`, `review_priority`, and `review_reason` payloads so the UI can distinguish ready, applied, blocked, pending, failed, and unsupported runs. Successful experiments can feed back into strategy settings: threshold experiments adjust `bid_now_threshold` / `review_threshold`, workload experiments adjust `auto_workload_penalty_multiplier`, and category focus experiments adjust `category_priority_overrides`; repeated success, rollback, failure, or pending history now scales the concrete threshold/category delta before recommendation and apply.

### Realtime

- `WS /api/v1/realtime/events` - Stream normalized dashboard events for bid-decision notifications, bid submissions, crawl completion/failure, and strategy monitor completion/failure

Realtime events use a common envelope: `event_id`, `event_type`, `created_at`, and `payload`. The WebSocket stream requires an operator access token by default; pass it as `?token=<access_token>` or an `Authorization: Bearer <access_token>` header. Clients can send `{"event_type": "ping"}` and receive a `pong` event for connection health checks. Reconnecting clients can request local in-memory replay with `?replay=true&after_event_id=<last_seen_event_id>&replay_limit=<n>`. The `connection.opened` payload reports `replay.requested`, `delivered_event_count`, `available_event_count`, `history_limit`, and `after_event_id_found`; if the last event was evicted, retained local events are replayed and `after_event_id_found=false` signals that the client should reconcile from HTTP state. Single-process deployments use local fanout; set `REALTIME_FANOUT_BACKEND=postgres` to relay live events between API workers through PostgreSQL `LISTEN/NOTIFY`. PostgreSQL fanout is not durable cross-worker replay: each API process retains only its own last `REALTIME_HISTORY_LIMIT` events.

### Legacy Admin

- `GET /api/v1/admin/users` - Legacy compatibility endpoint returning the singleton operator snapshot
- `GET /api/v1/admin/stats` - Legacy compatibility endpoint returning operator-centric system stats
- `PUT /api/v1/admin/users/{user_id}/deactivate` - Legacy compatibility endpoint for deactivating the singleton operator

### Operations

- `POST /api/v1/operations/crawl` - Collect KONEPS notices (`execution_mode=mock` by default, `live` uses the public homepage search and falls back safely; set `source=koneps-openapi` to use the public BidPublicInfoService OpenAPI, or `source=koneps-scsbid` to backfill ScsbidInfoService award/opening rows)
- `POST /api/v1/operations/crawl/async` - Queue a KONEPS crawl task, persist a `crawl_job_id`, and return a pollable task id
- `GET /api/v1/operations/crawl/tasks/{task_id}` - Check async KONEPS crawl progress and fetch the final crawl payload when complete
- `POST /api/v1/operations/classify` - Classify project fit against the singleton operator profile using rule-based filters and semantic similarity (`user_id` is now optional for backward compatibility)
- `POST /api/v1/operations/opportunity-analysis` - Run a multi-angle award analysis that blends fit, market, similarity, price, and action guidance into one response
- `POST /api/v1/operations/bid-decision` - Decide whether the single user should pursue a bid now based on fit, probability, urgency, and current workload
- `POST /api/v1/operations/bid-decisions` - Evaluate and persist a bid decision record with workflow status (`planned`, `reviewing`, `submitted`, `skipped`)
- `GET /api/v1/operations/bid-decisions` - List persisted bid decision records for the singleton operator
- `GET /api/v1/operations/bid-decisions/{decision_record_id}` - Get one persisted bid decision with project snapshot and recent timeline
- `GET /api/v1/operations/projects/{project_id}/bid-decision-timeline` - Fetch recent bid decision history for one project
- `POST /api/v1/operations/notify/telegram` - Build and best-effort send a Telegram notification payload
- `POST /api/v1/operations/telegram/callback` - Process Telegram inline button callbacks for persisted bid decisions and strategy edit confirmations
- `POST /api/v1/operations/telegram/webhook` - Process raw Telegram webhook updates for `/start`, strategy text commands, strategy edit buttons, and bid decision buttons
- `POST /api/v1/operations/telegram/sync` - Manually fetch pending Telegram updates via polling and process them immediately
- `GET /api/v1/operations/telegram/status` - Inspect Telegram webhook/polling diagnostics, pending update visibility, and detected chat ids

`POST /api/v1/operations/allocate` remains as a deprecated compatibility alias while the domain language migrates away from multi-user allocation.

Telegram strategy commands:

- `/strategy` - Show the current watch strategy, short command help, and inline edit buttons for `업종`, `지역`, `키워드`, `예산`, `임계치`, `알림 범위`, and `후보 수`
- `/strategy_set categories=software,security regions=서울 keywords=AI,데이터 min_budget=90000000 max_budget=180000000 match=0.65 probability=0.60 bid_now=0.75 review=0.50 high_priority=true limit=10` - Update watch rules from chat
- `/strategy_clear categories regions keywords budget thresholds` - Reset selected watch-rule groups

The `/strategy` edit buttons use a staged flow: choose a field, send the new value, review the parsed change, then tap `적용` or `취소`. Invalid values leave the stored strategy unchanged and reply with the current value plus a valid example. The text commands above remain supported for faster bulk edits.

When a bid decision is saved or a real bid is submitted, the backend now also creates an operator notification record and best-effort sends the same summary to Telegram when bot credentials are configured. The web dashboard still keeps the full recent notification history even if Telegram delivery is unavailable.

Strategy monitoring now suppresses repeat alerts for candidates that already appeared in the immediately previous completed run. Newly surfaced candidates still generate a web notification and best-effort Telegram alert when thresholds allow, while recurring candidates are marked as continuing opportunities without spamming the operator again. The run detail endpoint makes this comparison explicit through `new`, `continuing`, and `dropped` candidate groups.

Automatic Telegram decision alerts are now filtered down to high-priority opportunities only. By default, only `bid_now` decisions that clear both the configured priority threshold and probability threshold are pushed to Telegram, while lower-value opportunities remain visible on the web dashboard only.

High-priority decision alerts now include Telegram inline buttons for `투찰`, `검토`, and `보류`. The backend processes those callbacks through `/api/v1/operations/telegram/callback`; in real deployments, Telegram still needs a reachable webhook or polling worker so button clicks can arrive at the service.

For local debugging, `/api/v1/operations/telegram/sync` can pull pending updates directly from Telegram with `getUpdates`, and `/api/v1/operations/telegram/status` shows whether the bot is configured, whether Telegram has a webhook registered, and which chat ids were recently observed. This makes it much easier to diagnose `chat not found` without guessing.

When a real bid is submitted through `/api/v1/bids/`, the backend now promotes the latest active bid decision record for that project to `submitted`. If no prior decision exists, it creates a fallback submitted decision record so the bid trail remains auditable.

The crawl response now includes top-level `metadata` describing the resolved execution mode, search entry URL, live page count, opening-result enrichment counts, and persisted `crawl_job_id`. The async crawl kickoff endpoint also pre-creates a queued `crawl_job_id`, so the web layer can immediately associate a task id with a persisted crawl history record.

During persistence, each crawled notice is now also matched to an existing `Project` (using explicit `notice_number`, detail URL, agency metadata, and then strict title/budget/deadline heuristics) or auto-created as a new project when no safe match exists. The resulting `project_id` is written back onto the associated `HistoricalData`, `TenderResult`, and single-notice `CrawlJob` rows, and repeated crawls upsert the same tender-result snapshot instead of duplicating it.

Internal project lifecycle tracking is now richer than a simple open/closed split. Crawled notices can be normalized to `open`, `re_notice`, `closed`, `awarded`, `failed`, or `cancelled`, and operator strategy monitoring treats both `open` and `re_notice` as currently actionable opportunities.

## Development

### Running Tests

```bash
pytest
pytest -v  # Verbose
pytest --cov=app  # With coverage
```

### Code Quality

```bash
# Format code
black app/

# Lint
flake8 app/

# Type checking
mypy app/
```

### Database Migrations

```bash
# Create migration
alembic revision --autogenerate -m "Add new table"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Configuration

Edit `.env` file to configure:

- Database connection
- JWT secrets
- Optional background-job settings
- AI model settings
- CORS origins
- Email settings

## Environment Variables

See `.env.example` for all available options.

Key variables:

- `DATABASE_URL` - PostgreSQL connection string
- `DATABASE_USER` / `DATABASE_PASSWORD` / `DATABASE_HOST` / `DATABASE_PORT` / `DATABASE_NAME` - When all are set, they now deterministically compose `DATABASE_URL` (used by Docker Compose to swap `localhost` for `db` safely)
- `CELERY_BROKER_URL` - Optional task broker URL (defaults to `memory://` for local development; use an AMQP URL for the optional RabbitMQ worker path)
- `CELERY_RESULT_BACKEND` - Optional task result backend (defaults to `cache+memory://`, but automatically upgrades to `db+${DATABASE_URL}` when the broker is external unless you set an explicit backend)
- `CELERY_TASK_DEFAULT_QUEUE` - Default Celery queue name shared by API, worker, and beat
- `CELERY_OPS_QUEUE` - Queue for operational jobs such as crawling, Telegram polling, and strategy monitoring
- `CELERY_ML_BACKFILL_QUEUE` - Queue for ML backfill work such as project embedding rebuilds
- `CELERY_ML_TRAINING_QUEUE` - Queue consumed only by the training worker
- `CELERY_ML_REEVALUATION_QUEUE` - Queue for ML/analytics re-evaluation jobs
- `CELERY_ALLOW_INLINE_ML_TASKS` - Keep false in normal environments so ML jobs never execute in the API request path
- `CELERY_WORKER_CONCURRENCY` - Worker process count used by broker-backed execution
- `CELERY_WORKER_PREFETCH_MULTIPLIER` - Prefetch multiplier; the default `1` keeps long-running jobs fairer across workers
- `CELERY_WORKER_MAX_TASKS_PER_CHILD` - Recycle worker children after a fixed number of tasks to limit memory creep
- `CELERY_TASK_TIME_LIMIT_SECONDS` / `CELERY_TASK_SOFT_TIME_LIMIT_SECONDS` - Hard/soft execution limits for background tasks
- `CELERY_RESULT_EXPIRES_SECONDS` - Result-retention window used by Celery's backend cleanup task
- `CELERY_TASK_TRACK_STARTED` - Emit `STARTED` status updates for long-running jobs
- `CELERY_WORKER_SEND_TASK_EVENTS` / `CELERY_TASK_SEND_SENT_EVENT` - Enable worker/task events for monitoring and troubleshooting
- `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP` - Retry broker connection while worker/beat are booting
- `CELERY_BROKER_CONNECTION_MAX_RETRIES` - Maximum broker reconnect attempts during startup
- `CELERY_BROKER_PUBLISH_MAX_RETRIES` - Producer-side publish retry ceiling for transient broker failures
- `REALTIME_REQUIRE_AUTH` - Require an operator access token for dashboard WebSocket connections
- `REALTIME_FANOUT_BACKEND` - Realtime event fanout backend: `local` for one API process or `postgres` for PostgreSQL `LISTEN/NOTIFY` fanout across workers
- `REALTIME_POSTGRES_CHANNEL` - PostgreSQL notification channel used when `REALTIME_FANOUT_BACKEND=postgres`
- `REALTIME_HISTORY_LIMIT` - Number of recent realtime events retained in each API process for diagnostics/replay metadata
- `CELERY_RABBITMQ_USER` / `CELERY_RABBITMQ_PASSWORD` / `CELERY_RABBITMQ_VHOST` - Optional RabbitMQ compose defaults used by the `tasks` profile
- `CELERY_RABBITMQ_PORT` / `CELERY_RABBITMQ_MANAGEMENT_PORT` - RabbitMQ AMQP and management UI ports exposed by Docker Compose
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED` - Enable periodic operator strategy monitoring
- `OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES` - Monitoring interval used by the periodic scheduler/beat entry
- `OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP` - When using the in-process scheduler, execute one monitoring cycle immediately at startup
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT` - Maximum candidate count used by scheduled monitoring runs
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY` - Restrict scheduled runs to `bid_now` opportunities only
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS` - Workload cap injected into scheduled monitoring runs
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE` - Workload score injected into scheduled monitoring runs
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY` - Whether scheduled runs restrict similar-project analysis to the same category
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT` - Similar-project lookup depth used during scheduled monitoring
- `OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY` - Minimum similarity threshold used during scheduled monitoring
- `PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED` - Enable periodic forward paper-bidding runs for currently open/re-notice projects
- `PAPER_BIDDING_FORWARD_RUN_ON_STARTUP` - When using the in-process scheduler, run forward paper-bidding immediately at startup
- `PAPER_BIDDING_FORWARD_INTERVAL_MINUTES` - Forward paper-bidding scheduler/beat interval
- `PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT` - Maximum open/re-notice projects included in a scheduled forward run
- `PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY` - Optional category filter for scheduled forward runs
- `PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO` - Scheduled paper-bid scenario (`conservative`, `base`, or `aggressive`)
- `PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT` - Historical bid-rate sample limit used during scheduled forward runs
- `PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST` - Persist scheduled forward paper-bidding runs and generated paper bids
- `ML_RELEASE_MANIFEST_DIR` - Local directory for signed release manifests
- `ML_RELEASE_MANIFEST_ARCHIVE_DIR` - Local archive directory for manifests moved out by retention
- `ML_RELEASE_MANIFEST_RETENTION_LIMIT` - Number of recent local manifests to retain before archiving older files
- `ML_RELEASE_MANIFEST_SIGNING_KEY` - HMAC signing key for release manifests; required in production
- `ML_RELEASE_MANIFEST_SIGNING_KEY_ID` - Human-readable signing key identifier stored in signatures
- `ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE` - Require existing manifests to contain a valid signature before loading
- `ML_RELEASE_OBJECT_STORAGE_URL` - Optional `file://...` or `s3://bucket/prefix` target for manifest/artifact publishing
- `ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH` - Automatically publish manifests and referenced artifacts after creation
- `ML_RELEASE_PREDICTOR_GATE_POLICY` - Release gate policy preset (`standard`, `canary`, `strict`, or `advisory`)
- `ML_RELEASE_PREDICTOR_GATE_MIN_DATASET_QUALITY_STATUS` - Optional dataset quality floor (`failed`, `warning`, or `passed`) overriding the policy default
- `ML_RELEASE_PREDICTOR_GATE_REQUIRE_REPORT` - Require predictor manifests to include a backtest report before rollout
- `ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT` - Minimum backtest samples required by the predictor promotion gate
- `ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE` - Maximum average absolute bid-rate error allowed by the promotion gate
- `ML_RELEASE_PREDICTOR_GATE_MAX_GUARDRAIL_RATE` - Maximum optional guardrail rate allowed when the backtest report provides it
- `ML_RELEASE_PREDICTOR_GATE_MAX_FALLBACK_RATE` - Maximum optional fallback rate allowed when the backtest report provides it
- `JWT_SECRET_KEY` - Secret key for tokens
- `OPERATOR_PASSWORD_RESET_TOKEN` - Optional server-side token that enables the dashboard password reset form and `/api/v1/auth/password-reset`
- `DEBUG` - Debug mode (true/false)
- `ENVIRONMENT` - Environment (development/production)
- `ENABLE_SEMANTIC_CLASSIFICATION` - Enable sentence-transformer based hybrid notice classification
- `CLASSIFIER_EMBEDDING_MODEL` - Sentence-Transformers model name for semantic classification
- `CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY` - Keep embedding model loading offline-only; when the model is not cached locally the API falls back immediately instead of hanging on downloads
- `CLASSIFIER_SEMANTIC_MATCH_THRESHOLD` - Base threshold used when blending semantic similarity into the classification score
- `PRICE_PREDICTION_PREFERRED_PREDICTOR` - Predictor preference (`historical`, `lstm`, `ensemble`, or `auto` for rolling backtest selection)
- `PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS` - Enable artifact-backed LSTM/Ensemble predictors when configured
- `PRICE_PREDICTION_LSTM_MODEL_PATH` - Filesystem path to the current LSTM JSON artifact used by predictor inference
- `PRICE_PREDICTION_ENSEMBLE_MODEL_PATH` - Filesystem path to the current ensemble JSON artifact used by predictor inference
- `PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES` - Minimum prior samples required before a holdout point can be used in predictor backtests
- `PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE` - Number of recent historical points used for rolling auto-selection backtests
- `KONEPS_HEADLESS` - Run KONEPS crawling without a visible browser window
- `KONEPS_TIMEOUT_MS` - Browser action timeout for Playwright
- `KONEPS_MAX_ITEMS` - Maximum items returned per crawl request
- `KONEPS_OPENAPI_BID_PUBLIC_INFO_URL` - Public Data Portal BidPublicInfoService endpoint for notice collection
- `KONEPS_OPENAPI_SCSBID_INFO_URL` - Public Data Portal ScsbidInfoService endpoint for award/opening-result collection
- `KONEPS_OPENAPI_SERVICE_KEY` - Public Data Portal service key used when `source=koneps-openapi` or `source=koneps-scsbid`
- `KONEPS_OPENAPI_ENCODED_SERVICE_KEY` - Optional already-URL-encoded service key; used without double-encoding when the configured key is rejected
- `KONEPS_OPENAPI_MAX_ITEMS` - Maximum items returned per OpenAPI crawl request
- `KONEPS_OPENAPI_TIMEOUT_SECONDS` - HTTP timeout for KONEPS OpenAPI requests
- `TELEGRAM_BOT_TOKEN` - Telegram bot token used for real message delivery
- `TELEGRAM_BOT_USERNAME` - Human-readable Telegram bot username for local operator setup
- `TELEGRAM_CHAT_ID` - Target Telegram chat/user id that receives bid decisions and submission alerts
- `TELEGRAM_SEND_TIMEOUT_SECONDS` - HTTP timeout used for Telegram Bot API delivery
- `TELEGRAM_DECISION_PRIORITY_THRESHOLD` - Minimum priority score required before a decision alert is pushed to Telegram automatically
- `TELEGRAM_DECISION_PROBABILITY_THRESHOLD` - Minimum probability score required before a decision alert is pushed to Telegram automatically
- `TELEGRAM_WEBHOOK_SECRET` - Optional secret token that must match `X-Telegram-Bot-Api-Secret-Token` for webhook requests
- `TELEGRAM_POLLING_LIMIT` - Default maximum number of Telegram updates pulled per sync/poll cycle
- `TELEGRAM_POLLING_TIMEOUT_SECONDS` - Default long-poll timeout for manual sync or background polling

`API_DOCKER_TARGET` is also read by Docker Compose as a build-time selector (`api-runtime`, `api-embedding`, `api-training`, `api-ml-full`). The FastAPI settings object ignores it inside the container, so it is safe to keep it in `.env`.

To receive messages, the target account should start a chat with the configured bot at least once. Otherwise Telegram will politely pretend the bot is shouting into the void.

With the default `memory://` task broker, KONEPS crawl and other lightweight ops jobs can still run eagerly in-process for local development. Embedding backfill, training, and re-evaluation jobs stay queued by default so they cannot consume API request capacity. For actual ML execution, switch `CELERY_BROKER_URL` to RabbitMQ or another real broker, then run the optional worker stack.

Periodic strategy monitoring and forward paper-bidding now follow the same rule: with the default in-memory broker, the API process can run an in-process scheduler from startup when the corresponding `*_SCHEDULE_ENABLED=true`. When you switch to a real broker, the Celery app exposes `jobs.monitor_operator_strategy` and `jobs.run_forward_paper_bidding` schedules through Celery beat, so the separate `beat` + `worker` services can take over without changing the execution logic.

## Troubleshooting

### Database connection error

- Ensure PostgreSQL is running
- Check `DATABASE_URL` in `.env`
- Verify credentials

### Import errors

- Reinstall dependencies: `pip install -r requirements.txt`
- Verify Python version 3.11+

### Playwright browser launch errors

- Run `make install-browser` locally before using `execution_mode=live`
- Rebuild the Docker image after dependency changes so Chromium is reinstalled
- If Chromium is unstable in containers, keep Docker Desktop memory generous and use the provided Compose defaults with `init` and larger shared memory

### Port already in use

- Change `API_PORT` in `.env` or use `--port` flag

## Deployment

For production deployment, see `.github/copilot-instructions.md` for detailed deployment guidelines.

## Contributing

1. Create feature branch
2. Make changes
3. Run tests and linting
4. Commit changes
5. Push and create PR

## License

Internal use only - 나라 장터

## Support

Contact development team for support and questions.
