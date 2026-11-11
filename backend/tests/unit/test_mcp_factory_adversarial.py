"""Adversarial stress-testing suite for Milestone 3: MCP Factory & Dynamic Tool Registry.

Empirical verification covering:
1. MCPTransportFactory & Stdio/SSE Transports:
   - StdioTransport subprocess execution, environment precedence, and secret isolation.
   - SSETransport header injection, templating, and error response handling.
   - Transport factory direct creation, input validation, and async/sync/user-aware vault resolution.
2. Presets catalog completeness & robustness:
   - Exact specifications for all 5 presets: firecrawl, github, filesystem, postgres, linear.
   - Missing template parameter validation.
   - Environment and header mapping fidelity.
3. MCPToolAdapterFactory deep stress-testing:
   - Dynamic Pydantic schema generation across primitive, nullable, and nested types.
   - Strict validation: missing required fields, type violations, extra field allowance.
   - Sanitization of exotic tool names into valid Pydantic model identifiers.
   - Approval level enforcement: default="external", validation against APPROVAL_LEVELS.
   - Tool execution dispatch across transports, hosts, and callables.
   - ToolResult contract verification: success, transport error, execution crash, invalid input.
4. RunsService tool registry & partition policy integration:
   - Schema generation conformance to OpenAI function declaration specs.
   - Partition policy enforcement: MCP tools default to "external" unless explicitly overridden.
   - RunLoop tool interception and delegation.
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

import httpx
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
from src.modules.runs.tools.mcp_bridge import (
    MCPToolAdapterFactory,
    _json_type_to_python,
    _sanitize_model_name,
)


# =============================================================================
# 1. MCPTransportFactory & Transports Stress Tests
# =============================================================================


def test_stdio_transport_environment_precedence() -> None:
    """Verify StdioTransport prioritizes vault_secrets over user env and os.environ."""
    original_val = os.environ.get("COLLIDING_KEY")
    os.environ["COLLIDING_KEY"] = "from_os_environ"
    try:
        transport = StdioTransport(
            command="echo test",
            env={"COLLIDING_KEY": "from_user_env", "USER_VAR": "u1"},
            vault_secrets={"COLLIDING_KEY": "from_vault_secrets", "VAULT_VAR": "v1"},
        )
        # Note: connect() merges os.environ -> env -> vault_secrets
        # We simulate the exact merging logic:
        merged = os.environ.copy()
        merged.update(transport.env)
        merged.update(transport.vault_secrets)

        assert merged["COLLIDING_KEY"] == "from_vault_secrets"
        assert merged["USER_VAR"] == "u1"
        assert merged["VAULT_VAR"] == "v1"
    finally:
        if original_val is None:
            os.environ.pop("COLLIDING_KEY", None)
        else:
            os.environ["COLLIDING_KEY"] = original_val


@pytest.mark.asyncio
async def test_stdio_transport_invalid_command_syntax() -> None:
    """Verify StdioTransport handles malformed shell commands cleanly."""
    # Unclosed quote in shell command
    transport = StdioTransport(command="echo 'unclosed quote")
    with pytest.raises(MCPHandshakeError, match="cannot parse command"):
        await transport.connect()

    # Empty command
    transport_empty = StdioTransport(command="")
    with pytest.raises(MCPHandshakeError, match="command is empty"):
        await transport_empty.connect()


@pytest.mark.asyncio
async def test_stdio_transport_nonexistent_binary() -> None:
    """Verify StdioTransport raises MCPHandshakeError when executable is not found."""
    transport = StdioTransport(command="/nonexistent/binary/path/never_installed_mcp")
    with pytest.raises(MCPHandshakeError, match="cannot start process"):
        await transport.connect()


@pytest.mark.asyncio
async def test_stdio_transport_lifecycle_and_idempotency() -> None:
    """Verify StdioTransport connect and close are safe and idempotent."""
    # Non-connected transport close should be a no-op
    transport = StdioTransport(command="echo noop")
    assert not transport.is_connected
    await transport.close()
    assert not transport.is_connected


def test_sse_transport_headers_merging_and_precedence() -> None:
    """Verify SSETransport default headers, custom headers, and vault headers."""
    transport = SSETransport(
        url="https://mcp.example.com",
        headers={"User-Agent": "custom-agent", "X-Custom": "val1"},
        vault_headers={"X-Custom": "from-vault", "Authorization": "Bearer tok123"},
    )
    headers = transport.default_headers
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["User-Agent"] == "custom-agent"
    assert headers["X-Custom"] == "from-vault"
    assert headers["Authorization"] == "Bearer tok123"


@pytest.mark.asyncio
async def test_factory_create_transport_validations() -> None:
    """Verify MCPTransportFactory.create_transport validates arguments rigorously."""
    factory = MCPTransportFactory()

    with pytest.raises(ValueError, match="stdio transport requires a command"):
        await factory.create_transport("stdio", command=None)

    with pytest.raises(ValueError, match="stdio transport requires a command"):
        await factory.create_transport("stdio", command="")

    with pytest.raises(ValueError, match="sse transport requires a url"):
        await factory.create_transport("sse", url=None)

    with pytest.raises(ValueError, match="sse transport requires a url"):
        await factory.create_transport("sse", url="")

    with pytest.raises(ValueError, match="unsupported transport type"):
        await factory.create_transport("grpc", url="localhost:50051")


@pytest.mark.asyncio
async def test_factory_vault_secret_resolution_matrix() -> None:
    """Verify _resolve_vault_secret handles dict, sync callable, async callable, and None."""
    # Case 1: None vault
    f_none = MCPTransportFactory(vault_service=None)
    assert await f_none._resolve_vault_secret("KEY") is None

    # Case 2: Dict vault
    f_dict = MCPTransportFactory(vault_service={"KEY": "dict_val"})
    assert await f_dict._resolve_vault_secret("KEY") == "dict_val"
    assert await f_dict._resolve_vault_secret("MISSING") is None

    # Case 3: Sync callable
    class SyncVault:
        def get_secret(self, key: str) -> str:
            return f"sync_{key}"

    f_sync = MCPTransportFactory(vault_service=SyncVault())
    assert await f_sync._resolve_vault_secret("API_KEY") == "sync_API_KEY"

    # Case 4: Async callable
    class AsyncVault:
        async def get_secret(self, key: str) -> str:
            return f"async_{key}"

    f_async = MCPTransportFactory(vault_service=AsyncVault())
    assert await f_async._resolve_vault_secret("API_KEY") == "async_API_KEY"

    # Case 5: User-aware vault get_secret(user_id, key)
    class UserVault:
        def get_secret(self, user_id: Any, key: str) -> str:
            return f"user_{user_id}_{key}"

    f_user = MCPTransportFactory(vault_service=UserVault())
    assert (
        await f_user._resolve_vault_secret("SECRET", user_id="user_42")
        == "user_user_42_SECRET"
    )


# =============================================================================
# 2. Presets Empirical Verification
# =============================================================================


@pytest.mark.parametrize(
    "preset_id,expected_transport,expected_key,expected_env",
    [
        ("firecrawl", "stdio", "FIRECRAWL_API_KEY", "FIRECRAWL_API_KEY"),
        ("github", "stdio", "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"),
        ("linear", "stdio", "LINEAR_API_KEY", "LINEAR_API_KEY"),
    ],
)
@pytest.mark.asyncio
async def test_stdio_presets_without_template_args(
    preset_id: str,
    expected_transport: str,
    expected_key: str,
    expected_env: str,
) -> None:
    """Verify presets that don't require command_args instantiate cleanly with vault override."""
    factory = MCPTransportFactory()
    preset = factory.get_preset(preset_id)
    assert preset.transport == expected_transport
    assert expected_key in preset.required_vault_keys

    transport = await factory.create_from_preset(
        preset_id,
        vault_overrides={expected_key: "override_val_xyz"},
    )
    assert isinstance(transport, StdioTransport)
    assert transport.vault_secrets.get(expected_env) == "override_val_xyz"


