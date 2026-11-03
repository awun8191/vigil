"""External MCP server client: stdio and SSE transport, handshake, tool discovery.

The stdio transport speaks a minimal newline-delimited JSON-RPC dialect: one
``initialize`` request carrying ``protocolVersion``, ``capabilities`` and
``clientInfo``, the ``notifications/initialized`` notification, then the work
request (``tools/list`` or ``tools/call``). Each message is a single JSON object
per line, matched by ``id``.

The SSE transport does not do that. It posts one ``tools/list`` or ``tools/call``
request and reads the answer, with no ``initialize`` request and no
``notifications/initialized`` notification, so a server that enforces the
handshake rejects it. TODO(StreamMCP): open the session (the ``GET`` event
stream), keep the session id it returns, echo that id on every ``POST``, and
send the initialize request and the initialized notification before the work
request.

Only the background worker calls this module. Request handlers never spawn a
process: registration enqueues a ``discover_mcp_tools`` job and returns
202, and the worker runs discovery there.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
from asyncio.subprocess import PIPE
from dataclasses import dataclass, field
from typing import Any

import httpx

#: Transports that ``discover_tools`` accepts.
SUPPORTED_TRANSPORTS: tuple[str, ...] = ("stdio", "sse")

#: Time limit of one handshake request round trip, in seconds.
HANDSHAKE_TIMEOUT_SECONDS = 10.0

#: Time limit of the SSE handshake stream, in seconds.
SSE_TIMEOUT_SECONDS = 10.0

#: Largest single protocol line accepted from a third party server.
MAX_LINE_BYTES = 1_048_576

#: Protocol version announced in the initialize request.
MCP_PROTOCOL_VERSION = "2024-11-05"

#: Client name announced in ``clientInfo``.
MCP_CLIENT_NAME = "vigil"

#: Client version announced in ``clientInfo``.
MCP_CLIENT_VERSION = "1.0"

#: Trailing bytes of a server's stderr kept for the error message.
STDERR_TAIL_BYTES = 4096

#: Longest wait for a terminated server to exit before it is killed, in seconds.
STOP_TIMEOUT_SECONDS = 5.0

#: Longest bounded wait for the stderr drain to reach EOF before the tail is read.
STDERR_DRAIN_JOIN_SECONDS = 0.2


class MCPHandshakeError(ValueError):
    """A third party MCP server failed the handshake. The message names the reason."""


class MCPToolCollisionError(ValueError):
    """A discovered tool name collides with a built-in tool."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Connection parameters of one third party MCP server."""

    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class MCPToolDefinition:
    """One tool discovered on a third party MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    level: str = "external"


def _normalize_tools(raw: Any) -> list[dict[str, Any]]:
    """Validate a raw tool list. Every entry needs a non-empty string name."""
    if not isinstance(raw, list):
        raise MCPHandshakeError(
            "handshake failed: the tools/list result holds no tool list"
        )
    tools: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise MCPHandshakeError(
                "handshake failed: a tools/list entry is not an object"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MCPHandshakeError(
                "handshake failed: a tools/list entry has no name"
            )
        tools.append(
            {
                "name": name,
                "description": entry.get("description", ""),
                "input_schema": entry.get("input_schema", {}),
            }
        )
    return tools


class _StderrTail:
    """Bounded tail of a server's stderr, kept for the error message only."""

    def __init__(self, limit: int = STDERR_TAIL_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        excess = len(self._buffer) - self._limit
        if excess > 0:
            del self._buffer[:excess]

    def text(self) -> str:
        return bytes(self._buffer).decode("utf-8", "replace").strip()


async def _drain_stderr(
    stream: asyncio.StreamReader | None,
    tail: _StderrTail,
) -> None:
    """Read a server's stderr until EOF so a chatty server never blocks the pipe.

    A server writing more than one pipe buffer of diagnostics would block on its
    own ``write`` call, never answer the request, and hang the call until the
    timeout. Draining concurrently is the only way to keep the exchange moving.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        tail.feed(chunk)


def _initialize_params() -> dict[str, Any]:
    """Build the initialize params the MCP schema requires."""
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": MCP_CLIENT_NAME, "version": MCP_CLIENT_VERSION},
    }


async def _write_message(
    proc: asyncio.subprocess.Process,
    message: dict[str, Any],
    step: str,
) -> None:
    """Write one JSON-RPC line to the server's stdin."""
    line = (json.dumps(message) + "\n").encode("utf-8")
    try:
        proc.stdin.write(line)  # type: ignore[union-attr]
        await proc.stdin.drain()  # type: ignore[union-attr]
    except (BrokenPipeError, ConnectionResetError) as exc:
        code = proc.returncode
        raise MCPHandshakeError(
            f"stdio handshake failed during {step}: "
            f"the process exited with code {code}"
        ) from exc


async def _exchange(
    proc: asyncio.subprocess.Process,
    message: dict[str, Any],
    timeout: float,
    step: str,
) -> dict[str, Any]:
    """Send one request line and return the response object with the matching id.

    A server may interleave notifications (logging, progress) with responses,
    so lines without our id are skipped until the answer arrives. The whole
    wait, not each line, is bounded by ``timeout``.
    """
    await _write_message(proc, message, step)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0.0:
            raise MCPHandshakeError(
                f"stdio handshake failed during {step}: "
                f"no response within {timeout:g} seconds"
            )
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), remaining)  # type: ignore[union-attr]
        except TimeoutError as exc:
            raise MCPHandshakeError(
                f"stdio handshake failed during {step}: "
                f"no response within {timeout:g} seconds"
            ) from exc
        if not raw:
            code = proc.returncode
            raise MCPHandshakeError(
                f"stdio handshake failed during {step}: "
                f"the process exited with code {code} before it answered"
            )
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MCPHandshakeError(
                f"stdio handshake failed during {step}: invalid JSON response"
            ) from exc
        if not isinstance(response, dict):
            raise MCPHandshakeError(
                f"stdio handshake failed during {step}: "
                "the response is not an object"
            )
        if response.get("id") != message["id"]:
            # A notification (no id) or a response to another request.
            continue
        break
    if "error" in response:
        raise MCPHandshakeError(
            f"stdio handshake failed during {step}: "
            f"the server returned an error: {response['error']}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise MCPHandshakeError(
            f"stdio handshake failed during {step}: the result is not an object"
        )
    return result


