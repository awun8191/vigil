"""Unit tests for the stdio MCP client: fail-closed calls, schema handshake, stderr.

Every test drives a real subprocess, so the assertions are on what the server
received on its stdin and on what the client returned, never on a mock.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from src.config.settings import Settings
from src.integrations.mcp.mcp import (
    HANDSHAKE_TIMEOUT_SECONDS,
    MCPHandshakeError,
    MCPHostClient,
)
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


async def test_stdio_server_with_large_stderr_completes(tmp_path: Path) -> None:
    """A server that floods stderr still finishes: the client drains the pipe."""
    command, _log = write_scripted_server(
        tmp_path, stderr_bytes=512 * 1024, name="chatty_mcp_server.py"
    )
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(
        name="chatty-server", transport="stdio", command=command
    )

    started = time.monotonic()
    result = await make_host().execute_tool(
        "chatty-server", "echo_ext", {"msg": "hello"}, tool_call_id="call-3"
    )
    elapsed = time.monotonic() - started

    assert result.is_error is False, result.output
    assert result.output == "ECHO:hello|initialized=True"
    assert elapsed < HANDSHAKE_TIMEOUT_SECONDS


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


async def test_stderr_tail_reaches_the_error_message(tmp_path: Path) -> None:
    """A server that dies after writing stderr is reported with its own words."""
    script = tmp_path / "dies_after_stderr.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('BOOM: missing api key\\n')\n"
        "sys.stderr.flush()\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    with pytest.raises(MCPHandshakeError) as excinfo:
        await service.register_mcp_server(
            name="stderr-server",
            transport="stdio",
            command=f"{sys.executable} {script}",
        )
    assert "exited with code" in str(excinfo.value)
    assert "BOOM: missing api key" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Name resolution: a call addressed to one server must never run on another.
# ---------------------------------------------------------------------------


def inject_sse_server(name: str, tool_name: str) -> Any:
    """Store an SSE server and its tool without a handshake."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from src.modules.connectors.service import MCPDiscoveredToolDTO, MCPServerDTO

    server = MCPServerDTO(
        id=uuid4(),
        name=name,
        transport="sse",
        command=None,
        url=f"http://{name}.test/mcp",
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    ConnectorsService._mcp_servers[server.id] = server
    ConnectorsService._mcp_servers_by_name[name] = server
    ConnectorsService._mcp_tools[(server.id, tool_name)] = MCPDiscoveredToolDTO(
        server_id=server.id,
        server_name=name,
        name=tool_name,
        description="",
        input_schema={},
        level="external",
    )
    return server


async def test_shared_tool_name_is_ambiguous_and_runs_nowhere(tmp_path: Path) -> None:
    """Two servers expose one name: the gap is an error, and neither server runs."""
    command_a, log_a = write_scripted_server(tmp_path, name="alpha_server.py")
    command_b, log_b = write_scripted_server(tmp_path, name="beta_server.py")
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(name="alpha", transport="stdio", command=command_a)
    await service.register_mcp_server(name="beta", transport="stdio", command=command_b)
    log_a.write_text("", encoding="utf-8")
    log_b.write_text("", encoding="utf-8")

    # The builder always passes "default", which names no server, so the tool
    # name is the only handle. Two owners means the call is not runnable.
    result = await make_host().execute_tool(
        "default", "echo_ext", {"msg": "hello"}, tool_call_id="call-ambig"
    )

    assert result.is_error is True
    assert "ambiguous" in str(result.output)
    assert "alpha" in str(result.output) and "beta" in str(result.output)
    assert [entry for entry in read_log(log_a) if entry["method"] == "tools/call"] == []
    assert [entry for entry in read_log(log_b) if entry["method"] == "tools/call"] == []


async def test_a_single_owner_resolves_and_runs(tmp_path: Path) -> None:
    """One server owns the name: the default connector id still reaches it."""
    command, log_path = write_scripted_server(tmp_path)
    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(name="solo", transport="stdio", command=command)
    log_path.write_text("", encoding="utf-8")

    result = await make_host().execute_tool(
        "default", "echo_ext", {"msg": "solo"}, tool_call_id="call-solo"
    )

    assert result.is_error is False, result.output
    assert result.output == "ECHO:solo|initialized=True"
    assert [entry["method"] for entry in read_log(log_path)] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


# ---------------------------------------------------------------------------
# SSE transport: an unparseable reply and a reply with no result both fail.
# ---------------------------------------------------------------------------


def mock_sse_reply(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route every httpx client the module builds through a mock transport."""
    import httpx

    from src.integrations.mcp import mcp as mcp_module

    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", factory)


async def test_sse_unparseable_reply_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 whose body is not JSON and not an event is an error, not success."""

    def handler(request: Any) -> Any:
        import httpx

        return httpx.Response(200, text="<html>not a protocol reply</html>")

    mock_sse_reply(monkeypatch, handler)
    inject_sse_server("sse-garbled", "sse_tool")

    result = await make_host().execute_tool(
        "sse-garbled", "sse_tool", {}, tool_call_id="call-sse-1"
    )

    assert result.is_error is True
    assert "was not run" in str(result.output)


async def test_sse_reply_without_a_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply with no result object proves nothing, so it is an error."""

    def handler(request: Any) -> Any:
        import httpx

        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1})

    mock_sse_reply(monkeypatch, handler)
    inject_sse_server("sse-empty", "sse_tool")

    result = await make_host().execute_tool(
        "sse-empty", "sse_tool", {}, tool_call_id="call-sse-2"
    )

    assert result.is_error is True
    assert "no result" in str(result.output)


async def test_sse_result_still_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real result is still a success: the fail-closed guards are not blanket."""

    def handler(request: Any) -> Any:
        import httpx

        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "SSE-OK"}], "isError": False},
            },
        )

    mock_sse_reply(monkeypatch, handler)
    inject_sse_server("sse-good", "sse_tool")

    result = await make_host().execute_tool(
        "sse-good", "sse_tool", {}, tool_call_id="call-sse-3"
    )

    assert result.is_error is False, result.output
    assert result.output == "SSE-OK"


