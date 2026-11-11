"""Dynamic MCP tool adapter bridge.

Adapts discovered third-party MCP tool definitions into Vigil runtime Tool
instances with dynamic Pydantic argument validation, approval partitioning,
and transport/client execution dispatch.
"""
from __future__ import annotations

import inspect
import re
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field, create_model

from src.integrations.mcp.mcp import MCPToolDefinition
from src.modules.runs.tools import APPROVAL_LEVELS, ApprovalLevel, Tool, ToolContext, ToolResult


def _sanitize_model_name(name: str) -> str:
    """Turn a tool name like 'echo_ext' or 'search-files' into a valid PascalCase class name."""
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    parts = [part.capitalize() for part in cleaned.split("_") if part]
    base = "".join(parts) or "McpDynamic"
    return f"{base}Args"


def _json_type_to_python(schema_type: Any) -> type:
    """Map JSON Schema primitive types to Python types."""
    if isinstance(schema_type, list):
        # E.g. ["string", "null"]
        non_null = [t for t in schema_type if t != "null"]
        if non_null:
            return _json_type_to_python(non_null[0])
        return Any

    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(str(schema_type), Any)


class MCPToolAdapterFactory:
    """Converts discovered MCPToolDefinition into runtime Vigil Tool instances."""

    @classmethod
    def create_args_model(cls, tool_def: MCPToolDefinition) -> type[BaseModel]:
        """Dynamically create a Pydantic model validating the MCP input_schema."""
        schema = tool_def.input_schema if isinstance(tool_def.input_schema, dict) else {}
        properties = schema.get("properties")
        required_fields = set(schema.get("required") or ())

        model_name = _sanitize_model_name(tool_def.name)

        if not isinstance(properties, dict) or not properties:
            # Schema without explicit properties: allow any extra arguments
            return create_model(model_name, __config__=ConfigDict(extra="allow"))

        field_definitions: dict[str, Any] = {}
        for prop_name, prop_spec in properties.items():
            if not isinstance(prop_spec, dict):
                field_definitions[prop_name] = (Any, Field(default=None))
                continue

            py_type = _json_type_to_python(prop_spec.get("type"))
            description = str(prop_spec.get("description", ""))
            default_val = prop_spec.get("default", None)

            is_required = prop_name in required_fields and default_val is None

            if is_required:
                field_definitions[prop_name] = (
                    py_type,
                    Field(..., description=description),
                )
            else:
                field_definitions[prop_name] = (
                    py_type | None,
                    Field(default=default_val, description=description),
                )

        return create_model(
            model_name,
            __config__=ConfigDict(extra="allow"),
            **field_definitions,
        )

    @classmethod
    def adapt(
        cls,
        tool_def: MCPToolDefinition,
        executor_or_connector_id: Any,
        mcp_host: Any = None,
        *,
        connector_id: str | None = None,
        level: str = "external",
        description: str | None = None,
    ) -> Tool:
        """Wrap an MCPToolDefinition into a Vigil Tool instance.

        Args:
            tool_def: Discovered MCP tool definition with name, description, schema.
            executor_or_connector_id: Either an MCPTransportProtocol / executor instance,
                or a connector_id string if mcp_host is supplied as the third positional arg.
            mcp_host: Optional MCPHostProtocol instance when connector_id is passed as second arg.
            connector_id: Optional connector id string.
            level: Approval level; defaults to 'external'.
            description: Optional description override.

        Returns:
            A Vigil Tool instance ready for registration and execution.
        """
        if isinstance(executor_or_connector_id, str) and mcp_host is not None:
            resolved_connector_id = executor_or_connector_id
            executor = mcp_host
        else:
            resolved_connector_id = connector_id or "default"
            executor = executor_or_connector_id

        args_model = cls.create_args_model(tool_def)
        tool_desc = description or tool_def.description or f"External MCP tool {tool_def.name}"
        approval_lvl: ApprovalLevel = (
            level if level in APPROVAL_LEVELS else "external"  # type: ignore[assignment]
        )

        async def handler(parsed: BaseModel, context: ToolContext) -> ToolResult:
            args = parsed.model_dump()

            # Mode 1: MCPTransportProtocol with call_tool(name, arguments) -> (output, is_error)
            if hasattr(executor, "call_tool") and callable(executor.call_tool):
                try:
                    outcome = executor.call_tool(tool_def.name, args)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                    output, is_error = outcome
                    if is_error:
                        return ToolResult.failure(str(output))
                    return ToolResult.success(output)
                except Exception as exc:
                    return ToolResult.failure(
                        f"MCP tool {tool_def.name!r} transport call failed: {exc}"
                    )

            # Mode 2: MCPHostProtocol with execute_tool(connector_id, name, arguments)
            if hasattr(executor, "execute_tool") and callable(executor.execute_tool):
                try:
                    outcome = executor.execute_tool(
                        resolved_connector_id, tool_def.name, args
                    )
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                    if isinstance(outcome, ToolResult):
                        return outcome
                    is_error = getattr(outcome, "is_error", False)
                    output = getattr(outcome, "output", outcome)
                    if is_error:
                        return ToolResult.failure(str(output))
                    return ToolResult.success(output)
                except Exception as exc:
                    return ToolResult.failure(
                        f"MCP tool {tool_def.name!r} host execution failed: {exc}"
                    )

            # Mode 3: Direct callable(name, arguments)
            if callable(executor):
                try:
                    outcome = executor(tool_def.name, args)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                    if isinstance(outcome, ToolResult):
                        return outcome
                    if isinstance(outcome, tuple) and len(outcome) == 2:
                        output, is_error = outcome
                        if is_error:
                            return ToolResult.failure(str(output))
                        return ToolResult.success(output)
                    return ToolResult.success(outcome)
                except Exception as exc:
                    return ToolResult.failure(
                        f"MCP tool {tool_def.name!r} callable failed: {exc}"
                    )

            return ToolResult.failure(
                f"No suitable transport or executor found for MCP tool {tool_def.name!r}"
            )

        return Tool.define(
            name=tool_def.name,
            description=tool_desc,
            level=approval_lvl,
            args_model=args_model,
            handler=handler,
        )


__all__ = ["MCPToolAdapterFactory"]
