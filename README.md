# 나라 장터 AI 입찰 서비스 (Korea Marketplace AI Bidding Service)

FastAPI-based backend service for intelligent bidding in the Korea Marketplace platform.

The product is being aligned around a **single operator workflow** rather than a multi-tenant marketplace. Public procurement data is collected and scored so one operator can identify, prioritize, and pursue the best bid opportunities quickly.

## Features

- **Price Prediction AI**: ML-based price prediction for projects
- **Bid Recommendation Engine**: AI-powered bidding recommendations based on historical data
- **Hybrid Notice Classification**: Rule-based filtering plus semantic similarity scoring for operator-company fit
- **Document Analysis**: Automatic requirement extraction and complexity analysis
- **Real-time Notifications**: WebSocket-based notification system
- **Single Operator Workspace**: Centralized operator profile, workload overview, and bid planning surface
- **Analytics & Reporting**: Operator-centric analytics module

## Technology Stack

- **Backend**: FastAPI 0.104+
- **Database**: PostgreSQL 16+ with pgvector
- **Background Jobs**: Celery-ready task scaffold with in-memory defaults (no Redis dependency)
- **ML Libraries**: scikit-learn, sentence-transformers, transformers
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
├── requirements.txt      # Python dependencies
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
   docker-compose up -d
   ```

   The API image is based on the official Playwright Python image, so Chromium and the required Linux browser dependencies are already available for `execution_mode=live` inside the container. The bundled database now uses a PostgreSQL image with `pgvector` preinstalled, and Docker initializes the `vector` extension automatically.

2. **Check services**

   ```bash
   docker-compose ps
   ```

3. **View logs**

   ```bash
   docker-compose logs -f api
   ```

4. **Stop services**

   ```bash
   docker-compose down
   ```

## API Endpoints

### Authentication

- `POST /api/v1/auth/bootstrap` - Initialize the singleton operator account
- `POST /api/v1/auth/register` - Deprecated compatibility alias for bootstrap
- `POST /api/v1/auth/session` - Create a login session for the singleton operator account
- `POST /api/v1/auth/login` - Deprecated compatibility alias for session creation
- `GET /api/v1/auth/me` - Get the current operator account

### Operator

- `GET /api/v1/operator/profile` - Get the singleton operator profile and company fit settings
- `PUT /api/v1/operator/profile` - Update company fit settings used by classification and planning
- `GET /api/v1/operator/strategy` - Get the singleton operator's watch strategy for monitoring and alerts
- `PUT /api/v1/operator/strategy` - Update watch rules such as focus categories, regions, keywords, budgets, and thresholds
- `GET /api/v1/operator/strategy/candidates` - Preview currently open projects that match the stored strategy and are ranked by bid priority
- `POST /api/v1/operator/strategy/monitor` - Execute the stored strategy, persist bid decisions for selected candidates, and create operator notifications/Telegram alerts when applicable
- `POST /api/v1/operator/strategy/monitor/async` - Queue the strategy monitor in the background and return a pollable task id
- `GET /api/v1/operator/strategy/monitor/tasks/{task_id}` - Check the background strategy monitor status and fetch the final persisted result
- `GET /api/v1/operator/strategy/monitor/runs` - List recent strategy monitor execution history with status, counts, and trigger source
- `GET /api/v1/operator/strategy/monitor/runs/{run_id}` - Inspect one strategy monitor run with full payloads and new/continuing/dropped candidate diff details
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
- `POST /api/v1/projects/embeddings/rebuild` - Batch refresh stored project embeddings for existing notices
- `POST /api/v1/projects/embeddings/rebuild/async` - Queue a batch embedding rebuild task and return a pollable task id
- `GET /api/v1/projects/embeddings/rebuild/tasks/{task_id}` - Check async embedding rebuild progress and fetch the final batch summary

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

### Analytics

- `POST /api/v1/analytics/event` - Log an operator analytics event
- `GET /api/v1/analytics/summary` - Get operator workflow analytics summary
- `GET /api/v1/analytics/operator-stats` - Get singleton operator statistics
- `GET /api/v1/analytics/prediction-feedback` - Compare stored predictions and bid-decision recommendations against linked tender results
- `GET /api/v1/analytics/user-stats/{user_id}` - Deprecated compatibility alias for operator statistics

The prediction feedback analytics endpoint summarizes how close the latest stored `predicted_price` and `recommended_amount` were to the final `winning_amount` for projects that already have a linked `TenderResult`. It reports average absolute error rates, counts within 1% and 3%, and whether the latest bid-decision recommendation outperformed the raw price prediction.

### Legacy Admin

- `GET /api/v1/admin/users` - Legacy compatibility endpoint returning the singleton operator snapshot
- `GET /api/v1/admin/stats` - Legacy compatibility endpoint returning operator-centric system stats
- `PUT /api/v1/admin/users/{user_id}/deactivate` - Legacy compatibility endpoint for deactivating the singleton operator

### Operations

- `POST /api/v1/operations/crawl` - Collect KONEPS notices (`execution_mode=mock` by default, `live` uses the public homepage search and falls back safely)
- `POST /api/v1/operations/crawl/async` - Queue a KONEPS crawl task, persist a `crawl_job_id`, and return a pollable task id
- `GET /api/v1/operations/crawl/tasks/{task_id}` - Check async KONEPS crawl progress and fetch the final crawl payload when complete
- `POST /api/v1/operations/classify` - Classify project fit against the singleton operator profile using rule-based filters and semantic similarity (`user_id` is now optional for backward compatibility)
- `POST /api/v1/operations/opportunity-analysis` - Run a multi-angle award analysis that blends fit, market, similarity, price, and action guidance into one response
- `POST /api/v1/operations/bid-decision` - Decide whether the single user should pursue a bid now based on fit, probability, urgency, and current workload
- `POST /api/v1/operations/bid-decisions` - Evaluate and persist a bid decision record with workflow status (`planned`, `reviewing`, `submitted`, `skipped`)
- `GET /api/v1/operations/bid-decisions` - List persisted bid decision records for the singleton operator
- `POST /api/v1/operations/notify/telegram` - Build and best-effort send a Telegram notification payload
- `POST /api/v1/operations/telegram/callback` - Process Telegram inline button callbacks (`투찰`, `검토`, `보류`) for persisted bid decisions
- `POST /api/v1/operations/telegram/webhook` - Process raw Telegram webhook updates for `/start` messages and inline button callbacks
- `POST /api/v1/operations/telegram/sync` - Manually fetch pending Telegram updates via polling and process them immediately
- `GET /api/v1/operations/telegram/status` - Inspect Telegram webhook/polling diagnostics, pending update visibility, and detected chat ids

`POST /api/v1/operations/allocate` remains as a deprecated compatibility alias while the domain language migrates away from multi-user allocation.

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
- `CELERY_BROKER_URL` - Optional task broker URL (defaults to in-memory for local development)
- `CELERY_RESULT_BACKEND` - Optional task result backend (defaults to in-memory cache for local development)
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
- `JWT_SECRET_KEY` - Secret key for tokens
- `DEBUG` - Debug mode (true/false)
- `ENVIRONMENT` - Environment (development/production)
- `ENABLE_SEMANTIC_CLASSIFICATION` - Enable sentence-transformer based hybrid notice classification
- `CLASSIFIER_EMBEDDING_MODEL` - Sentence-Transformers model name for semantic classification
- `CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY` - Keep embedding model loading offline-only; when the model is not cached locally the API falls back immediately instead of hanging on downloads
- `CLASSIFIER_SEMANTIC_MATCH_THRESHOLD` - Base threshold used when blending semantic similarity into the classification score
- `KONEPS_HEADLESS` - Run KONEPS crawling without a visible browser window
- `KONEPS_TIMEOUT_MS` - Browser action timeout for Playwright
- `KONEPS_MAX_ITEMS` - Maximum items returned per crawl request
- `TELEGRAM_BOT_TOKEN` - Telegram bot token used for real message delivery
- `TELEGRAM_BOT_USERNAME` - Human-readable Telegram bot username for local operator setup
- `TELEGRAM_CHAT_ID` - Target Telegram chat/user id that receives bid decisions and submission alerts
- `TELEGRAM_SEND_TIMEOUT_SECONDS` - HTTP timeout used for Telegram Bot API delivery
- `TELEGRAM_DECISION_PRIORITY_THRESHOLD` - Minimum priority score required before a decision alert is pushed to Telegram automatically
- `TELEGRAM_DECISION_PROBABILITY_THRESHOLD` - Minimum probability score required before a decision alert is pushed to Telegram automatically
- `TELEGRAM_WEBHOOK_SECRET` - Optional secret token that must match `X-Telegram-Bot-Api-Secret-Token` for webhook requests
- `TELEGRAM_POLLING_LIMIT` - Default maximum number of Telegram updates pulled per sync/poll cycle
- `TELEGRAM_POLLING_TIMEOUT_SECONDS` - Default long-poll timeout for manual sync or background polling

To receive messages, the target account should start a chat with the configured bot at least once. Otherwise Telegram will politely pretend the bot is shouting into the void.

With the default `memory://` task broker, embedding rebuild and KONEPS crawl jobs run eagerly in-process so the async task APIs remain usable without a separate worker. For truly out-of-process execution, point Celery at a real broker/backend and run a worker process.

Periodic strategy monitoring now follows the same rule: with the default in-memory broker, the API process can run an in-process scheduler from startup when `OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED=true`. When you switch to a real broker, the Celery app also exposes a beat schedule entry for `jobs.monitor_operator_strategy`, so a standard worker + beat deployment can take over without changing the monitoring logic.

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
