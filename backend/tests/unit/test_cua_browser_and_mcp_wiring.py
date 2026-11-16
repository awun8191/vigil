"""Unit and end-to-end tests for CUA browser driver and wired MCP runtime."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from httpx import ASGITransport
import pytest

from src.config.settings import Settings
from src.dependencies import RunToolExecutor
from src.integrations.mcp.factory import MCP_PRESETS, SSETransport
from src.integrations.mcp.mcp import (
    MCPHostClient,
    MCPServerConfig,
    MCPToolDefinition,
    discover_tools,
)
from src.integrations.repositories import LLMResponseDTO
from src.main import create_app
from src.modules.connectors.service import ConnectorsService
from src.modules.jobs.service import JobsService
from src.modules.runs.repository import AgentConfigDTO, MessageCreateDTO, RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal
from src.modules.runs.tools import invoke_tool
from src.modules.runs.tools.browser import (
    FALLBACK_PNG_BYTES,
    BrowserClickArgs,
    BrowserCUATaskArgs,
    BrowserExtractArgs,
    BrowserNavigateArgs,
    BrowserPressKeyArgs,
    BrowserScrollArgs,
    BrowserSnapshotArgs,
    BrowserTypeArgs,
    _SESSIONS,
    browser_click,
    browser_cua_task,
    browser_extract,
    browser_navigate,
    browser_press_key,
    browser_scroll,
    browser_snapshot,
    browser_type,
    close_browser_session,
)
from src.worker import JobContext, _drive_run


def _make_context(
    tmp_path: Path,
    *,
    secrets: dict[str, str] | None = None,
    llm: object | None = None,
    run_id: object | None = None,
):
    from src.modules.runs.tools import ToolContext

    return ToolContext(
        runner=SimpleNamespace(),
        container_id="host-only",
        delivery_dir=tmp_path,
        secrets=secrets or {},
        llm=llm,
        run_id=run_id,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _clean_browser_and_mcp_state():
    _SESSIONS.clear()
    ConnectorsService.reset_mcp_cache()
    yield
    _SESSIONS.clear()
    ConnectorsService.reset_mcp_cache()


# =============================================================================
# 1. CUA Browser Tools (Firecrawl Interactive Session + CUA Vision Loop)
# =============================================================================


@pytest.mark.asyncio
async def test_cua_tools_require_active_session(tmp_path: Path) -> None:
    """Interactive CUA tools fail cleanly before browser_navigate is called."""
    ctx = _make_context(tmp_path)
    for name, args in (
        ("browser_snapshot", {}),
        ("browser_click", {"selector": "@e1"}),
        ("browser_type", {"text": "hello"}),
        ("browser_scroll", {"direction": "down"}),
        ("browser_press_key", {"key": "Enter"}),
    ):
        res = await invoke_tool(name, args, ctx)
        assert res.ok is False, f"{name} should fail without active session"
        assert "browser_navigate" in (res.error or "")


@pytest.mark.asyncio
async def test_browser_click_requires_selector_or_coordinates(tmp_path: Path) -> None:
    """browser_click rejects calls that specify neither selector nor (x, y)."""
    ctx = _make_context(tmp_path)
    res = await invoke_tool("browser_click", {}, ctx)
    assert res.ok is False
    assert "invalid arguments" in (res.error or "")


@pytest.mark.asyncio
async def test_firecrawl_full_cua_interaction_cycle(tmp_path: Path) -> None:
    """Exercise navigate -> snapshot -> click -> type -> scroll -> press_key -> extract -> cua_task."""
    ctx = _make_context(
        tmp_path,
        secrets={"FIRECRAWL_API_KEY": "fc-cua-key"},
        llm=SimpleNamespace(
            describe_image=AsyncMock(
                side_effect=[
                    json.dumps({"action": "click", "selector": "@e1"}),
                    json.dumps({"action": "done", "summary": "Clicked submit"}),
                ]
            )
        ),
    )
    b64_png = base64.b64encode(FALLBACK_PNG_BYTES).decode()

    def make_exec_resp(url: str, title: str, elements: list[dict], content: str = "") -> httpx.Response:
        payload = {
            "url": url,
            "title": title,
            "viewport": {"width": 1280, "height": 800},
            "elements": elements,
            "text_excerpt": "Welcome to Vigil",
            "screenshot": b64_png,
            "content": content,
        }
        return httpx.Response(
            200,
            json={"success": True, "result": json.dumps(payload)},
            request=httpx.Request("POST", "https://api.firecrawl.dev/v2/interact/sess-cua/execute"),
        )

    create_resp = httpx.Response(
        200,
        json={
            "success": True,
            "id": "sess-cua",
            "liveViewUrl": "https://liveview.firecrawl.dev/sess-cua",
            "interactiveLiveViewUrl": "https://liveview.firecrawl.dev/sess-cua?interactive=1",
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        },
        request=httpx.Request("POST", "https://api.firecrawl.dev/v2/interact"),
    )
    el = [{"id": "@e1", "tag": "button", "text": "Submit", "x": 120, "y": 240, "selector": '[data-vigil-id="e1"]'}]
    responses = [
        create_resp,
        make_exec_resp("https://example.com/", "Home", el),
        make_exec_resp("https://example.com/", "Home", el),
        make_exec_resp("https://example.com/next", "Next", el),
        make_exec_resp("https://example.com/next", "Next", el),
        make_exec_resp("https://example.com/next", "Next", el),
        make_exec_resp("https://example.com/next", "Next", el),
        make_exec_resp("https://example.com/next", "Next", el, content="# Extracted Live DOM"),
        make_exec_resp("https://example.com/next", "Next", el),
        make_exec_resp("https://example.com/done", "Done", el),
    ]

    with patch("src.modules.runs.tools.browser.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            FIRECRAWL_API_KEY=None,
            FIRECRAWL_BASE_URL="https://api.firecrawl.dev",
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=responses):
            nav = await browser_navigate(BrowserNavigateArgs(url="https://example.com"), ctx)
            assert nav.ok is True
            assert nav.content["elements"][0]["id"] == "@e1"
            assert nav.artifact_ref and Path(nav.artifact_ref).is_file()

            snap = await browser_snapshot(BrowserSnapshotArgs(), ctx)
            assert snap.ok is True
            assert snap.content["action"] == "snapshot"

            clk = await browser_click(BrowserClickArgs(selector="@e1"), ctx)
            assert clk.ok is True
            assert clk.content["url"] == "https://example.com/next"

            typ = await browser_type(BrowserTypeArgs(selector="@e1", text="hello", press_enter=True), ctx)
            assert typ.ok is True
            assert typ.content["action"] == "type"

            scr = await browser_scroll(BrowserScrollArgs(direction="down", amount=300), ctx)
            assert scr.ok is True
            assert scr.content["action"] == "scroll"

            key = await browser_press_key(BrowserPressKeyArgs(key="Escape"), ctx)
            assert key.ok is True
            assert key.content["action"] == "press_key"

            ext = await browser_extract(BrowserExtractArgs(url=None), ctx)
            assert ext.ok is True
            assert "# Extracted Live DOM" in ext.content["markdown"]

            cua = await browser_cua_task(BrowserCUATaskArgs(goal="Click submit", max_steps=3), ctx)
            assert cua.ok is True
            assert cua.content["completed"] is True
            assert len(cua.content["actions_taken"]) == 2


@pytest.mark.asyncio
async def test_local_chromium_cdp_real_interactive_session(tmp_path: Path) -> None:
    """If Chromium is installed locally, drive a real headless CDP session end-to-end."""
    if not (shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")):
        pytest.skip("No Chromium binary installed")

    html = (
        "<!doctype html><html><head><title>CUA Test</title></head>"
        "<body>"
        "<h1>Vigil CUA</h1>"
        "<input id='name' placeholder='Name' />"
        "<button id='go' onclick=\"document.getElementById('out').innerText='Hello ' + document.getElementById('name').value\">Go</button>"
        "<div id='out'>waiting</div>"
        "</body></html>"
    )
    url = "data:text/html;charset=utf-8," + html.replace("#", "%23")
    ctx = _make_context(tmp_path, secrets={"BROWSER_DRIVER": "cdp"})

    try:
        nav = await browser_navigate(BrowserNavigateArgs(url=url, capture_screenshot=True), ctx)
        assert nav.ok is True, nav.error
        assert nav.content["driver"] == "cdp"
        assert nav.content["title"] == "CUA Test"
        assert nav.artifact_ref and Path(nav.artifact_ref).read_bytes().startswith(b"\x89PNG")
        elements = nav.content["elements"]
        input_el = next(e for e in elements if e["tag"] == "input")
        button_el = next(e for e in elements if e["tag"] == "button")

        typ = await browser_type(
            BrowserTypeArgs(selector=input_el["id"], text="Vigil"), ctx
        )
        assert typ.ok is True, typ.error

        clk = await browser_click(
            BrowserClickArgs(selector=button_el["id"]), ctx
        )
        assert clk.ok is True, clk.error
        assert "Hello Vigil" in clk.content["text_excerpt"]

        ext = await browser_extract(BrowserExtractArgs(url=None), ctx)
        assert ext.ok is True, ext.error
        assert "Hello Vigil" in ext.content["markdown"]
    finally:
        await close_browser_session(tmp_path)


# =============================================================================
# 2. Persistent MCP Sessions, Aliasing, and Streamable HTTP
# =============================================================================


STATEFUL_MCP_SERVER = textwrap.dedent(
    """\
    import json, os, sys

    TOOLS = [
        {
            "name": "browser_navigate",
            "description": "Stateful navigate",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"]
            }
        },
        {
            "name": "counter_inc",
            "description": "Increment in-memory counter",
            "inputSchema": {
                "type": "object",
                "properties": {"step": {"type": "integer"}}
            }
        }
    ]

    counter = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "stateful", "version": "1.0"}}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params") or {}
            tname = params.get("name")
            args = params.get("arguments") or {}
            if tname == "browser_navigate":
                res = {"content": [{"type": "text", "text": f"NAV:{args.get('url')}|SECRET:{os.environ.get('CUSTOM_TOKEN', '')}"}], "isError": False}
            elif tname == "counter_inc":
                counter += int(args.get("step", 1))
                res = {"content": [{"type": "text", "text": f"COUNT:{counter}"}], "isError": False}
            else:
                res = {"content": [{"type": "text", "text": f"UNKNOWN:{tname}"}], "isError": True}
        else:
            res = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.mark.asyncio
