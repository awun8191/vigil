"""Pure ASGI middleware generating and propagating request correlation IDs."""
from __future__ import annotations

import uuid
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config.logging import reset_request_id, set_request_id

REQUEST_ID_HEADER = b"x-request-id"


class RequestIdMiddleware:
    """ASGI middleware that extracts or generates X-Request-ID and binds it to state & contextvar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract existing X-Request-ID from inbound headers
        inbound_request_id: str | None = None
        for key, value in scope.get("headers", []):
            if key.lower() == REQUEST_ID_HEADER:
                decoded = value.decode("latin1").strip()
                if decoded:
                    inbound_request_id = decoded
                break

        # Generate fresh UUID4 if header is missing or blank
        request_id = inbound_request_id if inbound_request_id else str(uuid.uuid4())

        # Store in ASGI scope state
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        # Bind to structlog contextvar
        token = set_request_id(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Filter out existing X-Request-ID to prevent duplication
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode("latin1")))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_request_id(token)
