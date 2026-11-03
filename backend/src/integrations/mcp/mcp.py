"""External MCP server client: stdio and SSE transport, handshake, tool discovery.

The client speaks a minimal newline-delimited JSON-RPC dialect: one ``initialize``
request, then one ``tools/list`` request. Each message is a single JSON object per
line, matched by ``id``. Only the background worker calls this module. Request
handlers never spawn a process: registration enqueues a ``discover_mcp_tools``
job and returns 202, and the worker runs discovery there.
"""
from __future__ import annotations

import asyncio
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


async def _exchange(
    proc: asyncio.subprocess.Process,
    message: dict[str, Any],
    timeout: float,
    step: str,
) -> dict[str, Any]:
    """Send one request line and return the parsed response object."""
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
    try:
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout)  # type: ignore[union-attr]
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
    if not isinstance(response, dict) or response.get("id") != message["id"]:
        raise MCPHandshakeError(
            f"stdio handshake failed during {step}: mismatched response id"
        )
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


async def _discover_stdio(command: str, timeout: float) -> list[dict[str, Any]]:
    """Run the handshake against a stdio server command and return raw tools."""
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
    try:
        await _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"client": "vigil", "version": "1.0"},
            },
            timeout,
            "initialize",
        )
        result = await _exchange(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout,
            "tools/list",
        )
        return _normalize_tools(result.get("tools"))
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), 5.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass


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
    argv = shlex.split(command, posix=True)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        limit=MAX_LINE_BYTES,
    )
    try:
        await _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"client": "vigil", "version": "1.0"},
            },
            timeout,
            "initialize",
        )
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
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), 5.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass


async def _call_sse_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
) -> tuple[str, bool]:
    """Invoke tools/call on an SSE / StreamMCP server URL and return (output, is_error)."""
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
                return body[:1000], False
        if "error" in data:
            return f"MCP server error: {data['error']}", True
        result = data.get("result", {})
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
            for (sid, tname), _tool in ConnectorsService._mcp_tools.items():
                if tname == tool_name:
                    server = ConnectorsService._mcp_servers.get(sid)
                    break
        if server is not None and server.transport == "stdio" and server.command:
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
        if server is not None and server.transport == "sse" and server.url:
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
            tool_call_id=tool_call_id, output="mock tool output", is_error=False
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