async def test_mcp_persistent_session_aliasing_and_camel_case_schema(tmp_path: Path) -> None:
    """Verify inputSchema (camelCase), browser_* aliasing, vault env injection, and stateful reuse."""
    assert "playwright" in MCP_PRESETS
    script = tmp_path / "stateful_mcp.py"
    script.write_text(STATEFUL_MCP_SERVER, encoding="utf-8")
    command = f"{sys.executable} {script}"

    service = ConnectorsService(session=None, settings=Settings(APP_ENV="test"))
    await service.register_mcp_server(name="playwright", transport="stdio", command=command)

    tools = await service.list_mcp_tools("playwright")
    names = {t.name for t in tools}
    assert "playwright__browser_navigate" in names
    assert "counter_inc" in names
    nav_tool = next(t for t in tools if t.name == "playwright__browser_navigate")
    assert nav_tool.remote_name == "browser_navigate"
    assert "url" in nav_tool.input_schema.get("properties", {})

    host = MCPHostClient(Settings(APP_ENV="test"))
    host.start_session("run-1", vault_secrets={"CUSTOM_TOKEN": "vault-xyz"})
    try:
        r1 = await host.execute_tool("playwright", "counter_inc", {"step": 2})
        r2 = await host.execute_tool("playwright", "counter_inc", {"step": 3})
        assert r1.is_error is False and r1.output == "COUNT:2"
        assert r2.is_error is False and r2.output == "COUNT:5"

        r_nav = await host.execute_tool(
            "playwright", "playwright__browser_navigate", {"url": "https://vigil.test"}
        )
        assert r_nav.is_error is False
        assert r_nav.output == "NAV:https://vigil.test|SECRET:vault-xyz"
    finally:
        await host.close_session("run-1")