async def _handshake(
    proc: asyncio.subprocess.Process,
    timeout: float,
) -> None:
    """Run the schema-correct initialize exchange.

    The request carries ``protocolVersion``, ``capabilities`` and ``clientInfo``;
    the response is followed by the ``notifications/initialized`` notification
    the schema requires before any other request.
    """
    await _exchange(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": _initialize_params(),
        },
        timeout,
        "initialize",
    )
    await _write_message(
        proc,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "notifications/initialized",
    )


async def _spawn_stdio(
    command: str,
) -> tuple[asyncio.subprocess.Process, _StderrTail, asyncio.Task[None]]:
    """Start a stdio server and drain its stderr concurrently from the first byte."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise MCPHandshakeError(
            f"stdio handshake failed: cannot parse the command: {exc}"
        ) from exc
    if not argv:
        raise MCPHandshakeError("stdio handshake failed: the command is empty")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            limit=MAX_LINE_BYTES,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise MCPHandshakeError(
            f"stdio handshake failed: cannot start the process: {exc}"
        ) from exc
    tail = _StderrTail()
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, tail))
    return proc, tail, stderr_task


async def _join_drain(
    stderr_task: asyncio.Task[None],
    timeout: float = STDERR_DRAIN_JOIN_SECONDS,
) -> None:
    """Wait a short bounded time for the stderr drain to reach EOF.

    The tail is read right after this call, so a drain that is still running
    must not be racing the read. A server that is still alive never reaches EOF,
    so the wait is bounded: it gives up and leaves the drain running for
    ``_stop_stdio`` to cancel.
    """
    if stderr_task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(stderr_task), timeout)
    except TimeoutError:
        return


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Terminate a server, kill it when it ignores SIGTERM, then reap it.

    Both waits are shielded: a cancellation raises out of this coroutine but
    never cancels the wait itself, so the child is still reaped.
    """
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(asyncio.shield(proc.wait()), STOP_TIMEOUT_SECONDS)
        return
    except TimeoutError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(proc.wait()), STOP_TIMEOUT_SECONDS)