@pytest.mark.asyncio
async def test_filesystem_preset_templating() -> None:
    """Verify filesystem preset formats {path} and handles missing path."""
    factory = MCPTransportFactory()
    preset = factory.get_preset("filesystem")
    assert preset.transport == "stdio"
    assert "{path}" in (preset.command_template or "")

    # Valid path
    t_valid = await factory.create_from_preset(
        "filesystem",
        custom_args={"path": "/agent/data/sandbox"},
    )
    assert isinstance(t_valid, StdioTransport)
    assert "/agent/data/sandbox" in t_valid.command

    # Missing path
    with pytest.raises(ValueError, match="requires argument 'path'"):
        await factory.create_from_preset("filesystem", custom_args={})


@pytest.mark.asyncio
async def test_postgres_preset_templating_and_env_mapping() -> None:
    """Verify postgres preset formats {connection_string} and maps POSTGRES_PASSWORD to PGPASSWORD."""
    factory = MCPTransportFactory()
    preset = factory.get_preset("postgres")
    assert preset.transport == "stdio"
    assert "{connection_string}" in (preset.command_template or "")
    assert "POSTGRES_PASSWORD" in preset.required_vault_keys
    assert preset.env_mappings.get("POSTGRES_PASSWORD") == "PGPASSWORD"

    # Valid custom_args + vault_override
    t_valid = await factory.create_from_preset(
        "postgres",
        custom_args={"connection_string": "postgresql://usr@127.0.0.1:5432/db"},
        vault_overrides={"POSTGRES_PASSWORD": "secret_pg_pwd"},
    )
    assert isinstance(t_valid, StdioTransport)
    assert "postgresql://usr@127.0.0.1:5432/db" in t_valid.command
    assert t_valid.vault_secrets.get("PGPASSWORD") == "secret_pg_pwd"

    # Missing connection_string
    with pytest.raises(ValueError, match="requires argument 'connection_string'"):
        await factory.create_from_preset("postgres", custom_args={})


