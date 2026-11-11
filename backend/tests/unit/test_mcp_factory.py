"""Comprehensive unit tests for the pluggable MCP factory and dynamic tool registry.

Tests verify:
1. Preset catalog definitions (firecrawl, github, filesystem, postgres, linear) and factory configuration.
2. Vault environment variable injection for StdioTransport and header injection for SSETransport.
3. Real subprocess execution and JSON-RPC 2.0 handshake with credential verification.
4. Dynamic Pydantic argument model generation from JSON Schema and argument validation.
5. Adaptation of MCPToolDefinition into Vigil Tool instances via MCPToolAdapterFactory.
6. Execution dispatch through transports, hosts, and callables returning ToolResult.
7. Integration with RunsService tool schemas and partition approval policies.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from src.config.settings import Settings
from src.integrations.mcp.factory import (
    MCP_PRESETS,
    MCPPreset,
    MCPTransportFactory,
    MCPTransportProtocol,
    SSETransport,
    StdioTransport,
)
from src.integrations.mcp.mcp import MCPHandshakeError, MCPToolDefinition
from src.modules.runs.repository import AgentConfigDTO
from src.modules.runs.service import RunsService
from src.modules.runs.tools import Tool, ToolContext, ToolResult
from src.modules.runs.tools.mcp_bridge import MCPToolAdapterFactory


# -----------------------------------------------------------------------------
# 1. Preset Catalog & Factory Instantiation Tests
# -----------------------------------------------------------------------------


def test_presets_catalog_contains_required_services() -> None:
    """Verify all five required presets exist in MCP_PRESETS catalog."""
    expected_presets = {"firecrawl", "github", "filesystem", "postgres", "linear"}
    assert expected_presets.issubset(set(MCP_PRESETS.keys()))

    for preset_id in expected_presets:
        preset = MCP_PRESETS[preset_id]
        assert isinstance(preset, MCPPreset)
        assert preset.id == preset_id
        assert preset.name
        assert preset.description
        assert preset.transport in ("stdio", "sse")


def test_preset_configurations_and_mappings() -> None:
    """Verify preset command templates, required vault keys, and env mappings."""
    fc = MCP_PRESETS["firecrawl"]
    assert "@firecrawl/mcp-server" in (fc.command_template or "")
    assert "FIRECRAWL_API_KEY" in fc.required_vault_keys
    assert fc.env_mappings.get("FIRECRAWL_API_KEY") == "FIRECRAWL_API_KEY"

    gh = MCP_PRESETS["github"]
    assert "@modelcontextprotocol/server-github" in (gh.command_template or "")
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in gh.required_vault_keys

    fs = MCP_PRESETS["filesystem"]
    assert "{path}" in (fs.command_template or "")

    pg = MCP_PRESETS["postgres"]
    assert "{connection_string}" in (pg.command_template or "")
    assert "POSTGRES_PASSWORD" in pg.required_vault_keys
    assert pg.env_mappings.get("POSTGRES_PASSWORD") == "PGPASSWORD"

    ln = MCP_PRESETS["linear"]
    assert "@linear/mcp-server" in (ln.command_template or "")
    assert "LINEAR_API_KEY" in ln.required_vault_keys


def test_factory_preset_management() -> None:
    """Verify factory get_preset, list_presets, and custom registration."""
    factory = MCPTransportFactory()
    assert len(factory.list_presets()) >= 5

    pg = factory.get_preset("postgres")
    assert pg.id == "postgres"

    with pytest.raises(KeyError, match="unknown MCP preset"):
        factory.get_preset("non_existent_preset")

    custom = MCPPreset(
        id="custom_weather",
        name="Weather Service",
        description="Local weather provider",
        transport="stdio",
        command_template="weather-mcp",
    )
    factory.register_preset(custom)
    assert factory.get_preset("custom_weather").name == "Weather Service"


# -----------------------------------------------------------------------------
# 2. Vault Environment Variable & Header Injection Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_preset_filesystem_custom_args() -> None:
    """Verify template formatting with custom_args in create_from_preset."""
    factory = MCPTransportFactory()
    transport = await factory.create_from_preset(
        "filesystem",
        custom_args={"path": "/var/workspace/data"},
    )
    assert isinstance(transport, StdioTransport)
    assert "/var/workspace/data" in transport.command


@pytest.mark.asyncio
async def test_create_from_preset_missing_custom_arg_raises() -> None:
    """Verify ValueError is raised when required template placeholder is missing."""
    factory = MCPTransportFactory()
    with pytest.raises(ValueError, match="requires argument"):
        await factory.create_from_preset("filesystem", custom_args={})


@pytest.mark.asyncio
async def test_create_from_preset_postgres_vault_env_injection() -> None:
    """Verify vault secrets are injected into environment variables via env_mappings."""
    factory = MCPTransportFactory()
    transport = await factory.create_from_preset(
        "postgres",
        custom_args={"connection_string": "postgresql://user@localhost:5432/db"},
        vault_overrides={"POSTGRES_PASSWORD": "super_secret_pg_pass"},
    )
    assert isinstance(transport, StdioTransport)
    assert transport.vault_secrets.get("PGPASSWORD") == "super_secret_pg_pass"


@pytest.mark.asyncio
async def test_create_from_preset_with_vault_service_async() -> None:
    """Verify resolution of secrets from an async vault service."""
    class StubVaultService:
        async def get_secret(self, key: str) -> str | None:
            secrets = {
                "FIRECRAWL_API_KEY": "fc-live-secret-token-12345",
                "LINEAR_API_KEY": "lin-key-9999",
            }
            return secrets.get(key)

    factory = MCPTransportFactory(vault_service=StubVaultService())
    transport = await factory.create_from_preset("firecrawl")
    assert isinstance(transport, StdioTransport)
    assert transport.vault_secrets.get("FIRECRAWL_API_KEY") == "fc-live-secret-token-12345"


@pytest.mark.asyncio
async def test_sse_transport_header_injection() -> None:
    """Verify SSETransport merges vault headers and default headers properly."""
    transport = SSETransport(
        url="https://mcp.internal.local/sse",
        vault_headers={"Authorization": "Bearer secret-bearer-token"},
        headers={"X-Custom-Client": "vigil-test"},
    )
    headers = transport.default_headers
    assert headers["Authorization"] == "Bearer secret-bearer-token"
    assert headers["X-Custom-Client"] == "vigil-test"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json, text/event-stream"


@pytest.mark.asyncio
async def test_create_transport_direct_types() -> None:
    """Verify factory create_transport builds stdio and sse directly."""
    factory = MCPTransportFactory()
    stdio = await factory.create_transport(
        "stdio",
        command="echo hello",
        vault_secrets={"FOO": "bar"},
    )
    assert isinstance(stdio, StdioTransport)
    assert stdio.command == "echo hello"
    assert stdio.vault_secrets == {"FOO": "bar"}

    sse = await factory.create_transport(
        "sse",
        url="https://example.com/sse",
        vault_headers={"X-Key": "val"},
    )
    assert isinstance(sse, SSETransport)
    assert sse.url == "https://example.com/sse"
    assert sse.vault_headers == {"X-Key": "val"}

    with pytest.raises(ValueError, match="unsupported transport type"):
        await factory.create_transport("websocket")


# -----------------------------------------------------------------------------
# 3. Real Subprocess & JSON-RPC 2.0 Handshake Verification
# -----------------------------------------------------------------------------


SCRIPTED_MCP_SERVER = textwrap.dedent(
    """\
    import json, os, sys

    vault_var = os.environ.get("INJECTED_TEST_SECRET", "")

    TOOLS = [
        {
            "name": "echo_vault",
            "description": "Echo back arguments with vault secret",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Message to echo"}
                },
                "required": ["text"]
            }
        }
    ]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method")
        req_id = req.get("id")

        if method == "initialize":
            res = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-scripted-mcp", "version": "1.0"}
            }
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": res}) + "\\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}) + "\\n")
            sys.stdout.flush()
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            text_val = args.get("text", "")
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"ECHO:{text_val}|VAULT:{vault_var}"}],
                    "isError": False
                }
            }) + "\\n")
            sys.stdout.flush()
    """
)


@pytest.mark.asyncio
async def test_stdio_transport_real_subprocess_execution(tmp_path: Path) -> None:
    """Verify StdioTransport spawns a real process, handshakes, and injects vault env."""
    server_script = tmp_path / "mock_mcp_server.py"
    server_script.write_text(SCRIPTED_MCP_SERVER, encoding="utf-8")

    command = f"{sys.executable} {server_script}"
    transport = StdioTransport(
        command=command,
        vault_secrets={"INJECTED_TEST_SECRET": "top-secret-entropy-xyz"},
        timeout=5.0,
    )

    try:
        assert not transport.is_connected
        await transport.connect()
        assert transport.is_connected

        # List tools
        tools = await transport.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo_vault"
        assert tools[0].description == "Echo back arguments with vault secret"
        assert "text" in tools[0].input_schema.get("properties", {})

        # Call tool with vault secret verification
        output, is_error = await transport.call_tool("echo_vault", {"text": "hello_vigil"})
        assert not is_error
        assert "ECHO:hello_vigil|VAULT:top-secret-entropy-xyz" in output
    finally:
        await transport.close()
        assert not transport.is_connected


@pytest.mark.asyncio
async def test_stdio_transport_context_manager(tmp_path: Path) -> None:
    """Verify StdioTransport acts as an async context manager."""
    server_script = tmp_path / "cm_mcp_server.py"
    server_script.write_text(SCRIPTED_MCP_SERVER, encoding="utf-8")

    async with StdioTransport(
        command=f"{sys.executable} {server_script}",
        vault_secrets={"INJECTED_TEST_SECRET": "cm_test_secret"},
    ) as transport:
        assert transport.is_connected
        output, is_error = await transport.call_tool("echo_vault", {"text": "test_cm"})
        assert not is_error
        assert "VAULT:cm_test_secret" in output

    assert not transport.is_connected


# -----------------------------------------------------------------------------
# 4. Dynamic Schema Generation & Argument Validation Tests
# -----------------------------------------------------------------------------


def test_dynamic_args_model_primitive_types() -> None:
    """Verify MCPToolAdapterFactory creates typed Pydantic model from JSON Schema."""
    tool_def = MCPToolDefinition(
        name="calculate_metrics",
        description="Calculate resource metrics",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Metric name"},
                "count": {"type": "integer", "description": "Sample count"},
                "ratio": {"type": "number", "description": "Ratio threshold"},
                "active": {"type": "boolean", "default": True},
                "tags": {"type": "array"},
                "metadata": {"type": "object"},
            },
            "required": ["name", "count"],
        },
    )

    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    assert issubclass(model_cls, BaseModel)

    # Valid instance
    instance = model_cls.model_validate(
        {
            "name": "cpu_load",
            "count": 42,
            "ratio": 0.85,
            "tags": ["prod", "us-east"],
            "metadata": {"cluster": "alpha"},
        }
    )
    assert instance.name == "cpu_load"  # type: ignore[attr-defined]
    assert instance.count == 42  # type: ignore[attr-defined]
    assert instance.active is True  # default value  # type: ignore[attr-defined]

    # Required field missing
    with pytest.raises(ValidationError):
        model_cls.model_validate({"name": "only_name"})

    # Type mismatch
    with pytest.raises(ValidationError):
        model_cls.model_validate({"name": "bad_count", "count": "not_an_int"})


def test_dynamic_args_model_empty_properties_permissive() -> None:
    """Verify schemas with empty or missing properties allow arbitrary kwargs."""
    tool_def = MCPToolDefinition(
        name="wildcard_tool",
        input_schema={"type": "object"},
    )
    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    instance = model_cls.model_validate({"arbitrary_key": "any_value", "number": 123})
    assert instance.arbitrary_key == "any_value"  # type: ignore[attr-defined]
    assert instance.number == 123  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# 5. MCPToolAdapterFactory Adaptation & Invocation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapt_tool_default_external_level() -> None:
    """Verify adapted tool defaults to external approval level and Tool.define contract."""
    tool_def = MCPToolDefinition(
        name="github_create_issue",
        description="Create an issue on GitHub",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title"],
        },
    )

    recorded_calls: list[tuple[str, dict[str, Any]]] = []

    def mock_executor(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        recorded_calls.append((name, arguments))
        return f"Created issue #{101}: {arguments['title']}", False

    tool = MCPToolAdapterFactory.adapt(tool_def, mock_executor)
    assert isinstance(tool, Tool)
    assert tool.name == "github_create_issue"
    assert tool.level == "external"
    assert tool.description == "Create an issue on GitHub"

    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))
    result = await tool.invoke({"title": "Test Bug", "body": "Details here"}, ctx)

    assert isinstance(result, ToolResult)
    assert result.ok
    assert "Created issue #101" in str(result.content)
    assert len(recorded_calls) == 1
    assert recorded_calls[0] == ("github_create_issue", {"title": "Test Bug", "body": "Details here"})


@pytest.mark.asyncio
async def test_adapt_tool_with_transport_protocol() -> None:
    """Verify adaptation dispatches to an MCPTransportProtocol instance."""
    class FakeTransport:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
            if arguments.get("fail"):
                return "Transport intentional failure", True
            return f"Transport success for {name} with {arguments}", False

    tool_def = MCPToolDefinition(
        name="do_work",
        input_schema={"type": "object", "properties": {"fail": {"type": "boolean"}}},
    )
    tool = MCPToolAdapterFactory.adapt(tool_def, FakeTransport())

    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))

    success_res = await tool.invoke({"fail": False}, ctx)
    assert success_res.ok
    assert "Transport success" in str(success_res.content)

    fail_res = await tool.invoke({"fail": True}, ctx)
    assert not fail_res.ok
    assert "Transport intentional failure" in str(fail_res.error)


@pytest.mark.asyncio
async def test_adapt_tool_with_mcp_host_protocol() -> None:
    """Verify adaptation dispatches to an MCPHostProtocol (connector_id, name, args)."""
    class FakeHost:
        async def execute_tool(self, connector_id: str, name: str, arguments: dict[str, Any]) -> Any:
            return SimpleNamespace(
                output=f"Host response from connector {connector_id} for {name}",
                is_error=False,
            )

    tool_def = MCPToolDefinition(name="host_tool")
    tool = MCPToolAdapterFactory.adapt(tool_def, "conn_123", FakeHost())

    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))
    res = await tool.invoke({}, ctx)
    assert res.ok
    assert "from connector conn_123 for host_tool" in str(res.content)


@pytest.mark.asyncio
async def test_adapt_tool_argument_validation_failure() -> None:
    """Verify tool.invoke returns ToolResult.failure when arguments are invalid."""
    tool_def = MCPToolDefinition(
        name="strict_tool",
        input_schema={
            "type": "object",
            "properties": {"req_str": {"type": "string"}},
            "required": ["req_str"],
        },
    )
    tool = MCPToolAdapterFactory.adapt(tool_def, lambda name, args: ("ok", False))
    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))

    result = await tool.invoke({}, ctx)
    assert not result.ok
    assert "invalid arguments for tool 'strict_tool'" in (result.error or "")


# -----------------------------------------------------------------------------
# 6. RunsService Tool Registry & Partition Policy Integration Tests
# -----------------------------------------------------------------------------


def test_runs_service_mcp_tool_registration() -> None:
    """Verify RunsService registers and stores adapted MCP tools."""
    tool_def = MCPToolDefinition(name="custom_mcp_1")
    tool = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False))

    service = RunsService(
        session=None,
        settings=Settings(),
        mcp_tools=[tool],
    )

    registered = service.get_registered_mcp_tools()
    assert "custom_mcp_1" in registered
    assert registered["custom_mcp_1"].name == "custom_mcp_1"

    # Register additional tool dynamically
    tool2 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="custom_mcp_2"), lambda n, a: ("ok", False))
    service.register_mcp_tool(tool2)
    assert "custom_mcp_2" in service.get_registered_mcp_tools()


def test_runs_service_resolve_tool_schemas_includes_mcp() -> None:
    """Verify resolve_tool_schemas outputs OpenAI declarations for built-in and MCP tools."""
    tool_def = MCPToolDefinition(
        name="mcp_extract_meta",
        description="Extract webpage metadata",
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )
    mcp_tool = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False))

    service = RunsService(
        session=None,
        settings=Settings(),
        mcp_tools=[mcp_tool],
    )

    schemas = service.resolve_tool_schemas(declared=["read", "mcp_extract_meta"])
    schema_names = [s["function"]["name"] for s in schemas]

    assert "read" in schema_names
    assert "mcp_extract_meta" in schema_names

    mcp_schema = next(s for s in schemas if s["function"]["name"] == "mcp_extract_meta")
    assert mcp_schema["type"] == "function"
    assert mcp_schema["function"]["description"] == "Extract webpage metadata"
    assert "url" in mcp_schema["function"]["parameters"]["properties"]


def test_runs_service_partition_policy_defaults_mcp_to_external() -> None:
    """Verify adapted MCP tools default to external partition unless explicitly overridden."""
    mcp_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="linear_create_issue"),
        lambda n, a: ("ok", False),
    )

    service = RunsService(
        session=None,
        settings=Settings(),
        mcp_tools=[mcp_tool],
    )

    # Config declares both built-in 'read' and external 'linear_create_issue' without approval_policy
    config_default = AgentConfigDTO(
        id=uuid4(),
        name="test-agent",
        system_prompt="Test agent system prompt",
        model="gpt-4o",
        tools=["read", "linear_create_issue"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={},
        limits={"max_cost_micros": 0},
    )
    policy_default = service.resolve_partition_policy(config_default)

    # Built-in 'read' defaults to 'local'
    assert policy_default.partition_for("read") == "local"
    # MCP tool defaults to 'external'
    assert policy_default.partition_for("linear_create_issue") == "external"

    # Config explicitly allows linear_create_issue as local
    config_override = AgentConfigDTO(
        id=uuid4(),
        name="test-agent",
        system_prompt="Test agent system prompt",
        model="gpt-4o",
        tools=["read", "linear_create_issue"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={"linear_create_issue": "local"},
        limits={"max_cost_micros": 0},
    )
    policy_override = service.resolve_partition_policy(config_override)
    assert policy_override.partition_for("linear_create_issue") == "local"


@pytest.mark.asyncio
async def test_runs_service_mcp_tool_execution_in_loop() -> None:
    """Verify MCPToolRegistryExecutor dispatches registered MCP tools through tool.invoke."""
    executed_args: list[dict[str, Any]] = []

    def fake_mcp_exec(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        executed_args.append(args)
        return f"Echoed from MCP: {args.get('msg')}", False

    mcp_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(
            name="test_echo_mcp",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        ),
        fake_mcp_exec,
    )

    class DummyToolExecutor:
        async def execute(self, name: str, arguments: Any) -> Any:
            return {"name": name, "executed": "builtin"}

        async def execute_external(self, name: str, arguments: Any) -> Any:
            return {"name": name, "executed": "builtin_external"}

    service = RunsService(
        session=None,
        settings=Settings(),
        tools=DummyToolExecutor(),
        mcp_tools=[mcp_tool],
    )

    loop = service.build_loop()
    # The wrapped executor should intercept test_echo_mcp and delegate others
    res_mcp = await loop.tools.execute("test_echo_mcp", {"msg": "hello from loop"})
    assert res_mcp["ok"] is True
    assert "Echoed from MCP: hello from loop" in str(res_mcp["content"])
    assert len(executed_args) == 1

    # Non-MCP tool delegates to inner executor
    res_builtin = await loop.tools.execute("read", {"path": "file.txt"})
    assert res_builtin["executed"] == "builtin"