async def _stop_stdio(
    proc: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[None],
) -> None:
    """Stop the stderr drain first, then terminate the server and reap it.

    The drain goes first: a cancellation that arrives during worker shutdown
    would otherwise escape on the terminate wait and leave the drain task
    running against a dead pipe. A cancellation during the terminate still
    kills the child before it propagates, so no process leaks.
    """
    stderr_task.cancel()
    try:
        await _terminate(proc)
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    finally:
        # The drain is already cancelled, so this join cannot block.
        if not stderr_task.done():
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await stderr_task


async def _discover_stdio(command: str, timeout: float) -> list[dict[str, Any]]:
    """Run the handshake against a stdio server command and return raw tools."""
    proc, tail, stderr_task = await _spawn_stdio(command)
    try:
        await _handshake(proc, timeout)
        result = await _exchange(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout,
            "tools/list",
        )
        return _normalize_tools(result.get("tools"))
    except MCPHandshakeError as exc:
        # Let the drain finish reading what the server already wrote before
        # the tail is read, or the message loses the server's own words.
        await _join_drain(stderr_task)
        detail = tail.text()
        if not detail:
            raise
        raise MCPHandshakeError(f"{exc} (server stderr: {detail})") from exc
    finally:
        await _stop_stdio(proc, stderr_task)


async def _discover_sse(server_url: str, timeout: float) -> list[dict[str, Any]]:
    """Run the handshake against an SSE / StreamMCP server URL and return raw tools."""
    # First attempt: modern HTTP StreamMCP / SSE POST tools/list
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            post_resp = await client.post(
                server_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "User-Agent": "vigil/1.0",
                },
            )
            if post_resp.status_code == 200:
                body = post_resp.text
                for line in body.splitlines():
                    trimmed = line.strip()
                    if trimmed.startswith("data:"):
                        try:
                            payload = json.loads(trimmed[len("data:"):].strip())
                            if isinstance(payload, dict) and "result" in payload and "tools" in payload["result"]:
                                return _normalize_tools(payload["result"]["tools"])
                        except ValueError:
                            pass
                try:
                    payload = post_resp.json()
                    if isinstance(payload, dict) and "result" in payload and "tools" in payload["result"]:
                        return _normalize_tools(payload["result"]["tools"])
                except ValueError:
                    pass
    except (httpx.HTTPError, OSError):
        pass

    # Fallback attempt: classic GET event-stream handshake
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET", server_url, headers={"Accept": "text/event-stream"}
            ) as response:
                if response.status_code != 200:
                    raise MCPHandshakeError(
                        "SSE handshake failed: "
                        f"GET {server_url} returned status {response.status_code}"
                    )
                async for line in response.aiter_lines():
                    text = line.strip()
                    if not text.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(text[len("data:"):].strip())
                    except ValueError as exc:
                        raise MCPHandshakeError(
                            "SSE handshake failed: invalid JSON event"
                        ) from exc
                    if isinstance(payload, dict) and "tools" in payload:
                        return _normalize_tools(payload.get("tools"))
                    if isinstance(payload, dict) and "result" in payload and "tools" in payload["result"]:
                        return _normalize_tools(payload["result"]["tools"])
                raise MCPHandshakeError(
                    "SSE handshake failed: "
                    "the stream closed without a tools event"
                )
    except MCPHandshakeError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise MCPHandshakeError(
            f"SSE handshake failed: cannot reach {server_url}: {exc}"
        ) from exc


