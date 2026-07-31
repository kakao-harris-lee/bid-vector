# 나라 장터 AI 입찰 서비스 - Project Setup Instructions

## Project Overview
- **Type**: Python FastAPI Backend
- **Database**: MySQL
- **Key Features**: Price prediction AI, bid recommendation engine, document analysis, real-time notifications, admin dashboard, analytics/reporting

## Completed Steps
- [x] Project Requirements Clarified
- [x] Initial Setup Structure Created
- [x] Scaffold FastAPI project structure
- [x] Configure database and dependencies
- [x] Implement AI modules
- [x] Create admin dashboard backend
- [x] Add analytics module
- [x] Setup testing framework
- [x] Docker configuration

## Project Structure Completed
```
bid-vector/
├── app/
│   ├── api/              (Authentication, Projects, Bids, Predictions, Analytics, Admin)
│   ├── ai/               (Price Prediction, Bid Recommendation, Document Analysis)
│   ├── core/             (Config, Database, Security)
│   ├── models/           (Database Models)
│   ├── schemas/          (Pydantic Schemas)
│   └── main.py           (FastAPI App Entry)
├── tests/                (Unit Tests)
├── requirements.txt      (Python Dependencies)
├── Dockerfile            (Container Config)
├── docker-compose.yml    (Service Orchestration)
├── Makefile              (Development Tasks)
└── README.md             (Documentation)
```

## Next Steps
- [ ] Install dependencies
- [ ] Configure MySQL database
- [x] ~~Set up Redis cache~~ — 미채택(SUPERSEDED): 스택에 Redis 없음. Celery 브로커는 RabbitMQ, 결과 백엔드는 PostgreSQL이다. 근거·역할 경계는 [docs/operations/celery-broker-and-result-backend.md](../docs/operations/celery-broker-and-result-backend.md) 참고.
- [ ] Run development server
- [ ] Implement WebSocket notifications
- [ ] Add Celery for async tasks
- [ ] Deploy to production