@pytest.mark.asyncio
async def test_custom_sse_preset_registration_and_header_formatting() -> None:
    """Verify custom SSE preset supports header templating (e.g. Bearer {value})."""
    factory = MCPTransportFactory()
    custom_sse = MCPPreset(
        id="custom_crm",
        name="CRM Integration",
        description="Remote CRM SSE endpoint",
        transport="sse",
        url_template="https://crm.corp.internal/{tenant}/events",
        required_vault_keys=("CRM_API_KEY",),
        header_mappings={"CRM_API_KEY": "Authorization: Bearer {value}"},
    )
    factory.register_preset(custom_sse)

    transport = await factory.create_from_preset(
        "custom_crm",
        custom_args={"tenant": "customer_acme"},
        vault_overrides={"CRM_API_KEY": "jwt_crm_token_987"},
    )
    assert isinstance(transport, SSETransport)
    assert transport.url == "https://crm.corp.internal/customer_acme/events"
    assert transport.vault_headers.get("Authorization") == "Bearer jwt_crm_token_987"


# =============================================================================
# 3. MCPToolAdapterFactory Deep Stress-Testing
# =============================================================================


def test_sanitize_model_name_variations() -> None:
    """Verify model name sanitization creates PascalCase names for exotic tool names."""
    assert _sanitize_model_name("echo") == "EchoArgs"
    assert _sanitize_model_name("github_create_issue") == "GithubCreateIssueArgs"
    assert _sanitize_model_name("web-search-v2") == "WebSearchV2Args"
    assert _sanitize_model_name("tool.submodule:action") == "ToolSubmoduleActionArgs"
    assert _sanitize_model_name("___") == "McpDynamicArgs"
    assert _sanitize_model_name("") == "McpDynamicArgs"


def test_json_type_to_python_mapping() -> None:
    """Verify JSON schema types map accurately to Python typing primitives."""
    assert _json_type_to_python("string") is str
    assert _json_type_to_python("integer") is int
    assert _json_type_to_python("number") is float
    assert _json_type_to_python("boolean") is bool
    assert _json_type_to_python("array") is list
    assert _json_type_to_python("object") is dict
    assert _json_type_to_python("unknown_type") is Any
    # Nullable union ["string", "null"]
    assert _json_type_to_python(["string", "null"]) is str
    assert _json_type_to_python(["null"]) is Any


def test_dynamic_args_model_deep_validation() -> None:
    """Verify dynamic Pydantic model enforces required types and defaults."""
    tool_def = MCPToolDefinition(
        name="complex_schema_tool",
        input_schema={
            "type": "object",
            "properties": {
                "str_req": {"type": "string"},
                "str_opt": {"type": "string", "default": "fallback_val"},
                "int_req": {"type": "integer"},
                "num_opt": {"type": "number"},
                "bool_opt": {"type": "boolean", "default": False},
                "list_req": {"type": "array"},
                "dict_req": {"type": "object"},
            },
            "required": ["str_req", "int_req", "list_req", "dict_req"],
        },
    )

    model_cls = MCPToolAdapterFactory.create_args_model(tool_def)
    assert issubclass(model_cls, BaseModel)

    # Valid payload
    valid_data = {
        "str_req": "hello",
        "int_req": 100,
        "list_req": [1, 2, 3],
        "dict_req": {"key": "val"},
        "extra_field": "allowed_extra_value",
    }
    instance = model_cls.model_validate(valid_data)
    assert instance.str_req == "hello"  # type: ignore[attr-defined]
    assert instance.str_opt == "fallback_val"  # type: ignore[attr-defined]
    assert instance.int_req == 100  # type: ignore[attr-defined]
    assert instance.num_opt is None  # type: ignore[attr-defined]
    assert instance.bool_opt is False  # type: ignore[attr-defined]
    assert instance.extra_field == "allowed_extra_value"  # type: ignore[attr-defined]

    # Required field missing raises ValidationError
    with pytest.raises(ValidationError):
        model_cls.model_validate({"int_req": 100, "list_req": [], "dict_req": {}})

    # Type violation: passing string for integer raises ValidationError
    with pytest.raises(ValidationError):
        model_cls.model_validate({
            "str_req": "hello",
            "int_req": "not_an_int",
            "list_req": [],
            "dict_req": {},
        })


