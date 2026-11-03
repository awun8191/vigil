"""Unit tests for the stdio MCP client: fail-closed calls, schema handshake, stderr.

Every test drives a real subprocess, so the assertions are on what the server
received on its stdin and on what the client returned, never on a mock.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.integrations.mcp.mcp import MCPHostClient
from src.modules.connectors.service import ConnectorsService

SCRIPTED_SERVER = textwrap.dedent(
    """\
    import json, sys

    LOG = __LOG_PATH__
    STDERR_BYTES = __STDERR_BYTES__
    TOOLS = [{"name": "echo_ext", "description": "Echo", "input_schema": {"type": "object"}}]

    if STDERR_BYTES:
        # More than one pipe buffer: without a concurrent drain the server
        # blocks here and never answers, so the client times out.
        sys.stderr.write("x" * STDERR_BYTES)
        sys.stderr.flush()

    def record(entry):
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\\n")

    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        record({"method": method, "params": message.get("params"), "id": message.get("id")})
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "scripted-mcp", "version": "1.0"},
            }
        elif method == "notifications/initialized":
            initialized = True
            continue
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            arguments = (message.get("params") or {}).get("arguments") or {}
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": "ECHO:" + str(arguments.get("msg", ""))
                        + "|initialized=" + str(initialized),
                    }
                ],
                "isError": False,
            }
        else:
            result = {}
        sys.stdout.write(
            json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}) + "\\n"
        )
        sys.stdout.flush()
    """
)


def write_scripted_server(
    tmp_path: Path,
    stderr_bytes: int = 0,
    name: str = "scripted_mcp_server.py",
) -> tuple[str, Path]:
    """Write the scripted stdio server and return (launch command, log path)."""
    log_path = tmp_path / f"{name}.log"
    script = (
        SCRIPTED_SERVER.replace("__LOG_PATH__", json.dumps(str(log_path)))
        .replace("__STDERR_BYTES__", str(stderr_bytes))
    )
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    return f"{sys.executable} {path}", log_path


def read_log(log_path: Path) -> list[dict]:
    """Return every message the scripted server received, in order."""
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def make_host() -> MCPHostClient:
    return MCPHostClient(Settings(APP_ENV="test"))


@pytest.fixture(autouse=True)
def clear_mcp_registry():
    """Every test starts from an empty registry: the stores are class level."""
    ConnectorsService._mcp_servers.clear()
    ConnectorsService._mcp_servers_by_name.clear()
    ConnectorsService._mcp_tools.clear()
    yield
    ConnectorsService._mcp_servers.clear()
    ConnectorsService._mcp_servers_by_name.clear()
    ConnectorsService._mcp_tools.clear()


async def test_unknown_server_fails_closed() -> None:
    """A tool for an unregistered server is an error, never an invented success."""
    host = make_host()
    result = await host.execute_tool(
        "ghost-server", "echo_ext", {"msg": "hello"}, tool_call_id="call-1"
    )
    assert result.is_error is True
    assert result.tool_call_id == "call-1"
    assert "not registered" in str(result.output)
    assert "ghost-server" in str(result.output)
    assert "mock" not in str(result.output)


async def test_server_without_transport_fails_closed(tmp_path: Path) -> None:
    """A stored server with no usable command or URL is an error, not a success."""
    command, _log = write_scripted_server(tmp_path)
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    server = await service.register_mcp_server(
        name="broken-transport", transport="stdio", command=command
    )
    # Drop the launch details, keep the registration: the client must refuse.
    ConnectorsService._mcp_servers_by_name["broken-transport"] = type(server)(
        id=server.id,
        name=server.name,
        transport="websocket",
        command=None,
        url=None,
        enabled=True,
        created_at=server.created_at,
    )

    result = await make_host().execute_tool(
        "broken-transport", "echo_ext", {}, tool_call_id="call-2"
    )
    assert result.is_error is True
    assert "no usable transport" in str(result.output)


async def test_initialize_payload_matches_schema_on_tool_call(tmp_path: Path) -> None:
    """tools/call sends the schema initialize request, then the initialized notification."""
    command, log_path = write_scripted_server(tmp_path)
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(
        name="schema-server", transport="stdio", command=command
    )
    assert read_log(log_path)
    log_path.write_text("", encoding="utf-8")

    result = await make_host().execute_tool(
        "schema-server", "echo_ext", {"msg": "hi"}, tool_call_id="call-4"
    )
    assert result.is_error is False, result.output
    assert result.output == "ECHO:hi|initialized=True"

    entries = read_log(log_path)
    assert [entry["method"] for entry in entries] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    params = entries[0]["params"]
    assert params["protocolVersion"] == "2024-11-05"
    assert params["capabilities"] == {}
    assert params["clientInfo"] == {"name": "vigil", "version": "1.0"}
    # The notification is a notification: no id, so no response is expected.
    assert entries[1]["id"] is None


async def test_initialize_payload_matches_schema_on_discovery(tmp_path: Path) -> None:
    """The discovery path sends the same initialize request and notification."""
    command, log_path = write_scripted_server(tmp_path)
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(
        name="discovery-server", transport="stdio", command=command
    )

    entries = read_log(log_path)
    assert [entry["method"] for entry in entries] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    params = entries[0]["params"]
    assert params["protocolVersion"] == "2024-11-05"
    assert params["capabilities"] == {}
    assert params["clientInfo"] == {"name": "vigil", "version": "1.0"}
