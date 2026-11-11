"""Adversarial stress-testing suite for Milestone 3.

Empirically probes edge cases, failure modes, and security constraints in:
1. MCPTransportFactory & Transports (missing vault secrets, malformed presets, template formatting, process lifecycle)
2. MCPToolAdapterFactory (empty schemas, complex schemas, unions, name collisions, extra parameters, error dispatch)
3. RunsService Partition Policy & Tool Resolution (default external partition, overrides, schema resolution)
"""
from __future__ import annotations

import asyncio
import json
import os
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
from src.modules.runs.service import MCPToolRegistryExecutor, RunsService
from src.modules.runs.tools import Tool, ToolContext, ToolResult
from src.modules.runs.tools.mcp_bridge import MCPToolAdapterFactory


# =============================================================================
# SUITE 1: MCPTransportFactory & Transport Edge Cases
# =============================================================================


@pytest.mark.asyncio
async def test_probe_missing_vault_secrets_preset_graceful() -> None:
    """Probe factory behavior when required vault keys are missing.

    Verifies that missing vault keys do not crash factory creation (enabling
    mock/fallback workflows), and that unsupplied secrets are simply omitted
    from vault_secrets/vault_headers.
    """
    factory = MCPTransportFactory(vault_service={})
    transport = await factory.create_from_preset("firecrawl")
    assert isinstance(transport, StdioTransport)
    # FIRECRAWL_API_KEY was not in vault, so vault_secrets should not contain it
    assert "FIRECRAWL_API_KEY" not in transport.vault_secrets

    # With Postgres preset: POSTGRES_PASSWORD missing -> PGPASSWORD omitted
    pg_transport = await factory.create_from_preset(
        "postgres",
        custom_args={"connection_string": "postgres://localhost/db"},
    )
    assert isinstance(pg_transport, StdioTransport)
    assert "PGPASSWORD" not in pg_transport.vault_secrets


@pytest.mark.asyncio
async def test_probe_malformed_and_edge_preset_ids() -> None:
    """Probe factory with non-existent, empty, and malformed preset IDs."""
    factory = MCPTransportFactory()

    with pytest.raises(KeyError, match="unknown MCP preset"):
        factory.get_preset("non_existent")

    with pytest.raises(KeyError, match="unknown MCP preset"):
        factory.get_preset("")

    with pytest.raises(KeyError, match="unknown MCP preset"):
        await factory.create_from_preset("UNKNOWN_PRESET_999")

    # Case sensitivity check: "Postgres" vs "postgres"
    with pytest.raises(KeyError):
        factory.get_preset("Postgres")


@pytest.mark.asyncio
async def test_probe_template_placeholder_formatting_edge_cases() -> None:
    """Probe template string substitution with missing, extra, and unusual arguments."""
    factory = MCPTransportFactory()

    # 1. Missing required placeholder in command_template raises ValueError
    with pytest.raises(ValueError, match="requires argument 'path'"):
        await factory.create_from_preset("filesystem", custom_args={})

    # 2. Extra unneeded custom_args should be safely ignored
    fs_transport = await factory.create_from_preset(
        "filesystem",
        custom_args={"path": "/workspace/data", "unused_extra": "ignored_val"},
    )
    assert isinstance(fs_transport, StdioTransport)
    assert "/workspace/data" in fs_transport.command
    assert "unused_extra" not in fs_transport.command

    # 3. Custom preset with url_template placeholder
    factory.register_preset(
        MCPPreset(
            id="custom_sse",
            name="Custom SSE",
            description="Testing SSE placeholder",
            transport="sse",
            url_template="https://api.example.com/v1/{tenant}/mcp",
        )
    )
    # Missing tenant placeholder
    with pytest.raises(ValueError, match="requires argument 'tenant'"):
        await factory.create_from_preset("custom_sse", custom_args={})

    # Correct tenant placeholder
    sse_transport = await factory.create_from_preset(
        "custom_sse",
        custom_args={"tenant": "acme-corp"},
    )
    assert isinstance(sse_transport, SSETransport)
    assert sse_transport.url == "https://api.example.com/v1/acme-corp/mcp"


