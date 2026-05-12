"""Main FastAPI application"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine, Base
from app.api import routes
from app.services.bid_decision_schema import ensure_bid_decision_schema
from app.services.operator_strategy_schema import ensure_operator_strategy_schema
from app.services.prediction_schema import ensure_price_prediction_metadata_schema
from app.services.project_similarity import ensure_project_metadata_schema, ensure_project_vector_schema
from app.services.realtime import realtime_event_manager
from app.services.strategy_scheduler import strategy_scheduler

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle"""
    # Startup
    logger.info("Starting up application...")
    Base.metadata.create_all(bind=engine)
    ensure_bid_decision_schema(engine)
    ensure_operator_strategy_schema(engine)
    ensure_price_prediction_metadata_schema(engine)
    ensure_project_metadata_schema(engine)
    ensure_project_vector_schema(engine)
    await realtime_event_manager.start()
    await strategy_scheduler.start()

    yield

    # Shutdown
    await strategy_scheduler.stop()
    await realtime_event_manager.stop()
    logger.info("Shutting down application...")


# Create FastAPI app
app = FastAPI(
    title="나라 장터 AI 입찰 서비스",
    description="Korea Marketplace AI Bidding Service API",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(routes.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "bid-vector-api"}


@app.exception_handler(Exception)
async def exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
    )
