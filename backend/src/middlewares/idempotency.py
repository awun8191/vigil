"""Pure ASGI middleware implementing idempotency locking and response caching."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol, runtime_checkable
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

IDEMPOTENCY_HEADER = b"idempotency-key"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CachedResponse:
    """Immutable representation of a cached HTTP response."""

    def __init__(self, status_code: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body = body


@runtime_checkable
class IdempotencyStore(Protocol):
    """Protocol defining the idempotency storage engine contract."""

    async def get(self, key: str) -> CachedResponse | str | None:
        """Returns CachedResponse if completed, 'IN_FLIGHT' if processing, or None."""
        ...

    async def set_in_flight(self, key: str, ttl_seconds: int = 300) -> bool:
        """Acquire lock for key. Returns False if key already exists."""
        ...

    async def set_completed(self, key: str, response: CachedResponse, ttl_seconds: int = 86400) -> None:
        """Cache completed response."""
        ...

    async def release(self, key: str) -> None:
        """Release in-flight lock if execution failed or was cancelled."""
        ...


class InMemoryIdempotencyStore:
    """Thread-safe and asyncio-locked in-memory store for development and testing."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> CachedResponse | str | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry["expires_at"] <= time.time():
                del self._entries[key]
                return None
            return entry["payload"]

    async def set_in_flight(self, key: str, ttl_seconds: int = 300) -> bool:
        async with self._lock:
            now = time.time()
            entry = self._entries.get(key)
            if entry is not None and entry["expires_at"] > now:
                return False
            self._entries[key] = {
                "payload": "IN_FLIGHT",
                "expires_at": now + ttl_seconds,
            }
            return True

    async def set_completed(self, key: str, response: CachedResponse, ttl_seconds: int = 86400) -> None:
        async with self._lock:
            self._entries[key] = {
                "payload": response,
                "expires_at": time.time() + ttl_seconds,
            }

    async def release(self, key: str) -> None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry["payload"] == "IN_FLIGHT":
                del self._entries[key]


class IdempotencyMiddleware:
    """Pure ASGI middleware managing idempotency validation, locking, and replay."""

    def __init__(self, app: ASGIApp, store: IdempotencyStore | None = None) -> None:
        self.app = app
        self.store = store if store is not None else InMemoryIdempotencyStore()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET").upper() not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        # Find Idempotency-Key header in scope
        inbound_key: str | None = None
        has_idempotency_header = False
        for key, value in scope.get("headers", []):
            if key.lower() == IDEMPOTENCY_HEADER:
                has_idempotency_header = True
                inbound_key = value.decode("latin1").strip()
                break

        # If header was not supplied, request proceeds unmanaged
        if not has_idempotency_header:
            await self.app(scope, receive, send)
            return

        req_id = scope.get("state", {}).get("request_id", "")

        # Validate idempotency key length & content
        if not inbound_key or len(inbound_key) > 256:
            err_resp = JSONResponse(
                status_code=422,
                content={
                    "code": "INVALID_IDEMPOTENCY_KEY",
                    "message": "Idempotency-Key header must be between 1 and 256 characters",
                    "request_id": req_id,
                    "details": None,
                },
            )
            await err_resp(scope, receive, send)
            return

        # Check existing state in store
        cached = await self.store.get(inbound_key)
        if cached == "IN_FLIGHT":
            conflict_resp = JSONResponse(
                status_code=409,
                content={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "A request with this Idempotency-Key is currently in flight",
                    "request_id": req_id,
                    "details": None,
                },
            )
            await conflict_resp(scope, receive, send)
            return

        if isinstance(cached, CachedResponse):
            # Replay cached response
            replay_headers = [
                (k, v)
                for k, v in cached.headers
                if k.lower() not in {b"content-length", b"idempotent-replayed"}
            ]
            replay_headers.append((b"idempotent-replayed", b"true"))
            replay_headers.append((b"content-length", str(len(cached.body)).encode("ascii")))

            await send({
                "type": "http.response.start",
                "status": cached.status_code,
                "headers": replay_headers,
            })
            await send({
                "type": "http.response.body",
                "body": cached.body,
                "more_body": False,
            })
            return

        # Acquire lock
        acquired = await self.store.set_in_flight(inbound_key)
        if not acquired:
            conflict_resp = JSONResponse(
                status_code=409,
                content={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "A request with this Idempotency-Key is currently in flight",
                    "request_id": req_id,
                    "details": None,
                },
            )
            await conflict_resp(scope, receive, send)
            return

        # Track execution and response
        response_status = 200
        response_headers: list[tuple[bytes, bytes]] = []
        response_body_chunks: list[bytes] = []
        completed_successfully = False

        async def send_recorder(message: Message) -> None:
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    response_body_chunks.append(chunk)
            await send(message)

        try:
            await self.app(scope, receive, send_recorder)
            if 200 <= response_status < 400:
                full_body = b"".join(response_body_chunks)
                await self.store.set_completed(
                    inbound_key,
                    CachedResponse(response_status, response_headers, full_body),
                )
                completed_successfully = True
        finally:
            if not completed_successfully:
                await self.store.release(inbound_key)