@pytest.mark.asyncio
async def test_probe_vault_service_signatures_and_edge_cases() -> None:
    """Probe factory interaction with various vault service shapes."""
    # 1. None vault service
    factory_none = MCPTransportFactory(vault_service=None)
    t1 = await factory_none.create_from_preset("linear")
    assert isinstance(t1, StdioTransport)
    assert "LINEAR_API_KEY" not in t1.vault_secrets

    # 2. Sync get_secret
    class SyncVault:
        def get_secret(self, key: str) -> str | None:
            if key == "LINEAR_API_KEY":
                return "sync-key-123"
            return None

    factory_sync = MCPTransportFactory(vault_service=SyncVault())
    t2 = await factory_sync.create_from_preset("linear")
    assert isinstance(t2, StdioTransport)
    assert t2.vault_secrets.get("LINEAR_API_KEY") == "sync-key-123"

    # 3. Two-parameter get_secret(user_id, key)
    class UserScopedVault:
        async def get_secret(self, user_id: Any, key: str) -> str | None:
            if user_id == "user_42" and key == "GITHUB_PERSONAL_ACCESS_TOKEN":
                return "ghp_user42_token"
            return None

    factory_scoped = MCPTransportFactory(vault_service=UserScopedVault())
    t3 = await factory_scoped.create_from_preset("github", user_id="user_42")
    assert isinstance(t3, StdioTransport)
    assert t3.vault_secrets.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "ghp_user42_token"

    # 4. Vault overrides take precedence over vault service
    t4 = await factory_scoped.create_from_preset(
        "github",
        user_id="user_42",
        vault_overrides={"GITHUB_PERSONAL_ACCESS_TOKEN": "override_token"},
    )
    assert isinstance(t4, StdioTransport)
    assert t4.vault_secrets.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "override_token"


@pytest.mark.asyncio
async def test_probe_stdio_transport_failure_modes() -> None:
    """Probe StdioTransport under failure conditions (empty, malformed, non-existent)."""
    # 1. Empty command
    t_empty = StdioTransport(command="")
    with pytest.raises(MCPHandshakeError, match="command is empty"):
        await t_empty.connect()

    # 2. Whitespace-only command
    t_spaces = StdioTransport(command="    ")
    with pytest.raises(MCPHandshakeError, match="command is empty"):
        await t_spaces.connect()

    # 3. Unmatched quote
    t_quote = StdioTransport(command='npx -y "broken quote')
    with pytest.raises(MCPHandshakeError, match="cannot parse command"):
        await t_quote.connect()

    # 4. Non-existent binary
    t_nonexist = StdioTransport(command="/tmp/definitely_not_a_binary_executable_12345")
    with pytest.raises(MCPHandshakeError, match="cannot start process"):
        await t_nonexist.connect()

    # 5. Idempotent close
    await t_empty.close()
    await t_empty.close()


@pytest.mark.asyncio
async def test_probe_stdio_transport_immediate_exit_stderr_drain(tmp_path: Path) -> None:
    """Probe StdioTransport when child process exits immediately with stderr message."""
    script = tmp_path / "failing_server.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            sys.stderr.write("FATAL: database connection refused on port 5432\\n")
            sys.stderr.flush()
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )

    transport = StdioTransport(command=f"{sys.executable} {script}", timeout=2.0)
    with pytest.raises(MCPHandshakeError) as exc_info:
        await transport.connect()

    # Handshake fails because child process exited with code 1 before replying
    err_msg = str(exc_info.value)
    assert "stdio handshake failed during initialize" in err_msg
    assert "exited with code 1 before it answered" in err_msg
    await transport.close()


# =============================================================================
# SUITE 2: MCPToolAdapterFactory Edge Cases
# =============================================================================


def test_probe_adapter_empty_and_minimal_schemas() -> None:
    """Probe dynamic args model generation with empty, None, and minimal schemas."""
    # 1. None schema
    tool_none = MCPToolDefinition(name="tool_none", input_schema=None)  # type: ignore[arg-type]
    m_none = MCPToolAdapterFactory.create_args_model(tool_none)
    inst_none = m_none.model_validate({"any_field": 42})
    assert inst_none.any_field == 42  # type: ignore[attr-defined]

    # 2. Empty dict schema
    tool_empty = MCPToolDefinition(name="tool_empty", input_schema={})
    m_empty = MCPToolAdapterFactory.create_args_model(tool_empty)
    inst_empty = m_empty.model_validate({"foo": "bar"})
    assert inst_empty.foo == "bar"  # type: ignore[attr-defined]

    # 3. Object schema with no properties key
    tool_no_props = MCPToolDefinition(name="tool_no_props", input_schema={"type": "object"})
    m_no_props = MCPToolAdapterFactory.create_args_model(tool_no_props)
    inst_no_props = m_no_props.model_validate({"x": [1, 2, 3]})
    assert inst_no_props.x == [1, 2, 3]  # type: ignore[attr-defined]

    # 4. Schema with empty properties dict
    tool_props_empty = MCPToolDefinition(
        name="tool_props_empty", input_schema={"type": "object", "properties": {}}
    )
    m_props_empty = MCPToolAdapterFactory.create_args_model(tool_props_empty)
    inst_props_empty = m_props_empty.model_validate({"val": 999})
    assert inst_props_empty.val == 999  # type: ignore[attr-defined]


