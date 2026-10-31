"""Unit tests verifying AppException class hierarchy, status codes, and attributes."""
import pytest
from src.config.exceptions import (
    AppException,
    BadGatewayError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)


def test_app_exception_defaults():
    """Verify base AppException default attributes."""
    exc = AppException("An error occurred")
    assert str(exc) == "An error occurred"
    assert exc.message == "An error occurred"
    assert exc.code == "INTERNAL_ERROR"
    assert exc.status_code == 500
    assert exc.details is None


def test_app_exception_custom_attributes():
    """Verify AppException custom attributes override defaults."""
    exc = AppException(
        message="Custom message",
        code="CUSTOM_CODE",
        status_code=418,
        details={"info": "teapot"},
    )
    assert exc.message == "Custom message"
    assert exc.code == "CUSTOM_CODE"
    assert exc.status_code == 418
    assert exc.details == {"info": "teapot"}


def test_not_found_error():
    """Verify NotFoundError sets 404 and NOT_FOUND code."""
    exc = NotFoundError("Card not found", code="CARD_NOT_FOUND", details={"id": 12})
    assert isinstance(exc, AppException)
    assert exc.status_code == 404
    assert exc.code == "CARD_NOT_FOUND"
    assert exc.message == "Card not found"
    assert exc.details == {"id": 12}


def test_unauthorized_error():
    """Verify UnauthorizedError sets 401 and UNAUTHORIZED code."""
    exc = UnauthorizedError()
    assert isinstance(exc, AppException)
    assert exc.status_code == 401
    assert exc.code == "UNAUTHORIZED"
    assert exc.message == "Unauthorized"


def test_forbidden_error():
    """Verify ForbiddenError sets 403 and FORBIDDEN code."""
    exc = ForbiddenError()
    assert isinstance(exc, AppException)
    assert exc.status_code == 403
    assert exc.code == "FORBIDDEN"
    assert exc.message == "Forbidden"


def test_conflict_error():
    """Verify ConflictError sets 409 and CONFLICT code."""
    exc = ConflictError("Device already paired")
    assert isinstance(exc, AppException)
    assert exc.status_code == 409
    assert exc.code == "CONFLICT"
    assert exc.message == "Device already paired"


def test_validation_error():
    """Verify ValidationError sets 422 and VALIDATION_ERROR code."""
    exc = ValidationError("Invalid pairing code format")
    assert isinstance(exc, AppException)
    assert exc.status_code == 422
    assert exc.code == "VALIDATION_ERROR"
    assert exc.message == "Invalid pairing code format"


def test_upstream_exceptions():
    """Verify BadGatewayError (502) and ServiceUnavailableError (503)."""
    bg = BadGatewayError()
    assert bg.status_code == 502
    assert bg.code == "BAD_GATEWAY"

    su = ServiceUnavailableError()
    assert su.status_code == 503
    assert su.code == "SERVICE_UNAVAILABLE"


def test_inheritance_tree():
    """Verify all custom exception subclasses inherit from AppException and Exception."""
    subclasses = [
        NotFoundError,
        UnauthorizedError,
        ForbiddenError,
        ConflictError,
        ValidationError,
        BadGatewayError,
        ServiceUnavailableError,
    ]
    for sub in subclasses:
        assert issubclass(sub, AppException)
        assert issubclass(sub, Exception)