# ---------------------------------------------------------------------------
# stdio teardown: the drain stops first, the child never leaks, the tail is
# read only after the drain has been joined.
# ---------------------------------------------------------------------------

IGNORES_SIGTERM_SERVER = textwrap.dedent(
    """\
    import signal, sys, time

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stderr.write("IGNORING-SIGTERM\\n")
    sys.stderr.flush()
    while True:
        time.sleep(0.05)
    """
)


def write_ignores_sigterm_server(tmp_path: Path) -> str:
    path = tmp_path / "ignores_sigterm.py"
    path.write_text(IGNORES_SIGTERM_SERVER, encoding="utf-8")
    return f"{sys.executable} {path}"


async def wait_until_ignoring_sigterm(tail: Any) -> None:
    """Wait for the child to install its SIGTERM handler and say so on stderr."""
    for _ in range(200):
        if "IGNORING-SIGTERM" in tail.text():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the child never started: {tail.text()!r}")


async def test_stop_stdio_kills_a_server_that_ignores_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server that ignores SIGTERM is killed, and the drain is not left running."""
    from src.integrations.mcp import mcp as mcp_module

    command = write_ignores_sigterm_server(tmp_path)
    proc, tail, stderr_task = await mcp_module._spawn_stdio(command)
    await wait_until_ignoring_sigterm(tail)
    monkeypatch.setattr(mcp_module, "STOP_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    await mcp_module._stop_stdio(proc, stderr_task)
    elapsed = time.monotonic() - started

    assert stderr_task.done()
    assert await asyncio.wait_for(proc.wait(), 5.0) is not None
    # SIGTERM was ignored, so only the kill removed it.
    assert proc.returncode == -9
    assert elapsed < 3.0


async def test_stop_stdio_cancelled_mid_wait_still_kills_the_child(tmp_path: Path) -> None:
    """A cancellation during worker shutdown kills the child and stops the drain."""
    from src.integrations.mcp import mcp as mcp_module

    command = write_ignores_sigterm_server(tmp_path)
    proc, tail, stderr_task = await mcp_module._spawn_stdio(command)
    await wait_until_ignoring_sigterm(tail)

    stopper = asyncio.create_task(mcp_module._stop_stdio(proc, stderr_task))
    await asyncio.sleep(0.2)
    assert proc.returncode is None, "the child exited before the cancellation"

    # The drain is stopped before the terminate is waited on, so a second
    # cancellation cannot escape ahead of it.
    assert stderr_task.done()

    stopper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopper

    assert await asyncio.wait_for(proc.wait(), 5.0) is not None
    assert proc.returncode == -9
    assert "IGNORING-SIGTERM" in tail.text()


async def test_discover_joins_the_drain_before_reading_the_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The discovery error path joins the stderr drain before it reads the tail."""
    from src.integrations.mcp import mcp as mcp_module

    events: list[str] = []
    real_join = mcp_module._join_drain
    real_text = mcp_module._StderrTail.text

    async def spy_join(task: Any, timeout: float = 0.2) -> None:
        events.append("join")
        await real_join(task, timeout)

    def spy_text(self: Any) -> str:
        events.append("text")
        return real_text(self)

    monkeypatch.setattr(mcp_module, "_join_drain", spy_join)
    monkeypatch.setattr(mcp_module._StderrTail, "text", spy_text)

    script = tmp_path / "dies.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('DRAIN-ME\\n')\n"
        "sys.stderr.flush()\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    with pytest.raises(MCPHandshakeError):
        await ConnectorsService(
            session=None, settings=Settings(APP_ENV="test")
        ).register_mcp_server(
            name="join-server",
            transport="stdio",
            command=f"{sys.executable} {script}",
        )

    assert events.count("join") == 1
    assert events.index("join") < events.index("text")


async def test_join_drain_leaves_a_still_running_drain_alone() -> None:
    """The join is bounded: a drain that never reaches EOF is not cancelled."""
    from src.integrations.mcp import mcp as mcp_module

    tail = mcp_module._StderrTail()
    started = asyncio.Event()

    async def never_ends() -> None:
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(never_ends())
    await started.wait()
    began = time.monotonic()
    await mcp_module._join_drain(task, timeout=0.05)
    elapsed = time.monotonic() - began

    assert task.done() is False
    assert elapsed < 1.0
    assert tail.text() == ""
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