def test_probe_adapter_complex_nested_and_union_schemas() -> None:
    """Probe dynamic schema generation for nested objects, arrays, and type unions."""
    tool_def = MCPToolDefinition(
        name="complex_tool",
        input_schema={
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "description": "Arbitrary configuration map",
                },
                "tags": {
                    "type": "array",
                    "description": "Tag list",
                },
                "nullable_str": {
                    "type": ["string", "null"],
                    "description": "Nullable string union",
                },
                "nullable_int": {
                    "type": ["integer", "null"],
                    "description": "Nullable int union",
                },
                "untyped_prop": {
                    "description": "Property with no explicit JSON type",
                },
                "malformed_spec": "not_a_dictionary",
            },
            "required": ["config"],
        },
    )

    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    assert issubclass(model_cls, BaseModel)

    # Valid payload
    valid = model_cls.model_validate(
        {
            "config": {"debug": True, "retries": 3},
            "tags": ["env:staging", "version:2.1"],
            "nullable_str": "hello",
            "nullable_int": None,
            "untyped_prop": {"custom": "object"},
        }
    )
    assert valid.config == {"debug": True, "retries": 3}  # type: ignore[attr-defined]
    assert valid.tags == ["env:staging", "version:2.1"]  # type: ignore[attr-defined]
    assert valid.nullable_str == "hello"  # type: ignore[attr-defined]
    assert valid.nullable_int is None  # type: ignore[attr-defined]

    # Missing required 'config'
    with pytest.raises(ValidationError):
        model_cls.model_validate({"tags": ["a"]})

    # Type mismatch: config must be dict
    with pytest.raises(ValidationError):
        model_cls.model_validate({"config": "not_a_dict"})

    # Type mismatch: tags must be list
    with pytest.raises(ValidationError):
        model_cls.model_validate({"config": {}, "tags": "not_a_list"})


def test_probe_adapter_extra_parameters_preserved() -> None:
    """Probe whether extra parameters are allowed and passed through model_dump."""
    tool_def = MCPToolDefinition(
        name="strict_with_extra",
        input_schema={
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        },
    )

    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    instance = model_cls.model_validate(
        {"action": "deploy", "extra_1": "custom_val", "extra_2": 42}
    )
    dumped = instance.model_dump()
    assert dumped["action"] == "deploy"
    assert dumped["extra_1"] == "custom_val"
    assert dumped["extra_2"] == 42


def test_probe_adapter_name_sanitization_and_unusual_characters() -> None:
    """Probe model generation with special characters in tool name."""
    tool_def = MCPToolDefinition(
        name="github.com/v1-alpha:search-issues",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    assert issubclass(model_cls, BaseModel)
    assert "Github" in model_cls.__name__


@pytest.mark.asyncio
async def test_probe_adapter_error_and_exception_handling() -> None:
    """Probe MCPToolAdapterFactory execution dispatch under transport failures."""
    # 1. Transport raises an unexpected exception
    class ExplodingTransport:
        async def call_tool(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
            raise ConnectionResetError("Broken pipe to MCP child process")

    tool_def = MCPToolDefinition(name="fail_transport")
    tool = MCPToolAdapterFactory.adapt(tool_def, ExplodingTransport())

    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))
    result = await tool.invoke({}, ctx)
    assert isinstance(result, ToolResult)
    assert not result.ok
    assert "Broken pipe" in str(result.error)

    # 2. Host execution raises an exception
    class ExplodingHost:
        async def execute_tool(self, conn: str, name: str, args: dict[str, Any]) -> Any:
            raise TimeoutError("Host timeout after 30s")

    tool_host = MCPToolAdapterFactory.adapt(tool_def, "conn_1", ExplodingHost())
    result_host = await tool_host.invoke({}, ctx)
    assert not result_host.ok
    assert "Host timeout" in str(result_host.error)

    # 3. Unsupported executor type
    tool_bad = MCPToolAdapterFactory.adapt(tool_def, 12345)  # neither transport, host, nor callable
    result_bad = await tool_bad.invoke({}, ctx)
    assert not result_bad.ok
    assert "No suitable transport or executor" in str(result_bad.error)


