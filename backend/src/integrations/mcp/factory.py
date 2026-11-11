"""Pluggable Model Context Protocol (MCP) transport factory and presets.

Provides an extensible transport protocol abstraction for MCP communication,
supporting:
- StdioTransport: Subprocess execution with newline-delimited JSON-RPC 2.0,
  stderr drain, and vault environment variable injection.
- SSETransport: HTTP SSE / StreamMCP transport with vault header injection.
- Presets: Pre-configured integrations for firecrawl, github, filesystem,
  postgres, and linear.
- MCPTransportFactory: Factory creating transports with credential injection.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from asyncio.subprocess import PIPE
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from src.integrations.mcp.mcp import (
    HANDSHAKE_TIMEOUT_SECONDS,
    MAX_LINE_BYTES,
    MCPHandshakeError,
    MCPToolDefinition,
    SSE_TIMEOUT_SECONDS,
    _StderrTail,
    _drain_stderr,
    _exchange,
    _handshake,
    _join_drain,
    _normalize_tools,
    _stop_stdio,
)


@runtime_checkable
class MCPTransportProtocol(Protocol):
    """Protocol for an active MCP transport connection."""

    async def connect(self) -> None:
        """Establish or verify the transport connection."""
        ...

    async def list_tools(self) -> list[MCPToolDefinition]:
        """Discover tools available on the MCP server."""
        ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Call a tool on the MCP server and return (output_text, is_error)."""
        ...

    async def close(self) -> None:
        """Close the transport connection and release resources."""
        ...