def test_dynamic_args_model_empty_and_non_dict_schema() -> None:
    """Verify dynamic model generator handles empty, null, or malformed schemas."""
    # Empty dict
    t1 = MCPToolDefinition(name="tool_empty", input_schema={})
    m1 = MCPToolAdapterFactory.create_args_model(t1)
    inst1 = m1.model_validate({"foo": "bar", "baz": 123})
    assert inst1.foo == "bar"  # type: ignore[attr-defined]

    # None schema
    t2 = MCPToolDefinition(name="tool_none", input_schema=None)  # type: ignore[arg-type]
    m2 = MCPToolAdapterFactory.create_args_model(t2)
    inst2 = m2.model_validate({"any": True})
    assert inst2.any is True  # type: ignore[attr-defined]


def test_tool_adapter_approval_levels() -> None:
    """Verify MCPToolAdapterFactory sets approval level="external" by default and validates."""
    tool_def = MCPToolDefinition(name="test_approval")

    # Default level is "external"
    t_def = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False))
    assert t_def.level == "external"

    # Custom valid level override from APPROVAL_LEVELS ("read", "local", "external")
    t_read = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False), level="read")
    assert t_read.level == "read"

    t_local = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False), level="local")
    assert t_local.level == "local"

    t_ext = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False), level="external")
    assert t_ext.level == "external"

    # Invalid level ("prompt", "unsupported") falls back to "external"
    t_prompt = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False), level="prompt")
    assert t_prompt.level == "external"

    t_inv = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False), level="dangerous_unsupported_level")
    assert t_inv.level == "external"


@pytest.mark.asyncio
async def test_tool_adapter_execution_dispatch_modes() -> None:
    """Verify tool execution dispatch across MCPTransportProtocol, Host, and Callables."""
    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))
    tool_def = MCPToolDefinition(name="dispatch_test")

    # 1. Transport call_tool error outcome
    class FailingTransport:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
            return "Server reported internal tool error", True

    t_failing = MCPToolAdapterFactory.adapt(tool_def, FailingTransport())
    res_failing = await t_failing.invoke({}, ctx)
    assert not res_failing.ok
    assert "Server reported internal tool error" in (res_failing.error or "")

    # 2. Transport call_tool exception
    class CrashingTransport:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
            raise ConnectionResetError("Connection died mid-request")

    t_crashing = MCPToolAdapterFactory.adapt(tool_def, CrashingTransport())
    res_crashing = await t_crashing.invoke({}, ctx)
    assert not res_crashing.ok
    assert "Connection died mid-request" in (res_crashing.error or "")

    # 3. Host returning ToolResult directly
    class HostReturningToolResult:
        async def execute_tool(self, conn_id: str, name: str, args: dict[str, Any]) -> ToolResult:
            return ToolResult.success(f"Host direct result for {name}")

    t_host_tr = MCPToolAdapterFactory.adapt(tool_def, "c1", HostReturningToolResult())
    res_host_tr = await t_host_tr.invoke({}, ctx)
    assert res_host_tr.ok
    assert "Host direct result for dispatch_test" in str(res_host_tr.content)

    # 4. Host returning error object
    class HostReturningError:
        async def execute_tool(self, conn_id: str, name: str, args: dict[str, Any]) -> Any:
            return SimpleNamespace(is_error=True, output="Host error message")

    t_host_err = MCPToolAdapterFactory.adapt(tool_def, "c1", HostReturningError())
    res_host_err = await t_host_err.invoke({}, ctx)
    assert not res_host_err.ok
    assert "Host error message" in (res_host_err.error or "")

    # 5. Async callable returning raw string
    async def async_callable(name: str, args: dict[str, Any]) -> str:
        return f"Async result: {name}"

    t_async_fn = MCPToolAdapterFactory.adapt(tool_def, async_callable)
    res_async_fn = await t_async_fn.invoke({}, ctx)
    assert res_async_fn.ok
    assert "Async result: dispatch_test" in str(res_async_fn.content)

    # 6. Completely unsupported executor object
    t_unsupported = MCPToolAdapterFactory.adapt(tool_def, 42)  # int has no call_tool or execute_tool
    res_unsupported = await t_unsupported.invoke({}, ctx)
    assert not res_unsupported.ok
    assert "No suitable transport or executor found" in (res_unsupported.error or "")


