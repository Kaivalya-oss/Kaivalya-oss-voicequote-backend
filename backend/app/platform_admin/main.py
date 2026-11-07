"""The Platform Admin process. Started on its own: uvicorn app.platform_admin.main:app

It serves only admin routes, and the showroom app doesn't include them. Startup stops if this process
holds showroom credentials, if its database role has privileges it must not have, or if the schema
doesn't match this release.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppException
from app.platform_admin.config import forbidden_environment_present, get_admin_settings
from app.platform_admin.database import admin_sessionmaker, boundary_violations, dispose_admin_engine, schema_version
from app.platform_admin.routes import router
from app.platform_admin.showrooms import router as showrooms_router
from app.schemas.common import ErrorDetail, ErrorResponse

logger = logging.getLogger("voicequote.platform_admin")

EXPECTED_SCHEMA_VERSION = "c3a1_showroom_sessions"


class AdminStartupRefused(RuntimeError):
    pass


async def check_startup_boundary() -> None:
    forbidden = forbidden_environment_present()
    if forbidden:
        raise AdminStartupRefused(f"This process must not hold: {', '.join(forbidden)}.")
    async with admin_sessionmaker()() as session:
        violations = await boundary_violations(session)
        if violations:
            raise AdminStartupRefused(f"The database role has privileges it must not have: {', '.join(violations)}.")
        version = await schema_version(session)
        if version != EXPECTED_SCHEMA_VERSION:
            raise AdminStartupRefused(f"Database schema is at '{version}' but this release needs '{EXPECTED_SCHEMA_VERSION}'.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO)
    get_admin_settings()
    await check_startup_boundary()
    logger.info("Platform Admin API started.")
    yield
    await dispose_admin_engine()


def _error(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details)).model_dump()
    return JSONResponse(status_code=status_code, content=body)


def create_app() -> FastAPI:
    application = FastAPI(title="VoiceQuote Platform Admin API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @application.exception_handler(AppException)
    async def _app_error(request: Request, exc: AppException):
        logger.warning("%s on %s %s", exc.code, request.method, request.url.path)
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @application.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # Locations and error types only. Pydantic's errors include the input, which can be a password.
        details = [{"loc": [str(p) for p in e.get("loc", [])], "type": e.get("type")} for e in exc.errors()]
        logger.warning("Validation failed on %s %s", request.method, request.url.path)
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "Please check the values you entered.", details)

    @application.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))

    @application.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception):
        # The class name only: database driver messages can quote values.
        logger.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR", "Something went wrong.")

    @application.middleware("http")
    async def _security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # Stops here, or the server would log it again with its full message.
            logger.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
            response = _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR", "Something went wrong.")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if get_admin_settings().ADMIN_COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    origins = get_admin_settings().allowed_origins
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @application.get("/health", include_in_schema=False)
    async def health():
        return {"status": "healthy"}

    application.include_router(router)
    application.include_router(showrooms_router)
    return application


app = create_app()