# =============================================================================
# SUITE 3: RunsService Partition Policy & Tool Resolution
# =============================================================================


def test_probe_runs_service_partition_policy_isolation() -> None:
    """Verify that adapted MCP tools NEVER leak into local partition by default.

    Probes:
    1. MCP tool declared in config.tools without approval_policy -> 'external'.
    2. MCP tool NOT declared in config.tools -> 'external'.
    3. MCP tool explicitly overridden to 'local' -> 'local'.
    4. MCP tool explicitly overridden to 'background' -> 'background'.
    5. Built-in tool ('read') -> 'local'.
    6. Empty config -> 'external' default.
    """
    mcp_tool_1 = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="postgres_query"), lambda n, a: ("ok", False)
    )
    mcp_tool_2 = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="linear_delete_project"), lambda n, a: ("ok", False)
    )

    service = RunsService(
        session=None,
        settings=Settings(),
        mcp_tools=[mcp_tool_1, mcp_tool_2],
    )

    # Case 1: postgres_query in tools list, linear_delete_project not in tools list
    cfg1 = AgentConfigDTO(
        id=uuid4(),
        name="agent-default",
        system_prompt="Prompt",
        model="gpt-4o",
        tools=["read", "write", "postgres_query"],
        skills=[],
        memory_scopes=[],
        approval_policy={},
        limits={},
    )
    policy1 = service.resolve_partition_policy(cfg1)
    assert policy1.partition_for("read") == "local"
    assert policy1.partition_for("write") == "local"
    assert policy1.partition_for("postgres_query") == "external"
    assert policy1.partition_for("linear_delete_project") == "external"

    # Case 2: Explicitly override postgres_query to local, linear_delete_project to background
    cfg2 = AgentConfigDTO(
        id=uuid4(),
        name="agent-override",
        system_prompt="Prompt",
        model="gpt-4o",
        tools=["read", "postgres_query", "linear_delete_project"],
        skills=[],
        memory_scopes=[],
        approval_policy={
            "postgres_query": "local",
            "linear_delete_project": "background",
        },
        limits={},
    )
    policy2 = service.resolve_partition_policy(cfg2)
    assert policy2.partition_for("read") == "local"
    assert policy2.partition_for("postgres_query") == "local"
    assert policy2.partition_for("linear_delete_project") == "background"

    # Case 3: None config
    policy_none = service.resolve_partition_policy(None)
    assert policy_none.partition_for("postgres_query") == "external"
    assert policy_none.partition_for("read") == "external"


def test_probe_runs_service_resolve_tool_schemas_filtering() -> None:
    """Verify tool schemas only include declared tools (built-in and MCP)."""
    mcp_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(
            name="mcp_special_search",
            description="Special search",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        ),
        lambda n, a: ("ok", False),
    )

    service = RunsService(
        session=None,
        settings=Settings(),
        mcp_tools=[mcp_tool],
    )

    # When declared only has "read", mcp_special_search is NOT included
    schemas_read_only = service.resolve_tool_schemas(declared=["read"])
    names_read_only = [s["function"]["name"] for s in schemas_read_only]
    assert "read" in names_read_only
    assert "mcp_special_search" not in names_read_only

    # When declared includes "mcp_special_search"
    schemas_with_mcp = service.resolve_tool_schemas(declared=["read", "mcp_special_search"])
    names_with_mcp = [s["function"]["name"] for s in schemas_with_mcp]
    assert "read" in names_with_mcp
    assert "mcp_special_search" in names_with_mcp

    # Non-existent tool in declared is ignored cleanly without error
    schemas_nonexistent = service.resolve_tool_schemas(declared=["read", "ghost_tool"])
    names_nonexistent = [s["function"]["name"] for s in schemas_nonexistent]
    assert names_nonexistent == ["read"]


