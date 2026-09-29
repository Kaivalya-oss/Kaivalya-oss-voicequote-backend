from contextlib import asynccontextmanager
import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_v1_router
from app.api.routes.health import router as health_router
from app.core.config import settings
from app.core.logging import logger, setup_logging
from app.database.session import engine
from app.middleware.error_handler import register_error_handlers
from app.services.scheduler_service import shutdown_scheduler, start_scheduler

# Initialize Logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info(f"Starting {settings.APP_NAME} in [{settings.APP_ENV}] mode...")
    
    # Ensure storage folders exist
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.PDF_STORAGE_DIR, exist_ok=True)

    # Start background follow-up scheduler
    start_scheduler()

    yield

    # --- Shutdown ---
    logger.info("Shutting down application...")
    shutdown_scheduler()
    await engine.dispose()
    logger.info("Database connection pool cleanly closed.")


# Create FastAPI instance
app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Production-grade RESTful API backend for Two-Wheeler Dealership Sales Automation, "
        "Inventory Control, Quotation Generation, PDF Invoicing, WhatsApp Dispatches, "
        "and AI-Powered Lead Intelligence."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS Middleware
origins = settings.FRONTEND_URL if isinstance(settings.FRONTEND_URL, list) else [settings.FRONTEND_URL]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if "*" not in origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request timing and correlation logging middleware
@app.middleware("http")
async def add_process_time_and_log(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000.0
    response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
    logger.info(f"{request.method} {request.url.path} completed with {response.status_code} in {process_time:.1f}ms")
    return response


# Register global error handlers
register_error_handlers(app)

# Include Routers
app.include_router(health_router)
app.include_router(api_v1_router)


@app.get("/", summary="Root landing endpoint")
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "documentation": "/docs",
        "redoc": "/redoc",
        "health": "/health",
        "api_v1": "/api/v1",
    }