async def discover_tools(
    config: MCPServerConfig,
    timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
) -> list[MCPToolDefinition]:
    """Handshake with a third party MCP server and return its tools.

    Every discovered tool defaults to approval level ``external``. A caller
    lowers the level through an explicit act, never here.

    Raises:
        MCPHandshakeError: The server failed the handshake. The message
            names the reason, and the caller must not store the server.
    """
    if config.transport == "stdio":
        if not config.command or not config.command.strip():
            raise MCPHandshakeError(
                "stdio handshake failed: a stdio server needs a command"
            )
        raw_tools = await _discover_stdio(config.command, timeout)
    elif config.transport == "sse":
        if not config.url or not config.url.strip():
            raise MCPHandshakeError(
                "SSE handshake failed: an SSE server needs a URL"
            )
        raw_tools = await _discover_sse(config.url, SSE_TIMEOUT_SECONDS)
    else:
        raise MCPHandshakeError(
            f"handshake failed: unknown transport {config.transport!r}"
        )
    return [
        MCPToolDefinition(
            name=tool["name"],
            description=tool["description"]
            if isinstance(tool["description"], str)
            else "",
            input_schema=tool["input_schema"]
            if isinstance(tool["input_schema"], dict)
            else {},
            level="external",
        )
        for tool in raw_tools
    ]


async def _call_stdio_tool(
    command: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
) -> tuple[str, bool]:
    """Invoke tools/call on a stdio MCP server and return (output, is_error)."""
    proc, tail, stderr_task = await _spawn_stdio(command)
    try:
        await _handshake(proc, timeout)
        result = await _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout,
            "tools/call",
        )
        is_error = bool(result.get("isError", False))
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            ]
            return "\n".join(parts), is_error
        if isinstance(content, str):
            return content, is_error
        return json.dumps(result), is_error
    except MCPHandshakeError as exc:
        # Let the drain finish reading what the server already wrote before
        # the tail is read, or the message loses the server's own words.
        await _join_drain(stderr_task)
        detail = tail.text()
        if not detail:
            raise
        raise MCPHandshakeError(f"{exc} (server stderr: {detail})") from exc
    finally:
        await _stop_stdio(proc, stderr_task)