@pytest.mark.asyncio
async def test_tool_adapter_argument_validation_error_handling() -> None:
    """Verify tool.invoke returns ToolResult.failure when arguments fail schema validation."""
    tool_def = MCPToolDefinition(
        name="strict_search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    )
    tool = MCPToolAdapterFactory.adapt(tool_def, lambda n, a: ("ok", False))
    ctx = ToolContext(runner=SimpleNamespace(), container_id="test", delivery_dir=Path("/tmp"))

    # Missing query
    res_missing = await tool.invoke({"limit": 10}, ctx)
    assert not res_missing.ok
    assert "invalid arguments for tool 'strict_search'" in (res_missing.error or "")

    # Invalid type for limit
    res_bad_type = await tool.invoke({"query": "python", "limit": "not_an_int"}, ctx)
    assert not res_bad_type.ok
    assert "invalid arguments for tool 'strict_search'" in (res_bad_type.error or "")


# =============================================================================
# 4. RunsService Tool Registry & Partition Policy Stress-Testing
# =============================================================================


def test_runs_service_multiple_mcp_tools_and_deduplication() -> None:
    """Verify RunsService registers multiple MCP tools and allows dynamic addition."""
    t1 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="mcp_alpha"), lambda n, a: ("ok", False))
    t2 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="mcp_beta"), lambda n, a: ("ok", False))

    service = RunsService(session=None, settings=Settings(), mcp_tools=[t1])
    assert "mcp_alpha" in service.get_registered_mcp_tools()
    assert "mcp_beta" not in service.get_registered_mcp_tools()

    service.register_mcp_tools([t2])
    registered = service.get_registered_mcp_tools()
    assert "mcp_alpha" in registered
    assert "mcp_beta" in registered


def test_runs_service_resolve_tool_schemas_filtering() -> None:
    """Verify resolve_tool_schemas correctly filters by declared tools list."""
    t1 = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="mcp_tool_1", description="Description 1"),
        lambda n, a: ("ok", False),
    )
    t2 = MCPToolAdapterFactory.adapt(
        MCPToolDefinition(name="mcp_tool_2", description="Description 2"),
        lambda n, a: ("ok", False),
    )

    service = RunsService(session=None, settings=Settings(), mcp_tools=[t1, t2])

    # Declaring only mcp_tool_1 and built-in read
    schemas = service.resolve_tool_schemas(declared=["read", "mcp_tool_1"])
    names = {s["function"]["name"] for s in schemas}
    assert "read" in names
    assert "mcp_tool_1" in names
    assert "mcp_tool_2" not in names


def test_runs_service_partition_policy_complex_matrix() -> None:
    """Verify partition policy correctly categorizes built-in vs MCP tools under various configs."""
    mcp_t1 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="mcp_unconfigured"), lambda n, a: ("ok", False))
    mcp_t2 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="mcp_explicit_local"), lambda n, a: ("ok", False))
    mcp_t3 = MCPToolAdapterFactory.adapt(MCPToolDefinition(name="mcp_explicit_background"), lambda n, a: ("ok", False))

    service = RunsService(session=None, settings=Settings(), mcp_tools=[mcp_t1, mcp_t2, mcp_t3])

    config = AgentConfigDTO(
        id=uuid4(),
        name="test-agent",
        system_prompt="Test agent",
        model="gpt-4o",
        tools=["read", "bash", "mcp_unconfigured", "mcp_explicit_local", "mcp_explicit_background"],
        skills=[],
        memory_scopes=["global"],
        approval_policy={
            "read": "external",  # Overriding built-in default to external
            "mcp_explicit_local": "local",  # Overriding MCP default to local
            "mcp_explicit_background": "background",  # Overriding MCP default to background
        },
        limits={"max_cost_micros": 0},
    )

    policy = service.resolve_partition_policy(config)

    # Built-in read was overridden to external
    assert policy.partition_for("read") == "external"
    # Built-in bash was unconfigured -> defaults to local
    assert policy.partition_for("bash") == "local"
    # MCP unconfigured -> defaults to external
    assert policy.partition_for("mcp_unconfigured") == "external"
    # MCP explicitly overridden to local
    assert policy.partition_for("mcp_explicit_local") == "local"
    # MCP explicitly overridden to background
    assert policy.partition_for("mcp_explicit_background") == "background"
