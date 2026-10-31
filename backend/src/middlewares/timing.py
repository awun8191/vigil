"""Pure ASGI middleware recording request processing latency in X-Process-Time."""
from __future__ import annotations

import time
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PROCESS_TIME_HEADER = b"x-process-time"


class TimingMiddleware:
    """ASGI middleware measuring server processing latency and attaching X-Process-Time header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        scope.setdefault("state", {})["start_time"] = start_time

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                duration = time.perf_counter() - start_time
                formatted_duration = f"{duration:.6f}s"
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != PROCESS_TIME_HEADER
                ]
                headers.append((PROCESS_TIME_HEADER, formatted_duration.encode("latin1")))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