@pytest.mark.asyncio
async def test_sse_transport_streamable_http_session_id() -> None:
    """SSETransport runs initialize, captures Mcp-Session-Id, and forwards it on tools/call."""
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        body = json.loads(request.content.decode("utf-8"))
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "mcp-sess-42"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2024-11-05"}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}},
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})

    transport = SSETransport(url="https://mcp.example.com/mcp")
    transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=transport.default_headers,
    )
    try:
        out, is_err = await transport.call_tool("ping", {"a": 1})
        assert is_err is False
        assert out == "ok"
        assert seen_headers[-1].get("mcp-session-id") == "mcp-sess-42"
    finally:
        await transport.close()


# =============================================================================
# 3. End-to-End Wiring: RunsService, Worker Session Persistence & Connectors API
# =============================================================================


@pytest.mark.asyncio
async def test_drive_run_keeps_browser_session_alive_while_awaiting_approval(tmp_path: Path) -> None:
    """_drive_run must not close the browser session when run pauses for awaiting_approval."""
    settings = Settings(APP_ENV="test")
    run_id = uuid4()

    with patch("src.worker.build_tool_executor") as mock_exec_builder, patch(
        "src.worker.build_runs_service"
    ) as mock_svc_builder:
        executor = SimpleNamespace(close=AsyncMock())
        mock_exec_builder.return_value = executor

        mock_svc = SimpleNamespace(
            process=AsyncMock(return_value=SimpleNamespace(status="awaiting_approval"))
        )
        mock_svc_builder.return_value = mock_svc
        await _drive_run(
            SimpleNamespace(id=uuid4(), type="execute_run", payload={}),  # type: ignore[arg-type]
            JobContext(settings=settings),
            run_id,
        )
        executor.close.assert_awaited_once_with(close_browser=False)

        executor.close.reset_mock()
        mock_svc.process = AsyncMock(return_value=SimpleNamespace(status="completed"))
        await _drive_run(
            SimpleNamespace(id=uuid4(), type="execute_run", payload={}),  # type: ignore[arg-type]
            JobContext(settings=settings),
            run_id,
        )
        executor.close.assert_awaited_once_with(close_browser=True)


