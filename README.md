# 나라 장터 AI 입찰 서비스 (Korea Marketplace AI Bidding Service)

FastAPI-based backend service for intelligent bidding in the Korea Marketplace platform.

## Features

- **Price Prediction AI**: ML-based price prediction for projects
- **Bid Recommendation Engine**: AI-powered bidding recommendations based on historical data
- **Document Analysis**: Automatic requirement extraction and complexity analysis
- **Real-time Notifications**: WebSocket-based notification system
- **Admin Dashboard**: Backend support for admin operations
- **Analytics & Reporting**: Comprehensive analytics module

## Technology Stack

- **Backend**: FastAPI 0.104+
- **Database**: MySQL 8.0+
- **Cache/Queue**: Redis 7.0+
- **ML Libraries**: scikit-learn, TensorFlow, transformers
- **Container**: Docker & Docker Compose

## Project Structure

```
bid-vector/
├── app/
│   ├── api/              # API route handlers
│   │   ├── auth.py       # Authentication routes
│   │   ├── projects.py   # Project management
│   │   ├── bids.py       # Bid operations
│   │   ├── predictions.py # AI predictions
│   │   ├── analytics.py  # Analytics
│   │   └── admin.py      # Admin operations
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
- MySQL 8.0+ (or use Docker)
- Redis 7.0+ (or use Docker)

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

4. **Configure environment**

   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Initialize database**

   ```bash
   # The database tables will be created automatically on first run
   # Or manually run migrations if using Alembic
   ```

6. **Run the application**

   ```bash
   python -m app.main
   # Or with uvicorn
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

7. **Access the API**
   - API: <http://localhost:8000>
   - Docs: <http://localhost:8000/docs>
   - ReDoc: <http://localhost:8000/redoc>

### Docker Setup

1. **Build and run with Docker Compose**

   ```bash
   docker-compose up -d
   ```

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

- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login user
- `GET /api/v1/auth/me` - Get current user profile

### Projects

- `GET /api/v1/projects/` - List projects
- `POST /api/v1/projects/` - Create project
- `GET /api/v1/projects/{id}` - Get project details
- `PUT /api/v1/projects/{id}` - Update project

### Bids

- `GET /api/v1/bids/` - List bids
- `POST /api/v1/bids/` - Submit bid
- `GET /api/v1/bids/{id}` - Get bid details
- `PUT /api/v1/bids/{id}` - Update bid

### AI Predictions

- `POST /api/v1/predictions/price` - Predict project price
- `POST /api/v1/predictions/bid-recommendation` - Get bid recommendation
- `POST /api/v1/predictions/analyze-document` - Analyze project document

### Analytics

- `POST /api/v1/analytics/event` - Log analytics event
- `GET /api/v1/analytics/summary` - Get analytics summary
- `GET /api/v1/analytics/user-stats/{user_id}` - Get user statistics

### Admin

- `GET /api/v1/admin/users` - List all users
- `GET /api/v1/admin/stats` - Get system statistics
- `PUT /api/v1/admin/users/{user_id}/deactivate` - Deactivate user

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
- Redis connection
- AI model settings
- CORS origins
- Email settings

## Environment Variables

See `.env.example` for all available options.

Key variables:

- `DATABASE_URL` - MySQL connection string
- `REDIS_URL` - Redis connection
- `JWT_SECRET_KEY` - Secret key for tokens
- `DEBUG` - Debug mode (true/false)
- `ENVIRONMENT` - Environment (development/production)

## Troubleshooting

**Database connection error**

- Ensure MySQL is running
- Check `DATABASE_URL` in `.env`
- Verify credentials

**Redis connection error**

- Ensure Redis is running
- Check `REDIS_URL` in `.env`

**Import errors**

- Reinstall dependencies: `pip install -r requirements.txt`
- Verify Python version 3.11+

**Port already in use**

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
