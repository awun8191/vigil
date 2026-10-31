"""Adversarial challenge tests for Milestone 2 error handling, headers, and middleware pipeline.

Verifies:
1. Unmapped endpoints (404), method not allowed (405), validation errors (422),
   custom AppExceptions, and 500 server crashes strictly return {"code", "message", "request_id", "details"}.
2. Headers X-Request-ID and X-Process-Time are present and valid on all responses.
3. Client-supplied X-Request-ID is echoed rather than replaced across all error conditions.
4. Duplicate request headers are handled correctly and client-supplied correlation ID is preserved.
5. Inbound blank or whitespace headers trigger fallback UUID generation.
6. 500 crashes do not leak internal tracebacks, exception names, or sensitive information.
"""
from __future__ import annotations

import asyncio
import re
import uuid
import httpx
from pydantic import BaseModel
import pytest
from fastapi import FastAPI, Response
from src.main import create_app
from src.config.exceptions import (
    AppException,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

PROCESS_TIME_REGEX = re.compile(r"^\d+\.\d{6}s$")


class SampleBody(BaseModel):
    name: str
    amount: int


class CustomPaymentRequiredError(AppException):
    def __init__(self, message: str = "Payment required", details: dict | None = None) -> None:
        super().__init__(message=message, code="PAYMENT_REQUIRED", status_code=402, details=details)


@pytest.fixture
def adversarial_app() -> FastAPI:
    """Creates a full application instance with the complete production middleware stack and challenge routes."""
    app = create_app()

    @app.get("/test/custom-forbidden")
    def route_forbidden():
        raise ForbiddenError("Access to this domain resource is denied", details={"reason": "unsupported_scope"})

    @app.get("/test/custom-app-subclass")
    def route_subclass():
        raise CustomPaymentRequiredError(details={"invoice_id": "inv_12345"})

    @app.get("/test/crash-sync")
    def route_crash_sync():
        return 1 / 0

    @app.get("/test/crash-async")
    async def route_crash_async():
        await asyncio.sleep(0.005)
        raise RuntimeError("Internal database connection failed: secret_user:secret_pass")

    @app.post("/test/pydantic-validation")
    def route_pydantic(payload: SampleBody):
        return {"received": payload.model_dump()}

    @app.get("/test/cookies-duplicate")
    def route_cookies(response: Response):
        response.set_cookie("session_id", "abc", path="/")
        response.set_cookie("remember_me", "true", path="/")
        return {"cookies": "set"}

    return app


@pytest.mark.asyncio
async def test_adversarial_error_schema_conformance(adversarial_app: FastAPI):
    """
    Stress-test that 404, 405, 422, custom AppExceptions, and 500 crashes:
    1. Strictly return {"code", "message", "request_id", "details"} with no extra keys.
    2. Contain both X-Request-ID and X-Process-Time headers.
    3. Echo the client-supplied X-Request-ID in both header and body.
    """
    transport = httpx.ASGITransport(app=adversarial_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        test_cases = [
            ("404 unmapped route", "GET", "/nonexistent/unmapped/path", {}, 404, "HTTP_404"),
            ("405 method not allowed", "POST", "/test/custom-forbidden", {}, 405, "HTTP_405"),
            ("422 pydantic malformed json", "POST", "/test/pydantic-validation", {"content": "not-json", "headers": {"Content-Type": "application/json"}}, 422, "VALIDATION_ERROR"),
            ("422 pydantic type error", "POST", "/test/pydantic-validation", {"json": {"name": "test", "amount": "invalid-int"}}, 422, "VALIDATION_ERROR"),
            ("403 custom AppException", "GET", "/test/custom-forbidden", {}, 403, "FORBIDDEN"),
            ("402 custom AppException subclass", "GET", "/test/custom-app-subclass", {}, 402, "PAYMENT_REQUIRED"),
            ("500 sync zero division crash", "GET", "/test/crash-sync", {}, 500, "INTERNAL_SERVER_ERROR"),
            ("500 async unhandled crash", "GET", "/test/crash-async", {}, 500, "INTERNAL_SERVER_ERROR"),
        ]

        for desc, method, path, kwargs, expected_status, expected_code in test_cases:
            client_req_id = f"client-id-{expected_status}-{expected_code.lower()}"
            req_headers = kwargs.pop("headers", {})
            req_headers["X-Request-ID"] = client_req_id

            response = await client.request(method, path, headers=req_headers, **kwargs)

            # 1. Status Code
            assert response.status_code == expected_status, f"{desc} status mismatch: {response.status_code}"

            # 2. X-Request-ID Header Echo
            assert response.headers.get("x-request-id") == client_req_id, f"{desc} failed to echo X-Request-ID header"

            # 3. X-Process-Time Header Presence and Format
            process_time = response.headers.get("x-process-time")
            assert process_time is not None, f"{desc} missing X-Process-Time header"
            assert PROCESS_TIME_REGEX.match(process_time), f"{desc} invalid X-Process-Time format: {process_time}"

            # 4. Strict Body Schema
            body = response.json()
            assert set(body.keys()) == {"code", "message", "request_id", "details"}, f"{desc} schema keys mismatch: {body.keys()}"
            assert body["code"] == expected_code, f"{desc} code mismatch: {body['code']}"
            assert isinstance(body["message"], str) and len(body["message"]) > 0
            assert body["request_id"] == client_req_id, f"{desc} body request_id mismatch: {body['request_id']}"

            # 5. Shielding: ensure 500 errors don't leak traceback or credentials
            if expected_status == 500:
                assert "secret_pass" not in response.text
                assert "Traceback" not in response.text
                assert "ZeroDivisionError" not in response.text
                assert "RuntimeError" not in response.text


@pytest.mark.asyncio
async def test_header_preservation_and_deduplication(adversarial_app: FastAPI):
    """
    Verify client-supplied X-Request-ID is preserved and echoed rather than replaced,
    and multiple/duplicate request headers are handled cleanly without clobbering other headers.
    """
    transport = httpx.ASGITransport(app=adversarial_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # A. Preserve client-supplied X-Request-ID when duplicate headers are sent
        req = client.build_request(
            "GET",
            "/nonexistent",
            headers=[("X-Request-ID", "preserved-client-id-1"), ("X-Request-ID", "ignored-client-id-2")],
        )
        response = await client.send(req)

        # Inbound first header is preserved
        assert response.headers.get("x-request-id") == "preserved-client-id-1"
        assert response.json()["request_id"] == "preserved-client-id-1"

        # Raw response headers have exactly one X-Request-ID
        req_id_raw_headers = [v for k, v in response.headers.raw if k.lower() == b"x-request-id"]
        assert len(req_id_raw_headers) == 1, f"Expected 1 X-Request-ID header, found {len(req_id_raw_headers)}"

        # B. Ensure other duplicate response headers (e.g. Set-Cookie) are completely preserved
        cookie_resp = await client.get("/test/cookies-duplicate", headers={"X-Request-ID": "cookie-test-req"})
        assert cookie_resp.status_code == 200
        set_cookies = [v for k, v in cookie_resp.headers.raw if k.lower() == b"set-cookie"]
        assert len(set_cookies) == 2, f"Expected 2 Set-Cookie headers, found {len(set_cookies)}"
        assert cookie_resp.headers.get("x-request-id") == "cookie-test-req"


@pytest.mark.asyncio
async def test_blank_or_missing_request_id_generates_valid_uuid(adversarial_app: FastAPI):
    """
    Verify that when X-Request-ID is missing, blank, or all-whitespace:
    A valid fresh UUID4 is generated, echoed in the header, and matched in the error body.
    """
    transport = httpx.ASGITransport(app=adversarial_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for header_val in [None, "", "   ", "\t"]:
            headers = {}
            if header_val is not None:
                headers["X-Request-ID"] = header_val

            response = await client.get("/test/custom-forbidden", headers=headers)
            assert response.status_code == 403

            header_id = response.headers.get("x-request-id")
            body_id = response.json().get("request_id")

            assert header_id is not None
            assert body_id is not None
            assert header_id == body_id
            # Verify it's a valid UUID
            parsed_uuid = uuid.UUID(header_id)
            assert parsed_uuid.version == 4