@pytest.mark.asyncio
async def test_runs_service_expands_cua_browser_tools_and_mcp_levels() -> None:
    """Legacy configs with browser_navigate expand to all CUA tools and respect MCP tool levels."""
    from src.modules.runs.tools.mcp_bridge import MCPToolAdapterFactory

    journal = InMemoryRunJournal()
    local_mcp = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="mcp_read_only"),
        lambda n, a: ("read-ok", False),
        level="local",
    )
    ext_mcp = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="mcp_write_op"),
        lambda n, a: ("write-ok", False),
        level="external",
    )
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        journal=journal,  # type: ignore[arg-type]
        mcp_tools=[local_mcp, ext_mcp],
    )
    cfg = AgentConfigDTO(
        id=uuid4(),
        name="legacy-cfg",
        system_prompt="Prompt",
        model="gpt-4o",
        tools=["read", "browser_navigate"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={"browser_navigate": "local"},
        limits={"max_cost_micros": 0},
    )
    expanded = service._expand_declared_tools(cfg.tools)
    assert "browser_click" in expanded
    assert "browser_type" in expanded
    assert "browser_cua_task" in expanded
    assert "mcp_read_only" in expanded
    assert "mcp_write_op" in expanded

    policy = service.resolve_partition_policy(cfg)
    assert policy.partition_for("browser_click") == "local"
    assert policy.partition_for("mcp_read_only") == "local"
    assert policy.partition_for("mcp_write_op") == "external"


@pytest.mark.asyncio
async def test_connectors_mcp_tools_endpoints(tmp_path: Path) -> None:
    """Verify GET/PATCH/POST endpoints for discovered MCP tools and level toggling."""
    from src.dependencies import get_connectors_service, get_jobs_service

    settings = Settings(APP_ENV="test")
    app = create_app(settings)
    connectors = ConnectorsService(session=None, settings=settings)
    jobs = JobsService(session=None, settings=settings)

    script = tmp_path / "api_mcp.py"
    script.write_text(STATEFUL_MCP_SERVER, encoding="utf-8")
    await connectors.register_mcp_server(
        name="api-mcp-srv",
        transport="stdio",
        command=f"{sys.executable} {script}",
    )

    app.dependency_overrides[get_connectors_service] = lambda: connectors
    app.dependency_overrides[get_jobs_service] = lambda: jobs
    headers = {"Authorization": "Bearer vgt_testtoken"}

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        res_list = await client.get(
            "/api/v1/connectors/mcp-servers/api-mcp-srv/tools", headers=headers
        )
        assert res_list.status_code == 200
        tools = res_list.json()
        assert any(t["name"] == "counter_inc" and t["level"] == "external" for t in tools)

        res_patch = await client.patch(
            "/api/v1/connectors/mcp-servers/api-mcp-srv/tools/counter_inc",
            json={"level": "local"},
            headers=headers,
        )
        assert res_patch.status_code == 200
        assert res_patch.json()["level"] == "local"

        res_refresh = await client.post(
            "/api/v1/connectors/mcp-servers/api-mcp-srv/refresh", headers=headers
        )
        assert res_refresh.status_code == 202
        assert res_refresh.json()["status"] == "queued"


# =============================================================================
# 4. Default Browser Driver Selection & Disabling Drivers
# =============================================================================


@pytest.mark.asyncio
async def test_browser_driver_disabled_and_auto_skip(tmp_path: Path) -> None:
    from src.modules.runs.tools.browser import _resolve_driver

    # Global disable blocks browser_navigate and browser_extract
    ctx_disabled = _make_context(tmp_path, secrets={"BROWSER_DRIVER": "disabled"})
    res_nav = await browser_navigate(BrowserNavigateArgs(url="https://example.com"), ctx_disabled)
    assert res_nav.ok is False
    assert "disabled" in (res_nav.error or "").lower()

    res_ext = await browser_extract(BrowserExtractArgs(url="https://example.com"), ctx_disabled)
    assert res_ext.ok is False
    assert "disabled" in (res_ext.error or "").lower()

    # Disabling firecrawl skips it in auto mode even when FIRECRAWL_API_KEY is set
    ctx_no_fc = _make_context(
        tmp_path,
        secrets={
            "FIRECRAWL_API_KEY": "fc-test-key",
            "DISABLED_BROWSER_DRIVERS": "firecrawl",
        },
    )
    driver_name, _, _ = _resolve_driver(ctx_no_fc)
    assert driver_name != "firecrawl"

    # Explicitly requesting a disabled driver on browser_navigate fails cleanly
    res_explicit = await browser_navigate(
        BrowserNavigateArgs(url="https://example.com", driver="firecrawl"),
        ctx_no_fc,
    )
    assert res_explicit.ok is False
    assert "disabled" in (res_explicit.error or "").lower()

    # Disabling all drivers in auto falls back to mock
    ctx_no_fc_cdp = _make_context(
        tmp_path,
        secrets={
            "FIRECRAWL_API_KEY": "fc-test-key",
            "DISABLED_BROWSER_DRIVERS": "firecrawl,cdp,mcp",
        },
    )
    driver_fallback, _, _ = _resolve_driver(ctx_no_fc_cdp)
    assert driver_fallback == "mock"


@pytest.mark.asyncio
async def test_settings_browser_driver_preferences_and_vault_filtering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.dependencies import get_key_vault_service, get_settings_service
    from src.main import create_app
    from src.modules.audit.service import AuditService
    from src.modules.auth.state import DEFAULT_USER_ID
    from src.modules.settings.service import SettingsService
    from src.modules.settings import vault as vault_mod

    store: dict[tuple[str, str], str] = {}

    class _FakeKeyVaultService:
        def __init__(self, session=None, settings=None) -> None:
            self.session = session
            self.settings = settings

        async def get_secret(self, user_id, key: str) -> str | None:
            return store.get((str(user_id), key))

        async def set_secret(self, user_id, key: str, value: str, description: str | None = None):
            store[(str(user_id), key)] = value
            return SimpleNamespace(key_name=key, description=description)

        async def delete_secret(self, user_id, key: str) -> bool:
            return store.pop((str(user_id), key), None) is not None

        async def list_secrets(self, user_id) -> list:
            now = datetime.now(timezone.utc)
            return [
                vault_mod.VaultSecretDTO(
                    key_name=k,
                    masked_value="***",
                    created_at=now,
                    updated_at=now,
                )
                for (uid, k) in store
                if uid == str(user_id)
            ]

    monkeypatch.setattr(vault_mod, "KeyVaultService", _FakeKeyVaultService)
    # Also reset in-memory SettingsService cache for DEFAULT_USER_ID
    SettingsService._user_settings.pop(DEFAULT_USER_ID, None)

    settings = Settings(
        auth_token="vgt_testtoken",
        workspace_root=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        app_env="development",
    )
    app = create_app(settings=settings)
    dummy_session = SimpleNamespace()
    fake_vault = _FakeKeyVaultService(session=dummy_session, settings=settings)
    settings_svc = SettingsService(
        session=dummy_session,
        settings=settings,
        audit=AuditService(session=None, settings=settings),
    )

    app.dependency_overrides[get_settings_service] = lambda: settings_svc
    app.dependency_overrides[get_key_vault_service] = lambda: fake_vault
    headers = {"Authorization": "Bearer vgt_testtoken"}

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        patch_res = await client.patch(
            "/api/v1/settings",
            json={
                "default_browser_driver": "cdp",
                "disabled_browser_drivers": ["firecrawl", "mcp"],
            },
            headers=headers,
        )
        assert patch_res.status_code == 200, patch_res.text
        body = patch_res.json()
        assert body["default_browser_driver"] == "cdp"
        assert body["disabled_browser_drivers"] == ["firecrawl", "mcp"]

        get_res = await client.get("/api/v1/settings", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["default_browser_driver"] == "cdp"
        assert get_res.json()["disabled_browser_drivers"] == ["firecrawl", "mcp"]

        # Vault listing must hide internal browser preference keys
        vault_res = await client.get("/api/v1/settings/vault", headers=headers)
        assert vault_res.status_code == 200
        vault_keys = {item["key_name"] for item in vault_res.json()}
        assert "BROWSER_DRIVER" not in vault_keys
        assert "DISABLED_BROWSER_DRIVERS" not in vault_keys

    # Verify KeyVaultService persisted them so worker ToolContext receives them
    assert store[(str(DEFAULT_USER_ID), "BROWSER_DRIVER")] == "cdp"
    assert store[(str(DEFAULT_USER_ID), "DISABLED_BROWSER_DRIVERS")] == "firecrawl,mcp"

