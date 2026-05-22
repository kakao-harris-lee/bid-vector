"""Main FastAPI application"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import engine, Base
from app.api import routes
from app.services.bid_decision_schema import ensure_bid_decision_schema
from app.services.operator_strategy_schema import ensure_operator_strategy_schema
from app.services.prediction_schema import ensure_price_prediction_metadata_schema
from app.services.project_similarity import ensure_project_metadata_schema, ensure_project_vector_schema
from app.services.realtime import realtime_event_manager
from app.services.paper_bidding_scheduler import paper_bidding_forward_scheduler
from app.services.strategy_scheduler import strategy_scheduler

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


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
    await paper_bidding_forward_scheduler.start()

    yield

    # Shutdown
    await paper_bidding_forward_scheduler.stop()
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


def _dashboard_file_response(full_path: str = ""):
    """Serve the built dashboard SPA when frontend/dist exists."""
    index_path = FRONTEND_DIST_DIR / "index.html"
    if not index_path.is_file():
        return JSONResponse(
            status_code=404,
            content={"detail": "Dashboard frontend has not been built. Run `npm --prefix frontend run build`."},
        )

    if full_path:
        dist_root = FRONTEND_DIST_DIR.resolve()
        requested_path = (FRONTEND_DIST_DIR / full_path).resolve()
        try:
            requested_path.relative_to(dist_root)
        except ValueError:
            requested_path = index_path
        if requested_path.is_file():
            return FileResponse(requested_path)

    return FileResponse(index_path)


if (FRONTEND_DIST_DIR / "assets").is_dir():
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=FRONTEND_DIST_DIR / "assets"),
        name="dashboard-assets",
    )


@app.get("/dashboard", include_in_schema=False)
async def dashboard_index():
    """Serve the mobile dashboard SPA entrypoint."""
    return _dashboard_file_response()


@app.get("/dashboard/{full_path:path}", include_in_schema=False)
async def dashboard_spa_fallback(full_path: str):
    """Serve dashboard static files or fall back to the SPA entrypoint."""
    return _dashboard_file_response(full_path)


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
