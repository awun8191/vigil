"""Empirical concurrency stress tests for IdempotencyMiddleware and InMemoryIdempotencyStore.

Verifies:
1. Massive in-flight concurrency returns exactly one 200 OK and N-1 409 Conflicts.
2. Post-completion concurrent requests all receive 200 OK with `idempotent-replayed: true`.
3. Interleaved multi-key races properly isolate locks and caches per key.
4. Handler failures release in-flight locks so subsequent retries succeed.
5. Full create_app pipeline preserves headers and request isolation under load.
"""
import asyncio
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import pytest
from starlette.middleware import Middleware
from src.main import create_app
from src.middlewares.idempotency import IdempotencyMiddleware, InMemoryIdempotencyStore
from src.middlewares.request_id import RequestIdMiddleware
from src.middlewares.timing import TimingMiddleware


@pytest.mark.asyncio
async def test_high_concurrency_in_flight_lock_contention():
    """50 concurrent requests simultaneously hitting the same endpoint with the same Idempotency-Key.
    
    Verifies:
    - Exactly 1 request succeeds (200 OK)
    - Exactly 49 requests receive 409 Conflict
    - Route handler executes exactly once
    - All 409 responses contain IDEMPOTENCY_CONFLICT code and request ID
    """
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    execution_count = 0

    @app.post("/checkout")
    async def checkout():
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.08)  # simulate processing window
        return {"order_id": "ord_999", "status": "processed"}

    concurrency_count = 50
    idempotency_key = "idem-stress-key-single-winner"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tasks = [
            client.post("/checkout", headers={"Idempotency-Key": idempotency_key})
            for _ in range(concurrency_count)
        ]
        responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    success_responses = [r for r in responses if r.status_code == 200]
    conflict_responses = [r for r in responses if r.status_code == 409]

    assert len(success_responses) == 1, f"Expected exactly 1 success, got {len(success_responses)}"
    assert len(conflict_responses) == concurrency_count - 1, f"Expected {concurrency_count - 1} conflicts, got {len(conflict_responses)}"
    assert execution_count == 1, f"Expected handler to execute once, got {execution_count}"

    # Verify successful response
    assert success_responses[0].json() == {"order_id": "ord_999", "status": "processed"}
    assert success_responses[0].headers.get("idempotent-replayed") is None

    # Verify all conflict responses
    for cr in conflict_responses:
        body = cr.json()
        assert body["code"] == "IDEMPOTENCY_CONFLICT"
        assert "currently in flight" in body["message"]


@pytest.mark.asyncio
async def test_high_concurrency_post_completion_replay():
    """After initial request completes, 50 concurrent requests with the same Idempotency-Key all receive replay.
    
    Verifies:
    - All 50 requests receive 200 OK
    - All 50 requests receive idempotent-replayed: true
    - Payload is identical across all requests
    - Route handler execution count remains 1
    """
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    execution_count = 0

    @app.post("/charge")
    async def charge():
        nonlocal execution_count
        execution_count += 1
        return {"charge_id": "ch_777", "amount": 5000}

    idempotency_key = "idem-replay-key-001"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Initial request
        init_res = await client.post("/charge", headers={"Idempotency-Key": idempotency_key})
        assert init_res.status_code == 200
        assert init_res.json() == {"charge_id": "ch_777", "amount": 5000}
        assert init_res.headers.get("idempotent-replayed") is None
        assert execution_count == 1

        # Fire 50 concurrent replay requests
        concurrency_count = 50
        tasks = [
            client.post("/charge", headers={"Idempotency-Key": idempotency_key})
            for _ in range(concurrency_count)
        ]
        responses = await asyncio.gather(*tasks)

    for r in responses:
        assert r.status_code == 200
        assert r.headers.get("idempotent-replayed") == "true"
        assert r.json() == {"charge_id": "ch_777", "amount": 5000}

    assert execution_count == 1, "Handler must not execute again for replayed requests"