@pytest.mark.asyncio
async def test_probe_mcp_executor_delegation_and_context_propagation() -> None:
    """Verify MCPToolRegistryExecutor properly propagates ToolContext and handles inner."""
    tool_def = MCPToolDefinition(name="test_context_mcp")

    async def custom_callable(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        return "mcp_ok", False

    # Create adapted tool using MCPToolAdapterFactory
    mcp_tool = MCPToolAdapterFactory.adapt(
        tool_def,
        custom_callable,
        level="external",
    )

    class StubInnerExecutor:
        container_id = "test-sandbox-id"
        delivery_dir = Path("/tmp/delivery")
        sandbox = "sandbox-runner"

        async def execute(self, name: str, arguments: Any) -> Any:
            return {"inner": name}

        async def execute_external(self, name: str, arguments: Any) -> Any:
            return {"inner_external": name}

    executor = MCPToolRegistryExecutor(
        inner=StubInnerExecutor(),
        mcp_tools={"test_context_mcp": mcp_tool},
    )

    # 1. Execute MCP tool via execute()
    res1 = await executor.execute("test_context_mcp", {})
    assert res1["ok"] is True
    assert res1["content"] == "mcp_ok"

    # 2. Execute MCP tool via execute_external()
    res2 = await executor.execute_external("test_context_mcp", {})
    assert res2["ok"] is True

    # 3. Inner delegation for other tools
    inner_res1 = await executor.execute("read", {"path": "a.txt"})
    assert inner_res1 == {"inner": "read"}

    inner_res2 = await executor.execute_external("read", {"path": "a.txt"})
    assert inner_res2 == {"inner_external": "read"}


@pytest.mark.asyncio
async def test_probe_template_unclosed_and_positional_braces() -> None:
    """Verify behavior when command templates have unclosed or positional braces."""
    factory = MCPTransportFactory()

    # Unclosed brace in command template raises ValueError during format
    factory.register_preset(
        MCPPreset(
            id="bad_brace",
            name="Bad Brace",
            description="Testing unclosed brace",
            transport="stdio",
            command_template="echo {unclosed",
        )
    )
    with pytest.raises(ValueError):
        await factory.create_from_preset("bad_brace")

    # Positional brace in command template raises IndexError
    factory.register_preset(
        MCPPreset(
            id="positional_brace",
            name="Positional",
            description="Testing positional brace",
            transport="stdio",
            command_template="echo {}",
        )
    )
    with pytest.raises(IndexError):
        await factory.create_from_preset("positional_brace")


def test_probe_partition_policy_unrecognized_level_fails_safe_to_external() -> None:
    """Verify that unrecognized partition levels in approval_policy fall back safely to external."""
    mcp_tool = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="custom_cmd"), lambda n, a: ("ok", False)
    )
    service = RunsService(session=None, settings=Settings(), mcp_tools=[mcp_tool])

    cfg = AgentConfigDTO(
        id=uuid4(),
        name="test-agent",
        system_prompt="Prompt",
        model="gpt-4o",
        tools=["custom_cmd"],
        skills=[],
        memory_scopes=[],
        approval_policy={"custom_cmd": "SUPER_USER_BYPASS"},
        limits={},
    )
    policy = service.resolve_partition_policy(cfg)
    assert policy.partition_for("custom_cmd") == "external"


def test_probe_adapter_pydantic_model_field_collisions() -> None:
    """Probe behavior when tool schema defines properties matching BaseModel methods."""
    # When a schema uses protected BaseModel methods like model_dump,
    # Pydantic v2 raises ValueError unless protected_namespaces is relaxed.
    tool_def_model_dump = MCPToolDefinition(
        name="conflict_tool",
        input_schema={
            "type": "object",
            "properties": {"model_dump": {"type": "string"}},
        },
    )
    with pytest.raises(ValueError, match="protected namespace"):
        MCPToolAdapterFactory.create_args_model(tool_def_model_dump)

    # But normal properties like model_name, model_type, etc. succeed cleanly
    tool_def_model_name = MCPToolDefinition(
        name="embedding_tool",
        input_schema={
            "type": "object",
            "properties": {
                "model_name": {"type": "string"},
                "model_version": {"type": "integer"},
            },
        },
    )
    model_cls = MCPToolAdapterFactory.create_args_model(tool_def_model_name)
    instance = model_cls.model_validate({"model_name": "text-embedding-3-small", "model_version": 2})
    assert instance.model_name == "text-embedding-3-small"  # type: ignore[attr-defined]
    assert instance.model_version == 2  # type: ignore[attr-defined]