class StdioTransport:
    """Subprocess MCP transport with JSON-RPC 2.0 and vault environment injection."""

    def __init__(
        self,
        command: str,
        *,
        vault_secrets: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
    ) -> None:
        self.command = command
        self.vault_secrets = dict(vault_secrets or {})
        self.env = dict(env or {})
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._tail: _StderrTail | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._connected: bool = False
        self._next_id: int = 1

    @property
    def is_connected(self) -> bool:
        """Return True if the subprocess is alive and handshake succeeded."""
        return self._connected and self._proc is not None and self._proc.returncode is None

    async def connect(self) -> None:
        """Spawn the subprocess with injected vault environment variables and handshake."""
        if self.is_connected:
            return

        try:
            argv = shlex.split(self.command, posix=True)
        except ValueError as exc:
            raise MCPHandshakeError(
                f"stdio handshake failed: cannot parse command: {exc}"
            ) from exc
        if not argv:
            raise MCPHandshakeError("stdio handshake failed: command is empty")

        merged_env = os.environ.copy()
        merged_env.update(self.env)
        merged_env.update(self.vault_secrets)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
                limit=MAX_LINE_BYTES,
                env=merged_env,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise MCPHandshakeError(
                f"stdio handshake failed: cannot start process: {exc}"
            ) from exc

        self._tail = _StderrTail()
        self._stderr_task = asyncio.create_task(
            _drain_stderr(self._proc.stderr, self._tail)
        )

        try:
            await _handshake(self._proc, self.timeout)
            self._connected = True
        except Exception:
            await self.close()
            raise

    async def list_tools(self) -> list[MCPToolDefinition]:
        """Discover tools declared by the stdio server."""
        if not self.is_connected:
            await self.connect()

        assert self._proc is not None
        req_id = self._alloc_id()
        try:
            result = await _exchange(
                self._proc,
                {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}},
                self.timeout,
                "tools/list",
            )
            raw_tools = _normalize_tools(result.get("tools"))
            return [
                MCPToolDefinition(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    input_schema=tool.get("input_schema", {}),
                    level="external",
                )
                for tool in raw_tools
            ]
        except MCPHandshakeError as exc:
            if self._stderr_task:
                await _join_drain(self._stderr_task)
            detail = self._tail.text() if self._tail else ""
            if detail:
                raise MCPHandshakeError(f"{exc} (server stderr: {detail})") from exc
            raise

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Invoke tools/call over stdio JSON-RPC and return (output, is_error)."""
        if not self.is_connected:
            await self.connect()

        assert self._proc is not None
        req_id = self._alloc_id()
        try:
            result = await _exchange(
                self._proc,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                self.timeout,
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
            if self._stderr_task:
                await _join_drain(self._stderr_task)
            detail = self._tail.text() if self._tail else ""
            msg = f"{exc} (server stderr: {detail})" if detail else str(exc)
            return msg, True
        except Exception as exc:
            return f"stdio tool execution error: {type(exc).__name__}: {exc}", True

    async def close(self) -> None:
        """Terminate the server process and drain stderr."""
        self._connected = False
        if self._proc is not None and self._stderr_task is not None:
            await _stop_stdio(self._proc, self._stderr_task)
            self._proc = None
            self._stderr_task = None
            self._tail = None

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def __aenter__(self) -> "StdioTransport":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class SSETransport:
    """HTTP Server-Sent Events / StreamMCP transport with vault header injection."""

    def __init__(
        self,
        url: str,
        *,
        vault_headers: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = SSE_TIMEOUT_SECONDS,
    ) -> None:
        self.url = url
        self.vault_headers = dict(vault_headers or {})
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def default_headers(self) -> dict[str, str]:
        """Headers merged with user headers and injected vault headers."""
        base = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "vigil/1.0",
        }
        base.update(self.headers)
        base.update(self.vault_headers)
        return base

    async def connect(self) -> None:
        """Initialize the HTTP client if not already open."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self.default_headers,
            )

    async def list_tools(self) -> list[MCPToolDefinition]:
        """Discover tools from the SSE / StreamMCP endpoint."""
        await self.connect()
        assert self._client is not None

        headers = self.default_headers
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        # Attempt 1: Modern HTTP StreamMCP POST tools/list
        try:
            resp = await self._client.post(self.url, json=payload, headers=headers)
            if resp.status_code == 200:
                body = resp.text
                for line in body.splitlines():
                    trimmed = line.strip()
                    if trimmed.startswith("data:"):
                        try:
                            parsed = json.loads(trimmed[len("data:"):].strip())
                            if isinstance(parsed, dict) and "result" in parsed and "tools" in parsed["result"]:
                                return self._to_tool_defs(_normalize_tools(parsed["result"]["tools"]))
                        except ValueError:
                            pass
                try:
                    parsed = resp.json()
                    if isinstance(parsed, dict) and "result" in parsed and "tools" in parsed["result"]:
                        return self._to_tool_defs(_normalize_tools(parsed["result"]["tools"]))
                except ValueError:
                    pass
        except (httpx.HTTPError, OSError):
            pass

        # Attempt 2: Classic GET event-stream handshake fallback
        try:
            stream_headers = dict(headers)
            stream_headers["Accept"] = "text/event-stream"
            async with self._client.stream("GET", self.url, headers=stream_headers) as response:
                if response.status_code != 200:
                    raise MCPHandshakeError(
                        f"SSE handshake failed: GET {self.url} returned status {response.status_code}"
                    )
                async for line in response.aiter_lines():
                    text = line.strip()
                    if not text.startswith("data:"):
                        continue
                    try:
                        parsed = json.loads(text[len("data:"):].strip())
                    except ValueError as exc:
                        raise MCPHandshakeError("SSE handshake failed: invalid JSON event") from exc
                    if isinstance(parsed, dict) and "tools" in parsed:
                        return self._to_tool_defs(_normalize_tools(parsed.get("tools")))
                    if isinstance(parsed, dict) and "result" in parsed and "tools" in parsed["result"]:
                        return self._to_tool_defs(_normalize_tools(parsed["result"]["tools"]))
                raise MCPHandshakeError("SSE handshake failed: stream closed without a tools event")
        except MCPHandshakeError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise MCPHandshakeError(f"SSE handshake failed: cannot reach {self.url}: {exc}") from exc

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """Invoke tools/call over HTTP and return (output, is_error)."""
        await self.connect()
        assert self._client is not None

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        try:
            resp = await self._client.post(
                self.url,
                json=payload,
                headers=self.default_headers,
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
                        f"SSE tools/call failed: reply body is neither JSON nor SSE event; "
                        f"tool {name!r} was not run: {body[:300]!r}",
                        True,
                    )
            if not isinstance(data, dict):
                return (
                    f"SSE tools/call failed: reply is not a JSON object; tool {name!r} was not run",
                    True,
                )
            if "error" in data:
                return f"MCP server error: {data['error']}", True
            result = data.get("result")
            if not isinstance(result, dict):
                return (
                    f"SSE tools/call failed: reply carries no result object; tool {name!r} was not run",
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
        except Exception as exc:
            return f"SSE tool call error: {type(exc).__name__}: {exc}", True

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _to_tool_defs(raw_tools: list[dict[str, Any]]) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema", {}),
                level="external",
            )
            for tool in raw_tools
        ]

    async def __aenter__(self) -> "SSETransport":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


