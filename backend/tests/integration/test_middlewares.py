"""Integration tests for pure ASGI middlewares: RequestIdMiddleware, TimingMiddleware, and IdempotencyMiddleware."""
import asyncio
import re
import uuid
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware import Middleware

from src.config.logging import get_request_id
from src.middlewares.idempotency import (
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
)
from src.middlewares.request_id import RequestIdMiddleware
from src.middlewares.timing import TimingMiddleware


# --- RequestIdMiddleware Tests ---

@pytest.mark.asyncio
async def test_request_id_generated_when_absent():
    app = FastAPI(middleware=[Middleware(RequestIdMiddleware)])

    @app.get("/test")
    async def endpoint(request: Request):
        return {"state_id": request.state.request_id, "ctx_id": get_request_id()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/test")
        assert res.status_code == 200
        header_id = res.headers.get("x-request-id")
        assert header_id is not None
        uuid_obj = uuid.UUID(header_id)
        assert str(uuid_obj) == header_id
        assert res.json()["state_id"] == header_id
        assert res.json()["ctx_id"] == header_id


@pytest.mark.asyncio
async def test_request_id_propagated_when_present():
    app = FastAPI(middleware=[Middleware(RequestIdMiddleware)])

    @app.get("/test")
    async def endpoint(request: Request):
        return {"state_id": request.state.request_id}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/test", headers={"X-Request-ID": "custom-trace-1234"})
        assert res.status_code == 200
        assert res.headers.get("x-request-id") == "custom-trace-1234"
        assert res.json()["state_id"] == "custom-trace-1234"


@pytest.mark.asyncio
async def test_request_id_sanitizes_blank_header():
    app = FastAPI(middleware=[Middleware(RequestIdMiddleware)])

    @app.get("/test")
    async def endpoint(request: Request):
        return {"state_id": request.state.request_id}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/test", headers={"X-Request-ID": "   "})
        assert res.status_code == 200
        header_id = res.headers.get("x-request-id")
        assert header_id is not None and header_id != "   "
        uuid.UUID(header_id)


@pytest.mark.asyncio
async def test_request_id_contextvar_cleanup_on_success_and_error():
    app = FastAPI(middleware=[Middleware(RequestIdMiddleware)])

    @app.get("/ok")
    async def ok_endpoint():
        return {"ok": True}

    @app.get("/error")
    async def error_endpoint():
        raise RuntimeError("Crash")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/ok")
        assert get_request_id() is None

        with pytest.raises(RuntimeError):
            await client.get("/error")
        assert get_request_id() is None


@pytest.mark.asyncio
async def test_request_id_non_http_passthrough():
    scope = {"type": "lifespan"}
    called = False

    async def dummy_app(s, r, send):
        nonlocal called
        called = True

    middleware = RequestIdMiddleware(dummy_app)
    await middleware(scope, None, None)
    assert called is True


# --- TimingMiddleware Tests ---

@pytest.mark.asyncio
async def test_timing_middleware_header_format():
    app = FastAPI(middleware=[Middleware(TimingMiddleware)])

    @app.get("/ping")
    async def ping():
        return {"status": "ok"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/ping")
        assert res.status_code == 200
        header = res.headers.get("x-process-time")
        assert header is not None
        assert re.match(r"^\d+\.\d{6}s$", header)


@pytest.mark.asyncio
async def test_timing_middleware_latency_measurement():
    app = FastAPI(middleware=[Middleware(TimingMiddleware)])

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.05)
        return {"status": "slow"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/slow")
        header = res.headers.get("x-process-time")
        duration = float(header[:-1])
        assert duration >= 0.05


@pytest.mark.asyncio
async def test_timing_middleware_streaming():
    app = FastAPI(middleware=[Middleware(TimingMiddleware)])

    @app.get("/stream")
    async def stream():
        async def gen():
            for i in range(3):
                yield f"chunk-{i}\n"
        return StreamingResponse(gen(), media_type="text/plain")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/stream")
        assert res.headers.get("x-process-time") is not None
        assert res.text == "chunk-0\nchunk-1\nchunk-2\n"


# --- IdempotencyMiddleware & Store Tests ---

@pytest.mark.asyncio
async def test_in_memory_store_lifecycle():
    store = InMemoryIdempotencyStore()
    assert await store.set_in_flight("k1", ttl_seconds=10) is True
    assert await store.set_in_flight("k1") is False
    assert await store.get("k1") == "IN_FLIGHT"
    await store.release("k1")
    assert await store.get("k1") is None


@pytest.mark.asyncio
async def test_idempotency_ignores_get_method():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    @app.get("/read")
    async def read():
        return {"data": 1}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/read", headers={"Idempotency-Key": "key-read"})
        assert res.status_code == 200
        assert await store.get("key-read") is None


@pytest.mark.asyncio
async def test_idempotency_mutating_without_header_proceeds():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    @app.post("/write")
    async def write():
        return {"created": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/write")
        assert res.status_code == 200
        assert res.json() == {"created": True}


@pytest.mark.asyncio
async def test_idempotency_replay_success():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])
    execution_count = 0

    @app.post("/action")
    async def action():
        nonlocal execution_count
        execution_count += 1
        return {"count": execution_count}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/action", headers={"Idempotency-Key": "action-1"})
        assert r1.status_code == 200
        assert r1.json() == {"count": 1}
        assert r1.headers.get("idempotent-replayed") is None

        r2 = await client.post("/action", headers={"Idempotency-Key": "action-1"})
        assert r2.status_code == 200
        assert r2.json() == {"count": 1}
        assert r2.headers.get("idempotent-replayed") == "true"
        assert execution_count == 1


@pytest.mark.asyncio
async def test_idempotency_in_flight_conflict_returns_409():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    @app.post("/slow-action")
    async def slow_action():
        await asyncio.sleep(0.05)
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        t1 = client.post("/slow-action", headers={"Idempotency-Key": "concurrent-key"})
        t2 = client.post("/slow-action", headers={"Idempotency-Key": "concurrent-key"})
        res1, res2 = await asyncio.gather(t1, t2)
        statuses = {res1.status_code, res2.status_code}
        assert 200 in statuses
        assert 409 in statuses
        conflict_res = res1 if res1.status_code == 409 else res2
        assert conflict_res.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_idempotency_invalid_keys_return_422():
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware)])

    @app.post("/action")
    async def action():
        return {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Blank key
        r_blank = await client.post("/action", headers={"Idempotency-Key": "   "})
        assert r_blank.status_code == 422
        assert r_blank.json()["code"] == "INVALID_IDEMPOTENCY_KEY"

        # Oversized key (>256 chars)
        r_over = await client.post("/action", headers={"Idempotency-Key": "x" * 257})
        assert r_over.status_code == 422
        assert r_over.json()["code"] == "INVALID_IDEMPOTENCY_KEY"


@pytest.mark.asyncio
async def test_idempotency_error_responses_not_cached():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])
    attempts = 0

    @app.post("/flaky")
    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return JSONResponse({"error": "transient"}, status_code=500)
        return {"recovered": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/flaky", headers={"Idempotency-Key": "flaky-key"})
        assert r1.status_code == 500
        assert await store.get("flaky-key") is None

        r2 = await client.post("/flaky", headers={"Idempotency-Key": "flaky-key"})
        assert r2.status_code == 200
        assert r2.json() == {"recovered": True}
        assert attempts == 2


@pytest.mark.asyncio
async def test_idempotency_exception_releases_lock():
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    @app.post("/crash")
    async def crash():
        raise RuntimeError("Crash")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError):
            await client.post("/crash", headers={"Idempotency-Key": "crash-key"})
        assert await store.get("crash-key") is None


