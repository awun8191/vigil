"""A fake connector host for the Phase 4 end-to-end test.

The run reaches an external tool only after a human approval. The fake records
every call, so the test can prove that the connector ran once, after the
decision, and never on the request path.
"""
from __future__ import annotations

from typing import Any

from src.integrations.repositories import MCPToolDefinitionDTO, MCPToolResultDTO


class FakeConnector:
    """A connector host that records each external call and succeeds."""

    def __init__(self, output: str = "message sent") -> None:
        self.output = output
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def list_tools(self, connector_id: str) -> list[MCPToolDefinitionDTO]:
        """Return no declarations. The run never lists tools."""
        return []

    async def execute_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str = "",
    ) -> MCPToolResultDTO:
        """Record one external call and return a success result."""
        self.calls.append((connector_id, tool_name, dict(arguments)))
        return MCPToolResultDTO(
            tool_call_id=tool_call_id,
            output=self.output,
            is_error=False,
        )

    async def get_tool_approval_policy(self, connector_id: str, tool_name: str) -> str:
        """Report that a connector tool needs a human approval."""
        return "external"

    async def is_healthy(self, connector_id: str) -> bool:
        """Report a healthy connector."""
        return True

    def tool_names(self) -> list[str]:
        """Return the external tool names in call order."""
        return [tool_name for _, tool_name, _ in self.calls]