async def _call_sse_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
) -> tuple[str, bool]:
    """Invoke tools/call on an SSE / StreamMCP server URL and return (output, is_error).

    The method fails closed. A reply it cannot parse, and a reply that carries
    no ``result`` object, both return ``is_error=True``: neither proves the
    server ran the tool, and a success would hand the model an invented result.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            server_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "vigil/1.0",
            },
        )
        if resp.status_code != 200:
            return f"HTTP error {resp.status_code}: {resp.text[:300]}", True
        body = resp.text
        data: dict[str, Any] | None = None
        for line in body.splitlines():
            trimmed = line.strip()
            if trimmed.startswith("data:"):
                try:
                    data = json.loads(trimmed[len("data:"):].strip())
                    break
                except ValueError:
                    pass
        if data is None:
            try:
                data = resp.json()
            except ValueError:
                return (
                    "SSE tools/call failed: the reply body is neither a JSON "
                    f"object nor an SSE event; tool {tool_name!r} was not run: "
                    f"{body[:300]!r}",
                    True,
                )
        if not isinstance(data, dict):
            return (
                f"SSE tools/call failed: the reply is not a JSON object; "
                f"tool {tool_name!r} was not run",
                True,
            )
        if "error" in data:
            return f"MCP server error: {data['error']}", True
        result = data.get("result")
        if not isinstance(result, dict):
            return (
                f"SSE tools/call failed: the reply carries no result object; "
                f"tool {tool_name!r} was not run",
                True,
            )
        is_error = bool(result.get("isError", False))
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            ]
            return "\n".join(parts), is_error
        if isinstance(content, str):
            return content, is_error
        return json.dumps(result), is_error


class MCPHostClient:
    """Host client satisfying MCPHostProtocol for registered MCP servers."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def list_tools(self, connector_id: str) -> list[Any]:
        from src.integrations.repositories import MCPToolDefinitionDTO
        from src.modules.connectors.service import ConnectorsService

        server = ConnectorsService._mcp_servers_by_name.get(connector_id)
        if server is None:
            return []
        return [
            MCPToolDefinitionDTO(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
            for (sid, _), tool in ConnectorsService._mcp_tools.items()
            if sid == server.id
        ]

    async def execute_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str = "",
    ) -> Any:
        from src.integrations.repositories import MCPToolResultDTO
        from src.modules.connectors.service import ConnectorsService

        server = ConnectorsService._mcp_servers_by_name.get(connector_id)
        if server is None:
            # No server carries ``connector_id`` as its name, and the builder
            # never sets a real one: the tool name is the only remaining handle.
            # Resolve by name only when exactly one registered server owns it,
            # because a call addressed to one server must never run on another.
            owners = sorted(
                {
                    sid
                    for (sid, tname) in ConnectorsService._mcp_tools
                    if tname == tool_name
                }
            )
            if len(owners) > 1:
                names = sorted(
                    ConnectorsService._mcp_servers[sid].name
                    if sid in ConnectorsService._mcp_servers
                    else str(sid)
                    for sid in owners
                )
                return MCPToolResultDTO(
                    tool_call_id=tool_call_id,
                    output=(
                        f"MCP tool {tool_name!r} is ambiguous: servers "
                        f"{', '.join(repr(name) for name in names)} all expose it "
                        f"and connector {connector_id!r} names none of them; "
                        "the tool was not run"
                    ),
                    is_error=True,
                )
            if len(owners) == 1:
                server = ConnectorsService._mcp_servers.get(owners[0])
        if server is None:
            # Never report success for a tool that was not run: an invented
            # result would be fed back to the model as a real tool result.
            return MCPToolResultDTO(
                tool_call_id=tool_call_id,
                output=(
                    f"MCP server {connector_id!r} is not registered or has no "
                    f"transport; tool {tool_name!r} was not run"
                ),
                is_error=True,
            )
        if server.transport == "stdio" and server.command:
            try:
                output, is_error = await _call_stdio_tool(
                    server.command, tool_name, dict(arguments)
                )
                return MCPToolResultDTO(
                    tool_call_id=tool_call_id, output=output, is_error=is_error
                )
            except Exception as exc:  # noqa: BLE001
                return MCPToolResultDTO(
                    tool_call_id=tool_call_id, output=str(exc), is_error=True
                )
        if server.transport == "sse" and server.url:
            try:
                output, is_error = await _call_sse_tool(
                    server.url, tool_name, dict(arguments)
                )
                return MCPToolResultDTO(
                    tool_call_id=tool_call_id, output=output, is_error=is_error
                )
            except Exception as exc:  # noqa: BLE001
                return MCPToolResultDTO(
                    tool_call_id=tool_call_id, output=str(exc), is_error=True
                )
        return MCPToolResultDTO(
            tool_call_id=tool_call_id,
            output=(
                f"MCP server {server.name!r} has no usable transport "
                f"(transport={server.transport!r}); tool {tool_name!r} was not run"
            ),
            is_error=True,
        )

    async def get_tool_approval_policy(
        self,
        connector_id: str,
        tool_name: str,
    ) -> str:
        from src.modules.connectors.service import ConnectorsService

        server = ConnectorsService._mcp_servers_by_name.get(connector_id)
        if server is not None:
            tool = ConnectorsService._mcp_tools.get((server.id, tool_name))
            if tool is not None:
                return tool.level
        return "external"

    async def is_healthy(self) -> bool:
        return True

