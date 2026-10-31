"""Integration tests verifying uniform JSON error response contract and X-Request-ID echo."""
import httpx
from pydantic import BaseModel
import pytest
from fastapi import FastAPI
from src.config.exceptions import (
    BadGatewayError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
    register_exception_handlers,
)


@pytest.fixture
def error_test_app() -> FastAPI:
    """Fixture providing a test FastAPI application with registered exception handlers and test routes."""
    app = FastAPI(title="Error Contract Test App")
    register_exception_handlers(app)

    class SamplePayload(BaseModel):
        count: int
        name: str

    @app.get("/test/not-found")
    def route_not_found():
        raise NotFoundError(
            message="Card 99 not found",
            code="CARD_NOT_FOUND",
            details={"card_id": 99},
        )

    @app.get("/test/unauthorized")
    def route_unauthorized():
        raise UnauthorizedError(message="Bearer token expired")

    @app.get("/test/forbidden")
    def route_forbidden():
        raise ForbiddenError(message="Device not authorized for run execution")

    @app.get("/test/conflict")
    def route_conflict():
        raise ConflictError(message="Run is already executing")

    @app.get("/test/validation")
    def route_validation():
        raise ValidationError(
            message="Invalid parameters",
            details=[{"field": "query", "issue": "too short"}],
        )

    @app.get("/test/bad-gateway")
    def route_bad_gateway():
        raise BadGatewayError(message="Jev service timed out")

    @app.get("/test/service-unavailable")
    def route_service_unavailable():
        raise ServiceUnavailableError(message="Database pool exhausted")

    @app.post("/test/pydantic-validation")
    def route_pydantic(payload: SamplePayload):
        return payload

    @app.get("/test/crash")
    def route_crash():
        return 1 / 0

    return app


@pytest.mark.asyncio
async def test_app_exception_contract_and_header(error_test_app: FastAPI):
    """Verify AppException returns {code, message, request_id, details} and echoes X-Request-ID."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req_id = "test-corr-id-001"
        response = await client.get("/test/not-found", headers={"X-Request-ID": req_id})

        assert response.status_code == 404
        assert response.headers.get("x-request-id") == req_id

        data = response.json()
        assert set(data.keys()) == {"code", "message", "request_id", "details"}
        assert data["code"] == "CARD_NOT_FOUND"
        assert data["message"] == "Card 99 not found"
        assert data["request_id"] == req_id
        assert data["details"] == {"card_id": 99}


@pytest.mark.asyncio
async def test_all_app_exception_status_codes(error_test_app: FastAPI):
    """Verify status codes and error codes across all AppException subclasses."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthorized (401)
        r_unauth = await client.get("/test/unauthorized")
        assert r_unauth.status_code == 401
        assert r_unauth.json()["code"] == "UNAUTHORIZED"

        # Forbidden (403)
        r_forbid = await client.get("/test/forbidden")
        assert r_forbid.status_code == 403
        assert r_forbid.json()["code"] == "FORBIDDEN"

        # Conflict (409)
        r_conf = await client.get("/test/conflict")
        assert r_conf.status_code == 409
        assert r_conf.json()["code"] == "CONFLICT"

        # Custom Validation (422)
        r_val = await client.get("/test/validation")
        assert r_val.status_code == 422
        assert r_val.json()["code"] == "VALIDATION_ERROR"
        assert isinstance(r_val.json()["details"], list)

        # Bad Gateway (502)
        r_bg = await client.get("/test/bad-gateway")
        assert r_bg.status_code == 502
        assert r_bg.json()["code"] == "BAD_GATEWAY"

        # Service Unavailable (503)
        r_su = await client.get("/test/service-unavailable")
        assert r_su.status_code == 503
        assert r_su.json()["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_pydantic_validation_error_contract(error_test_app: FastAPI):
    """Verify FastAPI RequestValidationError returns uniform 422 payload."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req_id = "val-req-888"
        response = await client.post(
            "/test/pydantic-validation",
            json={"count": "not-an-int", "name": "widget"},
            headers={"X-Request-ID": req_id},
        )

        assert response.status_code == 422
        assert response.headers.get("x-request-id") == req_id

        data = response.json()
        assert set(data.keys()) == {"code", "message", "request_id", "details"}
        assert data["code"] == "VALIDATION_ERROR"
        assert data["request_id"] == req_id
        assert isinstance(data["details"], list)
        assert len(data["details"]) > 0


@pytest.mark.asyncio
async def test_starlette_unmapped_route_404_conformance(error_test_app: FastAPI):
    """Verify unmapped routes return uniform 404 payload rather than default FastAPI format."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/completely/nonexistent/path")

        assert response.status_code == 404
        assert "x-request-id" in response.headers

        data = response.json()
        assert set(data.keys()) == {"code", "message", "request_id", "details"}
        assert data["code"] == "HTTP_404"
        assert data["message"] == "Not Found"


@pytest.mark.asyncio
async def test_unhandled_crash_500_conformance(error_test_app: FastAPI):
    """Verify unhandled Python exceptions return safe generic 500 payload."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req_id = "crash-trace-123"
        response = await client.get("/test/crash", headers={"X-Request-ID": req_id})

        assert response.status_code == 500
        assert response.headers.get("x-request-id") == req_id

        data = response.json()
        assert set(data.keys()) == {"code", "message", "request_id", "details"}
        assert data["code"] == "INTERNAL_SERVER_ERROR"
        assert data["message"] == "An unexpected error occurred"
        assert data["request_id"] == req_id
        assert data["details"] is None


@pytest.mark.asyncio
async def test_generated_request_id_when_header_absent(error_test_app: FastAPI):
    """Verify a fresh valid UUID4 is generated and echoed when client provides no X-Request-ID."""
    transport = httpx.ASGITransport(app=error_test_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test/not-found")
        header_req_id = response.headers.get("x-request-id")
        body_req_id = response.json().get("request_id")

        assert header_req_id is not None
        assert body_req_id is not None
        assert header_req_id == body_req_id
        assert len(header_req_id) == 36