# --- Combined Middleware Pipeline Tests ---

@pytest.mark.asyncio
async def test_full_pipeline_headers_and_error_propagation():
    store = InMemoryIdempotencyStore()
    app = FastAPI(
        middleware=[
            Middleware(RequestIdMiddleware),
            Middleware(TimingMiddleware),
            Middleware(IdempotencyMiddleware, store=store),
        ]
    )

    counter = 0

    @app.post("/items")
    async def create_item(request: Request):
        nonlocal counter
        counter += 1
        return {
            "item_id": counter,
            "request_id": request.state.request_id,
        }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Initial post
        r1 = await client.post("/items", headers={"Idempotency-Key": "order-123"})
        assert r1.status_code == 200
        req_id_1 = r1.headers.get("x-request-id")
        assert req_id_1 is not None
        assert r1.headers.get("x-process-time") is not None
        assert r1.headers.get("idempotent-replayed") is None
        assert r1.json()["item_id"] == 1

        # Replay post with new client correlation ID
        r2 = await client.post(
            "/items",
            headers={"Idempotency-Key": "order-123", "X-Request-ID": "custom-second-id"},
        )
        assert r2.status_code == 200
        assert r2.headers.get("x-request-id") == "custom-second-id"
        assert r2.headers.get("x-process-time") is not None
        assert r2.headers.get("idempotent-replayed") == "true"
        assert r2.json()["item_id"] == 1

        # Error response from IdempotencyMiddleware gets Request-ID and Timing headers
        r_err = await client.post(
            "/items",
            headers={"Idempotency-Key": "   ", "X-Request-ID": "trace-422"},
        )
        assert r_err.status_code == 422
        assert r_err.headers.get("x-request-id") == "trace-422"
        assert r_err.headers.get("x-process-time") is not None
        assert r_err.json()["code"] == "INVALID_IDEMPOTENCY_KEY"
        assert r_err.json()["request_id"] == "trace-422"