@dataclass(frozen=True)
class MCPPreset:
    """Pre-configured definition of an MCP integration preset."""

    id: str
    name: str
    description: str
    transport: str  # "stdio" or "sse"
    command_template: str | None = None
    url_template: str | None = None
    required_vault_keys: tuple[str, ...] = ()
    env_mappings: dict[str, str] = field(default_factory=dict)
    header_mappings: dict[str, str] = field(default_factory=dict)


#: Built-in catalog of standard MCP presets
MCP_PRESETS: dict[str, MCPPreset] = {
    "firecrawl": MCPPreset(
        id="firecrawl",
        name="Firecrawl Web Scraper",
        description="Extract clean markdown, structured data, and screenshots from websites.",
        transport="stdio",
        command_template="npx -y @firecrawl/mcp-server",
        required_vault_keys=("FIRECRAWL_API_KEY",),
        env_mappings={"FIRECRAWL_API_KEY": "FIRECRAWL_API_KEY"},
    ),
    "github": MCPPreset(
        id="github",
        name="GitHub Integration",
        description="Search repos, read issues, manage PRs, and inspect code.",
        transport="stdio",
        command_template="npx -y @modelcontextprotocol/server-github",
        required_vault_keys=("GITHUB_PERSONAL_ACCESS_TOKEN",),
        env_mappings={"GITHUB_PERSONAL_ACCESS_TOKEN": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    ),
    "filesystem": MCPPreset(
        id="filesystem",
        name="Secure Filesystem",
        description="Local directory read and write access for agent workspaces.",
        transport="stdio",
        command_template="npx -y @modelcontextprotocol/server-filesystem {path}",
        required_vault_keys=(),
        env_mappings={},
    ),
    "postgres": MCPPreset(
        id="postgres",
        name="PostgreSQL Database",
        description="Inspect schemas, run SQL queries, and analyze relational tables.",
        transport="stdio",
        command_template="npx -y @modelcontextprotocol/server-postgres {connection_string}",
        required_vault_keys=("POSTGRES_PASSWORD",),
        env_mappings={"POSTGRES_PASSWORD": "PGPASSWORD"},
    ),
    "linear": MCPPreset(
        id="linear",
        name="Linear Project Management",
        description="Manage issues, cycles, projects, and backlog triage in Linear.",
        transport="stdio",
        command_template="npx -y @linear/mcp-server",
        required_vault_keys=("LINEAR_API_KEY",),
        env_mappings={"LINEAR_API_KEY": "LINEAR_API_KEY"},
    ),
}


class MCPTransportFactory:
    """Factory for instantiating MCP transports with vault credentials and presets."""

    def __init__(self, vault_service: Any = None) -> None:
        self.vault = vault_service
        self._presets: dict[str, MCPPreset] = dict(MCP_PRESETS)

    def get_preset(self, preset_id: str) -> MCPPreset:
        """Lookup a preset by id. Raises KeyError if unknown."""
        try:
            return self._presets[preset_id]
        except KeyError:
            raise KeyError(f"unknown MCP preset: {preset_id!r}") from None

    def list_presets(self) -> list[MCPPreset]:
        """Return all registered presets."""
        return list(self._presets.values())

    def register_preset(self, preset: MCPPreset) -> None:
        """Add or update a preset."""
        self._presets[preset.id] = preset

    async def _resolve_vault_secret(
        self, key: str, user_id: Any = None
    ) -> str | None:
        """Retrieve a secret from the vault service if available."""
        if self.vault is None:
            return None
        if isinstance(self.vault, dict):
            return self.vault.get(key)
        if callable(getattr(self.vault, "get_secret", None)):
            method = self.vault.get_secret
            import inspect

            sig = inspect.signature(method)
            if len(sig.parameters) >= 2 and user_id is not None:
                res = method(user_id, key)
            else:
                res = method(key)
            if inspect.isawaitable(res):
                res = await res
            return res
        return None

    async def create_transport(
        self,
        transport_type: str,
        *,
        command: str | None = None,
        url: str | None = None,
        vault_secrets: dict[str, str] | None = None,
        vault_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> MCPTransportProtocol:
        """Create a stdio or sse transport instance."""
        if transport_type == "stdio":
            if not command:
                raise ValueError("stdio transport requires a command")
            kwargs: dict[str, Any] = {
                "command": command,
                "vault_secrets": vault_secrets,
            }
            if timeout is not None:
                kwargs["timeout"] = timeout
            return StdioTransport(**kwargs)

        if transport_type == "sse":
            if not url:
                raise ValueError("sse transport requires a url")
            kwargs = {
                "url": url,
                "vault_headers": vault_headers,
            }
            if timeout is not None:
                kwargs["timeout"] = timeout
            return SSETransport(**kwargs)

        raise ValueError(f"unsupported transport type: {transport_type!r}")

    async def create_from_preset(
        self,
        preset_id: str,
        *,
        custom_args: dict[str, str] | None = None,
        user_id: Any = None,
        vault_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> MCPTransportProtocol:
        """Instantiate a transport from a preset with vault credential injection."""
        preset = self.get_preset(preset_id)
        custom_dict = custom_args or {}

        command = preset.command_template
        if command and "{" in command:
            try:
                command = command.format(**custom_dict)
            except KeyError as exc:
                raise ValueError(
                    f"preset {preset_id!r} requires argument {exc} in custom_args"
                ) from exc

        url = preset.url_template
        if url and "{" in url:
            try:
                url = url.format(**custom_dict)
            except KeyError as exc:
                raise ValueError(
                    f"preset {preset_id!r} requires argument {exc} in custom_args"
                ) from exc

        # Resolve environment variables from vault
        vault_secrets: dict[str, str] = {}
        for vault_key, env_var in preset.env_mappings.items():
            val = (vault_overrides or {}).get(vault_key)
            if val is None:
                val = await self._resolve_vault_secret(vault_key, user_id=user_id)
            if val is not None:
                vault_secrets[env_var] = val

        # Resolve HTTP headers from vault
        vault_headers: dict[str, str] = {}
        for vault_key, header_spec in preset.header_mappings.items():
            val = (vault_overrides or {}).get(vault_key)
            if val is None:
                val = await self._resolve_vault_secret(vault_key, user_id=user_id)
            if val is not None:
                if ":" in header_spec:
                    h_name, h_val_pattern = header_spec.split(":", 1)
                    vault_headers[h_name.strip()] = h_val_pattern.strip().format(value=val)
                else:
                    vault_headers[header_spec] = val

        return await self.create_transport(
            transport_type=preset.transport,
            command=command,
            url=url,
            vault_secrets=vault_secrets,
            vault_headers=vault_headers,
            timeout=timeout,
        )


__all__ = [
    "MCPTransportProtocol",
    "StdioTransport",
    "SSETransport",
    "MCPPreset",
    "MCP_PRESETS",
    "MCPTransportFactory",
]
