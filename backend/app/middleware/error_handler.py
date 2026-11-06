import traceback

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppException
from app.core.logging import logger
from app.schemas.common import ErrorResponse, ErrorDetail


def _log_unhandled(request: Request, exc: Exception) -> None:
    """The exception's class and where it happened, never its message.

    Messages can quote request values, and database errors can quote SQL parameters. The traceback
    here holds code locations and source lines only.
    """
    location = "".join(traceback.format_tb(exc.__traceback__))
    logger.error(f"Unhandled {type(exc).__name__} on {request.method} {request.url.path}\n{location}")


def _unhandled_response() -> JSONResponse:
    content = ErrorResponse(
        success=False,
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An internal server error occurred. Please contact system support.",
        ),
    ).model_dump()
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=content)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        # The code only: some messages name the email or record the request was about.
        logger.warning(f"Domain error [{exc.code}] on {request.method} {request.url.path}")
        content = ErrorResponse(
            success=False,
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        ).model_dump()
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Pydantic's errors include the submitted value, which can be a password. Log locations and types only.
        fields = [".".join(str(loc) for loc in err.get("loc", [])) + f" ({err.get('type')})" for err in exc.errors()]
        logger.warning(f"Validation error on {request.method} {request.url.path}: {', '.join(fields)}")
        clean_errors = []
        for err in exc.errors():
            clean_errors.append({
                "loc": [str(loc) for loc in err.get("loc", [])],
                "msg": err.get("msg"),
                "type": err.get("type"),
            })
        content = ErrorResponse(
            success=False,
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="Request validation failed. Please check your payload.",
                details=clean_errors,
            ),
        ).model_dump()
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=content)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        content = ErrorResponse(
            success=False,
            error=ErrorDetail(
                code=f"HTTP_{exc.status_code}",
                message=str(exc.detail),
            ),
        ).model_dump()
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        _log_unhandled(request, exc)
        return _unhandled_response()

    # An Exception handler alone still lets the error propagate, and the server then logs it with
    # its full message. Catching it in a middleware stops it here.
    @app.middleware("http")
    async def contain_unhandled_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            _log_unhandled(request, exc)
            return _unhandled_response()