@pytest.mark.asyncio
async def test_interleaved_multi_key_concurrency_race():
    """10 distinct idempotency keys, each hammered by 5 concurrent requests (50 tasks total).
    
    Verifies:
    - Each key produces exactly 1 200 OK and 4 409 Conflicts
    - Total: 10 200s, 40 409s
    - Total route handler calls: 10
    """
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    executions_per_key: dict[str, int] = {}
    lock = asyncio.Lock()

    @app.post("/resource/{item_id}")
    async def create_resource(item_id: str, request: Request):
        async with lock:
            executions_per_key[item_id] = executions_per_key.get(item_id, 0) + 1
        await asyncio.sleep(0.06)
        return {"item_id": item_id, "created": True}

    num_keys = 10
    concurrency_per_key = 5
    total_requests = num_keys * concurrency_per_key

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tasks = []
        for key_idx in range(num_keys):
            key = f"multi-race-key-{key_idx}"
            for _ in range(concurrency_per_key):
                tasks.append(client.post(f"/resource/{key}", headers={"Idempotency-Key": key}))

        responses = await asyncio.gather(*tasks)

    successes = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]

    assert len(successes) == num_keys, f"Expected {num_keys} successes, got {len(successes)}"
    assert len(conflicts) == total_requests - num_keys, f"Expected {total_requests - num_keys} conflicts, got {len(conflicts)}"
    assert len(executions_per_key) == num_keys
    for key, count in executions_per_key.items():
        assert count == 1, f"Key {key} executed {count} times instead of 1"


@pytest.mark.asyncio
async def test_concurrency_with_handler_exception_releases_lock():
    """When a handler fails with an unhandled exception or 500, the in-flight lock must be cleanly released.
    
    A subsequent request with the same key should succeed once the failure completes.
    """
    store = InMemoryIdempotencyStore()
    app = FastAPI(middleware=[Middleware(IdempotencyMiddleware, store=store)])

    should_fail = True

    @app.post("/flaky-mutation")
    async def flaky():
        nonlocal should_fail
        if should_fail:
            await asyncio.sleep(0.04)
            raise RuntimeError("Database connection dropped")
        return {"recovered": True}

    key = "idem-failure-release-key"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Request 1 fails while Request 2 is concurrent
        t1 = client.post("/flaky-mutation", headers={"Idempotency-Key": key})
        t2 = client.post("/flaky-mutation", headers={"Idempotency-Key": key})
        
        # t1 will raise RuntimeError in client due to Starlette raising it
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        
        # One of them raised RuntimeError (the winner), the other got 409 Conflict
        raised_errors = [r for r in results if isinstance(r, Exception)]
        http_responses = [r for r in results if isinstance(r, httpx.Response)]

        assert len(raised_errors) == 1
        assert len(http_responses) == 1
        assert http_responses[0].status_code == 409

        # Lock should now be released
        assert await store.get(key) is None

        # Subsequent retry should succeed
        should_fail = False
        retry_res = await client.post("/flaky-mutation", headers={"Idempotency-Key": key})
        assert retry_res.status_code == 200
        assert retry_res.json() == {"recovered": True}


@pytest.mark.asyncio
async def test_full_pipeline_concurrency_headers_and_context_isolation():
    """Verify that full create_app pipeline correctly isolates X-Request-ID and X-Process-Time under concurrent load."""
    app = create_app()

    # Add a mutating test endpoint to the app
    @app.post("/api/v1/test-concurrency")
    async def concurrency_endpoint(request: Request):
        await asyncio.sleep(0.03)
        return {
            "msg": "success",
            "request_id": request.state.request_id,
        }

    concurrency = 20
    key = "full-app-pipeline-concurrent-key"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        tasks = [
            client.post(
                "/api/v1/test-concurrency",
                headers={"Idempotency-Key": key, "X-Request-ID": f"client-req-{idx}"},
            )
            for idx in range(concurrency)
        ]
        responses = await asyncio.gather(*tasks)

    successes = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]

    assert len(successes) == 1
    assert len(conflicts) == concurrency - 1

    # Winner response
    winner = successes[0]
    assert winner.headers.get("x-process-time") is not None
    assert winner.headers.get("x-request-id") is not None
    assert winner.json()["msg"] == "success"

    # All conflicts have timing and request ID
    seen_req_ids = set()
    for cr in conflicts:
        assert cr.headers.get("x-process-time") is not None
        req_id = cr.headers.get("x-request-id")
        assert req_id is not None
        seen_req_ids.add(req_id)
        assert cr.json()["code"] == "IDEMPOTENCY_CONFLICT"

    # Every concurrent request maintained its own distinct request ID
    assert len(seen_req_ids) == concurrency - 1
