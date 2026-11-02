"""Configuration, exception handling, security, and router factory for Vigil."""
from src.config.exceptions import (
    AppException,
    BadGatewayError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
    register_exception_handlers,
)
from src.config.logging import (
    configure_logging,
    get_logger,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from src.config.router import create_api_router
from src.config.security import (
    SecurityError,
    decrypt_secret,
    encrypt_secret,
    hash_token,
    verify_token,
)
from src.config.settings import Settings, get_settings

__all__ = [
    "AppException",
    "BadGatewayError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "SecurityError",
    "ServiceUnavailableError",
    "Settings",
    "UnauthorizedError",
    "ValidationError",
    "configure_logging",
    "create_api_router",
    "decrypt_secret",
    "encrypt_secret",
    "get_logger",
    "get_request_id",
    "get_settings",
    "hash_token",
    "register_exception_handlers",
    "reset_request_id",
    "set_request_id",
    "verify_token",
]
