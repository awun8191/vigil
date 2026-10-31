"""Uniform application exception hierarchy and FastAPI JSON error response handlers."""
from typing import Any
import uuid
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppException(Exception):
    """Base application exception with machine-readable code, HTTP status, and optional details."""

    def __init__(
        self,
        message: str = "Internal application error",
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


class NotFoundError(AppException):
    """Raised when a requested resource does not exist."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=404, details=details)


class UnauthorizedError(AppException):
    """Raised when authentication credentials are missing, invalid, or expired."""

    def __init__(
        self,
        message: str = "Unauthorized",
        code: str = "UNAUTHORIZED",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=401, details=details)


class ForbiddenError(AppException):
    """Raised when caller lacks authorization to perform requested action."""

    def __init__(
        self,
        message: str = "Forbidden",
        code: str = "FORBIDDEN",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=403, details=details)


class ConflictError(AppException):
    """Raised when an operation conflicts with existing entity or concurrent state."""

    def __init__(
        self,
        message: str = "Conflict detected",
        code: str = "CONFLICT",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=409, details=details)


class ValidationError(AppException):
    """Raised when request payload or business logic parameter validation fails."""

    def __init__(
        self,
        message: str = "Validation failed",
        code: str = "VALIDATION_ERROR",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=422, details=details)


class BadGatewayError(AppException):
    """Raised when an upstream dependency (Jev, LLM, MCP connector) returns an invalid response."""

    def __init__(
        self,
        message: str = "Bad gateway from upstream provider",
        code: str = "BAD_GATEWAY",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=502, details=details)


class ServiceUnavailableError(AppException):
    """Raised when a required service (database, sandbox daemon) is temporarily unavailable."""

    def __init__(
        self,
        message: str = "Service temporarily unavailable",
        code: str = "SERVICE_UNAVAILABLE",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=503, details=details)


def extract_request_id(request: Request) -> str:
    """Extract request correlation ID from request.state or fallback to headers/fresh UUID."""
    req_id = getattr(request.state, "request_id", None)
    if not req_id:
        req_id = request.headers.get("x-request-id") or request.headers.get("X-Request-ID")
    if not req_id or not str(req_id).strip():
        req_id = str(uuid.uuid4())
    return str(req_id).strip()


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handler for all domain AppException subclasses."""
    request_id = extract_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id,
            "details": exc.details,
        },
        headers={"X-Request-ID": request_id},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handler for FastAPI Pydantic request validation errors."""
    request_id = extract_request_id(request)
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "request_id": request_id,
            "details": jsonable_encoder(exc.errors()),
        },
        headers={"X-Request-ID": request_id},
    )


async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handler for Starlette/FastAPI built-in HTTP exceptions (e.g. 404 unmapped routes, 405)."""
    request_id = extract_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
            "request_id": request_id,
            "details": None,
        },
        headers={"X-Request-ID": request_id},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected internal exceptions (500)."""
    import time
    request_id = extract_request_id(request)
    headers = {"X-Request-ID": request_id}
    start_time = getattr(request.state, "start_time", None)
    if start_time is not None:
        duration = time.perf_counter() - start_time
        headers["X-Process-Time"] = f"{duration:.6f}s"

    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred",
            "request_id": request_id,
            "details": None,
        },
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register uniform exception handlers onto the target FastAPI application instance."""
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, starlette_http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
